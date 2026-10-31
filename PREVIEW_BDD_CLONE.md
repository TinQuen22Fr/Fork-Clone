# BDD preview : clonage automatique de la prod

Objectif : les previews lancees par la Forge ne doivent **jamais** ecrire dans
la base MongoDB de production. Elles utilisent une base dediee `forge_preview`,
qui est un clone frais de la base de prod reinitialise a chaque execution de
`upgrade.sh`.

## Principe

- Base de prod (ex. `forge`) : seule la Forge elle-meme y accede.
- Base preview `forge_preview` : utilisee uniquement par les conteneurs de
  preview demarres par `preview_runtime.py`.
- `upgrade.sh` ecrase `forge_preview` avec un dump complet de la base de prod,
  avant de rendre la main. La preview reflete donc l'etat de la prod au
  moment du dernier deploiement.
- Aucune anonymisation pour l'instant (clone brut). Les donnees sont dupliquees
  telles quelles. A durcir plus tard si besoin, sans retravailler l'infra.

## Ce qui a ete fait, et quand

| Horodatage (UTC)       | Action                                                      | Fichier                        |
|------------------------|-------------------------------------------------------------|--------------------------------|
| 2026-10-31T00:00:00Z   | Ajout de l'etape "Clonage BDD preview" dans `upgrade.sh`    | `upgrade.sh`                   |
| 2026-10-31T00:00:00Z   | Injection forcee `MONGO_URL` + `DB_NAME=forge_preview` dans les conteneurs preview | `backend/preview_runtime.py` |
| 2026-10-31T00:00:00Z   | Creation de ce document                                     | `PREVIEW_BDD_CLONE.md`         |

(L'horodatage reel du clonage est ecrit dans `.forge-preview-db-clone` a la
racine du projet apres chaque `upgrade.sh` reussi.)

## Mecanique

### Clonage (upgrade.sh)

Lu dans `backend/.env` :

- `MONGO_URL` (defaut : `mongodb://localhost:27017`) ;
- `DB_NAME`   (defaut : `forge`).

Commandes executees :

```bash
mongodump --uri="$MONGO_URL" --db="$DB_NAME" --archive=/tmp/forge-preview-clone.XXXX.archive --quiet
mongorestore --uri="$MONGO_URL" --archive=/tmp/forge-preview-clone.XXXX.archive --drop \
             --nsFrom="${DB_NAME}.*" --nsTo="forge_preview.*" --quiet
```

- `--drop` supprime les collections cibles avant restauration : `forge_preview`
  est systematiquement reinitialisee.
- `--nsFrom/--nsTo` renomme l'espace de noms a la volee : pas besoin de
  manipuler un fichier intermediaire pour changer le nom de base.
- En cas d'echec de l'une des deux etapes, l'upgrade **continue** : un
  avertissement est affiche, et le journal de la commande est pointe. On ne
  bloque jamais un deploiement pour un probleme de clonage.
- Un fichier `.forge-preview-db-clone` est ecrit a la racine du projet apres
  succes, contenant : date du clone, bases source/cible, duree, taille archive.

### Injection dans les previews (`backend/preview_runtime.py`)

Deux constantes de classe et une methode dediee :

```python
_PREVIEW_MONGO_URL = "mongodb://172.31.0.1:27017"
_PREVIEW_DB_NAME   = "forge_preview"

def _preview_db_env(self) -> dict[str, str]:
    return {"MONGO_URL": self._PREVIEW_MONGO_URL, "DB_NAME": self._PREVIEW_DB_NAME}
```

Ces deux valeurs sont injectees **apres** `_project_env`, donc elles
overridant toute valeur de meme nom qui serait presente dans le `.env` d'un
projet. Deux points d'injection :

- demarrage docker : via `--env CLE=VAL` passes au wrapper `forge-preview start` ;
- driver legacy non-docker : via `env.update(...)` avant exec.

`172.31.0.1` est l'adresse de la gateway du bridge Docker vue depuis le
conteneur : c'est ainsi que la preview atteint le `mongod` qui tourne sur
l'hote (ecoutant sur `127.0.0.1` cote hote).

## Prerequis cote serveur

A faire **une seule fois**, hors de ce depot, par toi (root) :

1. **Autoriser `mongod` a ecouter la gateway Docker.**

   Editer `/etc/mongod.conf` :

   ```bash
   sudo nano /etc/mongod.conf
   ```

   Trouver la section `net:` et ajouter `172.31.0.1` dans `bindIp` :

   ```yaml
   net:
     port: 27017
     bindIp: 127.0.0.1,172.31.0.1
   ```

   Puis redemarrer :

   ```bash
   sudo systemctl restart mongod
   sudo systemctl status mongod --no-pager
   ```

   Verification :

   ```bash
   ss -lntp | grep 27017
   # doit montrer 127.0.0.1:27017 ET 172.31.0.1:27017
   ```

2. **Installer les outils Mongo necessaires au dump/restore.**

   ```bash
   mongodump --version   # doit repondre
   mongorestore --version
   ```

   Si absent (sur Debian/Ubuntu) :

   ```bash
   sudo apt-get update
   sudo apt-get install -y mongodb-database-tools
   ```

3. **(Facultatif) Verifier qu'on peut atteindre mongod depuis le reseau Docker.**

   Un conteneur du reseau `forge-preview-net` doit pouvoir ouvrir `172.31.0.1:27017`.
   Si le reseau Docker n'utilise pas ce sous-reseau, remplacer la constante
   `_PREVIEW_MONGO_URL` dans `backend/preview_runtime.py` par la gateway
   effective (vue avec `docker network inspect forge-preview-net`).

## Securite

- Mongo tourne sans authentification sur cet hote. Une preview a donc
  techniquement acces aux deux bases (`forge` et `forge_preview`) via le port
  27017. La convention `DB_NAME=forge_preview` cote preview suffit car tu es
  l'unique utilisateur et le code embarque dans la preview est le tien.
- Pour durcir : activer l'auth Mongo, creer un utilisateur `preview_user`
  limite a `forge_preview`, remplacer `_PREVIEW_MONGO_URL` par une URL avec
  credentials. Chantier separe.

## Rafraichir la BDD preview sans redeployer (futur)

Non implemente aujourd'hui. Pour l'activer plus tard sans retoucher l'infra :
exposer un endpoint backend qui appelle le meme `mongodump | mongorestore`,
protege par l'auth de la Forge, declenche depuis l'UI (bouton "Rafraichir BDD
preview" a cote du bouton preview).

## Verifications

Apres un `upgrade.sh` reussi :

```bash
# Horodatage du dernier clone (ecrit par upgrade.sh)
cat /var/www/forge/.forge-preview-db-clone

# La base preview existe et contient les memes collections que la prod
mongosh --quiet --eval 'db.getSiblingDB("forge_preview").getCollectionNames()'
mongosh --quiet --eval 'db.getSiblingDB("forge").getCollectionNames()'
```

Lancer une preview, puis depuis l'hote verifier que le conteneur voit bien
`DB_NAME=forge_preview` :

```bash
sudo docker exec forge-preview-<projet> env | grep -E '^(MONGO_URL|DB_NAME)='
# MONGO_URL=mongodb://172.31.0.1:27017
# DB_NAME=forge_preview
```
