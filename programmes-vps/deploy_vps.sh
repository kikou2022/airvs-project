#!/bin/bash
# ============================================================
# Déploiement — App Programmes AIRVS → VPS OVH 1
# ============================================================
# À placer dans le dossier de développement :
#   .../dashboard/programmes-vps/
#
# Usage :
#   bash deploy_vps.sh              # déploiement standard (fichiers + restart)
#   bash deploy_vps.sh --app-only   # déploie uniquement app_vps_ovh_1.py
#   bash deploy_vps.sh --no-restart # déploie sans redémarrer le service
#   bash deploy_vps.sh --dry-run    # montre ce qui serait fait
#   bash deploy_vps.sh --status     # affiche le statut du service distant
#   bash deploy_vps.sh --logs       # affiche les 50 dernières lignes de log
#   bash deploy_vps.sh --pip        # force la mise à jour des dépendances
#   bash deploy_vps.sh --full       # fichiers + pip + restart
#   bash deploy_vps.sh --sync-patch # déploie aussi le patch worker
# ============================================================

set -euo pipefail

# --- Configuration ---
VPS_USER="ubuntu"
VPS_HOST="54.37.38.117"
APP_DIR="/opt/airvs-programmes"
SERVICE_NAME="airvs-programmes"
SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
MAX_BACKUPS=3

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# --- Flags ---
FLAG_APP_ONLY=false
FLAG_NO_RESTART=false
FLAG_DRY_RUN=false
FLAG_STATUS=false
FLAG_LOGS=false
FLAG_PIP=false
FLAG_FULL=false
FLAG_SYNC_PATCH=false

for arg in "$@"; do
    case "$arg" in
        --app-only)   FLAG_APP_ONLY=true ;;
        --no-restart) FLAG_NO_RESTART=true ;;
        --dry-run)    FLAG_DRY_RUN=true ;;
        --status)     FLAG_STATUS=true ;;
        --logs)       FLAG_LOGS=true ;;
        --pip)        FLAG_PIP=true ;;
        --full)       FLAG_FULL=true ;;
        --sync-patch) FLAG_SYNC_PATCH=true ;;
        --help|-h)
            head -25 "$0" | tail -20
            exit 0
            ;;
        *)
            echo -e "${RED}Option inconnue : $arg${NC}"
            echo "Utilisez --help pour voir les options disponibles."
            exit 1
            ;;
    esac
done

# ============================================================
# Fonctions utilitaires
# ============================================================

log_ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
log_info() { echo -e "  ${CYAN}[INFO]${NC} $1"; }
log_warn() { echo -e "  ${YELLOW}[!!]${NC} $1"; }
log_err()  { echo -e "  ${RED}[ERREUR]${NC} $1"; }

remote() {
    if [ "$FLAG_DRY_RUN" = true ]; then
        echo -e "  ${CYAN}[SSH]${NC} $1"
    else
        ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "$1"
    fi
}

scp_to_vps() {
    local src="$1"
    local dest="$2"
    if [ "$FLAG_DRY_RUN" = true ]; then
        echo -e "  ${CYAN}[SCP]${NC} $src → $VPS_HOST:$dest"
    else
        scp $SSH_OPTS "$src" "$VPS_USER@$VPS_HOST:$dest"
    fi
}

# ============================================================
# Vérifications locales
# ============================================================

if ! ssh $SSH_OPTS -o BatchMode=yes "$VPS_USER@$VPS_HOST" "exit 0" 2>/dev/null; then
    log_err "Impossible de se connecter à $VPS_USER@$VPS_HOST"
    echo "  Vérifiez que :"
    echo "    1. La clé SSH est configurée pour $VPS_USER@$VPS_HOST"
    echo "    2. Le VPS est accessible sur le réseau"
    echo "    3. Vous pouvez tester avec : ssh $VPS_USER@$VPS_HOST"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FILE_APP="$SCRIPT_DIR/app_vps_ovh_1.py"
FILE_INIT_DB="$SCRIPT_DIR/init_db_vps_ovh_1.py"
FILE_REQUIREMENTS="$SCRIPT_DIR/requirements_vps_ovh_1.txt"
FILE_SYNC_PATCH="$SCRIPT_DIR/worker_vps_sync_patch.py"

# --- Mode : statut du service ---
if [ "$FLAG_STATUS" = true ]; then
    echo -e "${CYAN}=== Statut service $SERVICE_NAME ===${NC}"
    remote "sudo systemctl status $SERVICE_NAME --no-pager -l 2>&1 | head -25"
    echo ""
    echo -e "${CYAN}=== Miroir DB ===${NC}"
    remote "ls -lh /var/www/html/airvs_mirror/db.json 2>/dev/null || echo '  Fichier absent'"
    echo ""
    echo -e "${CYAN}=== Dernières soumissions ===${NC}"
    remote "sudo sqlite3 $APP_DIR/programmes_vps.db \"SELECT date_soumission, artiste, titre, statut FROM soumissions ORDER BY id DESC LIMIT 5;\" 2>/dev/null || echo '  Base absente ou vide'"
    exit 0
fi

# --- Mode : logs ---
if [ "$FLAG_LOGS" = true ]; then
    echo -e "${CYAN}=== Logs $SERVICE_NAME (50 dernières lignes) ===${NC}"
    remote "sudo journalctl -u $SERVICE_NAME --no-pager -n 50"
    exit 0
fi

# --- Vérification des fichiers sources ---
if [ "$FLAG_APP_ONLY" = false ]; then
    for f in "$FILE_APP" "$FILE_INIT_DB" "$FILE_REQUIREMENTS"; do
        if [ ! -f "$f" ]; then
            log_err "Fichier manquant : $f"
            echo "  Le script doit être lancé depuis le dossier contenant les fichiers sources."
            exit 1
        fi
    done
    log_ok "Dossier source : $SCRIPT_DIR"
else
    if [ ! -f "$FILE_APP" ]; then
        log_err "Fichier manquant : $FILE_APP"
        exit 1
    fi
fi

# ============================================================
# Déploiement
# ============================================================

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN} Déploiement AIRVS Programmes → VPS OVH 1${NC}"
echo -e "${CYAN}============================================================${NC}"
echo -e "  VPS       : $VPS_USER@$VPS_HOST"
echo -e "  Cible     : $APP_DIR"
echo -e "  Service   : $SERVICE_NAME"
echo -e "  Source    : $SCRIPT_DIR"
if [ "$FLAG_DRY_RUN" = true ]; then
    echo -e "  ${YELLOW}MODE DRY-RUN (aucune modification)${NC}"
fi
echo ""

# --- Étape 1 : Vérification dossier cible ---
echo -e "${CYAN}--- Étape 1 : Vérification remote ---${NC}"
remote "mkdir -p $APP_DIR/logs $APP_DIR/data"
log_ok "Dossiers distants prêts"

# --- Étape 2 : Sauvegarde rotative sur le VPS ---
echo ""
echo -e "${CYAN}--- Étape 2 : Sauvegarde rotative ---${NC}"
if [ "$FLAG_DRY_RUN" = false ]; then
    # Sauvegarder l'app actuelle
    remote "cp $APP_DIR/app_vps_ovh_1.py $APP_DIR/app_vps_ovh_1.py.bak.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true"
    log_info "Sauvegarde créée"

    # Purger les backups au-delà de MAX_BACKUPS (garder les plus récents)
    remote "ls -1t $APP_DIR/app_vps_ovh_1.py.bak.* 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs -r rm -f"
    BACKUP_COUNT=$(remote "ls -1 $APP_DIR/app_vps_ovh_1.py.bak.* 2>/dev/null | wc -l" || echo "0")
    log_ok "Backups sur VPS : $BACKUP_COUNT (max $MAX_BACKUPS)"
else
    log_info "[DRY-RUN] Sauvegarde + purge"
fi

# --- Étape 3 : Transfert des fichiers ---
echo ""
echo -e "${CYAN}--- Étape 3 : Transfert des fichiers ---${NC}"

scp_to_vps "$FILE_APP" "$APP_DIR/app_vps_ovh_1.py"
log_ok "app_vps_ovh_1.py"

if [ "$FLAG_APP_ONLY" = false ]; then
    scp_to_vps "$FILE_INIT_DB" "$APP_DIR/init_db_vps_ovh_1.py"
    log_ok "init_db_vps_ovh_1.py"

    scp_to_vps "$FILE_REQUIREMENTS" "$APP_DIR/requirements_vps_ovh_1.txt"
    log_ok "requirements_vps_ovh_1.txt"
fi

if [ "$FLAG_SYNC_PATCH" = true ] && [ -f "$FILE_SYNC_PATCH" ]; then
    scp_to_vps "$FILE_SYNC_PATCH" "$APP_DIR/worker_vps_sync_patch.py"
    log_ok "worker_vps_sync_patch.py"
fi

# Permissions
remote "chmod 644 $APP_DIR/app_vps_ovh_1.py $APP_DIR/init_db_vps_ovh_1.py $APP_DIR/requirements_vps_ovh_1.txt"
remote "chown $VPS_USER:$VPS_USER $APP_DIR/app_vps_ovh_1.py $APP_DIR/init_db_vps_ovh_1.py $APP_DIR/requirements_vps_ovh_1.txt"

# --- Étape 4 : Vérification intégrité MD5 ---
echo ""
echo -e "${CYAN}--- Étape 4 : Vérification intégrité ---${NC}"
MD5_SRC=$(md5sum "$FILE_APP" | awk '{print $1}')
MD5_DST=$(remote "md5sum $APP_DIR/app_vps_ovh_1.py 2>/dev/null | awk '{print $1}'" || echo "")
if [ "$MD5_SRC" = "$MD5_DST" ] && [ -n "$MD5_DST" ]; then
    log_ok "MD5 app_vps_ovh_1.py identique (source ↔ VPS)"
else
    log_err "MD5 différent ou vide ! Src=$MD5_SRC Dst=$MD5_DST"
    if [ "$FLAG_DRY_RUN" = false ]; then
        echo "  Le transfert a peut-être échoué. Vérifiez manuellement :"
        echo "    ssh $VPS_USER@$VPS_HOST "md5sum $APP_DIR/app_vps_ovh_1.py""
        exit 1
    fi
fi

# --- Étape 5 : Vérification miroir ---
echo ""
echo -e "${CYAN}--- Étape 5 : Vérification miroir ---${NC}"
MIRROR_INFO=$(remote "ls -lh /var/www/html/airvs_mirror/db.json 2>/dev/null" || true)
if [ -n "$MIRROR_INFO" ]; then
    echo "  $MIRROR_INFO"
    log_ok "Miroir présent"
else
    log_warn "Miroir absent (/var/www/html/airvs_mirror/db.json)"
    echo "  Le matching ne fonctionnera pas tant que le miroir n'est pas copié."
fi

# --- Étape 6 : Dépendances Python ---
if [ "$FLAG_PIP" = true ] || [ "$FLAG_FULL" = true ]; then
    echo ""
    echo -e "${CYAN}--- Étape 6 : Mise à jour dépendances ---${NC}"
    remote "$APP_DIR/venv/bin/pip install -r $APP_DIR/requirements_vps_ovh_1.txt -q 2>&1 | tail -3"
    log_ok "Dépendances mises à jour"
fi

# --- Étape 7 : Redémarrage du service ---
if [ "$FLAG_NO_RESTART" = false ]; then
    echo ""
    echo -e "${CYAN}--- Étape 7 : Redémarrage service ---${NC}"

    if [ "$FLAG_DRY_RUN" = false ]; then
        remote "sudo systemctl restart $SERVICE_NAME"
        sleep 2

        if remote "sudo systemctl is-active --quiet $SERVICE_NAME" 2>/dev/null; then
            log_ok "Service $SERVICE_NAME redémarré et actif"
        else
            log_err "Le service n'a pas démarré correctement !"
            echo ""
            echo -e "${RED}=== Dernières lignes de log ===${NC}"
            remote "sudo journalctl -u $SERVICE_NAME --no-pager -n 20"
            echo ""
            echo "Commande pour restaurer la dernière sauvegarde :"
            echo "  ssh $VPS_USER@$VPS_HOST"
            echo "  sudo cp \$(ls -t $APP_DIR/app_vps_ovh_1.py.bak.* | head -1) $APP_DIR/app_vps_ovh_1.py"
            echo "  sudo systemctl restart $SERVICE_NAME"
            exit 1
        fi

        HTTP_CODE=$(remote "curl -sk -o /dev/null -w '%{http_code}' https://programmes.airvs.fr/login 2>/dev/null" || echo "000")
        if [ "$HTTP_CODE" = "200" ]; then
            log_ok "HTTPS programmes.airvs.fr/login → 200"
        else
            log_warn "HTTPS programmes.airvs.fr/login → $HTTP_CODE (peut être normal si pas de DNS local)"
        fi
    else
        log_info "[DRY-RUN] sudo systemctl restart $SERVICE_NAME"
    fi
else
    echo ""
    log_info "Redémarrage du service ignoré (--no-restart)"
fi

# ============================================================
# Résumé
# ============================================================
echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${GREEN} Déploiement terminé !${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
echo "  Fichiers déployés :"
echo "    → $APP_DIR/app_vps_ovh_1.py"
if [ "$FLAG_APP_ONLY" = false ]; then
    echo "    → $APP_DIR/init_db_vps_ovh_1.py"
    echo "    → $APP_DIR/requirements_vps_ovh_1.txt"
fi
if [ "$FLAG_SYNC_PATCH" = true ]; then
    echo "    → $APP_DIR/worker_vps_sync_patch.py"
fi
echo ""
echo "  Commandes utiles :"
echo "    bash $0 --status     # Vérifier le statut"
echo "    bash $0 --logs       # Voir les logs"
echo "    bash $0 --app-only   # Déployer uniquement l'app"
echo "    bash $0 --full        # Déploiement complet + pip"
echo "    bash $0 --dry-run    # Simulation"
echo ""