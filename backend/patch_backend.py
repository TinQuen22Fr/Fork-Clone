import re

file_path = "backend/server.py"
with open(file_path, "r") as f:
    content = f.read()

# 1. Ajouter le modèle AlertSettings après Favorite
new_model = """
class AlertSettings(BaseModel):
    enabled: bool = True
    radius_km: int = Field(20, ge=5, le=50)
    min_strikes: int = Field(1, ge=1)
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "07:00"
"""
content = content.replace("class Favorite(BaseModel):", new_model + "\nclass Favorite(BaseModel):")

# 2. Ajouter les routes alert-settings avant # ---------- Admin ----------
new_routes = """
@api_router.get("/user/alert-settings")
async def get_alert_settings(user=Depends(get_current_user)):
    user_doc = await db.users.find_one({"id": user["id"]}, {"alert_settings": 1})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    return user_doc.get("alert_settings", AlertSettings().model_dump())

@api_router.put("/user/alert-settings")
async def update_alert_settings(payload: AlertSettings, user=Depends(get_current_user)):
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"alert_settings": payload.model_dump()}}
    )
    return {"ok": True}
"""
content = content.replace("# ---------- Admin ----------", new_routes + "\n\n# ---------- Admin ----------")

with open(file_path, "w") as f:
    f.write(content)
