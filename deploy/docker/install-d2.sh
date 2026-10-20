#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d2.sh — Étape D2 : contrôle d'accès sans donner root au backend.
#   Installe le wrapper /usr/local/bin/forge-preview et la règle sudoers dédiée.
#   Le backend tourne en tant que `quentin` : il ne reçoit AUCUN accès docker
#   direct (pas de groupe `docker`, pas de socket), seulement ce wrapper fermé.
#
#   sudo bash deploy/docker/install-d2.sh
#
# Idempotent. Ne démarre aucun conteneur.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${FORGE_SERVICE_USER:-quentin}"
WRAPPER_SRC="$SRC_DIR/forge-preview"
WRAPPER_DST="/usr/local/bin/forge-preview"
SUDOERS_DST="/etc/sudoers.d/forge-preview-docker"
STATEDIR="/var/lib/forge-preview"

log()  { printf '\033[1;34m[D2]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[D2]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[D2]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "à lancer en root : sudo bash deploy/docker/install-d2.sh"
[ -f "$WRAPPER_SRC" ] || die "wrapper source introuvable : $WRAPPER_SRC"
id -u "$SERVICE_USER" >/dev/null 2>&1 || die "utilisateur inconnu : $SERVICE_USER"

# ---------------------------------------------------------------------------
# 1. Wrapper : copie root:root, non modifiable par le backend
# ---------------------------------------------------------------------------
log "installation du wrapper $WRAPPER_DST"
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST"

# Contrôle de bon sens : le wrapper ne doit jamais référencer le socket docker.
if grep -q 'docker\.sock' "$WRAPPER_DST"; then
  die "le wrapper référence docker.sock — installation refusée"
fi

# ---------------------------------------------------------------------------
# 2. Répertoire d'état des previews
# ---------------------------------------------------------------------------
install -o root -g root -m 0755 -d "$STATEDIR"
install -o root -g root -m 0755 -d "$STATEDIR/.docker"

# ---------------------------------------------------------------------------
# 3. Règle sudoers dédiée
# ---------------------------------------------------------------------------
log "écriture de la règle sudoers $SUDOERS_DST"
TMP_SUDO="$(mktemp /tmp/forge-preview-sudoers.XXXXXX)"
chmod 0600 "$TMP_SUDO"
cat > "$TMP_SUDO" <<SUDOEOF
# Généré par deploy/docker/install-d2.sh — étape D2 (previews Docker).
# Seul point d'entrée privilégié du backend vers Docker.
# Le wrapper valide verbe, projet (regex), port (plage) et workspace (racine).
# AUCUNE autre commande docker n'est autorisée pour cet utilisateur.
# Ne pas éditer à la main : relancer install-d2.sh.
${SERVICE_USER} ALL=(root) NOPASSWD: ${WRAPPER_DST} *
SUDOEOF

if visudo -cqf "$TMP_SUDO" 2>/dev/null; then
  install -o root -g root -m 0440 "$TMP_SUDO" "$SUDOERS_DST"
  rm -f "$TMP_SUDO"
  log "règle sudoers installée et validée par visudo"
else
  rm -f "$TMP_SUDO"
  die "règle sudoers invalide — rien n'a été installé"
fi

# ---------------------------------------------------------------------------
# 4. Vérification : aucun accès docker direct pour le backend
# ---------------------------------------------------------------------------
if id -nG "$SERVICE_USER" 2>/dev/null | grep -qw docker; then
  warn "ATTENTION : $SERVICE_USER est membre du groupe docker (équivaut à root)."
  warn "Retirer : sudo gpasswd -d $SERVICE_USER docker"
else
  log "$SERVICE_USER hors du groupe docker (conforme)."
fi

# Détection de toute règle sudoers donnant accès à docker directement.
# Seules les lignes actives sont examinées (les commentaires du fichier généré
# citent le mot « docker » sans rien autoriser).
SUDO_ACTIVE_LINES="$(grep -rhvE '^[[:space:]]*(#|$)' /etc/sudoers /etc/sudoers.d/ 2>/dev/null || true)"
if printf '%s\n' "$SUDO_ACTIVE_LINES" | grep -Eq '(^|[[:space:]])(/usr/bin/|/bin/)?docker([[:space:]]|$)'; then
  warn "une règle sudoers autorise /usr/bin/docker directement — à retirer manuellement."
else
  log "aucune règle sudoers n'autorise docker directement (conforme)."
fi

# ---------------------------------------------------------------------------
# 5. Test fonctionnel du wrapper via sudo, sans privilège interactif
# ---------------------------------------------------------------------------
log "test du wrapper via sudo -n (health)…"
if sudo -n -u root "$WRAPPER_DST" health >/tmp/d2-health.out 2>&1; then
  log "sudo -n $WRAPPER_DST health : OK ($(cat /tmp/d2-health.out | head -n1))"
else
  warn "sudo -n health a échoué — vérifier la règle sudoers (sortie ci-dessous) :"
  sed -n '1,5p' /tmp/d2-health.out
fi
rm -f /tmp/d2-health.out

log "D2 terminée. Wrapper : $WRAPPER_DST — Sudoers : $SUDOERS_DST"
