#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# Déploiement du player AIRVS + configuration des flux vers Debian 3
# ════════════════════════════════════════════════════════════════════
# Ce script :
#   1. Copie airvs_player.py vers /opt/airvs_player/ sur Debian 3
#   2. Copie flux_radio.json vers /opt/airvs_player/ (même dossier)
#   3. Redémarre le service airvs-radio pour prendre en compte les changements
#
# Prérequis (IDENTIQUES à l'ancien script) :
#   - Le fichier flux_radio.json doit exister à côté de ce script
#     (ou dans le dossier scripts_monitoring/)
#   - L'utilisateur sebastien doit avoir accès sudo sans mot de passe
#     UNIQUEMENT pour `systemctl restart airvs-radio.service` sur Debian 3
#     (ligne sudoers : sebastien ALL=(ALL) NOPASSWD: /bin/systemctl restart airvs-radio.service)
#   - Aucun autre droit sudo requis :
#       * scp crée les fichiers avec propriétaire=sebastien + permissions 644
#       * `systemctl is-active` fonctionne en lecture seule sans sudo
#       * `journalctl -u airvs-radio.service` fonctionne sans sudo
#         (le dashboard app.py l'utilise déjà ainsi via SSH)
# ════════════════════════════════════════════════════════════════════

set -e  # Arrêt en cas d'erreur

# ─── Configuration des chemins locaux ───────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SCRIPT="${SCRIPT_DIR}/airvs_player.py"
LOCAL_CONFIG="${SCRIPT_DIR}/flux_radio.json"

# ─── Configuration distante (Debian 3) ──────────────────────────────
REMOTE_USER="sebastien"
REMOTE_HOST="192.168.1.30"
REMOTE_DIR="/opt/airvs_player"
REMOTE_SCRIPT="${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/airvs_player.py"
REMOTE_CONFIG="${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/flux_radio.json"
SERVICE_NAME="airvs-radio.service"

# ─── Vérifications préalables ───────────────────────────────────────
if [ ! -f "$LOCAL_SCRIPT" ]; then
    echo "❌ ERREUR : Le fichier airvs_player.py est introuvable."
    echo "   Chemin attendu : $LOCAL_SCRIPT"
    exit 1
fi

if [ ! -f "$LOCAL_CONFIG" ]; then
    echo "⚠️  ATTENTION : Le fichier flux_radio.json est introuvable."
    echo "   Chemin attendu : $LOCAL_CONFIG"
    echo "   Le script sera copié sans le fichier de configuration."
    echo "   Le player utilisera la configuration par défaut (stream11)."
    echo ""
    read -p "Continuer quand même ? (o/N) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Oo]$ ]]; then
        echo "Abandon."
        exit 1
    fi
    COPY_CONFIG=false
else
    COPY_CONFIG=true
fi

# ─── Étape 1 : Copie du script Python ───────────────────────────────
echo "📦 [1/3] Copie du script airvs_player.py vers Debian 3..."
scp "$LOCAL_SCRIPT" "$REMOTE_SCRIPT"
echo "   ✅ Script copié vers ${REMOTE_DIR}/airvs_player.py"

# ─── Étape 2 : Copie du fichier de configuration JSON ──────────────
# Note : scp crée le fichier avec propriétaire=sebastien:sebastien
# et permissions 644 par défaut (umask), donc aucun sudo supplémentaire
# n'est nécessaire. Le service systemd peut lire ce fichier.
if [ "$COPY_CONFIG" = true ]; then
    echo "📦 [2/3] Copie du fichier flux_radio.json vers Debian 3..."
    scp "$LOCAL_CONFIG" "$REMOTE_CONFIG"
    echo "   ✅ Configuration copiée vers ${REMOTE_DIR}/flux_radio.json"
else
    echo "⏭️  [2/3] Copie du fichier flux_radio.json ignorée."
fi

# ─── Étape 3 : Redémarrage du service ──────────────────────────────
echo "🔄 [3/3] Redémarrage du service ${SERVICE_NAME} sur Debian 3..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" "sudo systemctl restart ${SERVICE_NAME}"

# Vérification du statut après redémarrage (lecture seule, pas de sudo requis)
sleep 2
echo ""
echo "📊 Statut du service après redémarrage :"
ssh "${REMOTE_USER}@${REMOTE_HOST}" "systemctl is-active ${SERVICE_NAME} 2>/dev/null && echo '   Service actif ✅' || echo '   Service inactif ❌'"

# Afficher les 5 dernières lignes du journal (sans sudo — fonctionne comme dans app.py)
echo ""
echo "📋 Dernières lignes du journal :"
ssh "${REMOTE_USER}@${REMOTE_HOST}" "journalctl -u ${SERVICE_NAME} --no-pager -n 5 --output=cat 2>/dev/null"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "✅ Déploiement terminé avec succès !"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "💡 Pour vérifier que le flux actif est bien chargé :"
echo "   ssh ${REMOTE_USER}@${REMOTE_HOST} \"journalctl -u ${SERVICE_NAME} --no-pager -n 20 | grep CONFIG\""
