"""Blueprint player — routes du lecteur AIRVS, archives M3U, flux radio, proxy audience.

Contient :
  - Statut et contrôle du service monitoring (start/stop/restart/journal)
  - Lecture et export des archives M3U et du flux actif
  - Relance du flux via SSH (systemctl restart)
  - Proxy audience temps réel et historique (délégation au worker Ubuntu)
  - Helper générique _worker_task_proxy (insertion tâche + polling)

Phase 1 multi-stations (2026-10-29) :
  - Toutes les routes acceptent un paramètre optionnel <stream_id>
    (défaut = MONITORING_DEFAULT_STREAM = "stream12" pour compat ascendante)
  - Les routes sans <stream_id> continuent de fonctionner comme avant
  - Les routes avec <stream_id> (ex: /api/flux_status/stream6) utilisent
    la config correspondante depuis MONITORING_STREAMS (config.py)
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
    MONITORING_STREAMS, MONITORING_DEFAULT_STREAM, MONITORING_DEFAULT_HOST,
    _SUBPROCESS_CREATIONFLAGS,
)
from utils import get_db_connection, _ssh_debian3, _ssh_generic
from blueprints.auth import login_requis
from pathlib import Path as _Path
import json as _json
import logging as _logging2
_logging2 = _logging2.getLogger(__name__)

player_bp = Blueprint('player', __name__)


# ═══════════════════════════════════════════
# Helpers multi-stations
# ═══════════════════════════════════════════

# Phase 2 (2026-10-29) : fichier d'assignment externe (modifiable via UI)
_ASSIGNMENT_FILE = _Path(__file__).resolve().parent.parent / "monitoring_assignment.json"

def _read_assignment():
    """Lit l'assignment courant depuis monitoring_assignment.json."""
    try:
        if _ASSIGNMENT_FILE.exists():
            with open(_ASSIGNMENT_FILE, 'r', encoding='utf-8') as f:
                return _json.load(f)
    except Exception as e:
        _logging2.warning(f"Erreur lecture assignment: {e}")
    return dict(MONITORING_DEFAULT_HOST)

def _write_assignment(assignment):
    """Écrit l'assignment dans monitoring_assignment.json."""
    try:
        with open(_ASSIGNMENT_FILE, 'w', encoding='utf-8') as f:
            _json.dump(assignment, f, indent=2)
    except Exception as e:
        _logging2.error(f"Erreur écriture assignment: {e}")

def _get_stream_config(stream_id=None):
    """Retourne la config du stream donné (host assigné inclus), ou le stream par défaut."""
    sid = stream_id or MONITORING_DEFAULT_STREAM
    stream = MONITORING_STREAMS.get(sid)
    if not stream:
        return None
    # Lire l'assignment courant pour savoir quel host est actif
    assignment = _read_assignment()
    host_key = assignment.get(sid, MONITORING_DEFAULT_HOST.get(sid, "dell"))

    # Option C (2026-10-30) : si le stream est "stopped", retourner une config minimale
    if host_key == "stopped":
        return {
            "label":        stream["label"],
            "flux_url":     stream["flux_url"],
            "color":        stream["color"],
            "ssh_host":     None,
            "ssh_user":     None,
            "service_name": None,
            "m3u_dir":      None,
            "host_key":     "stopped",
        }

    host_config = stream.get("hosts", {}).get(host_key)
    if not host_config:
        return None
    # Retourner un dict plat (compatible avec le code existant)
    return {
        "label":        stream["label"],
        "flux_url":     stream["flux_url"],
        "color":        stream["color"],
        "ssh_host":     host_config["ssh_host"],
        "ssh_user":     host_config["ssh_user"],
        "service_name": host_config["service_name"],
        "m3u_dir":      host_config["m3u_dir"],
        "host_key":     host_key,
    }


def _ssh_stream(s, cmd):
    if not s.get("ssh_host"):
        return -1, "", "Stream arrêté"
    """SSH vers la machine du stream config s."""
    return _ssh_generic(s['ssh_user'], s['ssh_host'], cmd)


def _ssh_cat_file_stream(s, remote_path):
    if not s.get("ssh_host"):
        return ""
    """Lit un fichier sur la machine du stream via SSH."""
    rc, stdout, _ = _ssh_stream(s, f"cat '{remote_path}' 2>/dev/null")
    return stdout if rc == 0 else ""


def _bridge_path_stream(s, filename):
    if not s.get("m3u_dir"):
        return None
    """Construit le chemin M3U pour le stream config s."""
    return f"{s['m3u_dir']}/{filename}"


# ── Helpers legacy (compat ascendante — utilisés par les routes sans <stream_id>) ──

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
@player_bp.route('/api/flux_status/<stream_id>')
@login_requis
def api_flux_status(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"statut": "erreur", "message": f"Stream inconnu: {stream_id}"}), 404
    try:
        today_str = date.today().strftime("%Y-%m-%d")
        archive_path = _bridge_path_stream(s, f"archive_{today_str}.m3u")
        active_path = _bridge_path_stream(s, "playlist_live_azuracast.m3u")

        archive_content = ""
        active_content = ""

        content = _ssh_cat_file_stream(s, archive_path)
        if content:
            archive_content = "\n".join(
                [l.strip() for l in content.split('\n') if l.strip() and not l.startswith("#")]
            )

        content = _ssh_cat_file_stream(s, active_path)
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
@player_bp.route('/relancer_flux/<stream_id>', methods=['POST'])
@login_requis
def relancer_flux(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": f"Stream inconnu: {stream_id}"}), 404
    try:
        cmd = [
            "ssh", f"{s['ssh_user']}@{s['ssh_host']}",
            f"sudo systemctl restart {s['service_name']}"
        ]
        result = sp.run(cmd, capture_output=True, text=True, timeout=10,
                        creationflags=_SUBPROCESS_CREATIONFLAGS)
        if result.returncode == 0:
            return jsonify({"status": "success", "message": f"Redémarrage envoyé à {s['label']}."})
        else:
            return jsonify({"status": "error", "message": f"Erreur SSH : {result.stderr or 'sortie vide'}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@player_bp.route('/export_archive')
@player_bp.route('/export_archive/<stream_id>')
@login_requis
def export_archive(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return "Stream inconnu", 404
    today_str = date.today().strftime("%Y-%m-%d")
    archive_path = _bridge_path_stream(s, f"archive_{today_str}.m3u")
    content = _ssh_cat_file_stream(s, archive_path)
    if not content:
        return "Aucune archive disponible", 404
    return Response(
        content,
        mimetype='audio/x-mpegurl',
        headers={'Content-Disposition': f'attachment; filename=archive_{today_str}.m3u'}
    )

@player_bp.route('/export_flux_actif')
@player_bp.route('/export_flux_actif/<stream_id>')
@login_requis
def export_flux_actif(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return "Stream inconnu", 404
    active_path = _bridge_path_stream(s, "playlist_live_azuracast.m3u")
    content = _ssh_cat_file_stream(s, active_path)
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
@player_bp.route('/api/airvs_player/<stream_id>/status')
@login_requis
def api_airvs_player_status(stream_id=None):
    """Retourne le statut du service monitoring + titre en cours + uptime."""
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"erreur": f"Stream inconnu: {stream_id}"}), 404
    rc, stdout, stderr = _ssh_stream(s, f"systemctl show {s['service_name']} --no-pager "
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
            if val and val != "":
                status_info["service_depuis"] = val

    if status_info["service_actif"]:
        rc2, stdout2, stderr2 = _ssh_stream(s,
            f"journalctl -u {s['service_name']} --no-pager -n 30 --output=cat | "
            "grep 'Titre :' | tail -1"
        )
        if rc2 == 0 and stdout2:
            titre = stdout2.replace("Titre :", "").strip()
            status_info["titre_en_cours"] = titre

    return jsonify(status_info)


@player_bp.route('/api/airvs_player/start', methods=['POST'])
@player_bp.route('/api/airvs_player/<stream_id>/start', methods=['POST'])
@login_requis
def api_airvs_player_start(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": f"Stream inconnu: {stream_id}"}), 404
    rc, stdout, stderr = _ssh_stream(s, f"sudo systemctl start {s['service_name']}")
    if rc == 0:
        return jsonify({"status": "success", "message": f"Service {s['service_name']} démarré."})
    return jsonify({"status": "error", "message": stderr or "Échec du démarrage"}), 500


@player_bp.route('/api/airvs_player/stop', methods=['POST'])
@player_bp.route('/api/airvs_player/<stream_id>/stop', methods=['POST'])
@login_requis
def api_airvs_player_stop(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": f"Stream inconnu: {stream_id}"}), 404
    rc, stdout, stderr = _ssh_stream(s, f"sudo systemctl stop {s['service_name']}")
    if rc == 0:
        return jsonify({"status": "success", "message": f"Service {s['service_name']} arrêté."})
    return jsonify({"status": "error", "message": stderr or "Échec de l'arrêt"}), 500


@player_bp.route('/api/airvs_player/restart', methods=['POST'])
@player_bp.route('/api/airvs_player/<stream_id>/restart', methods=['POST'])
@login_requis
def api_airvs_player_restart(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": f"Stream inconnu: {stream_id}"}), 404
    rc, stdout, stderr = _ssh_stream(s, f"sudo systemctl restart {s['service_name']}")
    if rc == 0:
        return jsonify({"status": "success", "message": f"Service {s['service_name']} redémarré."})
    return jsonify({"status": "error", "message": stderr or "Échec du redémarrage"}), 500


@player_bp.route('/api/airvs_player/journal')
@player_bp.route('/api/airvs_player/<stream_id>/journal')
@login_requis
def api_airvs_player_journal(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "journal": f"Stream inconnu: {stream_id}"}), 404
    lignes = request.args.get('lignes', 50, type=int)
    lignes = min(lignes, 200)
    rc, stdout, stderr = _ssh_stream(s,
        f"journalctl -u {s['service_name']} --no-pager -n {lignes} --output=cat"
    )
    if rc == 0:
        if stdout:
            return jsonify({"status": "ok", "journal": stdout})
        if stderr:
            return jsonify({"status": "ok",
                            "journal": f"[journalctl a retourné ceci sur stderr]\n{stderr}\n"
                                       f"[stdout vide — aucun log disponible]"})
        return jsonify({"status": "ok",
                        "journal": "[Aucun log disponible]\n"
                                   f"Le service {s['service_name']} existe mais n'a encore rien écrit "
                                   "dans le journal."})
    return jsonify({"status": "error",
                    "journal": stderr or "SSH inaccessible"})


@player_bp.route('/api/airvs_archives')
@player_bp.route('/api/airvs_archives/<stream_id>')
@login_requis
def api_airvs_archives(stream_id=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": f"Stream inconnu: {stream_id}"}), 404
    archives = []
    try:
        rc, stdout, stderr = _ssh_stream(s,
            f"cd '{s['m3u_dir']}' && "
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
@player_bp.route('/api/airvs_archive/<stream_id>/<date_str>')
@login_requis
def api_airvs_archive_date(stream_id=None, date_str=None):
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": f"Stream inconnu: {stream_id}"}), 404
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return jsonify({"status": "error", "message": "Format de date invalide."}), 400
    archive_path = _bridge_path_stream(s, f"archive_{date_str}.m3u")
    content = _ssh_cat_file_stream(s, archive_path)
    if not content:
        return jsonify({"status": "error", "message": f"Archive du {date_str} introuvable."}), 404
    lignes = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith("#")]
    return jsonify({"status": "ok", "date": date_str, "contenu": "\n".join(lignes), "nb_titres": len(lignes)})


# ══════════════════════════════════════════
# ROUTES MONITORING ASSIGNMENT (Phase 2 — bascule dynamique)
# ══════════════════════════════════════════

@player_bp.route('/api/monitoring/assignment')
@login_requis
def api_monitoring_assignment():
    """Retourne l'assignment courant + les hosts disponibles pour chaque stream."""
    assignment = _read_assignment()
    result = {}
    for sid, stream in MONITORING_STREAMS.items():
        host_key = assignment.get(sid, MONITORING_DEFAULT_HOST.get(sid, "dell"))
        result[sid] = {
            "label":           stream["label"],
            "assigned_to":     host_key,
            "available_hosts": list(stream.get("hosts", {}).keys()),
        }
    return jsonify({"status": "ok", "assignment": result})


@player_bp.route('/api/monitoring/switch/<stream_id>/<host_key>', methods=['POST'])
@login_requis
def api_monitoring_switch(stream_id, host_key):
    """Bascule le monitoring d'un stream vers un autre host (Option C : stop-and-replace).

    Si host_key == "stopped" : stoppe le service courant, ne démarre rien.
    Si host_key est un host réel :
      1. Stoppe le service courant du stream (sur son ancien host)
      2. Stoppe tout autre stream déjà actif sur le host cible (conflit carte son ALSA)
      3. Démarre le nouveau service sur le host cible
      4. Update assignment.json
    """
    stream = MONITORING_STREAMS.get(stream_id)
    if not stream:
        return jsonify({"status": "error", "message": f"Stream inconnu: {stream_id}"}), 404

    assignment = _read_assignment()
    old_host_key = assignment.get(stream_id, MONITORING_DEFAULT_HOST.get(stream_id, "dell"))

    if old_host_key == host_key:
        return jsonify({"status": "ok", "message": f"Déjà assigné à {host_key}", "changed": False})

    stopped_streams = []

    # --- Cas 1 : host_key == "stopped" → juste arrêter ---
    if host_key == "stopped":
        old_host = stream.get("hosts", {}).get(old_host_key)
        if old_host:
            _ssh_generic(old_host["ssh_user"], old_host["ssh_host"],
                         f"sudo systemctl stop {old_host['service_name']}")
        assignment[stream_id] = "stopped"
        _write_assignment(assignment)
        return jsonify({
            "status": "success",
            "message": f"Monitoring de {stream_id} arrêté (était sur {old_host_key})",
            "changed": True, "old_host": old_host_key, "new_host": "stopped",
            "stopped_streams": [],
        })

    # --- Cas 2 : host_key est un host réel ---
    new_host = stream.get("hosts", {}).get(host_key)
    if not new_host:
        return jsonify({"status": "error", "message": f"Hôte inconnu: {host_key}"}), 404

    # 1. Stop ancien service du stream
    old_host = stream.get("hosts", {}).get(old_host_key)
    if old_host:
        _ssh_generic(old_host["ssh_user"], old_host["ssh_host"],
                     f"sudo systemctl stop {old_host['service_name']}")

    # 2. Option C : stopper tout autre stream déjà actif sur le host cible
    for other_sid, other_host_key in list(assignment.items()):
        if other_sid == stream_id or other_host_key == "stopped":
            continue
        if other_host_key == host_key:
            other_stream = MONITORING_STREAMS.get(other_sid)
            if other_stream:
                other_host_cfg = other_stream.get("hosts", {}).get(host_key)
                if other_host_cfg:
                    _ssh_generic(other_host_cfg["ssh_user"], other_host_cfg["ssh_host"],
                                 f"sudo systemctl stop {other_host_cfg['service_name']}")
                    assignment[other_sid] = "stopped"
                    stopped_streams.append(other_sid)

    # 3. Start nouveau service
    rc, stdout, stderr = _ssh_generic(new_host["ssh_user"], new_host["ssh_host"],
                                       f"sudo systemctl start {new_host['service_name']}")
    if rc != 0:
        # Rollback : redémarrer l'ancien service
        if old_host:
            _ssh_generic(old_host["ssh_user"], old_host["ssh_host"],
                         f"sudo systemctl start {old_host['service_name']}")
        return jsonify({"status": "error",
                        "message": f"Échec démarrage sur {host_key}: {stderr or stdout}"}), 500

    # 4. Update assignment.json
    assignment[stream_id] = host_key
    _write_assignment(assignment)

    msg = f"Monitoring de {stream_id} basculé de {old_host_key} vers {host_key}"
    if stopped_streams:
        msg += f" — streams arrêtés sur {host_key}: {', '.join(stopped_streams)}"

    return jsonify({
        "status": "success",
        "message": msg,
        "changed": True,
        "old_host": old_host_key,
        "new_host": host_key,
        "stopped_streams": stopped_streams,
    })


# ═══════════════════════════════════════════════════════════════
# Volume ALSA (Option 1 — hardware volume)
# ═══════════════════════════════════════════════════════════════

def _detect_mixer_control(ssh_user, ssh_host):
    """Détecte le control mixer disponible (Master, Speaker, PCM)."""
    for control in ['Master', 'Speaker', 'PCM']:
        rc, stdout, _ = _ssh_generic(ssh_user, ssh_host,
                                      f"amixer -c 0 sget {control} 2>/dev/null")
        if rc == 0 and stdout and 'Playback' in stdout:
            return control
    return None


def _get_volume_alsa(s):
    """Lit le volume ALSA sur la machine assignée."""
    if not s.get("ssh_host"):
        return None
    control = _detect_mixer_control(s["ssh_user"], s["ssh_host"])
    if not control:
        return None
    rc, stdout, _ = _ssh_generic(s["ssh_user"], s["ssh_host"],
                                  f"amixer -c 0 sget {control}")
    if rc != 0:
        return None
    import re
    match = re.search(r'\[(\d+)%\]', stdout)
    return int(match.group(1)) if match else None


def _set_volume_alsa(s, volume):
    """Set le volume ALSA sur la machine assignée."""
    if not s.get("ssh_host"):
        return False, "Stream arrêté — pas de machine cible"
    control = _detect_mixer_control(s["ssh_user"], s["ssh_host"])
    if not control:
        return False, "Aucun control mixer trouvé"
    volume = max(0, min(100, int(volume)))
    rc, _, stderr = _ssh_generic(s["ssh_user"], s["ssh_host"],
                                  f"amixer -c 0 sset {control} {volume}%")
    if rc != 0:
        return False, stderr or "Échec réglage volume"
    return True, f"Volume réglé à {volume}% (control: {control})"


@player_bp.route('/api/monitoring/volume/<stream_id>')
@login_requis
def api_monitoring_volume_get(stream_id):
    """Lit le volume ALSA du stream assigné."""
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": "Stream inconnu"}), 404
    if s.get("host_key") == "stopped":
        return jsonify({"status": "ok", "volume": None, "message": "Stream arrêté"})
    vol = _get_volume_alsa(s)
    if vol is None:
        return jsonify({"status": "error", "message": "Impossible de lire le volume"}), 500
    return jsonify({"status": "ok", "volume": vol})


@player_bp.route('/api/monitoring/volume/<stream_id>', methods=['POST'])
@login_requis
def api_monitoring_volume_set(stream_id):
    """Set le volume ALSA du stream assigné."""
    s = _get_stream_config(stream_id)
    if not s:
        return jsonify({"status": "error", "message": "Stream inconnu"}), 404
    if s.get("host_key") == "stopped":
        return jsonify({"status": "error", "message": "Stream arrêté — volume indisponible"}), 400
    data = request.json or {}
    volume = data.get('volume')
    if volume is None:
        return jsonify({"status": "error", "message": "Paramètre 'volume' requis (0-100)"}), 400
    ok, msg = _set_volume_alsa(s, volume)
    if ok:
        return jsonify({"status": "ok", "volume": int(volume), "message": msg})
    return jsonify({"status": "error", "message": msg}), 500


# ══════════════════════════════════════════
# ROUTES AUDIENCE (Évolution 3 — non modifiées, pas stream-specific)
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
