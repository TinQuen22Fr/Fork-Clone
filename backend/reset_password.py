#!/usr/bin/env python3
"""
Réinitialise le mot de passe d'un compte existant, directement en base.

Usage (le mot de passe est demandé de façon masquée, jamais dans l'historique) :

    cd /var/www/forge/backend
    /var/www/forge/venv/bin/python reset_password.py admin@forge.dev

Le script lit MONGO_URL / DB_NAME depuis le .env, exactement comme le serveur.
"""
import sys
import asyncio
import getpass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import os
import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def main() -> int:
    if len(sys.argv) != 2:
        print("Usage : python reset_password.py <email>")
        return 2

    email = sys.argv[1].lower().strip()

    mongo_url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "forge").strip()
    if not mongo_url:
        print("MONGO_URL absent du .env — impossible de continuer.")
        return 1

    # Saisie masquée + confirmation.
    pw1 = getpass.getpass("Nouveau mot de passe : ")
    if len(pw1) < 6:
        print("Mot de passe trop court (minimum 6 caractères).")
        return 1
    pw2 = getpass.getpass("Confirmer : ")
    if pw1 != pw2:
        print("Les deux saisies diffèrent.")
        return 1

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = client[db_name]

    user = await db.users.find_one({"email": email})
    if not user:
        print(f"Aucun compte avec l'email {email}.")
        client.close()
        return 1

    await db.users.update_one(
        {"email": email},
        {"$set": {"password_hash": hash_password(pw1)}},
    )
    client.close()
    print(f"Mot de passe mis à jour pour {email}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
