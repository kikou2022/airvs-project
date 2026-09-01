"""app.py — Point d'entrée du Dashboard AIRVS v2 (Phase 2 : architecture Blueprints).

Ce fichier est maintenant MINIMAL : il ne contient plus que :
  - La création de l'application Flask
  - Le handler CORS global
  - La route principale (index)
  - L'enregistrement des 12 Blueprints
  - Le lancement du thread worker (sync MP3)
  - Le bloc de démarrage (schéma SQL, nettoyage)

Toute la logique métier est dans blueprints/.
"""

import os
import threading
from flask import Flask, render_template, request, session, redirect, url_for, jsonify

# ── Phase 1 : modules extraits ───────────────────────────────────────────
from config import (
    HOST,
    MOT_DE_PASSE_HASH,
)
from utils import (
    get_db_connection,
    _assurer_schema_airvs_avance,
)
from blueprints.auth import login_requis

# ── Phase 2 : Blueprints ─────────────────────────────────────────────────
from blueprints.login import login_bp
from blueprints.explorateur import explorateur_bp
from blueprints.sync_mp3 import sync_mp3_bp, worker_sync_mp3, task_dispatcher
from blueprints.flux import flux_bp
from blueprints.player import player_bp
from blueprints.azuracast import azuracast_bp
from blueprints.piges import piges_bp
from blueprints.grille import grille_bp
from blueprints.shazam import shazam_bp, _assurer_schema_shazam
from blueprints.programmation import programmation_bp
from blueprints.taches import taches_bp
from blueprints.pipeline_import import pipeline_import_bp


app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'cle_par_defaut_changer')


# ─── CORS global (LAN uniquement — machine isolée d'internet) ─────────
@app.after_request
def _add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


# ─── Route principale (page d'accueil) ─────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
@login_requis
def index():
    db = get_db_connection()
    if not db:
        return "Erreur DB", 500

    cursor = db.cursor()
    recherche = ""

    if request.method == 'POST':
        recherche = request.form.get('recherche', '').strip()

    if recherche:
        query = """
            SELECT ID, artist, title, year, duration, `path`, comments
            FROM songs
            WHERE song_type = 0 AND (artist LIKE %s OR title LIKE %s)
            ORDER BY artist ASC LIMIT 100
        """
        criteres = f"%{recherche}%"
        cursor.execute(query, (criteres, criteres))
        titre_page = f"Résultats pour : '{recherche}'"
    else:
        query = """
            SELECT ID, artist, title, year, duration, `path`, comments
            FROM songs
            WHERE song_type = 0
            ORDER BY ID DESC LIMIT 30
        """
        cursor.execute(query)
        titre_page = "30 Derniers titres synchronisés"

    titres = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('index.html', titres=titres, titre_page=titre_page, recherche=recherche)


# ─── Enregistrement des Blueprints ─────────────────────────────────────
app.register_blueprint(login_bp)
app.register_blueprint(explorateur_bp)
app.register_blueprint(sync_mp3_bp)
app.register_blueprint(flux_bp)
app.register_blueprint(player_bp)
app.register_blueprint(azuracast_bp)
app.register_blueprint(piges_bp)
app.register_blueprint(grille_bp)
app.register_blueprint(shazam_bp)
app.register_blueprint(programmation_bp)
app.register_blueprint(taches_bp)
app.register_blueprint(pipeline_import_bp)


# ─── Thread worker (sync MP3 en arrière-plan) ─────────────────────────
dispatcher_thread = threading.Thread(target=task_dispatcher, daemon=True)
dispatcher_thread.start()


# ─── Démarrage ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 50)
    print("  Démarrage du Dashboard AirVS v2 (Blueprints)")
    print("=" * 50)

    # Vérification du schéma SQL au démarrage
    print("  Vérification du schéma SQL...")
    try:
        if _assurer_schema_airvs_avance():
            print("  ✓ Schéma SQL vérifié (tables créées si nécessaire)")
        else:
            print("  ⚠ Schéma SQL : connexion DB impossible — voir logs")
    except Exception as e:
        print(f"  ⚠ Schéma SQL : erreur inattendue — {type(e).__name__}: {e}")

    # Vérification table Shazam
    try:
        if _assurer_schema_shazam():
            print("  ✓ Table Shazam vérifiée")
    except Exception as e:
        print(f"  ⚠ Shazam : {e}")

    # Vérification/migration table Manquants
    try:
        db_mq = get_db_connection()
        if db_mq:
            cur_mq = db_mq.cursor()
            _manq_migrations = [
                ('annee',     "VARCHAR(10) DEFAULT NULL"),
                ('album',     "VARCHAR(500) DEFAULT NULL"),
                ('animateur', "VARCHAR(100) DEFAULT NULL"),
                ('origine',   "VARCHAR(255) DEFAULT NULL"),
                ('statut',    "ENUM('en_attente','importe','resolu') DEFAULT 'en_attente'"),
                ('id_import', "INT DEFAULT NULL"),
                ('resolu_le', "DATETIME DEFAULT NULL"),
            ]
            for _col, _def in _manq_migrations:
                cur_mq.execute(f"SHOW COLUMNS FROM airvs_manquants LIKE '{_col}'")
                if not cur_mq.fetchone():
                    cur_mq.execute(f"ALTER TABLE airvs_manquants ADD COLUMN {_col} {_def}")
                    print(f"  + airvs_manquants : colonne {_col} ajoutée")
            db_mq.commit()
            cur_mq.close()
            db_mq.close()
            print("  ✓ Table Manquants vérifiée")
    except Exception as e:
        print(f"  ⚠ Manquants : {e}")

    # Nettoyage rétroactif doublons Shazam
    try:
        db = get_db_connection()
        if db:
            cursor = db.cursor()
            cursor.execute(
                "DELETE s1 FROM airvs_shazam s1 "
                "INNER JOIN airvs_shazam s2 "
                "ON LOWER(s1.artiste) = LOWER(s2.artiste) "
                "  AND LOWER(s1.titre) = LOWER(s2.titre) "
                "  AND s1.id > s2.id"
            )
            deleted = cursor.rowcount
            db.commit()
            cursor.close()
            db.close()
            if deleted:
                print(f"  ✓ {deleted} doublon(s) Shazam supprimé(s)")
    except Exception as e:
        print(f"  ⚠ Nettoyage Shazam : {e}")

    print()
    print(f"  12 Blueprints enregistrés")
    print(f"  Dashboard accessible sur http://0.0.0.0:5000")
    print("=" * 50)

    app.run(host=HOST, port=5000, debug=False)
