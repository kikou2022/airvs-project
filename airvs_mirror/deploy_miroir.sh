#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════════
# Déploiement du Miroir AIRVS → Debian 12 (.30) + VPS OVH 1
# ════════════════════════════════════════════════════════════════════════════════
# Workflow :
#   1. Source : dossier airvs_mirror/ du repo (ce script doit être à la racine)
#   2. Copie index.html via scp vers 2 destinations :
#        - Debian 12 (.30) : /var/www/html/airvs_mirror/index.html
#        - VPS OVH 1 (54.37.38.117) : /var/www/html/airvs_mirror/index.html
#   3. Vérification MD5 post-copie
#   4. Backup rotatif (3 max) sur chaque destination
#
# Phase 0 — Migration flags AZURA_*_S6_S7 (nov. 2026) :
#   - index.html modifié pour utiliser AzuraFlags.getBadgeHTML()
#   - Inclut <script src="/static/js/modules/azura_flags.js"></script>
#   - Le helper azura_flags.js doit être déployé en parallèle
#     → /var/www/html/airvs_mirror/static/js/modules/azura_flags.js
#
# URLs cibles :
#   - Miroir local  : http://192.168.1.30/airvs_mirror/ (LAN studio)
#   - Miroir public : https://miroir.db.airvs.fr/       (Internet)
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/"

# 2 destinations (structure identique au export_db_mirror_V4.py)
DESTINATIONS=(
    "debian12|sebastien@192.168.1.30|/var/www/html/airvs_mirror"
    "vps|ubuntu@54.37.38.117|/var/www/html/airvs_mirror"
)

# Fichiers à déployer
INDEX_FILE="index.html"
HELPER_JS_DIR="static/js/modules"
HELPER_JS_FILE="azura_flags.js"

# Nombre de backups à conserver
MAX_BACKUPS=3

# Options SSH
SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

# ─── Couleurs ANSI ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { printf "  ${GREEN}✅${NC} %-50s (%s octets)\n" "$1" "$2"; }
fail() { printf "  ${RED}❌${NC} %-50s MANQUANT\n" "$1"; }
warn() { printf "  ${YELLOW}⚠${NC}  %-50s %s\n" "$1" "$2"; }
info() { printf "  ${CYAN}ℹ${NC}  %s\n" "$1"; }

echo ""
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo "${BOLD}  DÉPLOIEMENT MIROIR AIRVS → Debian .30 + VPS OVH 1${NC}"
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "── Source ──"
echo "   $SOURCE_DIR"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 : Vérifications préalables
# ══════════════════════════════════════════════════════════════════════════════
if [ ! -f "${SOURCE_DIR}${INDEX_FILE}" ]; then
    echo "${RED}❌ ERREUR : ${SOURCE_DIR}${INDEX_FILE} introuvable${NC}"
    exit 1
fi

if [ ! -f "${SOURCE_DIR}${HELPER_JS_DIR}/${HELPER_JS_FILE}" ]; then
    echo "${RED}❌ ERREUR : ${SOURCE_DIR}${HELPER_JS_DIR}/${HELPER_JS_FILE} introuvable${NC}"
    echo "   Le helper JS est requis depuis Phase 0 (AzuraFlags.isInAzuracast)"
    exit 1
fi

# Tailles des fichiers source
INDEX_SIZE=$(stat -c%s "${SOURCE_DIR}${INDEX_FILE}")
HELPER_SIZE=$(stat -c%s "${SOURCE_DIR}${HELPER_JS_DIR}/${HELPER_JS_FILE}")

ok "${INDEX_FILE}" "$INDEX_SIZE"
ok "${HELPER_JS_DIR}/${HELPER_JS_FILE}" "$HELPER_SIZE"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 : Vérification de la présence de AzuraFlags dans index.html
# ══════════════════════════════════════════════════════════════════════════════
echo "── Vérifications Phase 0 ──"
if grep -q "AzuraFlags\." "${SOURCE_DIR}${INDEX_FILE}"; then
    AZURA_COUNT=$(grep -c "AzuraFlags\." "${SOURCE_DIR}${INDEX_FILE}")
    ok "AzuraFlags.* dans index.html" "$AZURA_COUNT occurrences"
else
    fail "AzuraFlags.* dans index.html"
    echo "${RED}   index.html ne contient pas de référence à AzuraFlags${NC}"
    echo "   → Vérifier que le fichier provient bien de Phase 0"
    exit 1
fi

if grep -q "azura_flags.js" "${SOURCE_DIR}${INDEX_FILE}"; then
    ok "Include azura_flags.js" "présent"
else
    fail "Include azura_flags.js"
    echo "${RED}   index.html ne charge pas azura_flags.js${NC}"
    exit 1
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 3 : Boucle de déploiement sur chaque destination
# ══════════════════════════════════════════════════════════════════════════════
GLOBAL_OK=true
FAILED_DESTINATIONS=()

for dest in "${DESTINATIONS[@]}"; do
    # Parser : nom|host|path
    IFS='|' read -r NOM HOST DEST_PATH <<< "$dest"

    echo "${BOLD}── Destination : $NOM ($HOST) ──${NC}"
    echo "   Chemin distant : $DEST_PATH"

    # 3a. Vérifier la connexion SSH
    if ! ssh $SSH_OPTS -o BatchMode=yes "$HOST" "exit 0" 2>/dev/null; then
        warn "Connexion SSH" "impossible vers $HOST"
        echo "   → Vérifie la clé SSH et la disponibilité de la machine"
        GLOBAL_OK=false
        FAILED_DESTINATIONS+=("$NOM")
        echo ""
        continue
    fi
    ok "Connexion SSH" "OK"

    # 3b. Vérifier que le dossier distant existe
    if ! ssh $SSH_OPTS "$HOST" "test -d '$DEST_PATH'" 2>/dev/null; then
        warn "Dossier distant" "n'existe pas : $DEST_PATH"
        echo "   → Création du dossier..."
        ssh $SSH_OPTS "$HOST" "sudo mkdir -p '$DEST_PATH' && sudo chown \$(whoami):\$USER '$DEST_PATH'" 2>/dev/null || true
    fi
    ok "Dossier distant" "$DEST_PATH"

    # 3c. Backup rotatif si un index.html existe déjà
    if ssh $SSH_OPTS "$HOST" "test -f '${DEST_PATH}/${INDEX_FILE}'" 2>/dev/null; then
        TIMESTAMP=$(date +%Y%m%d_%H%M%S)
        BACKUP_NAME="${INDEX_FILE}.backup_${TIMESTAMP}"
        echo "   Backup : ${BACKUP_NAME}"
        ssh $SSH_OPTS "$HOST" "cp '${DEST_PATH}/${INDEX_FILE}' '${DEST_PATH}/${BACKUP_NAME}'" 2>/dev/null

        # Purge : garder seulement les MAX_BACKUPS derniers
        BACKUP_COUNT=$(ssh $SSH_OPTS "$HOST" "ls -1 '${DEST_PATH}/${INDEX_FILE}.backup_'*" 2>/dev/null | wc -l)
        if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
            PURGE_N=$((BACKUP_COUNT - MAX_BACKUPS))
            ssh $SSH_OPTS "$HOST" "ls -1t '${DEST_PATH}/${INDEX_FILE}.backup_'* | tail -n $PURGE_N | xargs rm -f" 2>/dev/null
            warn "Purgé" "$PURGE_N backup(s) ancien(s)"
        fi
    fi

    # 3d. Créer l'arborescence pour le helper JS
    JS_DEST_DIR="${DEST_PATH}/${HELPER_JS_DIR}"
    ssh $SSH_OPTS "$HOST" "mkdir -p '$JS_DEST_DIR'" 2>/dev/null
    ok "Arborescence JS" "$JS_DEST_DIR"

    # 3e. Copier index.html
    if scp $SSH_OPTS -q "${SOURCE_DIR}${INDEX_FILE}" "${HOST}:${DEST_PATH}/${INDEX_FILE}" 2>/dev/null; then
        DST_SIZE=$(ssh $SSH_OPTS "$HOST" "stat -c%s '${DEST_PATH}/${INDEX_FILE}'" 2>/dev/null || echo 0)
        ok "Copie index.html" "$DST_SIZE octets"
    else
        fail "Copie index.html"
        GLOBAL_OK=false
        FAILED_DESTINATIONS+=("$NOM")
        echo ""
        continue
    fi

    # 3f. Copier azura_flags.js
    if scp $SSH_OPTS -q "${SOURCE_DIR}${HELPER_JS_DIR}/${HELPER_JS_FILE}" "${HOST}:${JS_DEST_DIR}/${HELPER_JS_FILE}" 2>/dev/null; then
        DST_JS_SIZE=$(ssh $SSH_OPTS "$HOST" "stat -c%s '${JS_DEST_DIR}/${HELPER_JS_FILE}'" 2>/dev/null || echo 0)
        ok "Copie azura_flags.js" "$DST_JS_SIZE octets"
    else
        fail "Copie azura_flags.js"
        GLOBAL_OK=false
        FAILED_DESTINATIONS+=("$NOM")
        echo ""
        continue
    fi

    # 3g. Vérification MD5 post-copie
    MD5_SRC_INDEX=$(md5sum "${SOURCE_DIR}${INDEX_FILE}" 2>/dev/null | awk '{print $1}')
    MD5_DST_INDEX=$(ssh $SSH_OPTS "$HOST" "md5sum '${DEST_PATH}/${INDEX_FILE}'" 2>/dev/null | awk '{print $1}')

    if [ "$MD5_SRC_INDEX" = "$MD5_DST_INDEX" ]; then
        ok "MD5 index.html" "concordant"
    else
        warn "MD5 index.html" "différent !"
        echo "      Src: $MD5_SRC_INDEX"
        echo "      Dst: $MD5_DST_INDEX"
        GLOBAL_OK=false
    fi

    MD5_SRC_JS=$(md5sum "${SOURCE_DIR}${HELPER_JS_DIR}/${HELPER_JS_FILE}" 2>/dev/null | awk '{print $1}')
    MD5_DST_JS=$(ssh $SSH_OPTS "$HOST" "md5sum '${JS_DEST_DIR}/${HELPER_JS_FILE}'" 2>/dev/null | awk '{print $1}')
    if [ "$MD5_SRC_JS" = "$MD5_DST_JS" ]; then
        ok "MD5 azura_flags.js" "concordant"
    else
        warn "MD5 azura_flags.js" "différent !"
        GLOBAL_OK=false
    fi

    # 3h. Vérifier que nginx peut servir le fichier (permissions)
    if ! ssh $SSH_OPTS "$HOST" "test -r '${DEST_PATH}/${INDEX_FILE}'" 2>/dev/null; then
        warn "Permissions index.html" "non lisible par nginx"
        ssh $SSH_OPTS "$HOST" "sudo chmod -R a+rX '$DEST_PATH'" 2>/dev/null
        info "Permissions corrigées (chmod a+rX)"
    fi
    echo ""
done

# ══════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ
# ══════════════════════════════════════════════════════════════════════════════
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
if [ "$GLOBAL_OK" = true ]; then
    echo "${GREEN}${BOLD}✅ DÉPLOIEMENT MIROIR TERMINÉ AVEC SUCCÈS${NC}"
else
    echo "${RED}${BOLD}❌ DÉPLOIEMENT TERMINÉ AVEC ERREURS${NC}"
    if [ ${#FAILED_DESTINATIONS[@]} -gt 0 ]; then
        echo "   Destinations échouées : ${FAILED_DESTINATIONS[*]}"
    fi
fi
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "${BOLD}📋 Vérifications post-déploiement :${NC}"
echo "   ${CYAN}1.${NC} Miroir local : http://192.168.1.30/airvs_mirror/"
echo "   ${CYAN}2.${NC} Miroir public : https://miroir.db.airvs.fr/"
echo "   ${CYAN}3.${NC} Vérifier que les titres AzuraCast s'affichent avec filtre"
echo "   ${CYAN}4.${NC} Vérifier le badge (POUSSÉ S6+S7 ou IN_AZURACAST legacy)"
echo ""

if [ "$GLOBAL_OK" = false ]; then
    exit 1
fi
