import re

file_path = "backend/push.py"
with open(file_path, "r") as f:
    content = f.read()

# Modifier _dispatch dans push.py pour filtrer par utilisateur
# On doit ajouter la logique de filtrage ici avant de passer à fcm.
# Mais push.py n'a pas accès au modèle User ou AlertSettings facilement.
