"""blueprints/sync_mp3.py -- Worker de synchronisation MP3, copie, upload et prefill.

Routes et workers extraits de app.py pour le blueprint sync_mp3_bp.

Fonctions workers (non-routes, importees par app.py pour lancer le thread) :
  - worker_sync_mp3
  - task_dispatcher

Routes :
  - POST /lancer_sync
  - POST /arreter_sync
  - GET  /lire_log
  - POST /lancer_prefill_sheets
  - GET  /lire_log_prefill
  - POST /lancer_copie
  - GET  /lire_log_copie
  - POST /upload_mp3_inbox
"""

import os
import json
import time
import re
import subprocess
import logging

import pymysql
import mutagen
from mutagen.easyid3 import EasyID3

from flask import Blueprint, request, jsonify

from config import LOG_FILE, UPLOAD_FOLDER, INBOX_FOLDERS, MP3GAIN_PATH
from utils import get_db_connection, task_queue, stop_flag
from blueprints.auth import login_requis

_logger = logging.getLogger('airvs.sync_mp3')


sync_mp3_bp = Blueprint('sync_mp3_bp', __name__)


# ── Worker local de synchronisation MP3 ──
def worker_sync_mp3(params, drapeau_arret):
    disques_selectionnes = params.get('disques', [])
    is_mode_test = params.get('mode_test', True)

    if not disques_selectionnes:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write("ERREUR: Aucun disque sélectionné.\n")
        return

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"⚡ DÉBUT DE TRAITEMENT EFFECTIF...\n")

    db = get_db_connection()
    if not db:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write("ERREUR: Connexion DB impossible.\n")
        return

    cursor = db.cursor()
    cursor.execute("SELECT ID, `path`, artist, title, year FROM songs WHERE year > 0 AND song_type = 0")
    all_songs = cursor.fetchall()

    disques_norm = [d.upper().replace("/", "\\") for d in disques_selectionnes]
    songs_to_process = [
        song for song in all_songs
        if any(song['path'].upper().replace("/", "\\").startswith(d) for d in disques_norm)
    ]
    total = len(songs_to_process)

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{total} titres trouvés. Scan en cours...\n\n")

    erreurs, modifs = 0, 0

    for index, song in enumerate(songs_to_process, 1):
        if drapeau_arret.is_set():
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"\n!!! SYNCHRONISATION ANNULÉE APRÈS {modifs} FICHIERS !!!\n")
            break

        if index % 200 == 0:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"Progression : {index}/{total} ({(index/total)*100:.1f}%) - {modifs} modifs\n")

        filepath = song['path'].replace("/", "\\")
        db_year = str(song['year'])

        if not filepath:
            erreurs += 1
            continue
        if not os.path.exists(filepath):
            erreurs += 1
            continue

        try:
            audio = EasyID3(filepath)
            current_tag_year = audio.get('date', [None])[0]

            if current_tag_year != db_year:
                if is_mode_test:
                    with open(LOG_FILE, 'a', encoding='utf-8') as f:
                        f.write(f"[TEST] {os.path.basename(filepath)} : Tag='{current_tag_year}' -> SQL='{db_year}'\n")
                    modifs += 1
                else:
                    audio['date'] = db_year
                    audio.save()
                    modifs += 1
        except mutagen.id3.ID3NoHeaderError:
            if not is_mode_test:
                audio = mutagen.File(filepath, easy=True)
                audio.add_tags()
                audio['date'] = db_year
                audio.save()
                modifs += 1
        except Exception:
            erreurs += 1

    cursor.close()
    db.close()

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"\n=== TERMINÉ === {modifs} fichiers traités sur {', '.join(disques_selectionnes)}. ===\n")


def task_dispatcher():
    while True:
        try:
            params = task_queue.get()
            stop_flag.clear()
            try:
                worker_sync_mp3(params, stop_flag)
            except Exception as e:
                with open(LOG_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"\n!!! ERREUR INTERCEPTÉE : {type(e).__name__} - {e} !!!\n")
            finally:
                task_queue.task_done()
        except Exception:
            time.sleep(5)


# ══════════════════════════════════════════
# ROUTES SYNCHRONISATION MP3 (Locale)
# ══════════════════════════════════════════
@sync_mp3_bp.route('/lancer_sync', methods=['POST'])
@login_requis
def lancer_sync():
    data = request.json
    disques = data.get('disques', [])
    mode_test = data.get('mode_test', True)

    if not disques:
        return jsonify({"error": "Aucun disque"}), 400

    task_queue.put({'disques': disques, 'mode_test': mode_test})

    message_initial = (
        f"\n{'=' * 50}\n"
        f"TÂCHE AJOUTÉE : {'MODE TEST' if mode_test else 'MODE RÉEL'} "
        f"sur {', '.join(disques)}\n"
        f"{'=' * 50}\n"
        f"En attente de traitement par le thread...\n"
    )
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        f.write(message_initial)

    return jsonify({"status": "queued", "queue_size": task_queue.qsize()})


@sync_mp3_bp.route('/arreter_sync', methods=['POST'])
@login_requis
def arreter_sync():
    stop_flag.set()
    with task_queue.mutex:
        task_queue.queue.clear()
    return jsonify({"status": "stop_requested"})


@sync_mp3_bp.route('/lire_log')
@login_requis
def lire_log():
    if not os.path.exists(LOG_FILE):
        return jsonify({"log": "En attente d'action..."})
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        contenu = f.read()
    return jsonify({"log": contenu})


# ══════════════════════════════════════════
# ROUTES PRÉ-REMPLISSAGE STRUCTURAL (Worker Ubuntu)
# ══════════════════════════════════════════
@sync_mp3_bp.route('/lancer_prefill_sheets', methods=['POST'])
@login_requis
def lancer_prefill_sheets():
    data = request.json
    if not data.get('sheets_id') or not data.get('onglet'):
        return jsonify({"error": "Il faut l'ID du Sheet et l'onglet."}), 400

    try:
        db = get_db_connection()
        cursor = db.cursor()
        parametres_json = json.dumps(data)
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, parametres, statut) VALUES (%s, %s, 'en_attente')",
            ('PREFILL_SHEETS', parametres_json)
        )
        db.commit()
        nouvelle_tache_id = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({"status": "queued", "task_id": nouvelle_tache_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@sync_mp3_bp.route('/lire_log_prefill')
@login_requis
def lire_log_prefill():
    task_id = request.args.get('task_id')
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        if task_id:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
        else:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees "
                "WHERE type_action = 'PREFILL_SHEETS' ORDER BY id DESC LIMIT 1"
            )
        tache = cursor.fetchone()
        cursor.close()
        db.close()

        if not tache or not tache.get('log_resultat'):
            return jsonify({"log": "En attente du Worker Ubuntu...", "statut": "vide"})
        return jsonify({"log": tache.get('log_resultat'), "statut": tache.get('statut')})
    except Exception as e:
        return jsonify({"log": f"Erreur SQL: {e}", "statut": "erreur"})


# ══════════════════════════════════════════
# ROUTES RECHERCHE AVANCÉE & COPIE (Atelier)
# ══════════════════════════════════════════
@sync_mp3_bp.route('/lancer_copie', methods=['POST'])
@login_requis
def lancer_copie():
    data = request.json
    destination = data.get('destination', '')
    fichiers = data.get('fichiers', [])
    dossier_nom = data.get('dossier_nom', '').strip()

    if not destination or not fichiers:
        return jsonify({"error": "Données manquantes"}), 400

    if dossier_nom:
        dossier_nom = re.sub(r'[\\/*?:"<>|]', "", dossier_nom)

    try:
        db = get_db_connection()
        cursor = db.cursor()
        parametres_json = json.dumps({
            "destination": destination,
            "fichiers": fichiers,
            "dossier_nom": dossier_nom
        })
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, parametres, statut) VALUES (%s, %s, 'en_attente')",
            ('COPIE_FICHIERS', parametres_json)
        )
        db.commit()
        nouvelle_tache_id = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({"status": "queued", "task_id": nouvelle_tache_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@sync_mp3_bp.route('/lire_log_copie')
@login_requis
def lire_log_copie():
    task_id = request.args.get('task_id')
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        if task_id:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
        else:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees "
                "WHERE type_action = 'COPIE_FICHIERS' ORDER BY id DESC LIMIT 1"
            )
        tache = cursor.fetchone()
        cursor.close()
        db.close()

        if not tache or not tache.get('log_resultat'):
            return jsonify({"log": "En attente du worker Ubuntu...", "statut": "vide"})
        return jsonify({"log": tache['log_resultat'], "statut": tache['statut']})
    except Exception as e:
        return jsonify({"log": f"Erreur de lecture SQL: {e}", "statut": "erreur"})


# ══════════════════════════════════════════
# ROUTES INBOX & UPLOAD DIRECT
# ══════════════════════════════════════════
def _inserer_sql_apres_action(chemin_final, id_subcat=0, id_genre=0):
    artist = 'Inconnu'
    title = os.path.basename(chemin_final)
    year = '0'
    duration = 0.0

    try:
        import gc
        gc.collect()
        time.sleep(0.2)
        from mutagen import File as MutagenFile

        audio = MutagenFile(chemin_final, easy=False)
        if audio is not None:
            if hasattr(audio.info, 'length') and audio.info.length:
                duration = float(audio.info.length)

            audio_easy = MutagenFile(chemin_final, easy=True)
            if audio_easy is not None:
                artist = str(audio_easy.get('artist', ['Inconnu'])[0])
                title = str(audio_easy.get('title', [os.path.basename(chemin_final)])[0])
                year_raw = str(audio_easy.get('date', ['0'])[0])
                year = year_raw.split('-')[0]
    except Exception:
        pass

    cue_string = f"0.00&0.00&{duration:.2f}&{duration:.2f}&0.00&0.00"

    db = get_db_connection()
    if not db:
        return "Erreur DB lors de l'insertion SQL"

    cursor = db.cursor()
    sql = """
        INSERT INTO songs (
            path, enabled, song_type, id_subcat, id_genre,
            duration, original_duration, artist, original_artist,
            title, alternate_title, album, composer, label, publisher,
            copyright, isrc, bpm,
            year, date_added, cue_times
        ) VALUES (%s, 1, 0, %s, %s, %s, %s, %s, %s, %s, '', '', '', '', '', '', '', '', 0, %s, NOW(), %s)
    """
    try:
        cursor.execute(sql, (
            chemin_final, id_subcat, id_genre,
            duration, duration, artist, artist, title,
            year, cue_string
        ))
        db.commit()
        new_id = cursor.lastrowid
        cursor.close()
        db.close()
        return f"{artist} - {title} (Durée: {int(duration)}s) (ID: {new_id})"
    except Exception as e:
        cursor.close()
        db.close()
        return f"Erreur SQL : {e}"


@sync_mp3_bp.route('/upload_mp3_inbox', methods=['POST'])
@login_requis
def upload_mp3_inbox():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "Aucun fichier"}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Nom de fichier vide"}), 400

        inbox_key = request.form.get('inbox_key', 'inbox_local')
        if inbox_key not in INBOX_FOLDERS:
            return jsonify({"error": "Tiroir inconnu"}), 400

        config_tiroir = INBOX_FOLDERS[inbox_key]
        dest_folder = config_tiroir["path"]
        id_subcat = config_tiroir.get("id_subcat", 0)
        id_genre = config_tiroir.get("id_genre", 0)

        os.makedirs(dest_folder, exist_ok=True)
        final_path = os.path.join(dest_folder, file.filename)

        if os.path.exists(final_path):
            return jsonify({"error": "Fichier déjà présent dans le tiroir !"}), 400

        file.save(final_path)

        # Normaliser le volume a 89 dB AVANT l'insertion SQL (contrainte AIRVS)
        try:
            proc = subprocess.run(
                [MP3GAIN_PATH, '-r', '-c', '-k', final_path],
                capture_output=True, text=True, timeout=120
            )
            if proc.returncode in (0, 2):
                _logger.info(f'mp3gain OK (inbox) : {file.filename}')
            else:
                err = (proc.stderr or proc.stdout or '').strip()[:200]
                _logger.error(f'mp3gain echoue (inbox) {file.filename} : rc={proc.returncode} {err}')
                os.remove(final_path)
                return jsonify({"error": f"mp3gain echoue : {err}. Fichier non importe."}), 500
        except FileNotFoundError:
            _logger.error('mp3gain non trouve (inbox)')
            os.remove(final_path)
            return jsonify({"error": "mp3gain non trouve sur le systeme. Fichier non importe."}), 500
        except subprocess.TimeoutExpired:
            _logger.error(f'mp3gain timeout (inbox) : {file.filename}')
            os.remove(final_path)
            return jsonify({"error": "Timeout mp3gain. Fichier non importe."}), 500
        except Exception as e:
            _logger.error(f'mp3gain erreur (inbox) : {e}')
            os.remove(final_path)
            return jsonify({"error": f"Erreur mp3gain : {e}. Fichier non importe."}), 500

        resultat = _inserer_sql_apres_action(final_path, id_subcat, id_genre)
        return jsonify({"status": "success", "message": f"Uploadé et inscrit : {resultat}"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
