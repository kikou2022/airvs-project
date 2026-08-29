"""Blueprint player — routes du lecteur AIRVS, archives M3U, flux radio, proxy audience.

Contient :
  - Statut et contrôle du service airvs-radio (start/stop/restart/journal)
  - Lecture et export des archives M3U et du flux actif
  - Relance du flux via SSH (systemctl restart)
  - Proxy audience temps réel et historique (délégation au worker Ubuntu)
  - Helper générique _worker_task_proxy (insertion tâche + polling)
"""

import json
import os
import re
import time

import pymysql
import subprocess as sp
from datetime import date
from flask import Blueprint, Response, jsonify, request

from config import (
    FLUX_BRIDGE_PATH,
    DEBIAN_3_IP, DEBIAN_3_SSH_USER,
    _SUBPROCESS_CREATIONFLAGS,
)
from utils import get_db_connection, _ssh_debian3
from blueprints.auth import login_requis

player_bp = Blueprint('player', __name__)


# ═══════════════════════════════════════════
# Helpers SSH pour le radio_bridge (Debian 3)
# ═══════════════════════════════════════════

def _ssh_cat_file(remote_path):
    """Lit un fichier sur Debian 3 via SSH. Retourne le contenu ou chaîne vide."""
    rc, stdout, stderr = _ssh_debian3(f"cat '{remote_path}' 2>/dev/null")
    return stdout if rc == 0 else ""


def _ssh_file_exists(remote_path):
    """Vérifie si un fichier existe sur Debian 3 via SSH."""
    rc, stdout, _ = _ssh_debian3(f"test -f '{remote_path}' && echo EXISTS")
    return rc == 0 and "EXISTS" in stdout


def _bridge_path(filename):
    """Construit un chemin absolu pour le radio_bridge sur Debian 3."""
    return f"{FLUX_BRIDGE_PATH}/{filename}"


# ══════════════════════════════════════════
# ROUTES PONT AZURACAST / RADIODJ
# ══════════════════════════════════════════
@player_bp.route('/api/flux_status')
@login_requis
def api_flux_status():
    try:
        today_str = date.today().strftime("%Y-%m-%d")
        archive_path = _bridge_path(f"archive_{today_str}.m3u")
        active_path = _bridge_path("playlist_live_azuracast.m3u")

        archive_content = ""
        active_content = ""

        content = _ssh_cat_file(archive_path)
        if content:
            archive_content = "\n".join(
                [l.strip() for l in content.split('\n') if l.strip() and not l.startswith("#")]
            )

        content = _ssh_cat_file(active_path)
        if content:
            active_content = "\n".join(
                [l.strip() for l in content.split('\n') if l.strip() and not l.startswith("#")]
            )

        nb_archive = len(archive_content.split('\n')) if archive_content else 0
        nb_actif = len(active_content.split('\n')) if active_content else 0

        return jsonify({
            "statut": "ok",
            "nb_titres_archive": nb_archive,
            "nb_titres_actif": nb_actif,
            "archive": archive_content,
            "actif": active_content
        })
    except Exception as e:
        return jsonify({"statut": "erreur", "message": str(e)}), 500


@player_bp.route('/relancer_flux', methods=['POST'])
@login_requis
def relancer_flux():
    try:
        cmd = [
            "ssh", f"{DEBIAN_3_SSH_USER}@{DEBIAN_3_IP}",
            "sudo systemctl restart airvs-radio.service"
        ]
        result = sp.run(cmd, capture_output=True, text=True, timeout=10,
                        creationflags=_SUBPROCESS_CREATIONFLAGS)
        if result.returncode == 0:
            return jsonify({"status": "success", "message": "Redémarrage envoyé à Debian 3."})
        else:
            return jsonify({"status": "error", "message": f"Erreur SSH : {result.stderr or 'sortie vide'}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@player_bp.route('/export_archive')
@login_requis
def export_archive():
    today_str = date.today().strftime("%Y-%m-%d")
    archive_path = _bridge_path(f"archive_{today_str}.m3u")
    content = _ssh_cat_file(archive_path)
    if not content:
        return "Aucune archive disponible", 404
    return Response(
        content,
        mimetype='audio/x-mpegurl',
        headers={'Content-Disposition': f'attachment; filename=archive_{today_str}.m3u'}
    )

@player_bp.route('/export_flux_actif')
@login_requis
def export_flux_actif():
    active_path = _bridge_path("playlist_live_azuracast.m3u")
    content = _ssh_cat_file(active_path)
    if not content:
        return "Aucun flux actif disponible", 404
    return Response(
        content,
        mimetype='audio/x-mpegurl',
        headers={'Content-Disposition': 'attachment; filename=flux_actif_azuracast.m3u'}
    )

# ══════════════════════════════════════════
# ROUTES AIRVS PLAYER (Monitoring & Contrôle)
# ══════════════════════════════════════════



@player_bp.route('/api/airvs_player/status')
@login_requis
def api_airvs_player_status():
    """Retourne le statut du service airvs-radio sur Debian 3 + titre en cours + uptime."""
    rc, stdout, stderr = _ssh_debian3("systemctl show airvs-radio.service --no-pager "
                                       "--property=ActiveState,SubState,ActiveEnterTimestamp")
    status_info = {
        "service_actif": False,
        "service_statut": "inconnu",
        "service_depuis": "",
        "titre_en_cours": "",
        "erreur": ""
    }

    if rc != 0:
        status_info["erreur"] = stderr or "SSH inaccessible"
        return jsonify(status_info)

    # Parser les propriétés systemctl
    for line in stdout.split('\n'):
        line = line.strip()
        if line.startswith("ActiveState="):
            val = line.split("=", 1)[1]
            status_info["service_actif"] = (val == "active")
            status_info["service_statut"] = val
        elif line.startswith("SubState="):
            val = line.split("=", 1)[1]
            status_info["service_statut"] = val
        elif line.startswith("ActiveEnterTimestamp="):
            val = line.split("=", 1)[1]
            # Format : "Day YYYY-MM-DD HH:MM:SS TZ"
            if val and val != "":
                # Garder les derniers mots significatifs
                status_info["service_depuis"] = val

    # Récupérer le titre en cours depuis le journal systemctl (dernière ligne "Titre :")
    if status_info["service_actif"]:
        rc2, stdout2, stderr2 = _ssh_debian3(
            "journalctl -u airvs-radio.service --no-pager -n 30 --output=cat | "
            "grep 'Titre :' | tail -1"
        )
        if rc2 == 0 and stdout2:
            # Extraire le titre après "Titre :"
            titre = stdout2.replace("Titre :", "").strip()
            status_info["titre_en_cours"] = titre

    return jsonify(status_info)


@player_bp.route('/api/airvs_player/start', methods=['POST'])
@login_requis
def api_airvs_player_start():
    """Démarre le service airvs-radio sur Debian 3."""
    rc, stdout, stderr = _ssh_debian3("sudo systemctl start airvs-radio.service")
    if rc == 0:
        return jsonify({"status": "success", "message": "Service airvs-radio démarré."})
    return jsonify({"status": "error", "message": stderr or "Échec du démarrage"}), 500


@player_bp.route('/api/airvs_player/stop', methods=['POST'])
@login_requis
def api_airvs_player_stop():
    """Arrête le service airvs-radio sur Debian 3."""
    rc, stdout, stderr = _ssh_debian3("sudo systemctl stop airvs-radio.service")
    if rc == 0:
        return jsonify({"status": "success", "message": "Service airvs-radio arrêté."})
    return jsonify({"status": "error", "message": stderr or "Échec de l'arrêt"}), 500


@player_bp.route('/api/airvs_player/restart', methods=['POST'])
@login_requis
def api_airvs_player_restart():
    """Redémarre le service airvs-radio sur Debian 3."""
    rc, stdout, stderr = _ssh_debian3("sudo systemctl restart airvs-radio.service")
    if rc == 0:
        return jsonify({"status": "success", "message": "Service airvs-radio redémarré."})
    return jsonify({"status": "error", "message": stderr or "Échec du redémarrage"}), 500


@player_bp.route('/api/airvs_player/journal')
@login_requis
def api_airvs_player_journal():
    """Retourne les dernières lignes du journal du service airvs-radio."""
    lignes = request.args.get('lignes', 50, type=int)
    lignes = min(lignes, 200)  # Plafond de sécurité
    # Important : NE PAS rediriger stderr vers /dev/null — sinon on masque les
    # vraies erreurs de journalctl (droits insuffisants, unité inexistante, etc.).
    # On capture stderr séparément pour pouvoir l'afficher si stdout est vide.
    rc, stdout, stderr = _ssh_debian3(
        f"journalctl -u airvs-radio.service --no-pager -n {lignes} --output=cat"
    )
    if rc == 0:
        if stdout:
            return jsonify({"status": "ok", "journal": stdout})
        # SSH OK mais stdout vide : trois sous-cas possibles
        if stderr:
            # journalctl s'est plaint (ex: "Failed to get journal entries" / "No journal files were found")
            return jsonify({"status": "ok",
                            "journal": f"[journalctl a retourné ceci sur stderr]\n{stderr}\n"
                                       f"[stdout vide — aucun log disponible]"})
        # Ni stdout ni stderr : le service existe mais n'a encore rien écrit
        return jsonify({"status": "ok",
                        "journal": "[Aucun log disponible]\n"
                                   "Le service airvs-radio.service existe mais n'a encore rien écrit "
                                   "dans le journal. Causes possibles :\n"
                                   "  - Démarrage très récent (les premières lignes arrivent en 1-2 s)\n"
                                   "  - Service actif mais mpv n'a pas encore détecté de titre\n"
                                   "  - Service arrêté (vérifiez le statut ci-dessus)"})
    # SSH lui-même a échoué
    return jsonify({"status": "error",
                    "journal": stderr or "SSH inaccessible (Debian 3 injoignable)"})


@player_bp.route('/api/airvs_archives')
@login_requis
def api_airvs_archives():
    """Liste toutes les archives M3U disponibles dans le radio_bridge sur Debian 3 (via SSH)."""
    archives = []
    try:
        rc, stdout, stderr = _ssh_debian3(
            f"cd '{FLUX_BRIDGE_PATH}' && "
            f"for f in archive_*.m3u; do "
            f"[ -f \"$f\" ] || continue; "
            f"nb=$(grep -cv '^#\\|^$' \"$f\" 2>/dev/null || echo 0); "
            f"sz=$(stat -c %s \"$f\" 2>/dev/null || echo 0); "
            f"printf '%s|%s|%s\\n' \"$f\" \"$nb\" \"$sz\"; "
            f"done"
        )
        if rc != 0 or not stdout:
            return jsonify({"status": "ok", "archives": []})

        for line in stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.strip().split('|')
            if len(parts) != 3:
                continue
            filename, nb_lignes_str, taille_str = parts
            date_str = filename.replace("archive_", "").replace(".m3u", "")
            try:
                nb_titres = int(nb_lignes_str)
                taille_ko = round(int(taille_str) / 1024, 1)
            except ValueError:
                nb_titres = 0
                taille_ko = 0
            archives.append({
                "date": date_str,
                "fichier": filename,
                "nb_titres": nb_titres,
                "taille_ko": taille_ko
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

    archives.sort(key=lambda x: x["date"], reverse=True)
    return jsonify({"status": "ok", "archives": archives})


@player_bp.route('/api/airvs_archive/<date_str>')
@login_requis
def api_airvs_archive_date(date_str):
    """Retourne le contenu d'une archive M3U pour une date donnée (via SSH Debian 3)."""
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return jsonify({"status": "error", "message": "Format de date invalide."}), 400
    archive_path = _bridge_path(f"archive_{date_str}.m3u")
    content = _ssh_cat_file(archive_path)
    if not content:
        return jsonify({"status": "error", "message": f"Archive du {date_str} introuvable."}), 404
    lignes = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith("#")]
    return jsonify({"status": "ok", "date": date_str, "contenu": "\n".join(lignes), "nb_titres": len(lignes)})


# ══════════════════════════════════════════
# ROUTES AUDIENCE (Évolution 3 — Stats agrégées, Approche B)
# ══════════════════════════════════════════
def _audience_proxy(endpoint, timeout=15):
    """Insère une tâche AUDIENCE_GET_DATA pour le worker Ubuntu, puis attend
    (polling léger) que le worker ait écrit le résultat JSON dans
    taches_planifiees.log_resultat. Retourne directement ce JSON au frontend."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, parametres, statut) VALUES (%s, %s, 'en_attente')",
            ('AUDIENCE_GET_DATA', json.dumps({"endpoint": endpoint}))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": f"Erreur création tâche: {e}"}), 500

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            db = get_db_connection()
            cursor = db.cursor(pymysql.cursors.DictCursor)
            cursor.execute(
                "SELECT statut, log_resultat FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
            tache = cursor.fetchone()
            cursor.close()
            db.close()
        except Exception:
            continue

        if not tache:
            continue

        if tache['statut'] == 'termine' and tache['log_resultat']:
            try:
                return jsonify(json.loads(tache['log_resultat']))
            except json.JSONDecodeError:
                return jsonify({"error": "Réponse agrégateur invalide (non-JSON)"}), 502

        if tache['statut'] == 'erreur':
            return jsonify({"error": "Agrégateur injoignable — voir logs worker Ubuntu"}), 503

    return jsonify({"error": "Timeout — le worker n'a pas répondu à temps"}), 504


@player_bp.route('/api/audience/realtime')
@login_requis
def api_audience_realtime():
    return _audience_proxy('realtime')


@player_bp.route('/api/audience/history')
@login_requis
def api_audience_history():
    hours = request.args.get('hours', 24, type=int)
    hours = min(max(hours, 1), 720)
    return _audience_proxy(f'history?hours={hours}')


def _worker_task_proxy(type_action, parametres, timeout=20):
    """Insère une tâche pour le worker Ubuntu et attend son résultat JSON.
    Version générique factorisée depuis _audience_proxy — réutilisable par
    toute nouvelle tâche worker suivant le pattern insertion + polling.

    Returns: (task_id, response_body, http_status)
    """
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, parametres, statut) VALUES (%s, %s, 'en_attente')",
            (type_action, json.dumps(parametres))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()
    except Exception as e:
        return None, {"error": f"Erreur création tâche: {e}"}, 500

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            db = get_db_connection()
            cursor = db.cursor(pymysql.cursors.DictCursor)
            cursor.execute(
                "SELECT statut, log_resultat FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
            tache = cursor.fetchone()
            cursor.close()
            db.close()
        except Exception:
            continue

        if not tache:
            continue
        if tache['statut'] == 'termine' and tache['log_resultat']:
            try:
                return task_id, json.loads(tache['log_resultat']), 200
            except json.JSONDecodeError:
                return task_id, {"error": "Réponse worker invalide (non-JSON)"}, 502
        if tache['statut'] == 'erreur':
            return task_id, {"error": "Tâche en erreur — voir logs worker Ubuntu"}, 503

    return task_id, {"error": "Timeout — le worker n'a pas répondu à temps"}, 504
