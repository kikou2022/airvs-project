"""blueprints/grille.py -- Grille éditoriale AIRVS.

Routes extraites de app.py pour le blueprint grille_bp.

Gestion de la grille de programmation hebdomadaire :
  - CRUD blocs (créer, modifier, supprimer)
  - Versioning (créer, activer, renommer, dupliquer, supprimer)
  - Validation (chevauchements, trous horaires)
  - Export / import JSON
  - Publication FTP via worker Ubuntu
"""

import os
import json
from datetime import datetime

import pymysql
from flask import Blueprint, request, jsonify

from config import (
    JOURS_MAP, JOURS_LABELS, PALETTE_AIRVS, API_TOKEN, PORT,
    _ge_to_min, _ge_times_overlap,
)
from utils import get_db_connection
from blueprints.auth import login_requis, token_requis


grille_bp = Blueprint('grille', __name__)


# ─── Helpers ────────────────────────────────────────────────────────────

def _ge_generer_grille_dict(version_label=None):
    """Génère le dictionnaire JSON de la grille active (ou d'une version donnée).

    Utilise get_db_connection() de prod (pymysql + DictCursor).
    Retourne le format attendu par programme.html (et cohérent avec la route
    /api/grille_editoriale/data) :
        {
          "meta": {
            "version": "1.0", "station": "AIRVS", "label": "Rentrée...",
            "updated": "YYYY-MM-DD HH:MM", "timeRange": {"start": "06:00", "end": "01:00"},
            "palette": [...]
          },
          "schedule": {
            "lundi": {"label": "Lundi", "blocks": [
              {"id": "bloc-1", "db_id": 1, "title": "...", "start": "HH:MM",
               "end": "HH:MM", "color": "#...", "type": "...", "description": "...",
               "host": "...", "subBlocks": [...]}
            ]},
            "mardi": {"label": "Mardi", "blocks": []},
            ...
          }
        }

    Filtre sur version_actif=1 (comme la route /data du dashboard) plutôt que
    sur version_label — c'est plus robuste car les blocs créés héritent du
    DEFAULT version_actif=1 du schéma.
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Version active (ou version_label si fourni)
        if version_label:
            cur.execute(
                "SELECT * FROM airvs_grille_versions WHERE label=%s",
                (version_label,)
            )
        else:
            cur.execute("SELECT * FROM airvs_grille_versions WHERE actif=1 LIMIT 1")
        version = cur.fetchone()
        if not version:
            return {
                'meta': {
                    'version': '1.0', 'station': 'AIRVS', 'label': None,
                    'updated': datetime.now().strftime("%Y-%m-%d %H:%M"),
                    'timeRange': {'start': '06:00', 'end': '01:00'},
                    'palette': PALETTE_AIRVS
                },
                'schedule': {JOURS_MAP[j]: {'label': JOURS_LABELS[j], 'blocks': []} for j in range(1, 8)}
            }

        # Blocs racines (parent_id IS NULL) pour la version active.
        # Filtre sur version_actif=1 (et non version_label) pour matcher
        # le comportement de /api/grille_editoriale/data et récupérer
        # les blocs même si version_label est resté à la valeur DEFAULT 'Grille'.
        cur.execute("""
            SELECT id, jour_semaine, heure_debut, heure_fin, titre, type,
                   description, animateur, couleur, parent_id, ordre
            FROM airvs_grille_editoriale
            WHERE version_actif = 1 AND parent_id IS NULL
            ORDER BY jour_semaine, heure_debut, ordre
        """)
        racines = cur.fetchall()

        # Construire le schedule au format attendu par programme.html
        schedule = {}
        for r in racines:
            cle = JOURS_MAP[r['jour_semaine']]
            # Sous-blocs
            cur.execute("""
                SELECT id, titre, heure_debut, heure_fin, description
                FROM airvs_grille_editoriale
                WHERE parent_id = %s AND version_actif = 1
                ORDER BY ordre, heure_debut
            """, (r['id'],))
            sub_blocks = []
            for sb in cur.fetchall():
                sub_blocks.append({
                    "id": f"sub-{sb['id']}",
                    "title": sb['titre'],
                    "start": sb['heure_debut'],
                    "end": sb['heure_fin'],
                    "description": sb['description'] or ''
                })

            if cle not in schedule:
                schedule[cle] = {"label": JOURS_LABELS[r['jour_semaine']], "blocks": []}
            schedule[cle]["blocks"].append({
                "id": f"bloc-{r['id']}",
                "db_id": r['id'],
                "title": r['titre'],
                "start": r['heure_debut'],
                "end": r['heure_fin'],
                "color": r['couleur'],
                "type": r['type'],
                "description": r['description'] or '',
                "host": r['animateur'] or '',
                "subBlocks": sub_blocks
            })

        # S'assurer que tous les jours sont présents (même vide)
        for j in range(1, 8):
            cle = JOURS_MAP[j]
            if cle not in schedule:
                schedule[cle] = {"label": JOURS_LABELS[j], "blocks": []}
        # Ordonner par jour de la semaine
        schedule = {JOURS_MAP[j]: schedule[JOURS_MAP[j]] for j in range(1, 8)}

        return {
            'meta': {
                'version': '1.0',
                'station': 'AIRVS',
                'label': version['label'],
                'updated': datetime.now().strftime("%Y-%m-%d %H:%M"),
                'timeRange': {'start': '06:00', 'end': '01:00'},
                'palette': PALETTE_AIRVS
            },
            'schedule': schedule
        }
    finally:
        conn.close()


def _ge_inserer_tache_sync_grille(ftp_config, dashboard_url):
    """Insère une tâche SYNC_GRILLE_WEB dans taches_planifiees.

    En production, utilise get_db_connection() (pymysql direct, pas besoin
    de bypass comme en dev). Le worker Ubuntu détecte la tâche et appelle
    /api/grille_editoriale/grille.json pour récupérer le JSON puis le pousse
    sur airvs.fr via FTP.

    Paramètres:
      - ftp_config : dict avec host, user, pass, remote_path
      - dashboard_url : URL LAN du dashboard (ex: http://192.168.1.39:5000)

    Retourne l'ID de la tâche insérée.
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO taches_planifiees (type_action, parametres, statut, date_creation)
            VALUES (%s, %s, 'en_attente', NOW())
        """, (
            'SYNC_GRILLE_WEB',
            json.dumps({
                'dashboard_url': dashboard_url,
                'api_token': API_TOKEN,
                'ftp_host': ftp_config.get('host', ''),
                'ftp_user': ftp_config.get('user', ''),
                'ftp_pass': ftp_config.get('pass', ''),
                'ftp_remote_path': ftp_config.get('remote_path', '/'),
            })
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _ge_ftp_config():
    """Récupère la configuration FTP depuis les variables d'environnement."""
    return {
        'host': os.getenv('FTP_HOST', ''),
        'user': os.getenv('FTP_USER', ''),
        'pass': os.getenv('FTP_PASS', ''),
        'remote_path': os.getenv('FTP_REMOTE_PATH', '/'),
    }


def _ge_ecrire_grille_json_local():
    """Écrit grille.json dans PUBLISH_PATH si configuré.

    Mécanisme de sécurité : appelé après chaque modification de la grille
    (création/modification/suppression de bloc, activation de version, import)
    pour garantir qu'une copie locale à jour existe toujours, indépendamment
    de la publication FTP via le worker Ubuntu.

    En production, PUBLISH_PATH pointe vers une version locale de la
    structure HTML du site airvs.fr (par exemple un miroir du dossier
    /public_html/ sur la machine Windows). Le fichier grille.json peut
    ainsi être consulté localement si la publication FTP échoue ou si le
    worker est indisponible.

    Retourne (ok: bool, detail: str).
    """
    publish_dir = os.getenv('PUBLISH_PATH', '').strip()
    if not publish_dir:
        return False, "PUBLISH_PATH non configuré (écriture locale désactivée)"
    if not os.path.isdir(publish_dir):
        try:
            os.makedirs(publish_dir, exist_ok=True)
            print(f"[Grille éditoriale] PUBLISH_PATH créé : {publish_dir}")
        except Exception as e:
            return False, f"PUBLISH_PATH inaccessible : {e}"

    data = _ge_generer_grille_dict()
    if not data:
        return False, "Impossible de générer la grille (DB inaccessible ?)"

    dest = os.path.join(publish_dir, 'grille.json')
    try:
        with open(dest, 'w', encoding='utf-8') as f:
            # default=str : convertit les objets non-sérialisables (datetime, Decimal)
            # en string via str(). Sans ça, json.dump lève TypeError sur date_creation
            # et date_modification (datetime.datetime venant de MariaDB) → fichier
            # tronqué à mi-écriture.
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"[Grille éditoriale] grille.json écrit localement : {dest}")
        return True, dest
    except Exception as e:
        msg = f"Erreur écriture grille.json local : {e}"
        print(f"[Grille éditoriale] {msg}")
        return False, msg


# ─── Routes ─────────────────────────────────────────────────────────────

@grille_bp.route('/api/grille_editoriale/data')
@login_requis
def api_grille_data():
    """Retourne toute la grille de la version active."""
    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    c.execute("SELECT label FROM airvs_grille_versions WHERE actif = 1 LIMIT 1")
    ver = c.fetchone()
    version_label = ver['label'] if ver else 'Grille'

    c.execute("""
        SELECT id, jour_semaine, heure_debut, heure_fin, titre, type,
               description, animateur, couleur, parent_id, ordre
        FROM airvs_grille_editoriale
        WHERE version_actif = 1 AND parent_id IS NULL
        ORDER BY jour_semaine, heure_debut
    """)
    racines = c.fetchall()

    schedule = {}
    for r in racines:
        cle = JOURS_MAP[r['jour_semaine']]

        c.execute("""
            SELECT id, titre, heure_debut, heure_fin, description
            FROM airvs_grille_editoriale
            WHERE parent_id = %s AND version_actif = 1
            ORDER BY ordre, heure_debut
        """, (r['id'],))
        sub_blocks = []
        for sb in c.fetchall():
            sub_blocks.append({
                "id": f"sub-{sb['id']}",
                "title": sb['titre'],
                "start": sb['heure_debut'],
                "end": sb['heure_fin'],
                "description": sb['description'] or ''
            })

        if cle not in schedule:
            schedule[cle] = {"label": JOURS_LABELS[r['jour_semaine']], "blocks": []}
        schedule[cle]["blocks"].append({
            "id": f"bloc-{r['id']}",
            "db_id": r['id'],
            "title": r['titre'],
            "start": r['heure_debut'],
            "end": r['heure_fin'],
            "color": r['couleur'],
            "type": r['type'],
            "description": r['description'] or '',
            "host": r['animateur'] or '',
            "subBlocks": sub_blocks
        })

    for j in range(1, 8):
        cle = JOURS_MAP[j]
        if cle not in schedule:
            schedule[cle] = {"label": JOURS_LABELS[j], "blocks": []}
    schedule = {k: schedule[k] for k in [JOURS_MAP[j] for j in range(1, 8)]}

    conn.close()
    return jsonify({
        "status": "ok",
        "data": {
            "meta": {
                "version": "1.0",
                "station": "AIRVS",
                "label": version_label,
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "timeRange": {"start": "06:00", "end": "01:00"},
                "palette": PALETTE_AIRVS
            },
            "schedule": schedule
        }
    })


@grille_bp.route('/api/grille_editoriale/bloc', methods=['POST'])
@login_requis
def api_grille_create_bloc():
    """Crée un nouveau bloc (mono-jour). Le multi-jours est géré côté frontend
    en appelant cette route N fois."""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Données manquantes"}), 400

    required = ['jour_semaine', 'heure_debut', 'heure_fin', 'titre']
    for field in required:
        if field not in data or not data[field]:
            return jsonify({"status": "error", "message": f"Champ '{field}' requis"}), 400

    # Validation horaire : autorise le passage minuit (ex: 22:55 → 01:00)
    if data['heure_debut'] == data['heure_fin']:
        return jsonify({"status": "error", "message": "L'heure de début et de fin ne peuvent pas être identiques"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    # Récupérer le label de la version active pour rattacher le bloc à cette version.
    # CRITIQUE : sans ça, le bloc hérite du DEFAULT version_label='Grille' et
    # ne sera pas trouvé par la route /version/activer/<id> qui filtre sur
    # version_label = <label de la version activée>.
    c.execute("SELECT label FROM airvs_grille_versions WHERE actif = 1 LIMIT 1")
    ver = c.fetchone()
    version_label_actif = ver['label'] if ver else 'Grille'

    # Détection de chevauchement en Python (gère le passage minuit)
    c.execute("""
        SELECT id, titre, heure_debut, heure_fin FROM airvs_grille_editoriale
        WHERE jour_semaine = %s AND version_actif = 1 AND parent_id IS NULL
    """, (data['jour_semaine'],))
    existing = c.fetchall()
    overlaps = [b for b in existing
                if _ge_times_overlap(data['heure_debut'], data['heure_fin'], b['heure_debut'], b['heure_fin'])]
    if overlaps:
        conn.close()
        return jsonify({
            "status": "error",
            "message": f"Chevauchement avec : {', '.join(o['titre'] for o in overlaps)}",
            "overlaps": overlaps
        }), 409

    c.execute("""
        INSERT INTO airvs_grille_editoriale
        (version_label, version_actif, jour_semaine, heure_debut, heure_fin, titre, type, description, animateur, couleur, parent_id, ordre)
        VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        version_label_actif,
        data['jour_semaine'],
        data['heure_debut'],
        data['heure_fin'],
        data['titre'],
        data.get('type', 'focus'),
        data.get('description', ''),
        data.get('animateur', ''),
        data.get('couleur', '#1DB954'),
        None,
        0
    ))
    bloc_id = c.lastrowid
    conn.commit()
    conn.close()

    # Mise à jour de la copie locale grille.json (sécurité — voir _ge_ecrire_grille_json_local)
    _ge_ecrire_grille_json_local()

    return jsonify({"status": "ok", "id": bloc_id, "message": "Bloc créé"})


@grille_bp.route('/api/grille_editoriale/bloc/<int:bloc_id>', methods=['PUT'])
@login_requis
def api_grille_update_bloc(bloc_id):
    """Met à jour un bloc existant."""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Données manquantes"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    c.execute("SELECT id FROM airvs_grille_editoriale WHERE id = %s", (bloc_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({"status": "error", "message": "Bloc introuvable"}), 404

    if 'heure_debut' in data and 'heure_fin' in data:
        if data['heure_debut'] == data['heure_fin']:
            conn.close()
            return jsonify({"status": "error", "message": "L'heure de début et de fin ne peuvent pas être identiques"}), 400

        # Chevauchement en Python (gère le passage minuit)
        jour_check = data.get('jour_semaine')
        if not jour_check:
            c.execute("SELECT jour_semaine FROM airvs_grille_editoriale WHERE id = %s", (bloc_id,))
            row = c.fetchone()
            jour_check = row['jour_semaine'] if row else None
        if jour_check:
            c.execute("""
                SELECT id, titre, heure_debut, heure_fin FROM airvs_grille_editoriale
                WHERE jour_semaine = %s AND version_actif = 1 AND parent_id IS NULL AND id != %s
            """, (jour_check, bloc_id))
            existing = c.fetchall()
            overlaps = [b for b in existing
                        if _ge_times_overlap(data['heure_debut'], data['heure_fin'], b['heure_debut'], b['heure_fin'])]
            if overlaps:
                conn.close()
                return jsonify({
                    "status": "error",
                    "message": f"Chevauchement avec : {', '.join(o['titre'] for o in overlaps)}"
                }), 409

    fields = []
    values = []
    for field in ['jour_semaine', 'heure_debut', 'heure_fin', 'titre', 'type', 'description', 'animateur', 'couleur']:
        if field in data:
            fields.append(f"{field} = %s")
            values.append(data[field])

    if not fields:
        conn.close()
        return jsonify({"status": "error", "message": "Aucun champ à modifier"}), 400

    fields.append("date_modification = NOW()")
    values.append(bloc_id)

    c.execute(f"UPDATE airvs_grille_editoriale SET {', '.join(fields)} WHERE id = %s", values)
    conn.commit()
    conn.close()

    # Mise à jour de la copie locale grille.json (sécurité — voir _ge_ecrire_grille_json_local)
    _ge_ecrire_grille_json_local()

    return jsonify({"status": "ok", "message": "Bloc mis à jour"})


@grille_bp.route('/api/grille_editoriale/bloc/<int:bloc_id>', methods=['DELETE'])
@login_requis
def api_grille_delete_bloc(bloc_id):
    """Supprime un bloc et ses sous-blocs (CASCADE manuelle)."""
    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    c.execute("SELECT id, titre FROM airvs_grille_editoriale WHERE id = %s", (bloc_id,))
    bloc = c.fetchone()
    if not bloc:
        conn.close()
        return jsonify({"status": "error", "message": "Bloc introuvable"}), 404

    c.execute("DELETE FROM airvs_grille_editoriale WHERE parent_id = %s", (bloc_id,))
    c.execute("DELETE FROM airvs_grille_editoriale WHERE id = %s", (bloc_id,))
    conn.commit()
    conn.close()

    # Mise à jour de la copie locale grille.json (sécurité — voir _ge_ecrire_grille_json_local)
    _ge_ecrire_grille_json_local()

    return jsonify({"status": "ok", "message": f"Bloc '{bloc['titre']}' supprimé"})


@grille_bp.route('/api/grille_editoriale/export')
@login_requis
def api_grille_export():
    """Génère le JSON au format exact pour programme.html (sauvegarde)."""
    data = _ge_generer_grille_dict()
    return jsonify(data)


@grille_bp.route('/api/grille_editoriale/grille.json')
@token_requis
def api_grille_json_public():
    """Endpoint public pour le worker Ubuntu : retourne le JSON brut de la grille.
    Authentification par token Bearer (décorateur @token_requis)."""
    data = _ge_generer_grille_dict()
    return jsonify(data)


@grille_bp.route('/api/grille_editoriale/publier', methods=['POST'])
@login_requis
def api_grille_publier():
    """Publie la grille sur airvs.fr. Effectue en parallèle :
       1. Écriture locale de grille.json dans PUBLISH_PATH (sécurité — immédiat)
       2. Insertion d'une tâche SYNC_GRILLE_WEB dans taches_planifiees
          → le worker Ubuntu va détecter la tâche, appeler l'endpoint
          /api/grille_editoriale/grille.json et pousser le JSON sur airvs.fr
          via FTP.

    Le mode local garantit qu'une copie à jour existe toujours, même si le
    worker est indisponible ou si la publication FTP échoue. Si PUBLISH_PATH
    n'est pas configuré, seul le mode FTP worker est utilisé."""
    # 1. Écriture locale (sécurité) — ne bloque pas la suite si elle échoue
    local_ok, local_detail = _ge_ecrire_grille_json_local()
    if not local_ok:
        print(f"[Grille éditoriale] Écriture locale ignorée : {local_detail}")

    # 2. Insertion de la tâche worker FTP
    dashboard_url = os.getenv('DASHBOARD_LAN_URL', f'http://127.0.0.1:{PORT}')
    ftp_config = _ge_ftp_config()
    try:
        tache_id = _ge_inserer_tache_sync_grille(ftp_config, dashboard_url)
        message = f"Tâche SYNC_GRILLE_WEB insérée (id={tache_id}). Le worker Ubuntu va la traiter."
        if local_ok:
            message += f" Copie locale mise à jour : {local_detail}"
        return jsonify({
            "status": "ok",
            "message": message,
            "tache_id": tache_id,
            "local_copy": local_ok,
            "local_path": local_detail if local_ok else None
        })
    except Exception as e:
        # Si l'insertion de tâche échoue mais que la copie locale a réussi,
        # on retourne un succès partiel plutôt qu'un échec total
        if local_ok:
            return jsonify({
                "status": "ok",
                "message": f"Copie locale OK ({local_detail}) mais tâche worker échouée : {e}",
                "tache_id": None,
                "local_copy": True,
                "local_path": local_detail
            })
        return jsonify({
            "status": "error",
            "message": f"Échec insertion tâche : {e}. Copie locale également échouée : {local_detail}"
        }), 500


@grille_bp.route('/api/grille_editoriale/versions')
@login_requis
def api_grille_versions():
    """Liste toutes les versions."""
    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()
    c.execute("SELECT id, label, actif, date_creation FROM airvs_grille_versions ORDER BY date_creation DESC")
    versions = c.fetchall()
    conn.close()
    return jsonify({"status": "ok", "versions": versions})


@grille_bp.route('/api/grille_editoriale/version', methods=['POST'])
@login_requis
def api_grille_create_version():
    """Crée une nouvelle version (inactive par défaut)."""
    data = request.get_json()
    label = data.get('label', '').strip()
    if not label:
        return jsonify({"status": "error", "message": "Nom de version requis"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    try:
        c.execute("INSERT INTO airvs_grille_versions (label, actif) VALUES (%s, 0)", (label,))
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return jsonify({"status": "ok", "id": new_id, "message": f"Version '{label}' créée"})
    except pymysql.IntegrityError:
        conn.close()
        return jsonify({"status": "error", "message": f"La version '{label}' existe déjà"}), 409


@grille_bp.route('/api/grille_editoriale/version/activer/<int:version_id>', methods=['PUT'])
@login_requis
def api_grille_activate_version(version_id):
    """Active une version (désactive toutes les autres)."""
    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    c.execute("SELECT id, label FROM airvs_grille_versions WHERE id = %s", (version_id,))
    ver = c.fetchone()
    if not ver:
        conn.close()
        return jsonify({"status": "error", "message": "Version introuvable"}), 404

    c.execute("UPDATE airvs_grille_versions SET actif = 0")
    c.execute("UPDATE airvs_grille_versions SET actif = 1 WHERE id = %s", (version_id,))
    c.execute("UPDATE airvs_grille_editoriale SET version_actif = 0")
    c.execute("UPDATE airvs_grille_editoriale SET version_actif = 1 WHERE version_label = %s", (ver['label'],))

    conn.commit()
    conn.close()

    # Mise à jour de la copie locale grille.json (sécurité — voir _ge_ecrire_grille_json_local)
    _ge_ecrire_grille_json_local()

    return jsonify({"status": "ok", "message": f"Version '{ver['label']}' activée"})


@grille_bp.route('/api/grille_editoriale/version/renommer/<int:version_id>', methods=['PUT'])
@login_requis
def api_grille_rename_version(version_id):
    """Renomme une version existante.

    Met à jour airvs_grille_versions.label ET airvs_grille_editoriale.version_label
    pour tous les blocs associés — sinon la correspondance serait cassée et les
    blocs ne seraient plus trouvés lors de l'activation.
    """
    data = request.get_json() or {}
    nouveau_label = (data.get('label') or '').strip()
    if not nouveau_label:
        return jsonify({"status": "error", "message": "Nouveau nom requis"}), 400
    if len(nouveau_label) > 100:
        return jsonify({"status": "error", "message": "Nom trop long (max 100 caractères)"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    # 1. Vérifier que la version existe
    c.execute("SELECT id, label FROM airvs_grille_versions WHERE id = %s", (version_id,))
    ver = c.fetchone()
    if not ver:
        conn.close()
        return jsonify({"status": "error", "message": "Version introuvable"}), 404

    ancien_label = ver['label']

    # 2. Vérifier l'unicité du nouveau nom (sauf si c'est le même)
    if nouveau_label != ancien_label:
        c.execute("SELECT id FROM airvs_grille_versions WHERE label = %s AND id != %s",
                  (nouveau_label, version_id))
        if c.fetchone():
            conn.close()
            return jsonify({
                "status": "error",
                "message": f"Une version nommée '{nouveau_label}' existe déjà"
            }), 409

    # 3. Mettre à jour le label dans airvs_grille_versions
    try:
        c.execute("UPDATE airvs_grille_versions SET label = %s WHERE id = %s",
                  (nouveau_label, version_id))
    except pymysql.IntegrityError:
        conn.close()
        return jsonify({
            "status": "error",
            "message": f"Le nom '{nouveau_label}' est déjà utilisé"
        }), 409

    # 4. Mettre à jour version_label dans airvs_grille_editoriale (tous les blocs associés)
    c.execute("UPDATE airvs_grille_editoriale SET version_label = %s WHERE version_label = %s",
              (nouveau_label, ancien_label))

    conn.commit()
    conn.close()

    # Mise à jour de la copie locale grille.json (au cas où c'est la version active)
    _ge_ecrire_grille_json_local()

    return jsonify({
        "status": "ok",
        "message": f"Version renommée : '{ancien_label}' → '{nouveau_label}'"
    })


@grille_bp.route('/api/grille_editoriale/version/dupliquer/<int:version_id>', methods=['POST'])
@login_requis
def api_grille_duplicate_version(version_id):
    """Duplique (fork) une version existante sous un nouveau nom.

    Crée une nouvelle version inactive avec le label fourni, copie tous les
    blocs de la version source (en gardant les mêmes horaires/titres/etc.),
    avec version_actif=0 (la copie n'est pas active par défaut).
    """
    data = request.get_json() or {}
    nouveau_label = (data.get('label') or '').strip()
    if not nouveau_label:
        return jsonify({"status": "error", "message": "Nom de la nouvelle version requis"}), 400
    if len(nouveau_label) > 100:
        return jsonify({"status": "error", "message": "Nom trop long (max 100 caractères)"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    # 1. Vérifier que la version source existe
    c.execute("SELECT id, label FROM airvs_grille_versions WHERE id = %s", (version_id,))
    ver = c.fetchone()
    if not ver:
        conn.close()
        return jsonify({"status": "error", "message": "Version source introuvable"}), 404

    source_label = ver['label']

    # 2. Vérifier l'unicité du nouveau nom
    c.execute("SELECT id FROM airvs_grille_versions WHERE label = %s", (nouveau_label,))
    if c.fetchone():
        conn.close()
        return jsonify({
            "status": "error",
            "message": f"Une version nommée '{nouveau_label}' existe déjà"
        }), 409

    # 3. Créer la nouvelle version (inactive)
    try:
        c.execute("INSERT INTO airvs_grille_versions (label, actif) VALUES (%s, 0)",
                  (nouveau_label,))
        new_version_id = c.lastrowid
    except pymysql.IntegrityError:
        conn.close()
        return jsonify({
            "status": "error",
            "message": f"Le nom '{nouveau_label}' est déjà utilisé"
        }), 409

    # 4. Copier tous les blocs racines de la version source vers la nouvelle version
    # (avec version_actif=0 — la copie n'est pas active par défaut)
    c.execute("""
        INSERT INTO airvs_grille_editoriale
          (version_label, version_actif, jour_semaine, heure_debut, heure_fin,
           titre, type, description, animateur, couleur, parent_id, ordre)
        SELECT %s, 0, jour_semaine, heure_debut, heure_fin,
               titre, type, description, animateur, couleur, parent_id, ordre
        FROM airvs_grille_editoriale
        WHERE version_label = %s
        ORDER BY jour_semaine, heure_debut, ordre
    """, (nouveau_label, source_label))
    blocs_copies = c.rowcount

    conn.commit()
    conn.close()

    # Pas de mise à jour de grille.json — la version copiée est inactive,
    # donc la grille affichée (version active) n'a pas changé.

    return jsonify({
        "status": "ok",
        "message": f"Version '{source_label}' dupliquée vers '{nouveau_label}' ({blocs_copies} bloc(s) copié(s)). Activez '{nouveau_label}' pour l'utiliser.",
        "new_version_id": new_version_id,
        "blocs_copies": blocs_copies
    })


@grille_bp.route('/api/grille_editoriale/version/<int:version_id>', methods=['DELETE'])
@login_requis
def api_grille_delete_version(version_id):
    """Supprime une version existante et tous ses blocs.

    Refuse la suppression si la version est active (pour éviter de vider
    la grille affichée par accident). L'utilisateur doit d'abord activer
    une autre version, puis supprimer celle-ci.
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    # 1. Vérifier que la version existe
    c.execute("SELECT id, label, actif FROM airvs_grille_versions WHERE id = %s", (version_id,))
    ver = c.fetchone()
    if not ver:
        conn.close()
        return jsonify({"status": "error", "message": "Version introuvable"}), 404

    # 2. Refuser la suppression si la version est active
    if ver['actif']:
        conn.close()
        return jsonify({
            "status": "error",
            "message": f"Impossible de supprimer la version active '{ver['label']}'. Activez d'abord une autre version."
        }), 409

    # 3. Supprimer tous les blocs associés (racines + sous-blocs)
    c.execute("DELETE FROM airvs_grille_editoriale WHERE version_label = %s", (ver['label'],))

    # 4. Supprimer la version elle-même
    c.execute("DELETE FROM airvs_grille_versions WHERE id = %s", (version_id,))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "ok",
        "message": f"Version '{ver['label']}' supprimée avec tous ses blocs"
    })


@grille_bp.route('/api/grille_editoriale/valider')
@login_requis
def api_grille_valider():
    """Vérifie les chevauchements (erreurs) et les trous horaires (avertissements)."""
    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    c.execute("""
        SELECT jour_semaine, heure_debut, heure_fin, titre
        FROM airvs_grille_editoriale
        WHERE version_actif = 1 AND parent_id IS NULL
        ORDER BY jour_semaine, heure_debut
    """)
    blocs = c.fetchall()
    conn.close()

    erreurs = []
    avertissements = []

    for jour in range(1, 8):
        jour_blocs = [b for b in blocs if b['jour_semaine'] == jour]
        jour_label = JOURS_LABELS[jour]

        jour_blocs.sort(key=lambda x: x['heure_debut'])

        # Détection des chevauchements (gère le passage minuit)
        for i in range(len(jour_blocs)):
            for j in range(i + 1, len(jour_blocs)):
                b1, b2 = jour_blocs[i], jour_blocs[j]
                if _ge_times_overlap(b1['heure_debut'], b1['heure_fin'],
                                     b2['heure_debut'], b2['heure_fin']):
                    erreurs.append(
                        f"{jour_label} : chevauchement entre '{b1['titre']}' ({b1['heure_debut']}-{b1['heure_fin']}) "
                        f"et '{b2['titre']}' ({b2['heure_debut']}-{b2['heure_fin']})"
                    )
                    break

        # Détection des trous
        if jour_blocs:
            if jour_blocs[0]['heure_debut'] > '06:00':
                avertissements.append(f"{jour_label} : trou de 06:00 à {jour_blocs[0]['heure_debut']}")
            for i in range(len(jour_blocs) - 1):
                if jour_blocs[i]['heure_fin'] < jour_blocs[i + 1]['heure_debut']:
                    # Ignorer si le bloc courant traverse minuit (fin < début = normal)
                    if _ge_to_min(jour_blocs[i]['heure_fin']) <= _ge_to_min(jour_blocs[i]['heure_debut']):
                        continue
                    avertissements.append(
                        f"{jour_label} : trou de {jour_blocs[i]['heure_fin']} à {jour_blocs[i + 1]['heure_debut']}"
                    )
            # Le dernier bloc doit arriver jusqu'à 01:00 ou traverser minuit
            last_end = jour_blocs[-1]['heure_fin']
            if not (_ge_to_min(last_end) <= _ge_to_min(jour_blocs[-1]['heure_debut'])):
                # Bloc normal (pas traversant minuit) : vérifier qu'il va jusqu'à 01:00
                if last_end != '01:00':
                    avertissements.append(f"{jour_label} : trou de {last_end} à 01:00")

    return jsonify({
        "status": "ok",
        "valide": len(erreurs) == 0,
        "erreurs": erreurs,
        "avertissements": avertissements,
        "nb_blocs": len(blocs)
    })


@grille_bp.route('/api/grille_editoriale/copier_jour', methods=['POST'])
@login_requis
def api_grille_copier_jour():
    """Copie tous les blocs d'un jour vers un autre (jour destination doit être vide)."""
    data = request.get_json()
    jour_src = data.get('jour_source')
    jour_dst = data.get('jour_destination')

    if not jour_src or not jour_dst:
        return jsonify({"status": "error", "message": "Jour source et destination requis"}), 400
    if jour_src == jour_dst:
        return jsonify({"status": "error", "message": "Les jours source et destination doivent être différents"}), 400
    if jour_src not in JOURS_LABELS or jour_dst not in JOURS_LABELS:
        return jsonify({"status": "error", "message": "Jour invalide (1-7)"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    # Récupérer le label de la version active (pour rattachement correct)
    c.execute("SELECT label FROM airvs_grille_versions WHERE actif = 1 LIMIT 1")
    ver = c.fetchone()
    version_label_actif = ver['label'] if ver else 'Grille'

    c.execute("""
        SELECT id, titre FROM airvs_grille_editoriale
        WHERE jour_semaine = %s AND version_actif = 1 AND parent_id IS NULL
    """, (jour_dst,))
    if c.fetchall():
        conn.close()
        return jsonify({
            "status": "error",
            "message": f"Le {JOURS_LABELS[jour_dst]} contient déjà des blocs. Supprimez-les d'abord ou choisissez un jour vide."
        }), 409

    # INSERT...SELECT en forçant version_label à la version active
    # (les blocs source ont peut-être un version_label obsolète 'Grille')
    c.execute("""
        INSERT INTO airvs_grille_editoriale
          (version_label, version_actif, jour_semaine, heure_debut, heure_fin,
           titre, type, description, animateur, couleur, parent_id, ordre)
        SELECT %s, 1, %s, heure_debut, heure_fin, titre, type, description,
               animateur, couleur, NULL, ordre
        FROM airvs_grille_editoriale
        WHERE jour_semaine = %s AND version_actif = 1 AND parent_id IS NULL
        ORDER BY heure_debut
    """, (version_label_actif, jour_dst, jour_src))
    copied = c.rowcount
    conn.commit()
    conn.close()

    # Mise à jour de la copie locale grille.json (sécurité — voir _ge_ecrire_grille_json_local)
    _ge_ecrire_grille_json_local()

    return jsonify({
        "status": "ok",
        "message": f"{copied} bloc(s) copié(s) du {JOURS_LABELS[jour_src]} vers le {JOURS_LABELS[jour_dst]}"
    })


@grille_bp.route('/api/grille_editoriale/import', methods=['POST'])
@login_requis
def api_grille_import():
    """Importe un JSON (remplace la grille active)."""
    data = request.get_json()
    if not data or 'schedule' not in data:
        return jsonify({"status": "error", "message": "JSON invalide — clé 'schedule' manquante"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Connexion DB impossible"}), 500
    c = conn.cursor()

    # Récupérer le label de la version active (même logique que create_bloc)
    c.execute("SELECT label FROM airvs_grille_versions WHERE actif = 1 LIMIT 1")
    ver = c.fetchone()
    version_label_actif = ver['label'] if ver else 'Grille'

    c.execute("DELETE FROM airvs_grille_editoriale WHERE version_actif = 1")

    jour_reverse = {v: k for k, v in JOURS_MAP.items()}

    total = 0
    for cle_jour, jour_data in data['schedule'].items():
        jour_num = jour_reverse.get(cle_jour)
        if not jour_num:
            continue

        for bloc in jour_data.get('blocks', []):
            c.execute("""
                INSERT INTO airvs_grille_editoriale
                (version_label, version_actif, jour_semaine, heure_debut, heure_fin, titre, type, description, animateur, couleur, parent_id, ordre)
                VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
            """, (
                version_label_actif,
                jour_num,
                bloc.get('start', '06:00'),
                bloc.get('end', '23:00'),
                bloc.get('title', 'Sans titre'),
                bloc.get('type', 'focus'),
                bloc.get('description', ''),
                bloc.get('host', ''),
                bloc.get('color', '#1DB954'),
                0
            ))
            new_id = c.lastrowid
            total += 1

            for sb in bloc.get('subBlocks', []):
                c.execute("""
                    INSERT INTO airvs_grille_editoriale
                    (version_label, version_actif, jour_semaine, heure_debut, heure_fin, titre, type, description, animateur, couleur, parent_id, ordre)
                    VALUES (%s, 1, %s, %s, %s, %s, 'focus', %s, '', '', %s, %s)
                """, (
                    version_label_actif,
                    jour_num,
                    sb.get('start', '06:00'),
                    sb.get('end', '23:00'),
                    sb.get('title', ''),
                    sb.get('description', ''),
                    new_id,
                    0
                ))
                total += 1

    conn.commit()
    conn.close()

    # Mise à jour de la copie locale grille.json (sécurité — voir _ge_ecrire_grille_json_local)
    _ge_ecrire_grille_json_local()

    return jsonify({"status": "ok", "message": f"Importé : {total} bloc(s)"})
