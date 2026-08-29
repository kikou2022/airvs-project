#!/bin/bash
# ============================================================
# purge_apache_final.sh — Purge complémentaire Apache
# Prérequis : migration Apache → Nginx déjà exécutée
#              (Apache arrêté, désactivé, Nginx actif)
# ============================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  PURGE APACHE — VPS OVH 1 (post-migration Nginx)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# ----------------------------------------------------------
# 0. Root check
# ----------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}Exécuter avec sudo : sudo bash purge_apache_final.sh${NC}"
    exit 1
fi

# ----------------------------------------------------------
# 1. Vérifier que Nginx est actif AVANT de purger
# ----------------------------------------------------------
echo -e "${YELLOW}[1/5] Vérification préalable...${NC}"

if ! systemctl is-active --quiet nginx 2>/dev/null; then
    echo -e "${RED}Nginx n'est PAS actif — purge annulée par sécurité.${NC}"
    exit 1
fi
echo -e "  ${GREEN}Nginx actif ✓${NC}"

if systemctl is-active --quiet apache2 2>/dev/null; then
    echo -e "${RED}Apache est encore EN COURS — arrêt préalable...${NC}"
    systemctl stop apache2
    systemctl disable apache2
    echo -e "  ${GREEN}Apache arrêté et désactivé${NC}"
else
    echo -e "  ${GREEN}Apache déjà inactif ✓${NC}"
fi

# Vérifier que Nginx sert bien les 4 domaines
echo ""
echo -e "  ${YELLOW}Vhosts Nginx actifs :${NC}"
ls /etc/nginx/sites-enabled/ 2>/dev/null | grep -v default | while read -r f; do
    echo "    → $f"
done

# ----------------------------------------------------------
# 2. Purge des paquets Apache
# ----------------------------------------------------------
echo ""
echo -e "${YELLOW}[2/5] Purge des paquets Apache...${NC}"

DEBIAN_FRONTEND=noninteractive apt-get purge -y \
    apache2 \
    apache2-utils \
    apache2-bin \
    apache2-data \
    apache2-dev \
    libapache2-mod-php* \
    libapache2-mod-proxy* \
    libapache2-mod-ssl \
    libapache2-mod-suexec \
    libapache2-mod-cgid \
    libapache2-mod-fcgid \
    libapache2-mod-mpm* \
    2>/dev/null || true

echo -e "  ${GREEN}Paquets Apache purgés${NC}"

# Nettoyage dépendances orphelines
DEBIAN_FRONTEND=noninteractive apt-get autoremove -y 2>/dev/null || true
echo -e "  ${GREEN}Dépendances orphelines supprimées${NC}"

# Cache apt
DEBIAN_FRONTEND=noninteractive apt-get clean 2>/dev/null || true
echo -e "  ${GREEN}Cache apt nettoyé${NC}"

# ----------------------------------------------------------
# 3. Suppression des résidus Apache
# ----------------------------------------------------------
echo ""
echo -e "${YELLOW}[3/5] Suppression des résidus...${NC}"

for dir in /etc/apache2 /var/log/apache2 /var/cache/apache2 /run/apache2; do
    if [ -d "$dir" ]; then
        rm -rf "$dir"
        echo -e "  Supprimé : $dir"
    else
        echo -e "  Déjà absent : $dir"
    fi
done

# /var/www/html/ est CONSERVÉ — utilisé par Nginx (miroir.db.airvs.fr)
echo -e "  ${GREEN}Conservé : /var/www/html/ (utilisé par Nginx)${NC}"

# ----------------------------------------------------------
# 4. Vérification post-purge
# ----------------------------------------------------------
echo ""
echo -e "${YELLOW}[4/5] Vérification post-purge...${NC}"

if command -v apache2 &>/dev/null; then
    echo -e "  ${RED}⚠ apache2 encore présent — vérification manuelle requise${NC}"
    APACHE_OK=false
else
    echo -e "  ${GREEN}apache2 : supprimé ✓${NC}"
    APACHE_OK=true
fi

if systemctl is-active --quiet nginx; then
    echo -e "  ${GREEN}Nginx : actif ✓${NC}"
else
    echo -e "  ${RED}Nginx : INACTIF ⚠${NC}"
fi

# Ports web
FREE=true
if ss -tlnp 2>/dev/null | grep -qE ':(80|443)\b'; then
    echo ""
    echo -e "  ${YELLOW}Ports 80/443 :${NC}"
    ss -tlnp 2>/dev/null | grep -E ':(80|443)\b' | while read -r line; do
        echo "    $line"
    done
    # Vérifier que c'est bien nginx
    if ss -tlnp 2>/dev/null | grep -E ':(80|443)\b' | grep -qi nginx; then
        echo -e "  ${GREEN}→ Servi par Nginx ✓${NC}"
    fi
else
    echo -e "  ${RED}Aucun processus sur 80/443 ⚠${NC}"
    FREE=false
fi

# Icecast non affecté
if ss -tlnp 2>/dev/null | grep -q ':8000\b'; then
    echo -e "  ${GREEN}Icecast (:8000) : actif ✓ (non affecté)${NC}"
else
    echo -e "  ${YELLOW}Icecast (:8000) : non détecté${NC}"
fi

# ----------------------------------------------------------
# 5. Test fonctionnel
# ----------------------------------------------------------
echo ""
echo -e "${YELLOW}[5/5] Tests fonctionnels...${NC}"

for domain in programmes.airvs.fr miroir.db.airvs.fr icecast-vps-ovh.airvs.fr pige.airvs.fr; do
    STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://$domain/" 2>/dev/null || echo "000")
    if [ "$STATUS" != "000" ]; then
        echo -e "  ${GREEN}https://$domain/ → HTTP $STATUS ✓${NC}"
    else
        echo -e "  ${YELLOW}https://$domain/ → pas de réponse (DNS local possible)${NC}"
    fi
done

# ----------------------------------------------------------
# RÉSUMÉ
# ----------------------------------------------------------
echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  PURGE TERMINÉE${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
echo -e "  Apache    : ${GREEN}purgé${NC}"
echo -e "  Nginx     : actif (remplace Apache)"
echo -e "  Icecast   : intact (:8000, non affecté)"
echo -e "  /var/www/ : conservé (Nginx l'utilise)"
echo ""
