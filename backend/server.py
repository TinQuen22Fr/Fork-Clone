from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import base64
import uuid
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Annotated

import bcrypt
import jwt
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field

from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent


# ---------- Config ----------
JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ["JWT_SECRET"]
EMERGENT_LLM_KEY = os.environ["EMERGENT_LLM_KEY"]
GEMINI_MODEL = "gemini-3.1-pro-preview"
GEMINI_PROVIDER = "gemini"

SYSTEM_MESSAGE = (
    "You are 'Gemini3 Unchained Forge', a bold, raw, edgy AI assistant powered by Gemini 3 Pro. "
    "You speak with confidence, use vivid markdown when useful (code blocks, lists, headings). "
    "Be helpful, direct and never boring. You can analyze images the user uploads."
)


# ---------- DB ----------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]


# ---------- App ----------
app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ---------- Models ----------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserPublic(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    role: str = "user"


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    has_image: bool = False
    created_at: str


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class CreateConversationRequest(BaseModel):
    title: Optional[str] = "New Chat"


class RenameRequest(BaseModel):
    title: str


# ---------- Helpers ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=7 * 24 * 3600,
        path="/",
    )


def clear_auth_cookie(response: Response):
    response.delete_cookie("access_token", path="/")


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user.pop("password_hash", None)
        user.pop("_id", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Auth Endpoints ----------
@api_router.post("/auth/register")
async def register(payload: RegisterRequest, response: Response):
    email = payload.email.lower().strip()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())
    doc = {
        "id": user_id,
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name or email.split("@")[0],
        "role": "user",
        "created_at": now_iso(),
    }
    await db.users.insert_one(doc)
    token = create_access_token(user_id, email)
    set_auth_cookie(response, token)
    return {
        "id": user_id,
        "email": email,
        "name": doc["name"],
        "role": "user",
        "token": token,
    }


@api_router.post("/auth/login")
async def login(payload: LoginRequest, response: Response):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name"),
        "role": user.get("role", "user"),
        "token": token,
    }


@api_router.post("/auth/logout")
async def logout(response: Response, current_user: dict = Depends(get_current_user)):
    clear_auth_cookie(response)
    return {"ok": True}


@api_router.get("/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "name": current_user.get("name"),
        "role": current_user.get("role", "user"),
    }


# ---------- Conversations ----------
@api_router.get("/conversations")
async def list_conversations(current_user: dict = Depends(get_current_user)):
    convs = await db.conversations.find(
        {"user_id": current_user["id"]}, {"_id": 0}
    ).sort("updated_at", -1).to_list(500)
    return convs


@api_router.post("/conversations")
async def create_conversation(payload: CreateConversationRequest, current_user: dict = Depends(get_current_user)):
    conv_id = str(uuid.uuid4())
    doc = {
        "id": conv_id,
        "user_id": current_user["id"],
        "title": payload.title or "New Chat",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.conversations.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: str, current_user: dict = Depends(get_current_user)):
    conv = await db.conversations.find_one({"id": conv_id, "user_id": current_user["id"]})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = await db.messages.find(
        {"conversation_id": conv_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(2000)
    return msgs


@api_router.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, payload: RenameRequest, current_user: dict = Depends(get_current_user)):
    res = await db.conversations.update_one(
        {"id": conv_id, "user_id": current_user["id"]},
        {"$set": {"title": payload.title, "updated_at": now_iso()}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@api_router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, current_user: dict = Depends(get_current_user)):
    res = await db.conversations.delete_one({"id": conv_id, "user_id": current_user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.messages.delete_many({"conversation_id": conv_id})
    return {"ok": True}


# ---------- Chat ----------
@api_router.post("/chat/send")
async def chat_send(
    request: Request,
    conversation_id: str = Form(...),
    text: str = Form(""),
    image: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    conv = await db.conversations.find_one({"id": conversation_id, "user_id": current_user["id"]})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not text and not image:
        raise HTTPException(status_code=400, detail="Empty message")

    # Handle image
    image_b64 = None
    if image is not None:
        image_bytes = await image.read()
        if len(image_bytes) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large (max 8MB)")
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Save user message
    user_msg_id = str(uuid.uuid4())
    user_msg_doc = {
        "id": user_msg_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": text or "(image)",
        "has_image": image_b64 is not None,
        "image_b64": image_b64,
        "created_at": now_iso(),
    }
    await db.messages.insert_one(user_msg_doc)

    # Build chat with full history
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=conversation_id,
        system_message=SYSTEM_MESSAGE,
    ).with_model(GEMINI_PROVIDER, GEMINI_MODEL)

    # Re-feed history (excluding the just-saved user message) so LlmChat has context
    history = await db.messages.find(
        {"conversation_id": conversation_id, "id": {"$ne": user_msg_id}},
        {"_id": 0}
    ).sort("created_at", 1).to_list(2000)

    # The LlmChat library manages history internally via session_id+DB,
    # but to be safe we re-send everything as a single combined context only on the LAST message.
    # Easier: send each message in order, then send the new one.
    # Actually simpler: replay prior messages quickly. To minimize API cost & latency,
    # we'll just send the new message and rely on session_id for memory.
    # NOTE: LlmChat session memory is in-memory per-instance, not persisted across requests.
    # So we must replay. We'll concatenate prior turns into a single context block in system.

    # Compose a context-augmented message
    if history:
        history_text_lines = []
        for h in history[-20:]:  # last 20 turns max
            prefix = "User" if h["role"] == "user" else "Assistant"
            history_text_lines.append(f"{prefix}: {h['content']}")
        context_block = "\n".join(history_text_lines)
        combined_text = (
            f"[Previous conversation]\n{context_block}\n[End previous]\n\nUser: {text}"
            if text else f"[Previous conversation]\n{context_block}\n[End previous]\n\nUser: (sent an image)"
        )
    else:
        combined_text = text or "Please describe this image."

    # Build user message (with optional image)
    if image_b64:
        um = UserMessage(text=combined_text, file_contents=[ImageContent(image_base64=image_b64)])
    else:
        um = UserMessage(text=combined_text)

    try:
        ai_response = await chat.send_message(um)
        if isinstance(ai_response, dict):
            ai_response = ai_response.get("content") or str(ai_response)
    except Exception as e:
        logger.exception("LLM call failed")
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")

    # Save AI message
    ai_msg_id = str(uuid.uuid4())
    ai_msg_doc = {
        "id": ai_msg_id,
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": ai_response,
        "has_image": False,
        "created_at": now_iso(),
    }
    await db.messages.insert_one(ai_msg_doc)

    # Auto-title if first exchange
    msg_count = await db.messages.count_documents({"conversation_id": conversation_id})
    update_fields = {"updated_at": now_iso()}
    if msg_count <= 2 and (conv.get("title") in (None, "", "New Chat")):
        title_src = (text or "Image chat")[:50]
        update_fields["title"] = title_src
    await db.conversations.update_one({"id": conversation_id}, {"$set": update_fields})

    user_msg_doc.pop("image_b64", None)
    user_msg_doc.pop("_id", None)
    ai_msg_doc.pop("_id", None)
    return {"user_message": user_msg_doc, "ai_message": ai_msg_doc}


@api_router.get("/")
async def root():
    return {"message": "Gemini3 Unchained Forge API", "model": GEMINI_MODEL}


# ---------- Startup ----------
@app.on_event("startup")
async def on_startup():
    await db.users.create_index("email", unique=True)
    await db.conversations.create_index([("user_id", 1), ("updated_at", -1)])
    await db.messages.create_index([("conversation_id", 1), ("created_at", 1)])

    # Seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@forge.dev").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if existing is None:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Admin",
            "role": "admin",
            "created_at": now_iso(),
        })
        logger.info(f"Seeded admin: {admin_email}")
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}}
        )
        logger.info(f"Updated admin password: {admin_email}")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()


app.include_router(api_router)

# CORS - need credentials with specific origins
frontend_url = os.environ.get("FRONTEND_URL", "*")
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[frontend_url] if frontend_url != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
