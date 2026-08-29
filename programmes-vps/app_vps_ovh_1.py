"""
============================================================
App Flask VPS — Programmes AIRVS
============================================================
Machine cible  : VPS OVH 1 (54.37.38.117)
Sous-domaine  : programmes.airvs.fr
Port interne  : 127.0.0.1:8060 (derrière Nginx)

ATTENTION : Ce fichier est distinct du dashboard Windows (app.py,
~7700 lignes, qui tourne sur 192.168.1.39/30 en LAN sans internet).
Ce fichier est une app lц╘gц╗re pour le VPS public.
============================================================

Endpoints :
  Web   : /login, /logout, /, /historique, /admin
  API   : /api/sync/pull, /api/sync/ack, /api/sync/status
           /api/shazam/external/pull, /api/shazam/external/ack
  Public: /api/shazam/add (MacroDroid, sans auth)
"""

import os
import json
import hashlib
import sqlite3
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, request, session, redirect, url_for,
                   jsonify, g, render_template_string)
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# ============================================================
# Configuration
# ============================================================

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24).hex())
app.config['SESSION_DURATION'] = int(os.getenv('SESSION_DURATION', 28800))

DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(__file__), '..', 'data', 'programmes.db'))
MIRROR_JSON_PATH = os.getenv('MIRROR_JSON_PATH', '/var/www/html/airvs_mirror/db.json')
API_TOKEN = os.getenv('API_TOKEN', '')

# Rate-limiting (fenêtre glissante en mémoire, thread-safe)
# Configurable via SHAZAM_RATE_LIMIT (req) et SHAZAM_RATE_WINDOW (secondes)
SHAZAM_RATE_LIMIT  = int(os.getenv('SHAZAM_RATE_LIMIT', 30))   # max req par fenêtre
SHAZAM_RATE_WINDOW = int(os.getenv('SHAZAM_RATE_WINDOW', 3600)) # fenêtre en secondes (1h)

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('airvs-programmes')

# Miroir JSON en mémoire
mirror_data = []
mirror_loaded_at = None
mirror_file_mtime = None


def load_mirror(force=False):
    """Charge ou re-charge db.json en mémoire s'il a été modifié sur le disque."""
    global mirror_data, mirror_loaded_at, mirror_file_mtime
    try:
        if not os.path.exists(MIRROR_JSON_PATH):
            if force or mirror_loaded_at is None:
                logger.warning(f"Miroir introuvable : {MIRROR_JSON_PATH}")
                mirror_data = []
                mirror_loaded_at = None
                mirror_file_mtime = None
            return False

        current_mtime = os.path.getmtime(MIRROR_JSON_PATH)
        if not force and mirror_file_mtime is not None and current_mtime == mirror_file_mtime:
            return True  # Déjà à jour

        with open(MIRROR_JSON_PATH, 'r', encoding='utf-8') as f:
            mirror_data = json.load(f)
        mirror_loaded_at = datetime.now()
        mirror_file_mtime = current_mtime
        logger.info(f"Miroir chargé : {len(mirror_data)} titres (mtime={current_mtime})")
        return True
    except FileNotFoundError:
        logger.warning(f"Miroir introuvable : {MIRROR_JSON_PATH}")
        mirror_data = []
        mirror_loaded_at = None
        mirror_file_mtime = None
        return False
    except json.JSONDecodeError as e:
        logger.error(f"Erreur parsing miroir JSON : {e}")
        return False

# ============================================================
# Base de données
# ============================================================

def get_db():
    """Connexion SQLite avec row_factory et timeout."""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10.0)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# ============================================================
# Flask-Login
# ============================================================

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'strong'


@login_manager.unauthorized_handler
def unauthorized():
    """Retourne une réponse 401 JSON pour l'API, ou redirige vers /login pour le Web."""
    if request.path.startswith('/api/'):
        return jsonify({"error": "Non authentifié", "status": "unauthorized"}), 401
    return redirect(url_for('login'))


class User(UserMixin):
    def __init__(self, user_row):
        self.id = user_row['id']
        self.username = user_row['username']
        self.display_name = user_row['display_name']
        self.role = user_row['role']
        self.actif = user_row['actif']

    @property
    def is_admin(self):
        return self.role == 'admin'


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM programmateurs WHERE id = ? AND actif = 1", (user_id,)).fetchone()
    if row:
        return User(row)
    return None

# ============================================================
# Décorateurs
# ============================================================

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Accès réservé aux administrateurs"}), 403
            return render_template_string('''<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>Accès refusé</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#1e293b;padding:2rem 3rem;border-radius:12px;text-align:center;max-width:400px}
.box h2{color:#f87171;margin-bottom:.5rem}
.box p{color:#94a3b8;font-size:.9rem;margin-bottom:1.5rem}
.box a{color:#38bdf8;text-decoration:none;font-size:.85rem}
.box a:hover{text-decoration:underline}
</style></head><body>
<div class="box">
<h2>Accès refusé</h2>
<p>Cette page est réservée aux administrateurs.</p>
<a href="/">Retour à l\'accueil</a>
</div></body></html>''')
        return f(*args, **kwargs)
    return decorated


def api_token_required(f):
    """Vérifie le token (Bearer ou X-API-Token) pour les endpoints appelés par le worker."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not API_TOKEN:
            return jsonify({"error": "API_TOKEN non configuré sur le serveur"}), 500
        auth = request.headers.get('Authorization', '')
        x_token = request.headers.get('X-API-Token', '')
        token = auth[7:] if auth.startswith('Bearer ') else x_token
        if not token:
            return jsonify({"error": "Token manquant"}), 401
        if token != API_TOKEN:
            return jsonify({"error": "Token invalide"}), 401
        return f(*args, **kwargs)
    return decorated


# ============================================================
# Rate-limiting (SQLite, partagé entre workers Gunicorn)
# ============================================================

def _get_client_ip() -> str:
    """Récupère l'IP réelle du client (proxy Nginx X-Forwarded-For)."""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def rate_limit(max_requests: int, window_seconds: int):
    """
    Décorateur de rate-limiting par IP — fenêtre glissante stockée dans SQLite.

    Compatible multi-workers Gunicorn (WAL SQLite garantit la cohérence).
    Renvoie HTTP 429 + header Retry-After si le quota est dépassé.

    Usage :
        @rate_limit(30, 3600)   # 30 req/h
        def ma_route(): ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = _get_client_ip()

            # Connexion SQLite directe (indépendante du contexte Flask g)
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                # Purge des entrées expirées (nettoyage opportuniste)
                conn.execute(
                    "DELETE FROM rate_limit_log "
                    "WHERE ip = ? AND ts < strftime('%s', 'now') - ?",
                    (ip, window_seconds)
                )

                # Compte les requêtes dans la fenêtre
                count = conn.execute(
                    "SELECT COUNT(*) FROM rate_limit_log "
                    "WHERE ip = ? AND ts >= strftime('%s', 'now') - ?",
                    (ip, window_seconds)
                ).fetchone()[0]

                if count >= max_requests:
                    # Calcule le Retry-After : temps avant expiration de la plus ancienne entrée
                    oldest_ts = conn.execute(
                        "SELECT MIN(ts) FROM rate_limit_log "
                        "WHERE ip = ? AND ts >= strftime('%s', 'now') - ?",
                        (ip, window_seconds)
                    ).fetchone()[0] or 0
                    retry_after = max(1, int(oldest_ts + window_seconds
                                            - datetime.now().timestamp()) + 1)
                    conn.commit()
                    logger.warning(
                        f"Rate-limit : IP {ip} — {count} req/{window_seconds}s "
                        f"(quota={max_requests})"
                    )
                    resp = jsonify({
                        "status": "rate_limited",
                        "message": (
                            f"Quota dépassé : {max_requests} requêtes "
                            f"par {window_seconds // 60} minute(s). "
                            f"Réessayez dans {retry_after}s."
                        ),
                        "retry_after": retry_after
                    })
                    resp.headers['Retry-After'] = str(retry_after)
                    return resp, 429

                # Enregistre la requête courante
                conn.execute(
                    "INSERT INTO rate_limit_log (ip, ts) "
                    "VALUES (?, strftime('%s', 'now'))",
                    (ip,)
                )
                conn.commit()
            finally:
                conn.close()

            return f(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# Miroir JSON
# ============================================================


def get_mirror_age():
    """Retourne l'âge du miroir en minutes, ou None."""
    if mirror_loaded_at is None:
        return None
    delta = datetime.now() - mirror_loaded_at
    return int(delta.total_seconds() / 60)

# ============================================================
# Nettoyage Shazam
# ============================================================

def nettoyer_artiste_shazam(artiste: str) -> str:
    """Supprime les suffixes parasites ajoutés par Shazam aux notifications."""
    suffixes = ["Shazam Dashboard", "Shazam"]
    for suffixe in suffixes:
        if artiste.endswith(suffixe):
            artiste = artiste[:-len(suffixe)].strip()
    return artiste.strip()

# ============================================================
# Matching 5 passes (version JSON en mémoire)
# ============================================================

def _normaliser_pour_recherche(texte: str) -> str:
    """
    Normalise un texte pour comparaison floue (même logique que le worker) :
    - minuscules, sans accents
    - remplace & et + par des espaces
    - remplace les mots de liaison 'et', 'and', 'x' par des espaces
    - supprime ponctuation et caractères spéciaux
    - collapse les espaces multiples
    """
    if not texte:
        return ""
    texte = str(texte).strip().lower()
    texte = ''.join(
        c for c in unicodedata.normalize('NFD', texte)
        if unicodedata.category(c) != 'Mn'
    )
    for caractere in "()[]&'+-":
        texte = texte.replace(caractere, " ")
    texte = re.sub(r'\b(?:et|and|x)\b', ' ', texte, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', texte).strip()


def _extraire_principal(texte: str) -> str:
    """
    Extrait la partie principale (même logique que le worker) :
    - "Towa Bird feat. Kathleen Hanna" → "Towa Bird"
    - "All Gone (feat. Kathleen Hanna)" → "All Gone"
    Conserve les parenthèses non-feat (cover, remix, radio edit...)
    """
    if not texte:
        return texte
    texte = re.sub(r'\s*\([^)]*(?:feat\.?|ft\.?|featuring)[^)]*\)\s*', ' ', texte, flags=re.IGNORECASE).strip()
    texte = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', texte, flags=re.IGNORECASE)[0].strip()
    return texte


def _extraire_nu(texte: str) -> str:
    """
    Extrait le noyau nu (même logique que le worker) :
    supprime TOUT contenu entre parenthèses ET les feat./ft./featuring.
    """
    if not texte:
        return texte
    texte = re.sub(r'\s*\([^)]*\)\s*', ' ', texte).strip()
    texte = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', texte, flags=re.IGNORECASE)[0].strip()
    return texte


def _mots_cles(texte: str) -> set:
    """Extrait les mots significatifs (>= 3 car.) d'un texte normalisé."""
    n = _normaliser_pour_recherche(texte)
    return {m for m in n.split() if len(m) >= 3}


def _get_artist(song: dict) -> str:
    return song.get('artist') or song.get('artiste') or ''


def _get_title(song: dict) -> str:
    return song.get('title') or song.get('titre') or ''


def match_all(artiste: str, titre: str, max_results: int = 10) -> list[dict]:
    """
    Algorithme de matching 5 passes sur le miroir en mémoire.
    Retourne TOUTES les variantes trouvées (doublons RadioDJ, remasters, feat...),
    classées par passe de la meilleure à la plus faible.
    Gère les clés 'artist'/'artiste' et 'title'/'titre' du db.json.
    """
    load_mirror()  # Hot-reload si db.json a été mis à jour sur disque
    if not mirror_data:
        return []

    results = []
    seen_ids = set()

    def _add(song, passe):
        sid = song.get('id')
        if sid is not None and sid not in seen_ids:
            seen_ids.add(sid)
            results.append({"song": song, "passe": passe})

    # Passe 1 : stricte
    art_low = artiste.lower().strip()
    tit_low = titre.lower().strip()
    for song in mirror_data:
        if (_get_artist(song).lower() == art_low and
                _get_title(song).lower() == tit_low):
            _add(song, 1)

    # Passe 2 : principale (sans feat/ft)
    art_p = _extraire_principal(artiste).lower()
    tit_p = _extraire_principal(titre).lower()
    if art_p != art_low or tit_p != tit_low:
        for song in mirror_data:
            s_art = _extraire_principal(_get_artist(song)).lower()
            s_tit = _extraire_principal(_get_title(song)).lower()
            if art_p and tit_p and s_art == art_p and s_tit == tit_p:
                _add(song, 2)

    # Passe 2b : nue (sans parenthèses)
    art_n = _extraire_nu(artiste).lower()
    tit_n = _extraire_nu(titre).lower()
    if art_n != art_p or tit_n != tit_p:
        if art_n and tit_n:
            for song in mirror_data:
                s_art = _extraire_nu(_get_artist(song)).lower()
                s_tit = _extraire_nu(_get_title(song)).lower()
                if s_art == art_n and s_tit == tit_n:
                    _add(song, "2b")

    # Passe 3 : normalisée avec contains
    art_norm = _normaliser_pour_recherche(artiste)
    tit_norm = _normaliser_pour_recherche(titre)
    art_p_norm = _normaliser_pour_recherche(_extraire_principal(artiste))
    tit_p_norm = _normaliser_pour_recherche(_extraire_principal(titre))
    art_n_norm = _normaliser_pour_recherche(_extraire_nu(artiste))
    tit_n_norm = _normaliser_pour_recherche(_extraire_nu(titre))
    pass3_count = 0
    for a_search, t_search in [
        (art_n_norm, tit_n_norm),
        (art_p_norm, tit_p_norm),
        (art_norm, tit_norm),
    ]:
        if a_search and t_search:
            for song in mirror_data:
                s_art = _normaliser_pour_recherche(_get_artist(song))
                s_tit = _normaliser_pour_recherche(_get_title(song))
                if a_search in s_art and t_search in s_tit:
                    _add(song, 3)
                    pass3_count += 1
                    if pass3_count >= 3:
                        break
            if pass3_count >= 3:
                break

    # Passe 4 : mots-clés (max 2 résultats)
    if len(results) < max_results:
        art_mots = _mots_cles(_extraire_nu(artiste))
        tit_mots = _mots_cles(_extraire_nu(titre))
        if len(art_mots) + len(tit_mots) >= 2:
            art_best = max(art_mots, key=len) if art_mots else ""
            tit_best = max(tit_mots, key=len) if tit_mots else ""
            pass4_count = 0
            for song in mirror_data:
                if len(results) >= max_results:
                    break
                s_art_mots = _mots_cles(_extraire_nu(_get_artist(song)))
                s_tit_mots = _mots_cles(_extraire_nu(_get_title(song)))
                score = 0
                if art_best and art_best in s_art_mots:
                    score += 1
                if tit_best and tit_best in s_tit_mots:
                    score += 1
                if score >= 2:
                    _add(song, 4)
                    pass4_count += 1
                    if pass4_count >= 2:
                        break

    # Passe 5 : inversion artiste ↔ titre
    # Si aucune passe n'a donné de résultat, tente en inversant les deux champs.
    # Utile quand l'utilisateur saisit "Titre - Artiste" au lieu de "Artiste - Titre".
    if not results:
        # 5a : stricte inversée
        for song in mirror_data:
            if (_get_artist(song).lower() == tit_low and
                    _get_title(song).lower() == art_low):
                _add(song, '5a')

        # 5b : principale inversée (sans feat)
        if not results and (art_p != art_low or tit_p != tit_low):
            for song in mirror_data:
                s_art = _extraire_principal(_get_artist(song)).lower()
                s_tit = _extraire_principal(_get_title(song)).lower()
                if s_art == tit_p and s_tit == art_p:
                    _add(song, '5b')

        # 5c : normalisée inversée
        if not results:
            for a_search, t_search in [
                (tit_n_norm, art_n_norm),
                (tit_p_norm, art_p_norm),
                (tit_norm, art_norm),
            ]:
                if a_search and t_search:
                    found_5c = False
                    for song in mirror_data:
                        s_art = _normaliser_pour_recherche(_get_artist(song))
                        s_tit = _normaliser_pour_recherche(_get_title(song))
                        if a_search in s_art and t_search in s_tit:
                            _add(song, '5c')
                            found_5c = True
                            break
                    if found_5c:
                        break

    return results[:max_results]


def match_5_passes(artiste: str, titre: str) -> dict | None:
    """Wrapper : retourne le meilleur match (compat soumettre)."""
    results = match_all(artiste, titre, max_results=1)
    return results[0] if results else None

# ============================================================
# ROUTES WEB
# ============================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Page de connexion."""
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            error = "Identifiant et mot de passe requis"
        else:
            db = get_db()
            user_row = db.execute(
                "SELECT * FROM programmateurs WHERE username = ? AND actif = 1",
                (username,)
            ).fetchone()
            if user_row:
                db_hash = user_row['password_hash']
                pw_valid = False
                need_hash_update = False

                if db_hash.startswith('pbkdf2:') or db_hash.startswith('scrypt:') or db_hash.startswith('argon2'):
                    pw_valid = check_password_hash(db_hash, password)
                else:
                    # Fallback rétrocompatible pour anciens mots de passe sha256
                    legacy_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
                    if db_hash == legacy_hash or check_password_hash(db_hash, password):
                        pw_valid = True
                        need_hash_update = True

                if pw_valid:
                    user = User(user_row)
                    login_user(user, remember=False, duration=timedelta(seconds=app.config['SESSION_DURATION']))
                    if need_hash_update:
                        new_hash = generate_password_hash(password)
                        db.execute(
                            "UPDATE programmateurs SET password_hash = ?, date_derniere_connexion = datetime('now') WHERE id = ?",
                            (new_hash, user.id)
                        )
                        logger.info(f"Connexion & migration du hash mot de passe vers werkzeug.security : {username}")
                    else:
                        db.execute(
                            "UPDATE programmateurs SET date_derniere_connexion = datetime('now') WHERE id = ?",
                            (user.id,)
                        )
                    db.commit()
                    logger.info(f"Connexion : {username}")
                    return redirect(url_for('index'))
            error = "Identifiant ou mot de passe incorrect"
            logger.warning(f"Échec connexion : {username}")
    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """Page principale : formulaire de soumission."""
    mirror_age = get_mirror_age()
    # Mode edition : pre-charger la soumission cote serveur
    edit_data = None
    edit_id = request.args.get('edit', type=int)
    if edit_id:
        db = get_db()
        row = db.execute("SELECT * FROM soumissions WHERE id = ?", (edit_id,)).fetchone()
        if row and (row['programmeur_id'] == current_user.id or current_user.is_admin):
            edit_data = dict(row)
            if edit_data.get('match_result'):
                try:
                    import json as _json
                    edit_data['match_result'] = _json.loads(edit_data['match_result'])
                except (_json.JSONDecodeError, TypeError):
                    edit_data['match_result'] = None
    return render_template_string(FORMULAIRE_TEMPLATE,
                                   user=current_user,
                                   mirror_age=mirror_age,
                                   mirror_count=len(mirror_data),
                                   edit_data=edit_data,
                                   edit_id=edit_id if edit_data else None,
                                   edit_locked=edit_data is not None and edit_data.get('statut') != 'pending')


@app.route('/historique')
@login_required
def historique():
    """Historique des soumissions de l’utilisateur connecté."""
    db = get_db()
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    query = db.execute(
        "SELECT s.*, p.display_name FROM soumissions s "
        "JOIN programmateurs p ON s.programmeur_id = p.id "
        "WHERE s.programmeur_id = ? "
        "ORDER BY s.date_soumission DESC LIMIT ? OFFSET ?",
        (current_user.id, per_page, offset)
    ).fetchall()

    total = db.execute(
        "SELECT COUNT(*) FROM soumissions WHERE programmeur_id = ?",
        (current_user.id,)
    ).fetchone()[0]

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    return render_template_string(HISTORIQUE_TEMPLATE,
                                   user=current_user,
                                   soumissions=query,
                                   page=page,
                                   total_pages=total_pages,
                                   total=total)


@app.route('/admin')
@admin_required
def admin():
    """Interface d'administration."""
    db = get_db()
    utilisateurs = db.execute(
        "SELECT p.*, COUNT(s.id) as nb_soumissions "
        "FROM programmateurs p LEFT JOIN soumissions s ON p.id = s.programmeur_id "
        "GROUP BY p.id ORDER BY p.date_creation"
    ).fetchall()

    pending_count = db.execute(
        "SELECT COUNT(*) FROM soumissions WHERE statut = 'pending'"
    ).fetchone()[0]

    shazam_pending = db.execute(
        "SELECT COUNT(*) FROM shazam_external WHERE statut = 'pending'"
    ).fetchone()[0]

    shazam_total = db.execute(
        "SELECT COUNT(*) FROM shazam_external"
    ).fetchone()[0]

    shazam_synced = db.execute(
        "SELECT COUNT(*) FROM shazam_external WHERE statut = 'synced'"
    ).fetchone()[0]

    return render_template_string(ADMIN_TEMPLATE,
                                   user=current_user,
                                   utilisateurs=utilisateurs,
                                   pending_count=pending_count,
                                   shazam_pending=shazam_pending,
                                   shazam_total=shazam_total,
                                   shazam_synced=shazam_synced)


# ============================================================
# API : Gestion utilisateurs (admin)
# ============================================================

@app.route('/api/admin/users/create', methods=['POST'])
@admin_required
def api_admin_create_user():
    """Création d'un nouvel utilisateur."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Payload JSON requis"}), 400

    username = data.get('username', '').strip().lower()
    display_name = data.get('display_name', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'animateur').strip()

    # Validations
    if not username or not display_name or not password:
        return jsonify({"error": "Identifiant, nom affiché et mot de passe requis"}), 400
    if len(username) < 2:
        return jsonify({"error": "Identifiant trop court (min 2 car.)"}), 400
    if len(password) < 4:
        return jsonify({"error": "Mot de passe trop court (min 4 car.)"}), 400
    if role not in ('admin', 'animateur'):
        role = 'animateur'

    db = get_db()

    # Vérifier unicité username
    existing = db.execute("SELECT id FROM programmateurs WHERE LOWER(username) = ?", (username,)).fetchone()
    if existing:
        return jsonify({"error": "Cet identifiant existe déjà"}), 409

    pw_hash = generate_password_hash(password)
    try:
        db.execute(
            "INSERT INTO programmateurs (username, display_name, password_hash, role, actif, date_creation) "
            "VALUES (?, ?, ?, ?, 1, datetime('now'))",
            (username, display_name, pw_hash, role)
        )
        db.commit()
        logger.info(f"Utilisateur créé : {username} ({role}) par {current_user.username}")
        return jsonify({"status": "ok", "username": username})
    except Exception as e:
        logger.error(f"Erreur création utilisateur : {e}")
        return jsonify({"error": "Erreur lors de la création"}), 500


@app.route('/api/admin/users/<int:uid>/toggle', methods=['POST'])
@admin_required
def api_admin_toggle_user(uid):
    """Active/désactive un utilisateur."""
    db = get_db()
    row = db.execute("SELECT id, username, actif FROM programmateurs WHERE id = ?", (uid,)).fetchone()
    if not row:
        return jsonify({"error": "Utilisateur introuvable"}), 404
    if row['id'] == current_user.id:
        return jsonify({"error": "Impossible de se désactiver soi-même"}), 400
    new_actif = 0 if row['actif'] else 1
    db.execute("UPDATE programmateurs SET actif = ? WHERE id = ?", (new_actif, uid))
    db.commit()
    logger.info(f"Utilisateur {row['username']} {'activé' if new_actif else 'désactivé'} par {current_user.username}")
    return jsonify({"status": "ok", "actif": bool(new_actif)})


@app.route('/api/admin/users/<int:uid>/delete', methods=['POST'])
@admin_required
def api_admin_delete_user(uid):
    """Supprime un utilisateur (et ses soumissions liées)."""
    db = get_db()
    row = db.execute("SELECT id, username FROM programmateurs WHERE id = ?", (uid,)).fetchone()
    if not row:
        return jsonify({"error": "Utilisateur introuvable"}), 404
    if row['id'] == current_user.id:
        return jsonify({"error": "Impossible de se supprimer soi-même"}), 400
    db.execute("DELETE FROM soumissions WHERE programmeur_id = ?", (uid,))
    db.execute("DELETE FROM programmateurs WHERE id = ?", (uid,))
    db.commit()
    logger.info(f"Utilisateur {row['username']} supprimé par {current_user.username}")
    return jsonify({"status": "ok"})


# ============================================================
# API : Soumission formulaire
# ============================================================

@app.route('/api/soumettre', methods=['POST'])
@login_required
def api_soumettre():
    """Soumission d'un titre par un utilisateur."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Payload JSON requis"}), 400

    artiste = data.get('artiste', '').strip()
    titre = data.get('titre', '').strip()
    genre = data.get('genre', '').strip() or None
    source = data.get('source', 'Shazam').strip()
    commentaire = data.get('commentaire', '').strip() or None
    song_id = data.get('song_id')

    # Validations
    if len(artiste) < 2 or len(titre) < 2:
        return jsonify({"error": "Artiste et titre requis (min 2 caractères)"}), 400

    # Cooldown 24h (même utilisateur, même artiste+titre)
    db = get_db()
    doublon = db.execute(
        "SELECT id FROM soumissions "
        "WHERE programmeur_id = ? AND LOWER(artiste) = LOWER(?) AND LOWER(titre) = LOWER(?) "
        "AND date_soumission > datetime('now', '-24 hours')",
        (current_user.id, artiste, titre)
    ).fetchone()

    if doublon:
        return jsonify({"status": "duplicate", "message": "Ce titre a déjà été soumis dans les dernières 24h"}), 200

    # Matching (song_id choisi par l'utilisateur ou auto)
    chosen_song_id = data.get('song_id')
    match_result = None
    if chosen_song_id:
        # Chercher le match précis par ID dans les résultats
        all_matches = match_all(artiste, titre)
        for m in all_matches:
            if m["song"].get('id') == chosen_song_id:
                match_result = m
                break
    if not match_result:
        match_result = match_5_passes(artiste, titre)
    match_json = json.dumps({"passes": [match_result["passe"]] if match_result else [],
                             "song_id": match_result["song"].get('id') if match_result else None},
                            ensure_ascii=False) if match_result else None

    video_url = data.get('video_url', '').strip() or None
    if video_url and not video_url.startswith(('http://', 'https://')):
        return jsonify({"error": "L'URL video doit commencer par http:// ou https://"}), 400

    # Insertion
    db.execute(
        "INSERT INTO soumissions (programmeur_id, artiste, titre, genre, source, commentaire, video_url, match_result) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (current_user.id, artiste, titre, genre, source, commentaire, video_url, match_json)
    )
    db.commit()

    logger.info(f"Soumission : {artiste} - {titre} (par {current_user.username})")

    response = {"status": "ok", "message": "Soumission enregistrée"}
    if match_result:
        response["match"] = {
            "song_id": match_result["song"].get('id'),
            "passe": match_result["passe"],
            "in_azuracast": 'IN_AZURACAST' in match_result["song"].get('comments', ''),
            "via_shazam": match_result["song"].get('label') == 'VIA_SHAZAM'
        }
    return jsonify(response), 200


# ============================================================
# API : Edition / suppression soumissions
# ============================================================

@app.route('/api/soumissions/<int:sid>')
@login_required
def api_get_soumission(sid):
    """Retourne une soumission pour edition."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM soumissions WHERE id = ?", (sid,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Soumission introuvable"}), 404
    if row['programmeur_id'] != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Non autoris\u00e9"}), 403
    d = dict(row)
    if d.get('match_result'):
        try:
            d['match_result'] = json.loads(d['match_result'])
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify(d)


@app.route('/api/soumissions/<int:sid>/edit', methods=['POST'])
@login_required
def api_edit_soumission(sid):
    """Modifie une soumission."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Payload JSON requis"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM soumissions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"error": "Soumission introuvable"}), 404
    if row['programmeur_id'] != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Non autoris\u00e9"}), 403
    already_synced = row['statut'] != 'pending'
    if already_synced:
        artiste = row['artiste']
        titre = row['titre']
        genre = row['genre']
        source = row['source']
    else:
        artiste = data.get('artiste', '').strip()
        titre = data.get('titre', '').strip()
        genre = data.get('genre', '').strip() or None
        source = data.get('source', 'Shazam').strip()
    commentaire = data.get('commentaire', '').strip() or None
    video_url = data.get('video_url', '').strip() or None
    if len(artiste) < 2 or len(titre) < 2:
        return jsonify({"error": "Artiste et titre requis (min 2 caracteres)"}), 400
    if video_url and not video_url.startswith(('http://', 'https://')):
        return jsonify({"error": "L'URL video doit commencer par http:// ou https://"}), 400
    match_json = row['match_result']
    if not already_synced:
        chosen_song_id = data.get('song_id')
        match_result = None
        if chosen_song_id:
            all_matches = match_all(artiste, titre)
            for m in all_matches:
                if m["song"].get('id') == chosen_song_id:
                    match_result = m
                    break
        if not match_result:
            match_result = match_5_passes(artiste, titre)
        match_json = json.dumps(
            {"passes": [match_result["passe"]] if match_result else [],
             "song_id": match_result["song"].get('id') if match_result else None},
            ensure_ascii=False
        ) if match_result else None
    db.execute(
        "UPDATE soumissions SET artiste=?, titre=?, genre=?, source=?, commentaire=?, video_url=?, match_result=? WHERE id=?",
        (artiste, titre, genre, source, commentaire, video_url, match_json, sid)
    )
    db.commit()
    logger.info(f"Soumission #{sid} modifiee par {current_user.username}")
    return jsonify({"status": "ok", "message": "Soumission modifiee"})


@app.route('/api/match', methods=['POST'])
@login_required
def api_match():
    """Matching temps réel — retourne toutes les variantes trouvées."""
    data = request.get_json()
    artiste = data.get('artiste', '').strip() if data else ''
    titre = data.get('titre', '').strip() if data else ''

    if len(artiste) < 2 or len(titre) < 2:
        return jsonify({"matches": [], "mirror_age": get_mirror_age(), "mirror_count": len(mirror_data)})

    # Debug logging
    logger.info(f"MATCH recherche : artiste='{artiste}', titre='{titre}'")
    if not mirror_data:
        logger.warning("MATCH : mirror_data est VIDE — db.json probablement introuvable")
        return jsonify({"matches": [], "mirror_age": None, "mirror_count": 0})
    else:
        logger.info(f"MATCH : mirror_data contient {len(mirror_data)} entrées")
        # Log les clés du premier élément pour vérifier la structure
        if mirror_data:
            sample_keys = list(mirror_data[0].keys())
            logger.info(f"MATCH : clés du 1er élément db.json = {sample_keys}")

    results = match_all(artiste, titre)
    logger.info(f"MATCH : {len(results)} résultat(s) trouvé(s)")
    matches = []
    for r in results:
        song = r["song"]
        # Gérer les clés possibles : 'artist' ou 'artiste', 'title' ou 'titre'
        artist_val = song.get('artist') or song.get('artiste', '')
        title_val = song.get('title') or song.get('titre', '')
        matches.append({
            "passe": r["passe"],
            "song_id": song.get('id'),
            "artist": artist_val,
            "title": title_val,
            "year": song.get('year') or song.get('annee'),
            "in_azuracast": 'IN_AZURACAST' in str(song.get('comments', '')),
            "via_shazam": song.get('label') == 'VIA_SHAZAM',
        })

    return jsonify({"matches": matches, "mirror_age": get_mirror_age(), "mirror_count": len(mirror_data)})


# ============================================================
# API : Sync Worker ↔ VPS (auth token)
# ============================================================

@app.route('/api/sync/pull')
@api_token_required
def api_sync_pull():
    """Récupère les soumissions en attente (pour le worker Ubuntu)."""
    db = get_db()
    rows = db.execute(
        "SELECT s.id, s.artiste, s.titre, s.genre, s.source, s.commentaire, s.video_url, s.date_soumission, "
        "p.display_name AS animateur "
        "FROM soumissions s LEFT JOIN programmateurs p ON s.programmeur_id = p.id "
        "WHERE s.statut = 'pending' ORDER BY s.id"
    ).fetchall()

    soumissions = [dict(r) for r in rows]
    last_id = rows[-1]['id'] if rows else 0

    # Log
    db.execute(
        "INSERT INTO sync_log (sens, type, nb_entrees) VALUES ('pull', 'soumissions', ?)",
        (len(soumissions),)
    )
    db.commit()

    return jsonify({"soumissions": soumissions, "last_id": last_id})


@app.route('/api/sync/ack', methods=['POST'])
@api_token_required
def api_sync_ack():
    """Confirme la réception des soumissions."""
    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({"status": "ok", "acknowledged": 0})

    db = get_db()
    placeholders = ','.join('?' * len(ids))
    db.execute(
        f"UPDATE soumissions SET statut = 'synced', date_sync = datetime('now') "
        f"WHERE id IN ({placeholders})",
        ids
    )
    # Log
    db.execute(
        "INSERT INTO sync_log (sens, type, nb_entrees, ids) VALUES ('ack', 'soumissions', ?, ?)",
        (len(ids), json.dumps(ids))
    )
    db.commit()

    logger.info(f"Sync ACK : {len(ids)} soumissions marquées synced")
    return jsonify({"status": "ok", "acknowledged": len(ids)})


@app.route('/api/sync/status')
@api_token_required
def api_sync_status():
    """État de la synchronisation."""
    db = get_db()
    pending = db.execute("SELECT COUNT(*) FROM soumissions WHERE statut = 'pending'").fetchone()[0]
    shazam_pending = db.execute("SELECT COUNT(*) FROM shazam_external WHERE statut = 'pending'").fetchone()[0]
    last_pull = db.execute(
        "SELECT date_sync FROM sync_log WHERE sens = 'pull' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    return jsonify({
        "pending_soumissions": pending,
        "pending_shazam_external": shazam_pending,
        "last_pull": last_pull['date_sync'] if last_pull else None,
        "mirror_age": get_mirror_age()
    })

# ============================================================
# API : Shazam externe (public, sans auth)
# ============================================================

@app.route('/api/soumissions/<int:sid>/delete', methods=['POST'])
@login_required
def api_delete_soumission(sid):
    """Supprime une soumission (propriétaire ou admin)."""
    db = get_db()
    row = db.execute("SELECT programmeur_id FROM soumissions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"error": "Soumission introuvable"}), 404
    if row['programmeur_id'] != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Non autoris\u00e9"}), 403
    db.execute("DELETE FROM soumissions WHERE id = ?", (sid,))
    db.commit()
    logger.info(f"Soumission #{sid} supprim\u00e9e par {current_user.username}")
    return jsonify({"status": "ok"})


# ============================================================
# API : Admin Shazam externe (liste + purge)
# ============================================================

@app.route('/api/admin/shazam_external')
@admin_required
def api_admin_shazam_external_list():
    """Retourne les dernières entrées shazam_external (max 100)."""
    db = get_db()
    rows = db.execute(
        "SELECT id, artiste, titre, date_shazam, statut, date_sync "
        "FROM shazam_external ORDER BY id DESC LIMIT 100"
    ).fetchall()
    return jsonify({"entries": [dict(r) for r in rows]})


@app.route('/api/admin/shazam_external/purge', methods=['POST'])
@admin_required
def api_admin_shazam_external_purge():
    """Purge les entrées shazam_external syncées depuis plus de N jours (défaut 7)."""
    data = request.get_json() or {}
    days = data.get('days', 7)
    try:
        days = int(days)
    except (ValueError, TypeError):
        days = 7
    days = max(1, min(days, 90))

    db = get_db()
    cursor = db.execute(
        "DELETE FROM shazam_external "
        "WHERE statut = 'synced' AND date_sync < datetime('now', ?)",
        (f'-{days} days',)
    )
    deleted = cursor.rowcount
    db.commit()

    logger.info(f"Purge shazam_external : {deleted} entrées supprimées (>{days}j)")
    return jsonify({"status": "ok", "deleted": deleted})


# ============================================================
# API : Shazam externe (public, sans auth)
# ============================================================

@app.route('/api/shazam/add', methods=['POST'])
@rate_limit(SHAZAM_RATE_LIMIT, SHAZAM_RATE_WINDOW)
def api_shazam_add():
    """
    Endpoint public pour MacroDroid (sans authentification).
    Même comportement que /api/shazam/add du dashboard Windows.
    """
    data = request.get_json(silent=True) or dict(request.form)

    artiste = nettoyer_artiste_shazam(data.get('artiste', ''))
    titre = data.get('titre', '').strip()
    animateur = (data.get('animateur') or request.args.get('animateur') or '').strip() or None

    if not artiste or not titre:
        logger.info(f"Shazam externe REJETE : artiste='{artiste}' titre='{titre}' data={data}")
        return jsonify({"status": "error", "message": "artiste et titre requis"}), 400

    # Cooldown 24h
    db = get_db()
    doublon = db.execute(
        "SELECT id FROM shazam_external "
        "WHERE LOWER(artiste) = LOWER(?) AND LOWER(titre) = LOWER(?) "
        "AND date_shazam > datetime('now', '-24 hours')",
        (artiste, titre)
    ).fetchone()

    if doublon:
        return jsonify({"status": "duplicate", "message": "Déjà enregistré dans les 24h"}), 200

    # Insertion
    db.execute(
        "INSERT INTO shazam_external (artiste, titre, animateur) VALUES (?, ?, ?)",
        (artiste, titre, animateur)
    )
    db.commit()

    logger.info(f"Shazam externe : {artiste} - {titre} (animateur: {animateur or 'N/A'}) [content-type={request.content_type}] keys={list(data.keys())}")
    return jsonify({"status": "ok"}), 200


# ============================================================
# API : Sync Shazam externe (auth token)
# ============================================================

@app.route('/api/shazam/external/pull')
@api_token_required
def api_shazam_external_pull():
    """Récupère les entrées shazam_external en attente."""
    db = get_db()
    rows = db.execute(
        "SELECT id, artiste, titre, animateur, date_shazam "
        "FROM shazam_external WHERE statut = 'pending' ORDER BY id"
    ).fetchall()

    entries = [dict(r) for r in rows]

    # Log
    db.execute(
        "INSERT INTO sync_log (sens, type, nb_entrees) VALUES ('pull', 'shazam_external', ?)",
        (len(entries),)
    )
    db.commit()

    return jsonify({"entries": entries})


@app.route('/api/shazam/external/ack', methods=['POST'])
@api_token_required
def api_shazam_external_ack():
    """Confirme la sync des entrées shazam_external."""
    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({"status": "ok", "acknowledged": 0})

    db = get_db()
    placeholders = ','.join('?' * len(ids))
    db.execute(
        f"UPDATE shazam_external SET statut = 'synced', date_sync = datetime('now') "
        f"WHERE id IN ({placeholders})",
        ids
    )
    # Log
    db.execute(
        "INSERT INTO sync_log (sens, type, nb_entrees, ids) VALUES ('ack', 'shazam_external', ?, ?)",
        (len(ids), json.dumps(ids))
    )
    db.commit()

    logger.info(f"Shazam external ACK : {len(ids)} entrées marquées synced")
    return jsonify({"status": "ok", "acknowledged": len(ids)})

# ============================================================
# Templates HTML (inline — fichiers séparés possibles en production)
# ============================================================

# --- Login ---
LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIRVS — Connexion</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh}
  .box{background:#1e293b;border-radius:12px;padding:2rem;width:100%;max-width:360px;box-shadow:0 25px 50px rgba(0,0,0,.4)}
  h1{text-align:center;font-size:1.3rem;margin-bottom:.5rem;color:#38bdf8}
  .sub{text-align:center;font-size:.8rem;color:#94a3b8;margin-bottom:1.5rem}
  label{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:.3rem}
  input[type=text],input[type=password]{width:100%;padding:.6rem;border:1px solid #334155;border-radius:6px;background:#0f172a;color:#e2e8f0;font-size:.9rem;margin-bottom:1rem}
  input:focus{outline:none;border-color:#38bdf8}
  .error{color:#f87171;font-size:.8rem;margin-bottom:.8rem;text-align:center}
  button{width:100%;padding:.7rem;background:#0ea5e9;color:#fff;border:none;border-radius:6px;font-size:.9rem;cursor:pointer}
  button:hover{background:#0284c7}
</style>
</head>
<body>
<div class="box">
  <h1>AIRVS Programmes</h1>
  <p class="sub">Espace programmateurs</p>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="POST">
    <label for="username">Identifiant</label>
    <input type="text" name="username" id="username" required autofocus>
    <label for="password">Mot de passe</label>
    <input type="password" name="password" id="password" required>
    <button type="submit">Se connecter</button>
  </form>
</div>
</body>
</html>'''

# --- Formulaire de soumission ---
FORMULAIRE_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIRVS — Soumission</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:1rem}
  .header{display:flex;justify-content:space-between;align-items:center;max-width:700px;margin:0 auto 1.5rem;padding:.8rem 0;border-bottom:1px solid #1e293b}
  .header h1{font-size:1.2rem;color:#38bdf8}
  .header a{color:#94a3b8;font-size:.8rem;text-decoration:none}
  .header a:hover{color:#38bdf8}
  .form-box{max-width:700px;margin:0 auto;background:#1e293b;border-radius:12px;padding:1.5rem}
  .mirror-info{font-size:.75rem;color:#64748b;margin-bottom:1rem;text-align:right}
  .row{display:flex;gap:1rem;margin-bottom:1rem}
  .field{flex:1}
  label{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:.3rem}
  input,select,textarea{width:100%;padding:.55rem;border:1px solid #334155;border-radius:6px;background:#0f172a;color:#e2e8f0;font-size:.85rem}
  input:focus,select:focus,textarea:focus{outline:none;border-color:#38bdf8}
  textarea{height:60px;resize:vertical}
  .match-result{margin:1rem 0;padding:.8rem;border-radius:8px;font-size:.85rem;display:none}
  .match-ok{background:#064e3b;border:1px solid #10b981;color:#6ee7b7}
  .match-ko{background:#1e293b;border:1px solid #334155;color:#94a3b8}
  .match-choices{margin-top:.6rem;max-height:220px;overflow-y:auto}
  .match-choice{display:flex;align-items:center;gap:.6rem;padding:.45rem .6rem;border-radius:6px;cursor:pointer;border:1px solid transparent;transition:background .15s,border-color .15s}
  .match-choice:hover{background:rgba(255,255,255,.07)}
  .match-choice.active{background:rgba(16,185,129,.15);border-color:#10b981}
  .match-choice input{display:none}
  .match-choice .mc-radio{width:14px;height:14px;border-radius:50%;border:2px solid #64748b;flex-shrink:0;display:flex;align-items:center;justify-content:center;transition:border-color .15s}
  .match-choice.active .mc-radio{border-color:#10b981}
  .match-choice.active .mc-radio::after{content:'';width:6px;height:6px;border-radius:50%;background:#10b981}
  .match-choice .mc-info{flex:1;min-width:0}
  .match-choice .mc-artist{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .match-choice .mc-title{color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .match-choice .mc-badges{display:flex;gap:.4rem;flex-shrink:0;font-size:.7rem}
  .mc-badge{padding:.1rem .4rem;border-radius:4px;font-weight:600}
  .mc-badge-azura{background:rgba(52,211,153,.2);color:#34d399}
  .mc-badge-shazam{background:rgba(251,191,36,.2);color:#fbbf24}
  .mc-badge-passe{background:rgba(56,189,248,.15);color:#38bdf8}
  .match-single{padding:.3rem 0}
  button.submit{padding:.7rem 2rem;background:#0ea5e9;color:#fff;border:none;border-radius:6px;font-size:.9rem;cursor:pointer}
  button.submit:hover{background:#0284c7}
  .toast{position:fixed;top:1rem;right:1rem;padding:.8rem 1.2rem;border-radius:8px;font-size:.85rem;opacity:0;transition:opacity .3s}
  .toast.show{opacity:1}
  .toast-ok{background:#064e3b;color:#6ee7b7;border:1px solid #10b981}
  .toast-err{background:#450a0a;color:#fca5a5;border:1px solid #ef4444}
  .edit-banner{background:#1e3a5f;border:1px solid #38bdf8;border-radius:8px;padding:.6rem 1rem;margin-bottom:1rem;font-size:.85rem;color:#38bdf8;display:none;align-items:center;gap:.5rem}
  .edit-banner .spinner{width:14px;height:14px;border:2px solid #1e293b;border-top-color:#38bdf8;border-radius:50%;animation:spin .6s linear infinite}
  .btn-cancel-edit{background:none;border:1px solid #64748b;color:#94a3b8;padding:.45rem 1rem;border-radius:8px;cursor:pointer;font-size:.85rem;margin-right:.5rem}
  .btn-cancel-edit:hover{background:#334155;color:#e2e8f0}
  .video-preview{margin-top:.5rem;border-radius:8px;overflow:hidden;max-width:480px}
  .video-preview iframe{width:100%;aspect-ratio:16/9;border:none;border-radius:8px}
  .video-thumb{display:flex;align-items:center;gap:.8rem;padding:.5rem;background:#1e293b;border-radius:8px;cursor:pointer;max-width:480px}
  .video-thumb:hover{background:#334155}
  .vt-info{display:flex;flex-direction:column;gap:.15rem}
  .vt-play{color:#38bdf8;font-size:.8rem}
  .vt-url{color:#64748b;font-size:.7rem;word-break:break-all;max-width:320px}
</style>
</head>
<body>
<div class="header">
  <h1>AIRVS Programmes</h1>
  <span>Bonjour, {{ user.display_name }} &middot; <a href="/historique">Historique</a>{% if user.is_admin %} &middot; <a href="/admin">Admin</a>{% endif %} &middot; <a href="/logout">D&eacute;connexion</a></span>
</div>
<div class="form-box">
  {% if edit_data %}<div class="edit-banner" id="editBanner" style="display:flex">{% if edit_locked %}<span style="color:#fbbf24;margin-right:.3rem">&#x1f512;</span><span>{{ "Modification restreinte (vid\u00e9o/commentaire) \u2014 #" ~ edit_id ~ " [" ~ edit_data.statut ~ "]" }}</span>{% else %}<span style="color:#38bdf8;margin-right:.3rem">&#x270e;</span><span>{{ "Modification de la soumission #" ~ edit_id ~ " [" ~ edit_data.statut ~ "]" }}</span>{% endif %}</div>{% else %}<div class="edit-banner" id="editBanner"><div class="spinner"></div><span id="editBannerText">Chargement...</span></div>{% endif %}
  <div class="mirror-info" id="mirrorInfo">{% if mirror_age is not none %}Base locale : {{ mirror_count if mirror_count is defined else '?' }} titres (mise \u00e0 jour il y a {{ mirror_age }} min){% else %}Base locale non disponible{% endif %}</div>
  <div class="row">
    <div class="field"><label for="artiste">Artiste *</label><input type="text" id="artiste" placeholder="Ex: Daft Punk" required value="{{ edit_data.artiste or "" }}" {{ "disabled" if edit_locked }}></div>
    <div class="field"><label for="titre">Titre *</label><input type="text" id="titre" placeholder="Ex: Get Lucky" required value="{{ edit_data.titre or "" }}" {{ "disabled" if edit_locked }}></div>
  </div>
  <div class="row">
    <div class="field"><label for="genre">Genre</label>
      <select id="genre" {{ "disabled" if edit_locked }}><option value="">--</option><option{{ " selected" if edit_data and edit_data.genre=="Pop" }}>Pop</option><option{{ " selected" if edit_data and edit_data.genre=="Rock" }}>Rock</option><option{{ " selected" if edit_data and edit_data.genre=="Jazz" }}>Jazz</option><option{{ " selected" if edit_data and edit_data.genre=="Blues" }}>Blues</option><option{{ " selected" if edit_data and edit_data.genre=="Soul / R&amp;B" }}>Soul / R&amp;B</option><option{{ " selected" if edit_data and edit_data.genre=="Funk" }}>Funk</option><option{{ " selected" if edit_data and edit_data.genre=="Electronic" }}>Electronic</option><option{{ " selected" if edit_data and edit_data.genre=="Hip-Hop" }}>Hip-Hop</option><option{{ " selected" if edit_data and edit_data.genre=="Reggae" }}>Reggae</option><option{{ " selected" if edit_data and edit_data.genre=="World" }}>World</option><option{{ " selected" if edit_data and edit_data.genre=="Classique" }}>Classique</option><option{{ " selected" if edit_data and edit_data.genre=="Chanson francaise" }}>Chanson francaise</option><option{{ " selected" if edit_data and edit_data.genre=="Autre" }}>Autre</option></select>
    </div>
    <div class="field"><label for="source">Source</label>
      <select id="source" {{ "disabled" if edit_locked }}><option{{ " selected" if edit_data and edit_data.source=="Shazam" }}>Shazam</option><option{{ " selected" if edit_data and edit_data.source=="Emission" }}>Emission</option><option{{ " selected" if edit_data and edit_data.source=="Chronique" }}>Chronique</option><option{{ " selected" if edit_data and edit_data.source=="Titre Discoth\u00e8que" }}>Titre Discoth\u00e8que</option><option{{ " selected" if edit_data and edit_data.source=="Recommandation" }}>Recommandation</option><option{{ " selected" if edit_data and edit_data.source=="Autre" }}>Autre</option></select>
    </div>
  </div>
  <div class="field"><label for="commentaire">Commentaire</label><textarea id="commentaire" placeholder="Optionnel">{{ edit_data.commentaire or "" }}</textarea></div>
  <div class="field"><label for="videoUrl">Vid\u00e9o (YouTube, Vimeo...)</label><input type="url" id="videoUrl" placeholder="https://www.youtube.com/watch?v=..." value="{{ edit_data.video_url or "" }}" ></div>
  <input type="hidden" id="editMode" value="{{ edit_id or "" }}">
  <div class="video-preview" id="videoPreview" style="display:none"></div>
  <div class="match-result" id="matchResult"></div>
  <div style="margin-top:1rem;text-align:right">
    {% if edit_data %}<a href="/" class="btn-cancel-edit" id="btnCancelEdit" style="display:inline-block;text-decoration:none">Annuler</a>
    <button class="submit" type="button" id="btnSubmit">{{ "Mettre \u00e0 jour" if not edit_locked else "Enregistrer (vid\u00e9o/commentaire)" }}</button>{% else %}<button class="submit" type="button" id="btnSubmit">Enregistrer</button>{% endif %}
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const editId=new URLSearchParams(location.search).get('edit');
function toast(msg,ok){const t=$('#toast');t.textContent=msg;t.className='toast show '+(ok?'toast-ok':'toast-err');setTimeout(()=>t.classList.remove('show'),3500)}
let matchArtist=null,matchTitle=null,selectedSongId=null,matchesData=[];

function badges(m){
  let b='';
  if(m.in_azuracast)b+='<span class="mc-badge mc-badge-azura">AzuraCast</span>';
  if(m.via_shazam)b+='<span class="mc-badge mc-badge-shazam">Shazam</span>';
  b+='<span class="mc-badge mc-badge-passe">P'+m.passe+'</span>';
  return b}

function choiceHTML(m,i,active){
  return '<div class="match-choice'+(active?' active':'')+"'"+' data-idx="'+i+'" onclick="selectChoice('+i+',true)"><input type="radio" name="mc" '+(active?'checked':'')+'><div class="mc-radio"></div><div class="mc-info"><div class="mc-artist">'+m.artist+'</div><div class="mc-title">'+m.title+(m.year?' ('+m.year+')':'')+'</div></div><div class="mc-badges">'+badges(m)+'</div></div>'}

function selectChoice(idx,fillField){
  const m=matchesData[idx];if(!m)return;
  selectedSongId=m.song_id;matchArtist=m.artist;matchTitle=m.title;
  if(fillField)$('#titre').value=m.title;
  document.querySelectorAll('.match-choice').forEach((el,i)=>{el.classList.toggle('active',i===idx);el.querySelector('input').checked=i===idx})}

async function doMatch(){
  const a=$('#artiste').value.trim(),t=$('#titre').value.trim(),r=$('#matchResult');
  if(a.length<2||t.length<2){r.style.display='none';return}
  try{const res=await fetch('/api/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({artiste:a,titre:t})});
  const data=await res.json();
  if(data.mirror_age!==null)$('#mirrorInfo').textContent='Base locale : '+(data.mirror_count||0)+' titres (mise \u00e0 jour il y a '+data.mirror_age+' min)';
  else $('#mirrorInfo').textContent='Base locale non disponible';
  const ms=data.matches||[];
  if(ms.length>0){
    matchesData=ms;
    if(!selectedSongId||!ms.find(m=>m.song_id===selectedSongId)){selectChoice(0,false)}
    const header=ms.length>1?'<strong>'+ms.length+' versions trouv\u00e9es en studio</strong> \u2014 choisissez la bonne :':'<strong>MP3 probablement disponible en studio</strong>';
    let html='<div class="'+(ms.length>1?'':'match-single')+'">'+header+'</div>';
    if(ms.length>1){
      html+='<div class="match-choices">';
      ms.forEach((m,i)=>{html+=choiceHTML(m,i,i===0)});
      html+='</div>'}
    r.className='match-result match-ok';r.style.display='block';r.innerHTML=html
  }else{selectedSongId=null;matchArtist=null;matchTitle=null;r.className='match-result match-ko';r.style.display='block';r.innerHTML='Nouveau titre &mdash; en attente de v\u00e9rification studio'}}
  catch(e){console.error(e)}}
function previewVideo(url){
  const vp=$('#videoPreview');if(!vp)return;
  if(!url||!url.match(/^https?:\/\//)){vp.style.display='none';return}
  let embed='';
  const yt=url.match(/(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([\w-]{11})/);
  if(yt){embed='<iframe src="https://www.youtube.com/embed/'+yt[1]+'" allowfullscreen></iframe>'}
  else{
    const vm=url.match(/vimeo\.com\/(\d+)/);
    if(vm)embed='<iframe src="https://player.vimeo.com/video/'+vm[1]+'" allowfullscreen></iframe>'}
  if(embed){vp.innerHTML=embed;vp.style.display='block'}
  else{var a=document.createElement('a');a.href=url;a.target='_blank';a.className='video-thumb';a.innerHTML='<div style="width:120px;height:68px;background:#1e293b;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#38bdf8;font-size:1.5rem">▶</div><div class="vt-info"><div class="vt-play">Ouvrir la vidéo</div><div class="vt-url">'+url.replace(/</g,'&lt;')+'</div></div>';vp.innerHTML='';vp.appendChild(a);vp.style.display='block'}
}

async function loadForEdit(id){
  const banner=$('#editBanner');
  const bannerText=$('#editBannerText');
  banner.style.display='flex';
  if(bannerText)bannerText.textContent='Chargement de la soumission #'+id+'...';
  try{
    const res=await fetch('/api/soumissions/'+id);
    if(!res.ok){
      const err=await res.json().catch(()=>({}));
      if(bannerText)bannerText.textContent='Erreur : '+(err.error||'soumission introuvable');
      banner.style.background='#7f1d1d';banner.style.borderColor='#f87171';
      $('#editMode').value='';
      return}
    const d=await res.json();
    if(d.artiste)$('#artiste').value=d.artiste;
    if(d.titre)$('#titre').value=d.titre;
    if(d.genre){const sel=$('#genre');for(let i=0;i<sel.options.length;i++){if(sel.options[i].text===d.genre){sel.selectedIndex=i;break}}}
    if(d.source){const sel=$('#source');for(let i=0;i<sel.options.length;i++){if(sel.options[i].text===d.source){sel.selectedIndex=i;break}}}
    if(d.commentaire)$('#commentaire').value=d.commentaire;
    if(d.video_url){$('#videoUrl').value=d.video_url;previewVideo(d.video_url)}
    const locked=d.statut&&d.statut!=='pending';
    if(locked){
      $('#artiste').disabled=true;$('#titre').disabled=true;$('#genre').disabled=true;$('#source').disabled=true;
      if(bannerText)bannerText.textContent='LOCK Modification restreinte (vid\u00e9o/commentaire) \u2014 #'+id+' ['+d.statut+']';
      $('#btnSubmit').textContent='Enregistrer (vid\u00e9o/commentaire)'
    }else{
      if(bannerText)bannerText.textContent='EDIT Modification de la soumission #'+id+' ['+d.statut+']';
      $('#btnSubmit').textContent='Mettre \u00e0 jour'
    }
    let cancelBtn=$('#btnCancelEdit');
    if(!cancelBtn){cancelBtn=document.createElement('a');cancelBtn.href='/';cancelBtn.id='btnCancelEdit';cancelBtn.className='btn-cancel-edit';cancelBtn.style.display='inline-block';cancelBtn.style.textDecoration='none';cancelBtn.textContent='Annuler';$('#btnSubmit').parentNode.insertBefore(cancelBtn,$('#btnSubmit'))}
    $('#editMode').value=id;
    if(d.match_result&&d.match_result.song_id){selectedSongId=d.match_result.song_id}
    doMatch()
  }catch(e){
    console.error('loadForEdit error:',e);
    if(bannerText)bannerText.textContent='Erreur r\u00e9seau';
    banner.style.background='#7f1d1d';banner.style.borderColor='#f87171'
  }
}

function cancelEdit(){window.location.href='/'}

// Submit handler (creation ou edition) — défini AVANT le bloc init pour être toujours disponible
document.getElementById('btnSubmit').addEventListener('click',async function(){
  const a=$('#artiste').value.trim(),t=$('#titre').value.trim();
  if(a.length<2||t.length<2){toast('Artiste et titre requis (min 2 car.)',false);return}
  const artisteFinal=matchArtist||a;const titreFinal=matchTitle||t;
  const payload={artiste:artisteFinal,titre:titreFinal,genre:$('#genre').value,source:$('#source').value,commentaire:$('#commentaire').value,video_url:$('#videoUrl').value,song_id:selectedSongId};
  try{
    let url='/api/soumettre';
    if($('#editMode').value){url='/api/soumissions/'+$('#editMode').value+'/edit'}
    const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await res.json();
    const corrige=artisteFinal!==a||titreFinal!==t;
    if(data.status==='ok'){
      if($('#editMode').value){
        toast('Soumission mise à jour'+(corrige?' (corrigée)':''),true);
        setTimeout(()=>window.location.href='/historique',800)
      }else{
        matchArtist=null;matchTitle=null;selectedSongId=null;matchesData=[];$('#artiste').value='';$('#titre').value='';$('#genre').value='';$('#commentaire').value='';$('#videoUrl').value='';$('#matchResult').style.display='none';$('#videoPreview').style.display='none';toast('Soumission enregistrée'+(corrige?' (corrigée)':''),true)
      }
    }
    else if(data.status==='duplicate')toast(data.message,false);
    else toast(data.error||'Erreur',false);
  }catch(e){toast('Erreur réseau',false)}});
</script>
<script>
// Event listeners + Init (bloc séparé : une erreur ici n'empêche pas le submit handler ci-dessus)
try{
let debounce;$('#videoUrl').addEventListener('input',function(){previewVideo(this.value)});
$('#artiste').addEventListener('input',()=>{matchArtist=null;selectedSongId=null;clearTimeout(debounce);debounce=setTimeout(doMatch,400)});
$('#titre').addEventListener('input',()=>{matchTitle=null;selectedSongId=null;clearTimeout(debounce);debounce=setTimeout(doMatch,400)});
// Init{% if edit_data and not edit_locked %}
selectedSongId={{ edit_data.match_result.song_id if edit_data and edit_data.match_result and edit_data.match_result.get('song_id') else 'null' }};
doMatch();{% elif edit_data and edit_locked %}
doMatch();{% if edit_data.video_url %}previewVideo({{ edit_data.video_url | tojson }});{% endif %}{% else %}
if(editId){loadForEdit(editId)}else{doMatch()}{% endif %}
}catch(e){console.error('Init error:',e)}
</script>
</body>
</html>'''

# --- Historique ---
HISTORIQUE_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIRVS — Historique</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:1rem}
  .header{display:flex;justify-content:space-between;align-items:center;max-width:900px;margin:0 auto 1.5rem;padding:.8rem 0;border-bottom:1px solid #1e293b}
  .header h1{font-size:1.2rem;color:#38bdf8}
  .header a{color:#94a3b8;font-size:.8rem;text-decoration:none}
  .header a:hover{color:#38bdf8}
  table{width:100%;max-width:900px;margin:0 auto;border-collapse:collapse;font-size:.82rem}
  th{background:#1e293b;color:#94a3b8;text-align:left;padding:.5rem .7rem;position:sticky;top:0}
  td{padding:.5rem .7rem;border-bottom:1px solid #1e293b}
  tr:hover{background:#1e293b}
  .badge{padding:.15rem .5rem;border-radius:10px;font-size:.7rem;font-weight:600}
  .badge-pending{background:#1e3a5f;color:#38bdf8}
  .badge-synced{background:#064e3b;color:#10b981}
  .badge-processed{background:#1e293b;color:#94a3b8}
  .btn-del{background:none;border:1px solid #7f1d1d;color:#fca5a5;padding:.2rem .5rem;border-radius:4px;cursor:pointer;font-size:.75rem}
  .btn-del:hover{background:#7f1d1d;color:#fff}
  .btn-del:disabled{opacity:.3;cursor:not-allowed}
  .btn-edit{background:none;border:1px solid #1e3a5f;color:#38bdf8;padding:.2rem .5rem;border-radius:4px;cursor:pointer;font-size:.75rem}
  .btn-edit:hover{background:#1e3a5f;color:#e2e8f0}
  .btn-edit:disabled{opacity:.3;cursor:not-allowed}
  .vid-link{color:#fbbf24;text-decoration:none;font-size:.75rem}
  .vid-link:hover{text-decoration:underline}
  .nav{max-width:900px;margin:1rem auto;text-align:center}
  .nav a{color:#38bdf8;text-decoration:none;padding:.3rem .7rem}
  .nav a:hover{text-decoration:underline}
  .nav .current{color:#e2e8f0;font-weight:600}

</style>
</head>
<body>
<div class="header">
  <h1>Historique des soumissions</h1>
  <span><a href="/">Nouvelle soumission</a>{% if user.is_admin %} &middot; <a href="/admin">Admin</a>{% endif %} &middot; <a href="/logout">D&eacute;connexion</a></span>
</div>
<div style="max-width:900px;margin:0 auto 1rem">
  <span style="font-size:.8rem;color:#64748b">{{ total }} soumission(s)</span>
</div>
{% if soumissions %}
<table>
<thead><tr><th>Date</th><th>Artiste</th><th>Titre</th><th>Genre</th><th>Source</th><th>Video</th><th>Statut</th><th></th></tr></thead>
<tbody>
{% for s in soumissions %}
<tr id="row-{{ s['id'] }}">
  <td>{{ s['date_soumission'][:16] }}</td>
  <td>{{ s['artiste'] }}</td>
  <td>{{ s['titre'] }}</td>
  <td>{{ s['genre'] or '-' }}</td>
  <td>{{ s['source'] }}</td>
  <td>{% if s['video_url'] %}<a class="vid-link" href="{{ s['video_url'] }}" target="_blank" rel="noopener" title="{{ s['video_url'] }}">&#x25b6;</a>{% else %}-{% endif %}</td>
  <td><span class="badge badge-{{ s['statut'] }}">{{ s['statut'] }}</span></td>
  <td><button class="btn-edit" onclick="editSoumission({{ s['id'] }})" title="Modifier">&#x270e;</button> <button class="btn-del" onclick="delSoumission({{ s['id'] }}, this)" title="Supprimer">&times;</button></td>
</tr>
{% endfor %}
</tbody>
</table>
{% else %}
<p style="text-align:center;color:#64748b;padding:2rem">Aucune soumission</p>
{% endif %}
<div class="nav">
  {% for p in range(1, total_pages + 1) %}
    {% if p == page %}<span class="current">{{ p }}</span>
    {% else %}<a href="?page={{ p }}">{{ p }}</a>{% endif %}
  {% endfor %}
</div>
<script>
function editSoumission(id){window.location.href='/?edit='+id}
async function delSoumission(id,btn){
  if(!confirm('Supprimer cette soumission ?')) return;
  btn.disabled=true;
  try{const r=await fetch('/api/soumissions/'+id+'/delete',{method:'POST'});
  const d=await r.json();
  if(d.status==='ok'){const row=document.getElementById('row-'+id);if(row)row.remove()}
  else{alert(d.error||'Erreur');btn.disabled=false}}
  catch(e){alert('Erreur r\u00e9seau');btn.disabled=false}}
</script>
</body>
</html>'''

# --- Administration ---
ADMIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIRVS — Administration</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:1rem}
  .header{display:flex;justify-content:space-between;align-items:center;max-width:960px;margin:0 auto 1.5rem;padding:.8rem 0;border-bottom:1px solid #1e293b}
  .header h1{font-size:1.2rem;color:#38bdf8}
  .header a{color:#94a3b8;font-size:.8rem;text-decoration:none}
  .stats{display:flex;gap:1rem;max-width:960px;margin:0 auto 1.5rem}
  .stat-card{flex:1;background:#1e293b;border-radius:10px;padding:1rem;text-align:center}
  .stat-card .num{font-size:1.8rem;font-weight:700;color:#38bdf8}
  .stat-card .label{font-size:.75rem;color:#94a3b8;margin-top:.3rem}
  .section{max-width:960px;margin:0 auto 2rem}
  .section h2{font-size:1rem;color:#94a3b8;margin-bottom:.8rem;padding-bottom:.4rem;border-bottom:1px solid #1e293b}
  .form-row{display:flex;gap:.7rem;align-items:end;flex-wrap:wrap}
  .form-row .field{display:flex;flex-direction:column;gap:.3rem}
  .form-row label{font-size:.75rem;color:#94a3b8}
  .form-row input,.form-row select{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:.45rem .6rem;font-size:.82rem;width:100%}
  .form-row input{width:160px}
  .form-row input.pw{width:120px}
  .btn{padding:.5rem 1.2rem;border:none;border-radius:6px;font-size:.82rem;cursor:pointer;font-weight:600}
  .btn-create{background:#0ea5e9;color:#fff}
  .btn-create:hover{background:#0284c7}
  table{width:100%;border-collapse:collapse;font-size:.82rem}
  th{background:#1e293b;color:#94a3b8;text-align:left;padding:.5rem .7rem}
  td{padding:.5rem .7rem;border-bottom:1px solid #1e293b}
  tr:hover{background:#1e293b}
  .badge{padding:.15rem .5rem;border-radius:10px;font-size:.7rem}
  .badge-admin{background:#1c1917;color:#fbbf24}
  .badge-animateur{background:#0c4a6e;color:#38bdf8}
  .badge-actif{background:#064e3b;color:#10b981}
  .badge-inactif{background:#1c1917;color:#78716c}
  .btn-sm{padding:.2rem .5rem;border:1px solid #334155;border-radius:4px;background:none;color:#94a3b8;font-size:.72rem;cursor:pointer;margin-left:.3rem}
  .btn-sm:hover{background:#1e293b;color:#e2e8f0}
  .btn-sm.btn-del{border-color:#7f1d1d;color:#fca5a5}
  .btn-sm.btn-del:hover{background:#450a0a}
  .form-msg{font-size:.8rem;margin-top:.5rem;min-height:1.2em}
  .form-msg.ok{color:#10b981}
  .form-msg.err{color:#fca5a5}
  .toast{position:fixed;top:1rem;right:1rem;padding:.8rem 1.2rem;border-radius:8px;font-size:.85rem;opacity:0;transition:opacity .3s}
  .toast.show{opacity:1}
  .toast-ok{background:#064e3b;color:#6ee7b7;border:1px solid #10b981}
  .toast-err{background:#450a0a;color:#fca5a5;border:1px solid #ef4444}
</style>
</head>
<body>
<div class="header">
  <h1>Administration</h1>
  <span><a href="/">Soumissions</a> &middot; <a href="/historique">Historique</a> &middot; <a href="/logout">D&eacute;connexion</a></span>
</div>
<div class="stats">
  <div class="stat-card"><div class="num">{{ pending_count }}</div><div class="label">Soumissions en attente</div></div>
  <div class="stat-card"><div class="num" id="stat-shazam-pending">{{ shazam_pending }}</div><div class="label">Shazam ext. en attente</div></div>
  <div class="stat-card"><div class="num">{{ shazam_total }}</div><div class="label">Shazam ext. total</div></div>
  <div class="stat-card"><div class="num">{{ utilisateurs|length }}</div><div class="label">Utilisateurs</div></div>
</div>
<div class="section">
  <h2>Shazam ext&eacute;rieur <span style="font-weight:400;font-size:.75rem;color:#64748b">(MacroDroid &rarr; VPS &rarr; Worker)</span></h2>
  <div style="display:flex;gap:.7rem;align-items:center;margin-bottom:.8rem">
    <button class="btn" style="background:#334155;color:#e2e8f0" onclick="loadShazamExt()">&#8635; Actualiser</button>
    <button class="btn btn-del" style="background:#7f1d1d;color:#fca5a5;border:none;padding:.5rem 1.2rem;border-radius:6px;font-size:.82rem;cursor:pointer;font-weight:600" onclick="purgeShazamExt()">Purger sync&eacute;s (&gt;7j)</button>
    <span style="font-size:.75rem;color:#64748b" id="shazam-ext-info"></span>
  </div>
  <table id="shazam-ext-table">
  <thead><tr><th>ID</th><th>Artiste</th><th>Titre</th><th>Date Shazam</th><th>Statut</th><th>Date Sync</th></tr></thead>
  <tbody id="shazam-ext-body">
    <tr><td colspan="6" style="text-align:center;color:#64748b;padding:1.5rem">Chargement...</td></tr>
  </tbody>
  </table>
</div>
<div class="section">
  <h2>Nouvel utilisateur</h2>
  <div class="form-row">
    <div class="field"><label>Identifiant</label><input type="text" id="nu-username" placeholder="ex: jp" autocomplete="off"></div>
    <div class="field"><label>Nom affich&eacute;</label><input type="text" id="nu-display" placeholder="ex: Jean-Pierre"></div>
    <div class="field"><label>Mot de passe</label><input type="password" id="nu-password" class="pw" placeholder="&bull;&bull;&bull;&bull;"></div>
    <div class="field"><label>R&ocirc;le</label>
      <select id="nu-role"><option value="admin">Admin</option><option value="animateur">Animateur</option></select>
    </div>
    <button class="btn btn-create" id="btnCreateUser">Cr&eacute;er</button>
  </div>
  <div class="form-msg" id="nu-msg"></div>
</div>
<div class="section">
  <h2>Utilisateurs existants</h2>
  <table>
  <thead><tr><th>Identifiant</th><th>Nom</th><th>R&ocirc;le</th><th>Soumissions</th><th>Derni&egrave;re connexion</th><th>Statut</th><th>Actions</th></tr></thead>
  <tbody>
{% for p in utilisateurs %}
<tr id="row-{{ p['id'] }}">
  <td>{{ p['username'] }}</td>
  <td>{{ p['display_name'] }}</td>
  <td><span class="badge badge-{{ p['role'] }}">{{ p['role'] }}</span></td>
  <td>{{ p['nb_soumissions'] }}</td>
  <td>{{ p['date_derniere_connexion'] or '-' }}</td>
  <td><span class="badge badge-{{ 'actif' if p['actif'] else 'inactif' }}" id="status-{{ p['id'] }}">{{ 'Actif' if p['actif'] else 'Inactif' }}</span></td>
  <td>
    <button class="btn-sm" onclick='toggleUser({{ p['id'] }},this)' {{ 'disabled' if p['id'] == user.id else '' }}>{{ 'D&eacute;sactiver' if p['actif'] else 'Activer' }}</button>
    <button class="btn-sm btn-del" onclick='deleteUser({{ p['id'] }},{{ p['display_name']|tojson }})' {{ 'disabled' if p['id'] == user.id else '' }}>Supprimer</button>
  </td>
</tr>
{% endfor %}
  </tbody>
  </table>
</div>
<div id="toast" class="toast"></div>
<script>
const $=s=>document.querySelector(s);
function toast(msg,ok){const t=$('#toast');t.textContent=msg;t.className='toast show '+(ok?'toast-ok':'toast-err');setTimeout(()=>t.classList.remove('show'),3500)}
function escH(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML}
async function loadShazamExt(){
  const body=document.getElementById('shazam-ext-body'),info=document.getElementById('shazam-ext-info');
  body.innerHTML='<tr><td colspan="6" style="text-align:center;color:#64748b;padding:1.5rem">Chargement...</td></tr>';
  try{const r=await fetch('/api/admin/shazam_external');
  const d=await r.json();
  const entries=d.entries||[];
  info.textContent=entries.length+' entr\u00e9e(s) affich\u00e9e(s)';
  if(!entries.length){body.innerHTML='<tr><td colspan="6" style="text-align:center;color:#64748b;padding:1.5rem">Aucune entr\u00e9e</td></tr>';return}
  let html='';
  let nbPending=0;
  entries.forEach(function(e){
    let pending=e.statut==='pending';
    if(pending)nbPending++;
    let badgeCls=pending?'badge-pending':'badge-synced';
    html+='<tr style="'+(pending?'':'opacity:.6')+'">'
      +'<td>'+e.id+'</td>'
      +'<td>'+escH(e.artiste)+'</td>'
      +'<td>'+escH(e.titre)+'</td>'
      +'<td>'+(e.date_shazam||'').slice(0,16)+'</td>'
      +'<td><span class="badge '+badgeCls+'">'+e.statut+'</span></td>'
      +'<td>'+(e.date_sync||'-').slice(0,16)+'</td>'
      +'</tr>';
  });
  body.innerHTML=html;
  const statEl=document.getElementById('stat-shazam-pending');
  if(statEl)statEl.textContent=nbPending;
  }catch(e){body.innerHTML='<tr><td colspan="6" style="text-align:center;color:#fca5a5;padding:1.5rem">Erreur</td></tr>'}}
async function purgeShazamExt(){
  if(!confirm('Supprimer les entr\u00e9es Shazam externe sync\u00e9es depuis plus de 7 jours ?'))return;
  try{const r=await fetch('/api/admin/shazam_external/purge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days:7})});
  const d=await r.json();
  if(d.status==='ok'){toast(d.deleted+' entr\u00e9e(s) purg\u00e9e(s)',true);loadShazamExt()}
  else toast(d.error||'Erreur',false)}
  catch(e){toast('Erreur r\u00e9seau',false)}}
async function createUser(){
  const u=$('#nu-username').value.trim().toLowerCase(),d=$('#nu-display').value.trim(),p=$('#nu-password').value,r=$('#nu-role').value,msg=$('#nu-msg');
  if(!u||!d||!p){msg.textContent='Tous les champs sont requis';msg.className='form-msg err';return}
  if(u.length<2){msg.textContent='Identifiant trop court (min 2)';msg.className='form-msg err';return}
  if(p.length<4){msg.textContent='Mot de passe trop court (min 4)';msg.className='form-msg err';return}
  try{const res=await fetch('/api/admin/users/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,display_name:d,password:p,role:r})});
  const data=await res.json();
  if(data.status==='ok'){msg.textContent='Utilisateur \\''+data.username+'\\' cr\\u00e9\\u00e9';msg.className='form-msg ok';$('#nu-username').value='';$('#nu-display').value='';$('#nu-password').value='';setTimeout(()=>location.reload(),1200)}
  else{msg.textContent=data.error||'Erreur';msg.className='form-msg err'}}
  catch(e){msg.textContent='Erreur r\\u00e9seau';msg.className='form-msg err'}}
async function toggleUser(uid,btn){
  try{const res=await fetch('/api/admin/users/'+uid+'/toggle',{method:'POST'});
  const data=await res.json();
  if(data.status==='ok'){toast(data.actif?'Utilisateur activ\\u00e9':'Utilisateur d\\u00e9sactiv\\u00e9',true);setTimeout(()=>location.reload(),600)}
  else toast(data.error||'Erreur',false)}
  catch(e){toast('Erreur r\\u00e9seau',false)}}
async function deleteUser(uid,name){
  if(!confirm('Supprimer '+name+' et toutes ses soumissions ?'))return;
  try{const res=await fetch('/api/admin/users/'+uid+'/delete',{method:'POST'});
  const data=await res.json();
  if(data.status==='ok'){toast(name+' supprim\\u00e9',true);setTimeout(()=>location.reload(),600)}
  else toast(data.error||'Erreur',false)}
  catch(e){toast('Erreur r\\u00e9seau',false)}}
$('#btnCreateUser').addEventListener('click',createUser);
$('#nu-password').addEventListener('keydown',e=>{if(e.key==='Enter')createUser()});
loadShazamExt();
</script>
</body>
</html>'''

# ============================================================
# Démarrage
# ============================================================

# Chargement du miroir au niveau module (Gunicorn importe le module,
# le bloc __main__ ne s'exécute pas en production)
load_mirror()

# Migration silencieuse : renommer l'ancien rôle en 'animateur'
try:
    _migrate_db = sqlite3.connect(DB_PATH, timeout=10.0)
    _migrate_db.execute("UPDATE programmateurs SET role = 'animateur' WHERE role = 'programmeur'")
    _migrate_db.commit()
    _migrate_db.close()
    logger.info("Migration rôle -> 'animateur' effectuée si nécessaire")
except Exception as e:
    logger.warning(f"Migration rôle échouée (non bloquant) : {e}")

# Migration : ajouter colonne animateur à shazam_external si absente
try:
    _migrate_db2 = sqlite3.connect(DB_PATH, timeout=10.0)
    _cols = [r[1] for r in _migrate_db2.execute("PRAGMA table_info(shazam_external)").fetchall()]
    if 'animateur' not in _cols:
        _migrate_db2.execute("ALTER TABLE shazam_external ADD COLUMN animateur TEXT DEFAULT NULL")
        _migrate_db2.commit()
        logger.info("Migration : colonne animateur ajoutée à shazam_external")
    _migrate_db2.close()
except Exception as e:
    logger.warning(f"Migration animateur shazam_external (non bloquant) : {e}")

# Migration : ajouter colonne video_url a soumissions si absente
try:
    _migrate_db3 = sqlite3.connect(DB_PATH, timeout=10.0)
    _cols3 = [r[1] for r in _migrate_db3.execute("PRAGMA table_info(soumissions)").fetchall()]
    if 'video_url' not in _cols3:
        _migrate_db3.execute("ALTER TABLE soumissions ADD COLUMN video_url TEXT DEFAULT NULL")
        _migrate_db3.commit()
        logger.info("Migration : colonne video_url ajoutee a soumissions")
    _migrate_db3.close()
except Exception as e:
    logger.warning(f"Migration video_url soumissions (non bloquant) : {e}")

if __name__ == '__main__':
    # Développement uniquement — en production : gunicorn via systemd
    app.run(host='127.0.0.1', port=8060, debug=False)
