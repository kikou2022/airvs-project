#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════════
# Déploiement du Dashboard AIRVS vers Windows (port 5001)
# ════════════════════════════════════════════════════════════════════════════════
# Workflow :
#   1. Source : dossier courant (ce script doit être à la racine de l'app)
#   2. Copie via cp vers le montage réseau Windows :
#        /mnt/win_dashboard_v2/  (→ C:\airvs_dashboard_v2\)
#   3. Nettoyage des fichiers obsolètes côté Windows
#   4. Vérification MD5 post-copie
#   5. Backup rotatif (3 max)
# ════════════════════════════════════════════════════════════════════════════════

set -u

# ─── Configuration ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/"
DEST_DIR="/mnt/win_dashboard_v2/"

# Nombre de backups à conserver (les plus anciens sont supprimés)
MAX_BACKUPS=3

# Fichiers Python racine (ordre : config.py et utils.py AVANT app.py)
CODE_PY_FILES=(
    "config.py"
    "utils.py"
    "app.py"
)

# Blueprints (package marker + modules)
BLUEPRINT_FILES=(
    "blueprints/__init__.py"
    "blueprints/auth.py"
    "blueprints/login.py"
    "blueprints/explorateur.py"
    "blueprints/sync_mp3.py"
    "blueprints/flux.py"
    "blueprints/player.py"
    "blueprints/azuracast.py"
    "blueprints/piges.py"
    "blueprints/grille.py"
    "blueprints/shazam.py"
    "blueprints/programmation.py"
    "blueprints/taches.py"
    "blueprints/pipeline_import.py"
)

# Fichiers frontend (à la racine source → copiés dans templates/ destination)
FRONTEND_FILES=(
    "index.html"
)

# Fichiers déjà dans templates/ (copiés en conservant le chemin)
TEMPLATE_FILES=(
    "templates/login.html"
)

# Dossier STATIC (copié récursivement)
STATIC_DIR="static"

# Fichiers CONFIG (jamais supprimés par le nettoyage)
CONFIG_FILES=(
    "config.json"
)

# ─── Couleurs ANSI ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { printf "  ${GREEN}✅${NC} %-40s (%s octets)\n" "$1" "$2"; }
fail() { printf "  ${RED}❌${NC} %-40s MANQUANT\n" "$1"; }
warn() { printf "  ${YELLOW}⚠${NC}  %-40s %s\n" "$1" "$2"; }
info() { printf "  ${CYAN}ℹ${NC}  %s\n" "$1"; }

echo ""
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo "${BOLD}  DÉPLOIEMENT DASHBOARD AIRVS → C:\airvs_dashboard_v2\${NC}"
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "── Chemins ──"
echo "   Source       : $SOURCE_DIR"
echo "   Destination  : $DEST_DIR"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 : Vérifications préalables
# ══════════════════════════════════════════════════════════════════════════════
if [ ! -d "$SOURCE_DIR" ]; then
    echo "${RED}❌ ERREUR : Le dossier source n'existe pas : $SOURCE_DIR${NC}"
    exit 1
fi

if [ ! -d "$DEST_DIR" ]; then
    echo "${RED}❌ ERREUR : Le dossier de destination n'existe pas : $DEST_DIR${NC}"
    echo "   Vérifie le partage Windows :"
    echo "   sudo mount -t cifs //192.168.1.39/airvs_dashboard_v2 /mnt/win_dashboard_v2 \\"
    echo "       -o username=USER,password=PASS,uid=sebastien,gid=sebastien"
    exit 1
fi
echo "${GREEN}✅ Source et destination accessibles${NC}"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 : Vérification des fichiers source
# ══════════════════════════════════════════════════════════════════════════════
echo "── Vérification des fichiers source ──"
MISSING_CRITICAL=()

for f in "${CODE_PY_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        SIZE=$(stat -c%s "${SOURCE_DIR}${f}")
        ok "$f" "$SIZE"
    else
        fail "$f"
        MISSING_CRITICAL+=("$f")
    fi
done

for f in "${BLUEPRINT_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        SIZE=$(stat -c%s "${SOURCE_DIR}${f}")
        ok "$f" "$SIZE"
    else
        fail "$f"
        MISSING_CRITICAL+=("$f")
    fi
done

if [ ${#MISSING_CRITICAL[@]} -gt 0 ]; then
    echo ""
    echo "${RED}❌ ARRÊT : ${#MISSING_CRITICAL[@]} fichier(s) critique(s) manquant(s) :${NC}"
    for f in "${MISSING_CRITICAL[@]}"; do echo "   - $f"; done
    exit 1
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 3 : Vérification config.json
# ══════════════════════════════════════════════════════════════════════════════
if [ -f "${SOURCE_DIR}config.json" ]; then
    echo "── Vérification config.json ──"
    python3 << PYEOF
import json, sys
try:
    with open("${SOURCE_DIR}config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    stations = cfg.get("azuracast", {}).get("stations", [])
    sheets = cfg.get("sheets", {})
    if not isinstance(stations, list) or not stations:
        print("   ⚠  Pas de section 'azuracast.stations' valide (fallback station 7)")
    else:
        ids = [s.get("id") for s in stations if isinstance(s, dict)]
        print(f"   ✅ {len(stations)} station(s) : {ids}")
    if sheets:
        print(f"   ✅ {len(sheets)} Google Sheet(s) : {list(sheets.keys())}")
except Exception as e:
    print(f"   ⚠  config.json illisible : {e}")
PYEOF
    echo ""
fi

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 4 : Backup rotatif (garde les N derniers)
# ══════════════════════════════════════════════════════════════════════════════
echo "── Backup pré-déploiement ──"
PARENT_DIR=$(dirname "$DEST_DIR")
if [ -f "${DEST_DIR}app.py" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_DIR="${PARENT_DIR}/_backup_dashboard_${TIMESTAMP}"
    if mkdir -p "$BACKUP_DIR" 2>/dev/null; then
        # Sauvegarder tout le contenu (sauf les backups précédents)
        for f in "${CODE_PY_FILES[@]}" "${BLUEPRINT_FILES[@]}" "${CONFIG_FILES[@]}"; do
            if [ -f "${DEST_DIR}${f}" ]; then
                mkdir -p "$(dirname "$BACKUP_DIR/$f")" 2>/dev/null
                cp "${DEST_DIR}${f}" "$BACKUP_DIR/$f" 2>/dev/null
            fi
        done
        # Sauvegarder templates et static
        [ -d "${DEST_DIR}templates/" ] && cp -r "${DEST_DIR}templates/" "$BACKUP_DIR/templates/" 2>/dev/null
        [ -d "${DEST_DIR}static/" ]    && cp -r "${DEST_DIR}static/"    "$BACKUP_DIR/static/" 2>/dev/null
        echo "${GREEN}✅ Backup créé : $(basename "$BACKUP_DIR")${NC}"

        # Purge : supprimer les backups les plus anciens au-delà de MAX_BACKUPS
        BACKUP_COUNT=$(ls -1d "${PARENT_DIR}/_backup_dashboard_"* 2>/dev/null | wc -l)
        if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
            PURGE_N=$((BACKUP_COUNT - MAX_BACKUPS))
            ls -1dt "${PARENT_DIR}/_backup_dashboard_"* 2>/dev/null | tail -n "$PURGE_N" | while read old_backup; do
                rm -rf "$old_backup"
                warn "Purgé" "$(basename "$old_backup")"
            done
        fi
    else
        warn "Backup" "Impossible de créer le répertoire"
    fi
else
    echo "${CYAN}ℹ  Première livraison — aucun backup nécessaire${NC}"
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 5 : Copie des fichiers
# ══════════════════════════════════════════════════════════════════════════════
mkdir -p "${DEST_DIR}" 2>/dev/null
COPY_FAILED=()

# 5a. Python racine
echo "── Copie Python ──"
for f in "${CODE_PY_FILES[@]}"; do
    if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
        ok "$f" "$(stat -c%s "${DEST_DIR}${f}")"
    else
        fail "$f"
        COPY_FAILED+=("$f")
    fi
done
echo ""

# 5b. Blueprints
echo "── Copie blueprints/ ──"
mkdir -p "${DEST_DIR}blueprints/" 2>/dev/null
for f in "${BLUEPRINT_FILES[@]}"; do
    if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
        ok "$f" "$(stat -c%s "${DEST_DIR}${f}")"
    else
        fail "$f"
        COPY_FAILED+=("$f")
    fi
done
echo ""

# 5c. Frontend (racine source → templates/ destination)
echo "── Copie frontend → templates/ ──"
mkdir -p "${DEST_DIR}templates/" 2>/dev/null
for f in "${FRONTEND_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}templates/${f}" 2>/dev/null; then
            ok "templates/$f" "$(stat -c%s "${DEST_DIR}templates/${f}")"
        else
            fail "templates/$f"
            COPY_FAILED+=("$f")
        fi
    fi
done

# 5d. Templates additionnels (chemin conservé)
for f in "${TEMPLATE_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        mkdir -p "$(dirname "${DEST_DIR}${f}")" 2>/dev/null
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
            ok "$f" "$(stat -c%s "${DEST_DIR}${f}")"
        else
            fail "$f"
            COPY_FAILED+=("$f")
        fi
    fi
done
echo ""

# 5e. Static (récursif)
echo "── Copie static/ ──"
if [ -d "${SOURCE_DIR}${STATIC_DIR}/" ]; then
    mkdir -p "${DEST_DIR}${STATIC_DIR}/" 2>/dev/null
    cp -r "${SOURCE_DIR}${STATIC_DIR}/"* "${DEST_DIR}${STATIC_DIR}/" 2>/dev/null
    STATIC_COUNT=$(find "${DEST_DIR}${STATIC_DIR}/" -type f 2>/dev/null | wc -l)
    echo "  ${GREEN}✅${NC} static/ copié (${STATIC_COUNT} fichiers)"
else
    echo "  ${YELLOW}⚠${NC}  Dossier ${STATIC_DIR}/ absent de la source"
fi
echo ""

# 5f. Config (traité à part, jamais supprimé par le nettoyage)
echo "── Copie config ──"
for f in "${CONFIG_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
            ok "$f" "$(stat -c%s "${DEST_DIR}${f}")"
        else
            fail "$f"
        fi
    fi
done
echo ""

# Sortie en erreur si des fichiers critiques ont échoué
if [ ${#COPY_FAILED[@]} -gt 0 ]; then
    echo "${RED}❌ ARRÊT : échec de copie pour ${#COPY_FAILED[@]} fichier(s) :${NC}"
    for f in "${COPY_FAILED[@]}"; do echo "   - $f"; done
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 6 : Nettoyage des fichiers obsolètes côté Windows
# ══════════════════════════════════════════════════════════════════════════════
echo "── Nettoyage des fichiers obsolètes ──"
CLEANED=0

# 6a. Blueprints : supprimer les .py qui n'existent plus dans la source
if [ -d "${DEST_DIR}blueprints/" ]; then
    for dst_file in "${DEST_DIR}blueprints/"*.py; do
        [ -f "$dst_file" ] || continue
        fname=$(basename "$dst_file")
        src_file="${SOURCE_DIR}blueprints/${fname}"
        # Ne jamais supprimer __init__.py
        [ "$fname" = "__init__.py" ] && continue
        if [ ! -f "$src_file" ]; then
            rm -f "$dst_file"
            warn "Supprimé" "blueprints/${fname}"
            CLEANED=$((CLEANED + 1))
        fi
    done
fi

# 6b. Modules JS : supprimer les .js qui n'existent plus dans la source
MODULES_DST="${DEST_DIR}static/js/modules/"
MODULES_SRC="${SOURCE_DIR}static/js/modules/"
if [ -d "$MODULES_DST" ]; then
    for dst_file in "${MODULES_DST}"*.js; do
        [ -f "$dst_file" ] || continue
        fname=$(basename "$dst_file")
        if [ ! -f "${MODULES_SRC}${fname}" ]; then
            rm -f "$dst_file"
            warn "Supprimé" "static/js/modules/${fname}"
            CLEANED=$((CLEANED + 1))
        fi
    done
fi

# 6c. Python racine : supprimer les .py qui ne sont pas dans CODE_PY_FILES
for dst_file in "${DEST_DIR}"*.py; do
    [ -f "$dst_file" ] || continue
    fname=$(basename "$dst_file")
    # Vérifier si ce fichier est dans la liste autorisée
    KEEP=false
    for allowed in "${CODE_PY_FILES[@]}"; do
        [ "$allowed" = "$fname" ] && KEEP=true && break
    done
    if [ "$KEEP" = false ]; then
        rm -f "$dst_file"
        warn "Supprimé" "${fname} (racine)"
        CLEANED=$((CLEANED + 1))
    fi
done

if [ "$CLEANED" -eq 0 ]; then
    echo "  ${GREEN}✅${NC} Aucun fichier obsolète"
else
    echo "  ${YELLOW}⚠${NC}  ${CLEANED} fichier(s) obsolète(s) supprimé(s)"
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 7 : Vérification MD5 post-copie
# ══════════════════════════════════════════════════════════════════════════════
echo "── Vérification intégrité MD5 ──"
ALL_OK=true
MD5_FAIL=0

# Liste dynamique : fichiers Python + blueprints + templates + modules JS
declare -a MD5_FILES=(
    "${CODE_PY_FILES[@]}"
    "${BLUEPRINT_FILES[@]}"
    "templates/index.html"
    "templates/login.html"
)

# Ajouter dynamiquement les modules JS présents dans la source
if [ -d "${SOURCE_DIR}static/js/modules/" ]; then
    while IFS= read -r jsfile; do
        MD5_FILES+=("static/js/modules/${jsfile}")
    done < <(cd "${SOURCE_DIR}static/js/modules/" && find . -name '*.js' -type f -printf '%P\n' 2>/dev/null | sort)
fi

# Ajouter style.css
MD5_FILES+=("static/css/style.css")

for f in "${MD5_FILES[@]}"; do
    SRC_FILE="${SOURCE_DIR}${f}"
    DST_FILE="${DEST_DIR}${f}"
    if [ ! -f "$DST_FILE" ]; then
        fail "$f (absent côté Windows)"
        ALL_OK=false
        MD5_FAIL=$((MD5_FAIL + 1))
        continue
    fi
    MD5_SRC=$(md5sum "$SRC_FILE" 2>/dev/null | awk '{print $1}')
    MD5_DST=$(md5sum "$DST_FILE" 2>/dev/null | awk '{print $1}')
    SIZE=$(stat -c%s "$DST_FILE")
    if [ "$MD5_SRC" = "$MD5_DST" ]; then
        ok "$f" "$SIZE"
    else
        printf "  ${RED}❌${NC} %-40s %s octets\n" "$f" "$SIZE"
        echo "     ${RED}Src: $MD5_SRC → Dst: $MD5_DST${NC}"
        ALL_OK=false
        MD5_FAIL=$((MD5_FAIL + 1))
    fi
done
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 8 : Vérification config.json côté Windows
# ══════════════════════════════════════════════════════════════════════════════
CONFIG_DEST="${DEST_DIR}config.json"
if [ -f "$CONFIG_DEST" ]; then
    echo "── Vérification config.json (côté Windows) ──"
    python3 << PYEOF
import json
try:
    with open("$CONFIG_DEST", encoding="utf-8") as f:
        cfg = json.load(f)
    stations = cfg.get("azuracast", {}).get("stations", [])
    if isinstance(stations, list) and stations:
        ids = [s.get("id") for s in stations if isinstance(s, dict)]
        print(f"   ✅ {len(stations)} station(s) : {ids}")
    else:
        print("   ⚠  Pas de stations AzuraCast (fallback station 7)")
except Exception as e:
    print(f"   ❌ config.json illisible : {e}")
PYEOF
    echo ""
fi

# ══════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ
# ══════════════════════════════════════════════════════════════════════════════
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
if [ "$ALL_OK" = true ] && [ "$CLEANED" -ge 0 ]; then
    echo "${GREEN}${BOLD}✅ DÉPLOIEMENT TERMINÉ AVEC SUCCÈS${NC}"
    [ "$CLEANED" -gt 0 ] && echo "   ${YELLOW}${CLEANED} fichier(s) obsolète(s) nettoyé(s)${NC}"
else
    echo "${RED}${BOLD}❌ DÉPLOIEMENT TERMINÉ AVEC ERREURS${NC}"
    echo "   ${RED}${MD5_FAIL} fichier(s) avec MD5 incorrect${NC}"
fi
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"

if [ "$ALL_OK" = false ]; then
    exit 1
fi

echo ""
echo "${BOLD}📋 Actions suivantes sur Windows (192.168.1.39) :${NC}"
echo "   ${CYAN}1.${NC} Vérifier que C:\airvs_dashboard_v2\.env existe"
echo "   ${CYAN}2.${NC} Relancer : python app.py"
echo "   ${CYAN}3.${NC} Vérifier : http://192.168.1.39:5001"
echo "   ${CYAN}4.${NC} Recharger avec Ctrl+Shift+R"
