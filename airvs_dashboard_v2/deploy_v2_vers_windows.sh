#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════════
# Déploiement v2 du Dashboard AIRVS vers C:\airvs_dashboard_v2\ (port 5001)
# ════════════════════════════════════════════════════════════════════════════════
# Workflow :
#   1. Les fichiers sont préparés dans un dossier temporaire Ubuntu :
#        /home/sebastien/Documents/Projets_2026/Projet_AIRVS/airvs_dashboard_v2/
#   2. Ce script copie via rsync vers le montage réseau Windows :
#        /mnt/win_dashboard_v2/  (→ C:\airvs_dashboard_v2\)
#   3. Vérification MD5 post-copie + vérification config.json
#
# ⚠️  PRODUCTION (C:\airvs\ port 5000) EST JAMAIS TOUCHÉE PAR CE SCRIPT
# ════════════════════════════════════════════════════════════════════════════════

set -u  # Sortir si variable non définie (mais PAS -e : on veut continuer après warnings)

# ─── Configuration ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/"                          # airvs_dashboard_v2/ (tout est ici)
DEST_DIR="/mnt/win_dashboard_v2/"

# ⚠️  SÉCURITÉ : vérifier qu'on ne pointe JAMAIS vers la production
PROD_DIR="/mnt/win_dashboard/"

# Fichiers CODE Python à déployer (Phase 1)
# Ordre important : config.py et utils.py AVANT app.py
CODE_PY_FILES=(
    "config.py"
    "utils.py"
    "app.py"
)

# Dossier et fichiers blueprints (Phase 2 : 11 blueprints)
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

# Fichiers FRONTEND (inchangés, copiés dans templates/)
FRONTEND_FILES=(
    "index.html"
)

# Fichiers déjà dans templates/ à copier tels quels
TEMPLATE_FILES=(
    "templates/login.html"
)

# Fichiers a copier a la racine (hors templates)
ROOT_FILES=()
# (index_sidebar.html a ete supprime — index.html est le seul template frontend)

# Dossier STATIC (CSS, JS, images, fonts…)
# Copié recursivement via cp -r
STATIC_DIR="static"

# Fichiers CONFIG (traités séparément, jamais supprimés par --delete)
CONFIG_FILES=(
    "config.json"
)

# Tous les fichiers qui DOIVENT être présents côté Windows après déploiement
POST_CHECK_FILES=(
    "app.py"
    "config.py"
    "utils.py"
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
    "templates/index.html"
    "templates/login.html"
    "config.json"
    "static/css/style.css"
)

# ─── Couleurs ANSI ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'  # No Color

ok()   { printf "  ${GREEN}✅${NC} %-35s (%s octets)\n" "$1" "$2"; }
fail() { printf "  ${RED}❌${NC} %-35s MANQUANT\n" "$1"; }
warn() { printf "  ${YELLOW}⚠️${NC}  %-35s %s\n" "$1" "$2"; }
info() { printf "  ${CYAN}ℹ️${NC}  %s\n" "$1"; }

# ─── Bannière ─────────────────────────────────────────────────────────────────
echo ""
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo "${BOLD}  DÉPLOIEMENT v2 → C:\\airvs_dashboard_v2\\ (port 5001)${NC}"
echo "${BOLD}  Phase 2 : Architecture Flask Blueprints (11 modules)${NC}"
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "── Chemins ──"
echo "   Source (temp)  : $SOURCE_DIR"
echo "   Destination    : $DEST_DIR  →  C:\\airvs_dashboard_v2\\"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 0 : VÉRIFICATION DE SÉCURITÉ — JAMAIS TOUCHER LA PRODUCTION
# ══════════════════════════════════════════════════════════════════════════════
if [ -d "$PROD_DIR" ] && [ "$DEST_DIR" = "$PROD_DIR" ]; then
    echo "${RED}🔴 ARRÊT IMMÉDIAT : DEST_DIR pointe vers la PRODUCTION !${NC}"
    echo "   DEST_DIR = $DEST_DIR"
    echo "   Ce script ne doit déployer que vers C:\\airvs_dashboard_v2\\ (port 5001)."
    exit 1
fi

if [ -d "$PROD_DIR" ]; then
    echo "${GREEN}🔒 Sécurité OK : production ($PROD_DIR) est distincte de la cible v2${NC}"
else
    echo "${YELLOW}⚠️  Dossier production non monté ($PROD_DIR) — vérifie que c'est normal${NC}"
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 : Vérifications préalables
# ════════════════════════════════════════════════════════════════════════════════
if [ ! -d "$SOURCE_DIR" ]; then
    echo "${RED}❌ ERREUR : Le dossier source n'existe pas :${NC}"
    echo "   $SOURCE_DIR"
    echo ""
    echo "   Crée-le d'abord et copie-y les fichiers v2 :"
    echo "   mkdir -p ${SOURCE_DIR}blueprints"
    exit 1
fi

if [ ! -d "$DEST_DIR" ]; then
    echo "${RED}❌ ERREUR : Le dossier de destination n'existe pas :${NC}"
    echo "   $DEST_DIR"
    echo ""
    echo "   Vérifie que le partage Windows v2 est bien monté. Exemple :"
    echo "   sudo mount -t cifs //192.168.1.39/airvs_dashboard_v2 /mnt/win_dashboard_v2 \\"
    echo "       -o username=USER,password=PASS,uid=sebastien,gid=sebastien"
    exit 1
fi
echo "${GREEN}✅ Dossier source et destination existent${NC}"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 : Vérification des fichiers CODE Python
# ════════════════════════════════════════════════════════════════════════════════
echo "── Fichiers CODE Python (Phase 1) ──"
MISSING_CODE=()
for f in "${CODE_PY_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        SIZE=$(stat -c%s "${SOURCE_DIR}${f}" 2>/dev/null || echo "?")
        MD5_SRC=$(md5sum "${SOURCE_DIR}${f}" 2>/dev/null | awk '{print $1}')
        ok "$f" "$SIZE"
        info "     MD5 source : $MD5_SRC"
    else
        fail "$f"
        MISSING_CODE+=("$f")
    fi
done
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 3 : Vérification des fichiers BLUEPRINTS
# ════════════════════════════════════════════════════════════════════════════════
echo "── Fichiers Blueprints (Phase 1.3) ──"
MISSING_BP=()
for f in "${BLUEPRINT_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        SIZE=$(stat -c%s "${SOURCE_DIR}${f}" 2>/dev/null || echo "?")
        MD5_SRC=$(md5sum "${SOURCE_DIR}${f}" 2>/dev/null | awk '{print $1}')
        ok "$f" "$SIZE"
        info "     MD5 source : $MD5_SRC"
    else
        fail "$f"
        MISSING_BP+=("$f")
    fi
done
# Vérifier aussi que le dossier blueprints/ existe dans la source
if [ ! -d "${SOURCE_DIR}blueprints/" ]; then
    echo "${RED}❌ Le dossier blueprints/ n'existe pas dans la source !${NC}"
    MISSING_BP+=("blueprints/ (dossier)")
fi
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 4 : Vérification des fichiers FRONTEND
# ════════════════════════════════════════════════════════════════════════════════
echo "── Fichiers Frontend ──"
MISSING_FRONT=()
for f in "${FRONTEND_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        SIZE=$(stat -c%s "${SOURCE_DIR}${f}" 2>/dev/null || echo "?")
        ok "$f" "$SIZE"
    else
        fail "$f"
        MISSING_FRONT+=("$f")
    fi
done
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 5 : Vérification des fichiers CONFIG
# ════════════════════════════════════════════════════════════════════════════════
echo "── Fichiers Config ──"
declare -A CONFIG_PATHS
CONFIG_MISSING=()
for f in "${CONFIG_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        CONFIG_PATHS["$f"]="${SOURCE_DIR}${f}"
        SIZE=$(stat -c%s "${SOURCE_DIR}${f}" 2>/dev/null || echo "?")
        ok "$f" "$SIZE"
    else
        fail "$f"
        CONFIG_MISSING+=("$f")
    fi
done
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 6 : Vérification du contenu de config.json (AzuraCast stations)
# ════════════════════════════════════════════════════════════════════════════════
CONFIG_SOURCE="${CONFIG_PATHS[config.json]:-}"
if [ -n "$CONFIG_SOURCE" ] && [ -f "$CONFIG_SOURCE" ]; then
    echo "── Vérification du contenu de config.json ──"
    echo "   Source lue : $CONFIG_SOURCE"
    python3 << PYEOF
import json
try:
    with open("$CONFIG_SOURCE", encoding="utf-8") as f:
        cfg = json.load(f)
    azura = cfg.get("azuracast", {})
    stations = azura.get("stations", [])
    sheets = cfg.get("sheets", {})
    if not isinstance(stations, list) or not stations:
        print("   ⚠️  config.json ne contient PAS de section 'azuracast.stations' valide.")
        print("      Le backend v2 retombera sur le fallback [station 7 seule].")
    else:
        ids = [s.get("id") for s in stations if isinstance(s, dict)]
        print(f"   ✅ config.json contient {len(stations)} station(s) : {ids}")
        if sheets:
            print(f"   ✅ config.json contient {len(sheets)} alias(es) de Google Sheet(s) : {list(sheets.keys())}")
except Exception as e:
    print(f"   ⚠️  config.json illisible ou JSON invalide : {e}")
PYEOF
    echo ""
fi

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 7 : Bilan des fichiers manquants — BLOQUANT si fichiers Phase 1 manquants
# ════════════════════════════════════════════════════════════════════════════════
TOTAL_MISSING=$((${#MISSING_CODE[@]} + ${#MISSING_BP[@]}))
if [ $TOTAL_MISSING -gt 0 ]; then
    echo "${RED}═══════════════════════════════════════════════════════════════${NC}"
    echo "${RED}🔴 BLOCAGE : $TOTAL_MISSING fichier(s) Python Phase 1 manquant(s) !${NC}"
    echo "${RED}   Sans ces fichiers, le dashboard v2 ne démarrera PAS.${NC}"
    echo ""
    for f in "${MISSING_CODE[@]}" "${MISSING_BP[@]}"; do
        echo "   - $f"
    done
    echo ""
    echo "   Corrige puis relance ce script."
    echo "${RED}═══════════════════════════════════════════════════════════════${NC}"
    exit 1
fi

if [ ${#MISSING_FRONT[@]} -gt 0 ]; then
    echo "${YELLOW}⚠️  ${#MISSING_FRONT[@]} fichier(s) frontend manquant(s) — le dashboard sera incomplet.${NC}"
    for f in "${MISSING_FRONT[@]}"; do
        echo "   - $f"
    done
    echo ""
fi

if [ ${#CONFIG_MISSING[@]} -gt 0 ]; then
    for f in "${CONFIG_MISSING[@]}"; do
        if [ "$f" = "config.json" ]; then
            echo "${RED}🔴 CRITIQUE : config.json est MANQUANT.${NC}"
            echo "   Sans ce fichier, le backend v2 retombe sur le fallback [station 7 seule]."
            echo "   La sync multi-station ne fonctionnera pas."
            echo ""
            read -p "   Continuer quand même ? (o/N) " -n 1 -r
            echo ""
            if [[ ! $REPLY =~ ^[Oo]$ ]]; then
                echo "   Abandon — config.json non déployé."
                exit 1
            fi
        fi
    done
fi

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 8 : Création d'un backup côté Windows (si v2 existe déjà)
# ════════════════════════════════════════════════════════════════════════════════
echo "── Backup pré-déploiement ──"
if [ -f "${DEST_DIR}app.py" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_DIR="${DEST_DIR}_backup_${TIMESTAMP}"
    if mkdir -p "$BACKUP_DIR" 2>/dev/null; then
        # Sauvegarder les fichiers clés
        for f in "${POST_CHECK_FILES[@]}"; do
            if [ -f "${DEST_DIR}${f}" ]; then
                # Créer le sous-dossier parent si nécessaire
                DEST_SUBDIR=$(dirname "$BACKUP_DIR/$f")
                mkdir -p "$DEST_SUBDIR" 2>/dev/null
                cp "${DEST_DIR}${f}" "$BACKUP_DIR/$f" 2>/dev/null
            fi
        done
        echo "${GREEN}✅ Backup créé : ${BACKUP_DIR}${NC}"
    else
        echo "${YELLOW}⚠️  Impossible de créer le backup — continue sans backup${NC}"
    fi
else
    echo "${CYAN}ℹ️  Première livraison v2 — aucun backup nécessaire${NC}"
fi
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 9 : Copie explicite des fichiers (approche deterministe)
# ════════════════════════════════════════════════════════════════════════════════
# NOTE : On utilise cp explicite (pas rsync filter) pour etre 100% deterministe
#        sur ce qui est copie. A evoluer vers rsync quand les fichiers seront
#        plus nombreux (Phase 2/3).

# 9a. Assurer que le dossier destination existe
mkdir -p "${DEST_DIR}" 2>/dev/null

# 9b. Copie des fichiers Python racine
echo "── Copie fichiers Python ──"
CP_OK=true
for f in "${CODE_PY_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
            SIZE=$(stat -c%s "${DEST_DIR}${f}" 2>/dev/null || echo "?")
            ok "$f" "$SIZE"
        else
            printf "  ${RED}\xe2\x9d\x8c${NC} %-35s echec copie\n" "$f"
            CP_OK=false
        fi
    fi
done
echo ""

# 9c. Copie des blueprints
echo "── Copie blueprints/ ──"
mkdir -p "${DEST_DIR}blueprints/" 2>/dev/null
for f in "${BLUEPRINT_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
            SIZE=$(stat -c%s "${DEST_DIR}${f}" 2>/dev/null || echo "?")
            ok "$f" "$SIZE"
        else
            printf "  ${RED}\xe2\x9d\x8c${NC} %-35s echec copie\n" "$f"
            CP_OK=false
        fi
    fi
done
echo ""

# 9d. Copie des fichiers frontend dans templates/
echo "── Copie frontend → templates/ ──"
mkdir -p "${DEST_DIR}templates/" 2>/dev/null
for f in "${FRONTEND_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}templates/${f}" 2>/dev/null; then
            SIZE=$(stat -c%s "${DEST_DIR}templates/${f}" 2>/dev/null || echo "?")
            ok "templates/$f" "$SIZE"
        else
            printf "  ${RED}\xe2\x9d\x8c${NC} %-35s echec copie\n" "templates/$f"
            CP_OK=false
        fi
    fi
done

# 9d-bis. Copie des fichiers déjà dans templates/
echo "── Copie templates additionnels ──"
for f in "${TEMPLATE_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        DEST_SUBDIR=$(dirname "${DEST_DIR}${f}")
        mkdir -p "$DEST_SUBDIR" 2>/dev/null
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
            SIZE=$(stat -c%s "${DEST_DIR}${f}" 2>/dev/null || echo "?")
            ok "$f" "$SIZE"
        else
            printf "  ${RED}\xe2\x9d\x8c${NC} %-35s echec copie\n" "$f"
            CP_OK=false
        fi
    fi
done

# 9e. Copie du dossier static/ (CSS, JS, images, fonts)
echo "── Copie static/ ──"
if [ -d "${SOURCE_DIR}${STATIC_DIR}/" ]; then
    mkdir -p "${DEST_DIR}${STATIC_DIR}/" 2>/dev/null
    STATIC_COUNT=0
    # Copier recursivement tous les fichiers du dossier static
    if cp -rv "${SOURCE_DIR}${STATIC_DIR}/"* "${DEST_DIR}${STATIC_DIR}/" 2>&1 | while read line; do echo "  $line"; done; then
        STATIC_COUNT=$(find "${DEST_DIR}${STATIC_DIR}/" -type f 2>/dev/null | wc -l)
        echo "  ${GREEN}✅${NC} static/ copié (${STATIC_COUNT} fichiers)"
    else
        echo "  ${RED}❌${NC} Echec de la copie de static/"
        CP_OK=false
    fi
    # Vérification explicite des modules JS (Phase 3)
    MODULES_DIR="${DEST_DIR}${STATIC_DIR}/js/modules/"
    if [ -d "$MODULES_DIR" ]; then
        MOD_COUNT=$(find "$MODULES_DIR" -name '*.js' -type f 2>/dev/null | wc -l)
        echo "  ${GREEN}✅${NC} static/js/modules/ présent (${MOD_COUNT} fichiers .js)"
    else
        echo "  ${RED}❌${NC} static/js/modules/ MANQUANT côté Windows"
        CP_OK=false
    fi
else
    echo "  ${YELLOW}⚠️${NC}  Dossier ${STATIC_DIR}/ absent de la source — CSS/JS non déployés"
fi
echo ""

# 9f. Copie des fichiers racine (hors templates)
for f in "${ROOT_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}${f}" ]; then
        if cp "${SOURCE_DIR}${f}" "${DEST_DIR}${f}" 2>/dev/null; then
            SIZE=$(stat -c%s "${DEST_DIR}${f}" 2>/dev/null || echo "?")
            ok "$f" "$SIZE"
        else
            printf "  ${RED}\xe2\x9d\x8c${NC} %-35s echec copie\n" "$f"
            CP_OK=false
        fi
    fi
done
echo ""

if [ "$CP_OK" = false ]; then
    echo "${YELLOW}Des echecs de copie ont ete detectes -- voir ci-dessus${NC}"
fi

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 10 : Copie séparée des fichiers CONFIG
# ════════════════════════════════════════════════════════════════════════════════
echo "── Copie des fichiers CONFIG vers Windows v2 ──"
for f in "${CONFIG_FILES[@]}"; do
    SRC="${CONFIG_PATHS[$f]:-}"
    if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
        warn "$f" "non copié (absent de la source)"
        continue
    fi
    DEST="${DEST_DIR}${f}"
    # Créer le dossier parent si nécessaire
    DEST_SUBDIR=$(dirname "$DEST")
    mkdir -p "$DEST_SUBDIR" 2>/dev/null
    if cp "$SRC" "$DEST" 2>/dev/null; then
        SIZE=$(stat -c%s "$DEST" 2>/dev/null || echo "?")
        ok "$f" "$SIZE"
    else
        printf "  ${RED}❌${NC} %-35s échec copie\n" "$f"
    fi
done
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 11 : Vérification post-copie + MD5
# ════════════════════════════════════════════════════════════════════════════════
echo "── Vérification post-copie + intégrité MD5 ──"
ALL_OK=true
MD5_MISMATCH=()

# Fichiers à vérifier avec MD5 source vs destination
MD5_CHECK_FILES=(
    "config.py"
    "utils.py"
    "app.py"
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
    "templates/index.html"
    "templates/login.html"
    "static/js/modules/utils.js"
    "static/js/modules/grille-editoriale.js"
    "static/js/modules/navigation.js"
    "static/js/modules/shazam-animateurs.js"
    "static/js/modules/pont-player.js"
    "static/js/modules/stats-audience.js"
    "static/js/modules/sync-azuracast.js"
    "static/js/modules/couleur.js"
    "static/js/modules/pool-tiers.js"
    "static/js/modules/piges-podcasts.js"
)

echo ""
echo "   ${BOLD}Vérification MD5 (source → destination) :${NC}"
for f in "${MD5_CHECK_FILES[@]}"; do
    DEST_FILE="${DEST_DIR}${f}"
    SRC_FILE="${SOURCE_DIR}${f}"
    if [ ! -f "$DEST_FILE" ]; then
        fail "$f (absent côté Windows)"
        ALL_OK=false
        MD5_MISMATCH+=("$f")
        continue
    fi
    MD5_SRC=$(md5sum "$SRC_FILE" 2>/dev/null | awk '{print $1}')
    MD5_DST=$(md5sum "$DEST_FILE" 2>/dev/null | awk '{print $1}')
    SIZE=$(stat -c%s "$DEST_FILE" 2>/dev/null || echo "?")
    if [ "$MD5_SRC" = "$MD5_DST" ]; then
        ok "$f" "$SIZE"
        info "     MD5 OK : $MD5_DST"
    else
        printf "  ${RED}❌${NC} %-35s %s octets${NC}\n" "$f" "$SIZE"
        echo "     ${RED}Source : $MD5_SRC${NC}"
        echo "     ${RED}Dest   : $MD5_DST${NC}"
        ALL_OK=false
        MD5_MISMATCH+=("$f")
    fi
done
echo ""

# Vérification des autres fichiers (pas de MD5, juste présence + taille)
echo "   ${BOLD}Vérification présence (fichiers non-Python) :${NC}"
for f in "config.json"; do
    DEST_FILE="${DEST_DIR}${f}"
    if [ -f "$DEST_FILE" ]; then
        SIZE=$(stat -c%s "$DEST_FILE" 2>/dev/null || echo "?")
        ok "$f" "$SIZE"
    else
        fail "$f (absent côté Windows)"
        ALL_OK=false
    fi
done

# Vérification du dossier static
echo ""
echo "   ${BOLD}Vérification dossier static/ :${NC}"
if [ -d "${DEST_DIR}static/" ]; then
    STATIC_FILE_COUNT=$(find "${DEST_DIR}static/" -type f 2>/dev/null | wc -l)
    STATIC_TOTAL_SIZE=$(du -sh "${DEST_DIR}static/" 2>/dev/null | awk '{print $1}')
    echo "  ${GREEN}✅${NC} static/ présent (${STATIC_FILE_COUNT} fichiers, ${STATIC_TOTAL_SIZE})"
    # Vérifier style.css specifiquement
    if [ -f "${DEST_DIR}static/css/style.css" ]; then
        SIZE=$(stat -c%s "${DEST_DIR}static/css/style.css" 2>/dev/null || echo "?")
        ok "static/css/style.css" "$SIZE"
    else
        fail "static/css/style.css (FICHIER CRITIQUE MANQUANT)"
        ALL_OK=false
    fi
else
    fail "static/ (dossier entier manquant — CSS/JS absents)"
    ALL_OK=false
fi
echo ""
# ════════════════════════════════════════════════════════════════════════════════
# ÉTAPE 12 : Vérification config.json côté Windows (stations AzuraCast)
# ════════════════════════════════════════════════════════════════════════════════
CONFIG_DEST="${DEST_DIR}config.json"
if [ -f "$CONFIG_DEST" ]; then
    echo "── Vérification config.json côté Windows ──"
    python3 << PYEOF
import json
try:
    with open("$CONFIG_DEST", encoding="utf-8") as f:
        cfg = json.load(f)
    azura = cfg.get("azuracast", {})
    stations = azura.get("stations", [])
    if not isinstance(stations, list) or not stations:
        print("   🔴 config.json côté Windows ne contient PAS 'azuracast.stations'.")
        print("      La sync multi-station ne fonctionnera pas.")
    else:
        ids = [s.get("id") for s in stations if isinstance(s, dict)]
        if 6 in ids and 7 in ids:
            print(f"   ✅ config.json OK : stations {ids} détectées")
        else:
            print(f"   ⚠️  Stations détectées : {ids} — la station 6 et/ou 7 manque.")
except Exception as e:
    print(f"   🔴 config.json côté Windows illisible : {e}")
PYEOF
    echo ""
fi

# ════════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ FINAL
# ════════════════════════════════════════════════════════════════════════════════
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
if [ "$ALL_OK" = true ]; then
    echo "${GREEN}${BOLD}✅ DÉPLOIEMENT v2 TERMINÉ AVEC SUCCÈS${NC}"
else
    echo "${YELLOW}${BOLD}⚠️  DÉPLOIEMENT TERMINÉ AVEC ANOMALIES${NC}"
    if [ ${#MD5_MISMATCH[@]} -gt 0 ]; then
        echo ""
        echo "   ${RED}Fichiers avec MD5 différent (corrompus à la copie) :${NC}"
        for f in "${MD5_MISMATCH[@]}"; do
            echo "   - $f"
        done
    fi
fi
echo "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "${BOLD}📋 Actions suivantes sur Windows (192.168.1.39) :${NC}"
echo ""
echo "   ${CYAN}1.${NC} Vérifier que ${BOLD}C:\\airvs_dashboard_v2\\.env${NC} existe (copie du .env actuel)"
echo "      Si absent : copier C:\\airvs\\.env → C:\\airvs_dashboard_v2\\.env"
echo ""
echo "   ${CYAN}2.${NC} Ouvrir un terminal dans ${BOLD}C:\\airvs_dashboard_v2\\${NC}"
echo ""
echo "   ${CYAN}3.${NC} Lancer le dashboard v2 :"
echo "      ${BOLD}python app.py${NC}"
echo ""
echo "   ${CYAN}4.${NC} Vérifier le message : ${BOLD}Running on http://0.0.0.0:5001${NC}"
echo ""
echo "   ${CYAN}5.${NC} Ouvrir dans le navigateur : ${BOLD}http://192.168.1.39:5001${NC}"
echo ""
echo "   ${CYAN}6.${NC} Recharger avec ${BOLD}Ctrl+Shift+R${NC} (cache navigateur)"
echo ""
echo "   ${CYAN}7.${NC} Tester les onglets critiques :"
echo "      - Login/Logout"
echo "      - Explorateur (recherche)"
echo "      - Grille editoriale"
echo "      - Shazam (liste + dropdown animateurs)"
echo "      - Sync AzuraCast (badge stations)"
echo ""
echo "${BOLD}🔒 RAPPEL : La production (C:\\airvs\\ port 5000) n'est PAS affectée.${NC}"
echo "${BOLD}   Les deux versions peuvent tourner simultanément.${NC}"
echo ""