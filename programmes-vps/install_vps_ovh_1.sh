#!/bin/bash
# ============================================================
# Script d'installation — App Programmes AIRVS sur VPS OVH 1
# ============================================================
# Version : 2.0 — idempotent, migration Apache → Nginx incluse
# Pré-requis : Ubuntu, accès sudo, fichiers app déjà dans APP_DIR
# Usage     : sudo bash install_vps_ovh_1.sh
# ============================================================

set -euo pipefail

# --- Configuration ---
APP_DIR="/opt/airvs-programmes"
DB_PATH="$APP_DIR/programmes_vps.db"
MIRROR_PATH="/var/www/html/airvs_mirror/db.json"
APP_USER="ubuntu"
DOMAIN="programmes.airvs.fr"
VPS_IP="54.37.38.117"

# Vhosts existants à migrer d'Apache vers Nginx (avec SSL existant)
VHOST_ICECAST="icecast-vps-ovh.airvs.fr"
VHOST_MIROIR="miroir.db.airvs.fr"
VHOST_PIGE="pige.airvs.fr"

# Vhosts Apache obsolètes à supprimer
VHOST_OBSOLETE_1="studio.airvs-normandie.fr"
VHOST_OBSOLETE_2="icecast-vps-ovh.airvs.site"

echo "============================================"
echo " Installation App Programmes AIRVS — VPS OVH 1"
echo " Version 2.0 — idempotent"
echo "============================================"
echo ""
echo "Répertoire app  : $APP_DIR"
echo "Base SQLite     : $DB_PATH"
echo "Domaine         : $DOMAIN"
echo "VPS IP          : $VPS_IP"
echo ""

# ============================================================
# 0. Vérifications préalables
# ============================================================
if [ "$EUID" -ne 0 ]; then
    echo "[ERREUR] Ce script doit être exécuté avec sudo."
    echo "        sudo bash install_vps_ovh_1.sh"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "[INFO] Python 3 non trouvé, installation..."
    apt update -qq && apt install -y python3 python3-pip python3-venv
else
    echo "[OK] $(python3 --version 2>&1)"
fi

# Vérifier que les fichiers app sont présents
if [ ! -f "$APP_DIR/app_vps_ovh_1.py" ] || [ ! -f "$APP_DIR/init_db_vps_ovh_1.py" ] || [ ! -f "$APP_DIR/requirements_vps_ovh_1.txt" ]; then
    echo "[ERREUR] Fichiers de l'app manquants dans $APP_DIR/"
    echo "        scp -r programmes-vps/* ubuntu@$VPS_IP:$APP_DIR/"
    exit 1
fi
echo "[OK] Fichiers de l'app détectés"

# ============================================================
# 1. Répertoires
# ============================================================
echo ""
echo "--- Étape 1 : Répertoires ---"
mkdir -p "$APP_DIR/logs"
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
echo "[OK] Répertoires créés"

# ============================================================
# 2. Environnement Python (venv)
# ============================================================
echo ""
echo "--- Étape 2 : Environnement Python ---"
VENV_DIR="$APP_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements_vps_ovh_1.txt" -q
echo "[OK] Venv prêt"

# ============================================================
# 3. Initialisation SQLite
# ============================================================
echo ""
echo "--- Étape 3 : Base SQLite ---"
if [ -f "$DB_PATH" ]; then
    echo "[OK] Base SQLite existante ($(du -h "$DB_PATH" | cut -f1)) — pas de réinitialisation"
else
    sudo -u "$APP_USER" "$VENV_DIR/bin/python" "$APP_DIR/init_db_vps_ovh_1.py" "$DB_PATH"
fi

# ============================================================
# 4. Configuration .env (idempotent)
# ============================================================
echo ""
echo "--- Étape 4 : Configuration .env ---"

# Générer les secrets seulement si .env n'existe pas ou est vide
if [ ! -s "$APP_DIR/.env" ]; then
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    API_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

    # Écrire le .env avec les variables déjà résolues (pas de heredoc avec ${})
    {
        echo "# Configuration App Programmes AIRVS — VPS OVH 1"
        echo "# Généré par install_vps_ovh_1.sh le $(date -Iseconds)"
        echo ""
        echo "DB_PATH=$DB_PATH"
        echo "MIRROR_JSON_PATH=$MIRROR_PATH"
        echo "API_TOKEN=$API_TOKEN"
        echo "SECRET_KEY=$SECRET_KEY"
        echo "FLASK_ENV=production"
        echo "SESSION_DURATION=28800"
        echo "LOG_LEVEL=INFO"
    } > "$APP_DIR/.env"

    chmod 600 "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    echo "[OK] Fichier .env créé"
    echo "     >>> NOTEZ CE TOKEN API (nécessaire pour le Worker) <<<"
    echo "     $API_TOKEN"
else
    echo "[OK] Fichier .env déjà existant — conservé tel quel"
fi

# ============================================================
# 5. Service systemd (idempotent)
# ============================================================
echo ""
echo "--- Étape 5 : Service systemd ---"
cat > /etc/systemd/system/airvs-programmes.service << SYSTEMD_EOF
[Unit]
Description=AIRVS Programmes Web App (Flask + Gunicorn)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/gunicorn \\
    --bind 127.0.0.1:8060 \\
    --workers 2 \\
    --timeout 120 \\
    --access-logfile $APP_DIR/logs/access.log \\
    --error-logfile $APP_DIR/logs/error.log \\
    app_vps_ovh_1:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF

echo "[OK] Service systemd écrit (daemon-reload + start plus tard)"

# ============================================================
# 6. Migration Apache → Nginx
# ============================================================
echo ""
echo "--- Étape 6 : Migration Apache → Nginx ---"

# 6a. Installer Nginx si besoin
if ! command -v nginx &> /dev/null; then
    echo "[INFO] Installation de Nginx..."
    apt install -y nginx
fi

# 6b. Supprimer les vhosts Apache obsolètes
for vhost in "$VHOST_OBSOLETE_1" "$VHOST_OBSOLETE_2"; do
    conf="/etc/apache2/sites-enabled/${vhost}.conf"
    if [ -f "$conf" ]; then
        a2dissite "$vhost" 2>/dev/null || true
        echo "[OK] Vhost Apache obsolète désactivé : $vhost"
    fi
done

# 6c. Arrêter Apache si actif
if systemctl is-active --quiet apache2 2>/dev/null; then
    echo "[INFO] Arrêt d'Apache..."
    systemctl stop apache2
    systemctl disable apache2 2>/dev/null || true
    echo "[OK] Apache arrêté et désactivé"
else
    echo "[OK] Apache déjà inactif"
fi

# 6d. Fichiers certbot requis par les server blocks SSL
if [ ! -f /etc/letsencrypt/options-ssl-nginx.conf ]; then
    cat > /etc/letsencrypt/options-ssl-nginx.conf << 'SSL_OPTIONS'
ssl_session_cache shared:le_nginx_SSL:10m;
ssl_session_timeout 1440m;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
ssl_ciphers "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384";
SSL_OPTIONS
    echo "[OK] /etc/letsencrypt/options-ssl-nginx.conf créé"
fi

if [ ! -f /etc/letsencrypt/ssl-dhparams.pem ]; then
    echo "[INFO] Génération des paramètres DH (peut prendre quelques secondes)..."
    openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048
    echo "[OK] /etc/letsencrypt/ssl-dhparams.pem créé"
fi

# 6e. Rate limiting (idempotent — supprime les anciens fichiers dupliqués)
rm -f /etc/nginx/conf.d/airvs-programmes-ratelimit.conf
if [ ! -f /etc/nginx/conf.d/airvs-ratelimit.conf ]; then
    cat > /etc/nginx/conf.d/airvs-ratelimit.conf << 'RATELIMIT'
# Rate limiting partagé — contexte http (UNE seule définition)
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/m;
RATELIMIT
    echo "[OK] Rate limiting configuré"
else
    echo "[OK] Rate limiting déjà configuré"
fi

# 6f. Server block : icecast-vps-ovh.airvs.fr (proxy → Icecast :8000)
echo "[INFO] Configuration vhost : $VHOST_ICECAST"
cat > "/etc/nginx/sites-available/$VHOST_ICECAST" << NGINX_ICECAST
# HTTP → HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $VHOST_ICECAST;
    return 301 https://\$server_name\$request_uri;
}

# HTTPS → proxy Icecast
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $VHOST_ICECAST;

    ssl_certificate     /etc/letsencrypt/live/$VHOST_ICECAST/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$VHOST_ICECAST/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Sécurité
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Support flux audio long (pas de timeout)
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

    access_log /var/log/nginx/icecast-access.log;
    error_log  /var/log/nginx/icecast-error.log;
}
NGINX_ICECAST

# 6g. Server block : miroir.db.airvs.fr (statique)
echo "[INFO] Configuration vhost : $VHOST_MIROIR"
cat > "/etc/nginx/sites-available/$VHOST_MIROIR" << NGINX_MIROIR
# HTTP → HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $VHOST_MIROIR;
    return 301 https://\$server_name\$request_uri;
}

# HTTPS → fichiers statiques
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $VHOST_MIROIR;

    ssl_certificate     /etc/letsencrypt/live/$VHOST_MIROIR/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$VHOST_MIROIR/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root /var/www/html/airvs_mirror;
    index index.html;

    autoindex off;

    # Sécurité
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    # Cache pour les fichiers statiques
    location ~* \.json\$ {
        add_header Content-Type application/json;
        add_header Cache-Control "public, max-age=3600";
    }

    access_log /var/log/nginx/miroir-access.log;
    error_log  /var/log/nginx/miroir-error.log;
}
NGINX_MIROIR

# 6h. Server block : pige.airvs.fr (statique)
echo "[INFO] Configuration vhost : $VHOST_PIGE"
cat > "/etc/nginx/sites-available/$VHOST_PIGE" << NGINX_PIGE
# HTTP → HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $VHOST_PIGE;
    return 301 https://\$server_name\$request_uri;
}

# HTTPS → fichiers statiques
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $VHOST_PIGE;

    ssl_certificate     /etc/letsencrypt/live/$VHOST_PIGE/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$VHOST_PIGE/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root /var/www/pige;

    autoindex off;

    # Sécurité
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    # Cache pour les fichiers audio
    location ~* \.(mp3|ogg|aac|wav)\$ {
        add_header Cache-Control "public, max-age=3600";
    }

    access_log /var/log/nginx/pige-access.log;
    error_log  /var/log/nginx/pige-error.log;
}
NGINX_PIGE

# 6i. Server block : programmes.airvs.fr (proxy → Gunicorn :8060)
echo "[INFO] Configuration vhost : $DOMAIN"
cat > "/etc/nginx/sites-available/$DOMAIN" << NGINX_PROGRAMMES
# HTTP → HTTPS (sera géré par certbot après SSL)
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    # Sécurité de base
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    client_max_body_size 1m;

    # Endpoint public Shazam externe (sans auth — rate limited)
    location /api/shazam/add {
        limit_req zone=api_limit burst=5 nodelay;
        proxy_pass http://127.0.0.1:8060;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Endpoints API sync (auth token — rate limited)
    location /api/ {
        limit_req zone=api_limit burst=5 nodelay;
        proxy_pass http://127.0.0.1:8060;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # App web (formulaire, historique, admin)
    location / {
        proxy_pass http://127.0.0.1:8060;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX_PROGRAMMES

# 6j. Supprimer l'ancien nom de server block s'il existe
rm -f /etc/nginx/sites-available/airvs-programmes
rm -f /etc/nginx/sites-enabled/airvs-programmes

# 6k. Activer tous les server blocks (idempotent)
for vhost in "$VHOST_ICECAST" "$VHOST_MIROIR" "$VHOST_PIGE" "$DOMAIN"; do
    ln -sf "/etc/nginx/sites-available/$vhost" "/etc/nginx/sites-enabled/$vhost"
done

# Supprimer le default si actif (évite les conflits)
if [ -f /etc/nginx/sites-enabled/default ]; then
    rm -f /etc/nginx/sites-enabled/default
fi

# 6l. Tester et démarrer Nginx
if nginx -t 2>&1; then
    echo "[OK] Config Nginx valide"
    systemctl start nginx 2>/dev/null || systemctl reload nginx
    systemctl enable nginx 2>/dev/null || true
    echo "[OK] Nginx démarré et activé"
else
    echo "[ERREUR] Config Nginx invalide — corrigez manuellement puis relancez ce script."
    exit 1
fi

# ============================================================
# 7. SSL Let's Encrypt
# ============================================================
echo ""
echo "--- Étape 7 : SSL (Let's Encrypt) ---"

# Installer le plugin Nginx certbot si besoin
if ! dpkg -l python3-certbot-nginx &> /dev/null 2>&1; then
    echo "[INFO] Installation de python3-certbot-nginx..."
    apt install -y python3-certbot-nginx
fi

# Vérifier si le SSL est déjà actif pour programmes.airvs.fr
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    echo "[OK] SSL déjà actif pour $DOMAIN"
else
    echo "[INFO] Vérification DNS pour $DOMAIN..."
    DIG_IP=$(dig +short "$DOMAIN" 2>/dev/null | tail -1)
    if [ "$DIG_IP" = "$VPS_IP" ]; then
        echo "[INFO] DNS OK ($DOMAIN → $VPS_IP), demande de certificat..."
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@airvs.fr
        echo "[OK] SSL activé pour $DOMAIN"
    else
        echo "[ATTENTION] $DNS ne résout pas encore vers $VPS_IP (actuel: ${DIG_IP:-vide})"
        echo "            Lancez manuellement après avoir configuré le DNS :"
        echo "            sudo certbot --nginx -d $DOMAIN"
    fi
fi

# ============================================================
# 8. Sauvegarde SQLite (cron)
# ============================================================
echo ""
echo "--- Étape 8 : Sauvegarde automatique SQLite ---"
BACKUP_DIR="/var/backups/airvs-programmes"
mkdir -p "$BACKUP_DIR"
cat > /etc/cron.daily/airvs-programmes-backup << CRON_BAK
#!/bin/bash
# Sauvegarde quotidienne de la base SQLite
BACKUP_DIR="/var/backups/airvs-programmes"
DB_PATH="$DB_PATH"
DATE=\$(date +%Y%m%d_%H%M%S)
mkdir -p "\$BACKUP_DIR"
sqlite3 "\$DB_PATH" ".backup '\$BACKUP_DIR/programmes_\$DATE.db'"
# Conserver les 7 derniers backups
ls -t "\$BACKUP_DIR"/programmes_*.db 2>/dev/null | tail -n +8 | xargs -r rm --
CRON_BAK
chmod +x /etc/cron.daily/airvs-programmes-backup
echo "[OK] Sauvegarde quotidienne configurée ($BACKUP_DIR)"

# ============================================================
# 9. Démarrage du service
# ============================================================
echo ""
echo "--- Étape 9 : Démarrage du service ---"
systemctl daemon-reload
systemctl enable airvs-programmes
systemctl restart airvs-programmes

# Attendre que le service soit prêt
sleep 2

if systemctl is-active --quiet airvs-programmes; then
    echo "[OK] Service airvs-programmes actif"
else
    echo "[ERREUR] Le service n'a pas démarré. Vérifiez :"
    echo "        sudo journalctl -u airvs-programmes --no-pager -n 30"
    exit 1
fi

# ============================================================
# 10. Résumé
# ============================================================
echo ""
echo "============================================"
echo " Installation terminée !"
echo "============================================"
echo ""
echo "Vhosts Nginx actifs :"
echo "  ✅ $VHOST_ICECAST  → proxy Icecast :8000"
echo "  ✅ $VHOST_MIROIR   → statique /var/www/html/airvs_mirror"
echo "  ✅ $VHOST_PIGE     → statique /var/www/pige"
echo "  ✅ $DOMAIN → proxy Gunicorn :8060"
echo ""
echo "Prochaines étapes :"
echo ""
echo "1. Changer le mot de passe admin :"
echo "   URL : https://$DOMAIN"
echo "   Login par défaut : admin / admin"
echo "   Commande de changement :"
echo "   $VENV_DIR/bin/python3 -c \"import hashlib,sqlite3; m='NOUVEAU_MDP'; h=hashlib.sha256(m.encode()).hexdigest(); c=sqlite3.connect('$DB_PATH'); c.execute('UPDATE programmateurs SET password_hash=? WHERE username=?',(h,'admin')); c.commit(); c.close(); print('OK')\""
echo ""
echo "2. Récupérer le token API pour le Worker :"
echo "   grep API_TOKEN $APP_DIR/.env"
echo ""
echo "3. Configurer MacroDroid Shazam externe :"
echo "   URL  : https://$DOMAIN/api/shazam/add"
echo "   POST : artiste=...&titre=..."
echo ""
echo "4. Test de l'endpoint Shazam externe :"
echo "   curl -X POST https://$DOMAIN/api/shazam/add -d 'artiste=Test&titre=Test'"
echo ""
echo "5. Nettoyage Apache (optionnel) :"
echo "   sudo apt remove --purge apache2 -y && sudo apt autoremove -y"
echo ""
