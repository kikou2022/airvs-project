#!/bin/bash
# ============================================================
# Migration Apache → Nginx — VPS OVH 1 (54.37.38.117)
# ============================================================
# Ce script :
#   1. Supprime les 2 vhosts Apache obsolètes
#   2. Crée les server blocks Nginx (réplique fidèle des configs Apache)
#   3. Arrête Apache, démarre Nginx
#   4. Configure SSL via certbot
#
# Vhosts conservés :
#   - icecast-vps-ovh.airvs.fr  → proxy 54.37.38.117:8000 (Liquidsoap → AzuraCast #2)
#   - miroir.db.airvs.fr        → static /var/www/html/airvs_mirror/
#   - pige.airvs.fr             → static /var/www/pige/
#
# Vhost supprimés :
#   - studio.airvs-normandie.fr
#   - icecast-vps-ovh.airvs.site
#
# Vhost nouveau :
#   - programmes.airvs.fr       → proxy 127.0.0.1:8060 (Gunicorn)
# ============================================================

set -euo pipefail

echo "============================================================"
echo " Migration Apache → Nginx — VPS OVH 1"
echo "============================================================"
echo ""

# Vérification root
if [ "$EUID" -ne 0 ]; then
    echo "[ERREUR] Ce script doit être exécuté avec sudo."
    echo "        sudo bash migrate_apache_to_nginx_vps_ovh_1.sh"
    exit 1
fi

# Vérifier Nginx installé
if ! command -v nginx &> /dev/null; then
    echo "[INFO] Nginx non installé, installation..."
    apt update -qq && apt install -y nginx
fi
echo "[OK] Nginx présent"

# ============================================================
# ÉTAPE 1 : Supprimer les 2 vhosts Apache obsolètes
# ============================================================
echo ""
echo "--- Étape 1 : Suppression vhosts Apache obsolètes ---"

for vhost in studio.airvs-normandie.fr icecast-vps-ovh.airvs.site; do
    for f in "/etc/apache2/sites-enabled/${vhost}.conf" \
             "/etc/apache2/sites-enabled/${vhost}-le-ssl.conf" \
             "/etc/apache2/sites-available/${vhost}.conf" \
             "/etc/apache2/sites-available/${vhost}-le-ssl.conf"; do
        if [ -f "$f" ]; then
            rm -f "$f"
            echo "  [SUPPRIMÉ] $f"
        fi
    done
done

# Vérifier qu'Apache est encore OK (optionnel, on va l'arrêter)
echo "[OK] Vhosts obsolètes supprimés"

# ============================================================
# ÉTAPE 2 : Sauvegarder les configs Apache actuelles (3 restants)
# ============================================================
echo ""
echo "--- Étape 2 : Sauvegarde configs Apache ---"

BACKUP_DIR="/root/apache-backup-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -r /etc/apache2/sites-available/ "$BACKUP_DIR/sites-available"
cp -r /etc/apache2/sites-enabled/ "$BACKUP_DIR/sites-enabled"
echo "[OK] Sauvegardé dans $BACKUP_DIR"

# ============================================================
# ÉTAPE 3 : Créer les server blocks Nginx
# ============================================================
echo ""
echo "--- Étape 3 : Création des server blocks Nginx ---"

# --- Rate limiting (contexte http) ---
cat > /etc/nginx/conf.d/airvs-ratelimit.conf << 'EOF'
# Rate limiting pour les endpoints API (programmes.airvs.fr)
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/m;
EOF
echo "  [OK] /etc/nginx/conf.d/airvs-ratelimit.conf"

# --- 1. icecast-vps-ovh.airvs.fr (proxy vers Icecast :8000) ---
# CRITIQUE : flux lu par AzuraCast #2. Ne pas interrompre.
cat > /etc/nginx/sites-available/icecast-vps-ovh.airvs.fr << 'NGINX'
# HTTP → redirection HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name icecast-vps-ovh.airvs.fr;
    return 301 https://$server_name$request_uri;
}

# HTTPS → proxy Icecast
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name icecast-vps-ovh.airvs.fr;

    ssl_certificate /etc/letsencrypt/live/icecast-vps-ovh.airvs.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/icecast-vps-ovh.airvs.fr/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Headers sécurité
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    access_log /var/log/nginx/icecast-access.log;
    error_log  /var/log/nginx/icecast-error.log;

    location / {
        proxy_pass http://54.37.38.117:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Icecast peut avoir des connexions longues (streaming)
        proxy_buffering off;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
NGINX
ln -sf /etc/nginx/sites-available/icecast-vps-ovh.airvs.fr /etc/nginx/sites-enabled/
echo "  [OK] icecast-vps-ovh.airvs.fr"

# --- 2. miroir.db.airvs.fr (static : /var/www/html/airvs_mirror/) ---
cat > /etc/nginx/sites-available/miroir.db.airvs.fr << 'NGINX'
# HTTP → redirection HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name miroir.db.airvs.fr;
    return 301 https://$server_name$request_uri;
}

# HTTPS → fichiers statiques
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name miroir.db.airvs.fr;

    ssl_certificate /etc/letsencrypt/live/miroir.db.airvs.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/miroir.db.airvs.fr/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root /var/www/html/airvs_mirror;
    index db.json;

    # Pas de listing de répertoire
    autoindex off;

    # Sécurité
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    # Cache agressif pour les fichiers statiques
    location ~* \.json$ {
        add_header Content-Type application/json;
        add_header Cache-Control "public, max-age=3600";
    }

    access_log /var/log/nginx/miroir-access.log;
    error_log  /var/log/nginx/miroir-error.log;
}
NGINX
ln -sf /etc/nginx/sites-available/miroir.db.airvs.fr /etc/nginx/sites-enabled/
echo "  [OK] miroir.db.airvs.fr"

# --- 3. pige.airvs.fr (static : /var/www/pige/) ---
cat > /etc/nginx/sites-available/pige.airvs.fr << 'NGINX'
# HTTP → redirection HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name pige.airvs.fr;
    return 301 https://$server_name$request_uri;
}

# HTTPS → fichiers MP3 statiques
default_type audio/mpeg;

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name pige.airvs.fr;

    ssl_certificate /etc/letsencrypt/live/pige.airvs.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pige.airvs.fr/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root /var/www/pige;
    autoindex on;

    # Sécurité
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    # Support des ranges pour la lecture audio (seek dans les MP3)
    location / {
        add_header Accept-Ranges bytes;
    }

    access_log /var/log/nginx/pige-access.log;
    error_log  /var/log/nginx/pige-error.log;
}
NGINX
ln -sf /etc/nginx/sites-available/pige.airvs.fr /etc/nginx/sites-enabled/
echo "  [OK] pige.airvs.fr"

# --- 4. programmes.airvs.fr (proxy Gunicorn :8060) ---
# Déjà créé par install_vps_ovh_1.sh — on s'assure qu'il existe
cat > /etc/nginx/sites-available/programmes.airvs.fr << 'NGINX'
# HTTP
server {
    listen 80;
    listen [::]:80;
    server_name programmes.airvs.fr;

    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Referrer-Policy strict-origin-when-cross-origin;

    client_max_body_size 1m;

    # Endpoint public Shazam externe (rate limited)
    location /api/shazam/add {
        limit_req zone=api_limit burst=5 nodelay;
        proxy_pass http://127.0.0.1:8060;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Endpoints API sync (rate limited)
    location /api/ {
        limit_req zone=api_limit burst=5 nodelay;
        proxy_pass http://127.0.0.1:8060;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # App web
    location / {
        proxy_pass http://127.0.0.1:8060;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/programmes.airvs.fr /etc/nginx/sites-enabled/
echo "  [OK] programmes.airvs.fr"

# --- Supprimer le default Nginx si présent ---
rm -f /etc/nginx/sites-enabled/default

# --- Vérifier la config Nginx ---
echo ""
echo "--- Vérification config Nginx ---"
if nginx -t 2>&1; then
    echo "[OK] Config Nginx valide"
else
    echo "[ERREUR] Config Nginx invalide — abandon. Apache est encore actif."
    echo "        Corrigez manuellement puis relancez ce script."
    exit 1
fi

# ============================================================
# ÉTAPE 4 : Basculement Apache → Nginx
# ============================================================
echo ""
echo "--- Étape 4 : Basculement ---"
echo ""
echo "Arrêt d'Apache..."
systemctl stop apache2
systemctl disable apache2
echo "[OK] Apache arrêté et désactivé"

echo "Démarrage de Nginx..."
systemctl enable nginx
systemctl start nginx
echo "[OK] Nginx démarré et activé"

# ============================================================
# ÉTAPE 5 : SSL via certbot (mise à jour pour Nginx)
# ============================================================
echo ""
echo "--- Étape 5 : Configuration SSL certbot ---"

if command -v certbot &> /dev/null; then
    # certbot peut ne pas avoir le plugin nginx
    if dpkg -l | grep -q python3-certbot-nginx; then
        echo "[INFO] Plugin Nginx certbot détecté"
    else
        echo "[INFO] Installation du plugin Nginx certbot..."
        apt install -y python3-certbot-nginx 2>/dev/null || true
    fi

    # Les certs existent déjà — certbot --nginx va juste mettre à jour
    # les server blocks Nginx avec sa config SSL optimale
    for domain in icecast-vps-ovh.airvs.fr miroir.db.airvs.fr pige.airvs.fr; do
        echo "  certbot --nginx -d $domain ..."
        certbot --nginx -d "$domain" --non-interactive --redirect 2>&1 | tail -1 || true
    done

    # Programmes : nouveau cert
    echo "  certbot --nginx -d programmes.airvs.fr (nouveau)..."
    certbot --nginx -d programmes.airvs.fr --non-interactive --redirect 2>&1 | tail -1 || true

    echo "[OK] SSL configuré via certbot"
else
    echo "[ATTENTION] certbot non installé. SSL à configurer manuellement."
    echo "  sudo apt install certbot python3-certbot-nginx"
    echo "  sudo certbot --nginx -d icecast-vps-ovh.airvs.fr -d miroir.db.airvs.fr -d pige.airvs.fr -d programmes.airvs.fr"
fi

# ============================================================
# ÉTAPE 6 : Vérification
# ============================================================
echo ""
echo "--- Étape 6 : Vérification ---"

ALL_OK=true

# Vérifier que Nginx tourne
if systemctl is-active --quiet nginx; then
    echo "[OK] Nginx actif"
else
    echo "[ERREUR] Nginx ne tourne PAS"
    ALL_OK=false
fi

# Vérifier que Apache est arrêté
if systemctl is-active --quiet apache2; then
    echo "[ATTENTION] Apache tourne encore (port 80/443 potentiellement en conflit)"
else
    echo "[OK] Apache arrêté"
fi

# Vérifier les ports
echo ""
echo "Ports en écoute :"
ss -tlnp | grep -E ':(80|443|8000|8060) ' || echo "  (aucun port web détecté)"

# Test curl local des vhosts
echo ""
echo "Tests HTTP locaux :"
for domain in icecast-vps-ovh.airvs.fr miroir.db.airvs.fr pige.airvs.fr programmes.airvs.fr; do
    STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://$domain/" 2>/dev/null || echo "000")
    if [ "$STATUS" != "000" ]; then
        echo "  [OK] $domain → HTTP $STATUS"
    else
        echo "  [--] $domain → pas de réponse (DNS peut ne pas résoudre en local)"
    fi
done

echo ""
echo "============================================================"
echo " Migration terminée !"
echo "============================================================"
echo ""
echo "Points de contrôle :"
echo "  1. Vérifier le flux Icecast depuis AzuraCast #2"
echo "  2. Vérifier https://miroir.db.airvs.fr/db.json"
echo "  3. Vérifier https://pige.airvs.fr/"
echo "  4. Vérifier https://programmes.airvs.fr/"
echo ""
echo "En cas de problème :"
echo "  sudo systemctl stop nginx"
echo "  sudo systemctl start apache2 && sudo systemctl enable apache2"
echo "  (configs Apache sauvegardées dans $BACKUP_DIR)"
echo ""
