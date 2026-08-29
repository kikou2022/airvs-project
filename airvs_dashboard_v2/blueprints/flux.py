"""blueprints/flux.py -- Configuration des flux radio et proxy stream Icecast.

Routes extraites de app.py pour le blueprint flux_bp.

Gestion de flux_radio.json (CRUD, validation, synchronisation vers Debian 3)
et proxy du flux Icecast via SSH+curl sur Debian 3.

Routes :
  - GET  /api/flux_config
  - GET  /api/flux_config/validate_remote
  - POST /api/flux_config/sync_remote
  - POST /api/flux_config
  - POST /api/flux_config/add
  - POST /api/flux_config/remove
  - GET  /api/flux_cors_check
  - GET  /api/stream_proxy/<flux_id>
"""

import os
import json
import subprocess as sp

from flask import Blueprint, request, jsonify, Response

from config import (
    FLUX_CONFIG_PATH,
    DEBIAN_3_IP, DEBIAN_3_SSH_USER,
    _SUBPROCESS_CREATIONFLAGS,
)
from utils import get_db_connection, _ssh_debian3
from blueprints.auth import login_requis


flux_bp = Blueprint('flux_bp', __name__)


# ══════════════════════════════════════════
# ROUTES CONFIG FLUX RADIO (flux_radio.json)
# ══════════════════════════════════════════
@flux_bp.route('/api/flux_config', methods=['GET'])
@login_requis
def api_flux_config():
    """Retourne la configuration actuelle des flux radio.
    Si le fichier n'existe pas localement, crée un flux par défaut (stream11)."""
    try:
        if not os.path.exists(FLUX_CONFIG_PATH):
            # Auto-création avec un flux par défaut pour éviter le dropdown vide
            config_defaut = {
                "flux_actif": "stream11",
                "flux_disponibles": [
                    {
                        "id": "stream11",
                        "nom": "AIRVS Principal",
                        "url": "https://icecast-vps-ovh.airvs.fr/stream11",
                        "codec": "AAC HE-AAC v2",
                        "bitrate": 48,
                        "detail": "libfdk_aac / aac_he_v2 / 44100Hz / Stéréo"
                    }
                ]
            }
            with open(FLUX_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(config_defaut, f, indent=2, ensure_ascii=False)
            print(f"[FLUX_CONFIG] Fichier {FLUX_CONFIG_PATH} créé avec flux par défaut (stream11).")
            return jsonify({"status": "ok", "config": config_defaut, "auto_created": True})

        with open(FLUX_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return jsonify({"status": "ok", "config": config})
    except json.JSONDecodeError as e:
        # JSON corrompu — retourner le détail de l'erreur pour diagnostic
        return jsonify({
            "status": "error",
            "message": f"JSON invalide dans flux_radio.json : {e}",
            "error_type": "json_decode",
            "line": e.lineno,
            "column": e.colno,
            "suggestion": "Corrigez le fichier flux_radio.json — virgules manquantes entre les objets ?"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@flux_bp.route('/api/flux_config/validate_remote')
@login_requis
def api_flux_config_validate_remote():
    """Valide le JSON flux_radio.json sur Debian 3 (celui réellement lu par le player)."""
    rc, stdout, stderr = _ssh_debian3(
        "python3 -c \"import json; json.load(open('/opt/airvs_player/flux_radio.json')); "
        "print('JSON_VALIDE')\" 2>&1"
    )
    if rc == 0 and 'JSON_VALIDE' in stdout:
        return jsonify({
            "status": "ok",
            "valide": True,
            "message": "Le fichier /opt/airvs_player/flux_radio.json est un JSON valide ✅"
        })
    return jsonify({
        "status": "ok",
        "valide": False,
        "message": f"❌ JSON invalide sur Debian 3 : {stdout or stderr}",
        "suggestion": "Vérifiez les virgules entre les objets du tableau flux_disponibles."
    })


@flux_bp.route('/api/flux_config/sync_remote', methods=['POST'])
@login_requis
def api_flux_config_sync_remote():
    """Copie le flux_radio.json local (Windows) vers Debian 3 via SCP, puis redémarre le service."""
    # Note : subprocess est déjà importé en haut du fichier sous l'alias sp.
    # On utilise sp.run() avec creationflags=_SUBPROCESS_CREATIONFLAGS pour
    # masquer la fenêtre console sous Windows (anti-flash).

    if not os.path.exists(FLUX_CONFIG_PATH):
        return jsonify({"status": "error", "message": "Aucun flux_radio.json local à synchroniser."}), 404

    try:
        # 1. Copier le fichier via SCP
        result = sp.run(
            ["scp", FLUX_CONFIG_PATH, f"{DEBIAN_3_SSH_USER}@{DEBIAN_3_IP}:/opt/airvs_player/flux_radio.json"],
            capture_output=True, text=True, timeout=15,
            creationflags=_SUBPROCESS_CREATIONFLAGS
        )
        if result.returncode != 0:
            return jsonify({
                "status": "error",
                "message": f"Échec SCP : {result.stderr or 'sortie vide'}"
            }), 500

        # 2. Valider le JSON sur Debian 3
        rc, stdout, stderr = _ssh_debian3(
            "python3 -c \"import json; json.load(open('/opt/airvs_player/flux_radio.json'))\" 2>&1"
        )
        if rc != 0:
            return jsonify({
                "status": "error",
                "message": f"Fichier copié mais JSON invalide sur Debian 3 : {stdout}"
            }), 500

        # 3. Redémarrer le service
        rc2, stdout2, stderr2 = _ssh_debian3("sudo systemctl restart airvs-radio.service")
        restart_msg = "Service redémarré ✅" if rc2 == 0 else f"⚠️ Redémarrage échoué : {stderr2}"

        return jsonify({
            "status": "ok",
            "message": f"flux_radio.json synchronisé vers Debian 3. {restart_msg}"
        })
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "Timeout SCP (15s)"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@flux_bp.route('/api/flux_config', methods=['POST'])
@login_requis
def api_flux_config_update():
    """Met à jour le flux actif dans la configuration. Le player sera redémarré automatiquement."""
    data = request.get_json(force=True)
    flux_id = data.get('flux_actif', '').strip()
    if not flux_id:
        return jsonify({"status": "error", "message": "ID de flux vide."}), 400

    try:
        # Lire la config actuelle
        config = {"flux_actif": "", "flux_disponibles": []}
        if os.path.exists(FLUX_CONFIG_PATH):
            with open(FLUX_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

        # Vérifier que le flux_id existe dans la liste
        ids_disponibles = [f.get('id') for f in config.get('flux_disponibles', [])]
        if flux_id not in ids_disponibles:
            return jsonify({"status": "error",
                            "message": f"Flux '{flux_id}' non trouvé. Disponibles : {', '.join(ids_disponibles)}"}), 400

        # Mettre à jour le flux actif
        config['flux_actif'] = flux_id

        with open(FLUX_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        # Redémarrer le service sur Debian 3 pour prendre en compte le changement
        restart_msg = ""
        rc, stdout, stderr = _ssh_debian3("sudo systemctl restart airvs-radio.service")
        if rc == 0:
            restart_msg = "Service redémarré."
        else:
            restart_msg = f"Attention : redémarrage échoué ({stderr})."

        return jsonify({"status": "ok",
                        "message": f"Flux actif changé vers '{flux_id}'. {restart_msg}",
                        "config": config})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@flux_bp.route('/api/flux_config/add', methods=['POST'])
@login_requis
def api_flux_config_add():
    """Ajoute un nouveau flux à la liste des flux disponibles."""
    data = request.get_json(force=True)
    flux_id = data.get('id', '').strip()
    flux_nom = data.get('nom', '').strip()
    flux_url = data.get('url', '').strip()
    flux_codec = data.get('codec', 'AAC HE-AAC v2').strip()
    flux_bitrate = data.get('bitrate', 0)
    flux_detail = data.get('detail', '').strip()

    if not flux_id or not flux_nom or not flux_url:
        return jsonify({"status": "error", "message": "ID, nom et URL sont requis."}), 400

    try:
        config = {"flux_actif": "", "flux_disponibles": []}
        if os.path.exists(FLUX_CONFIG_PATH):
            with open(FLUX_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

        # Vérifier que l'ID n'existe pas déjà
        ids_existants = [f.get('id') for f in config.get('flux_disponibles', [])]
        if flux_id in ids_existants:
            return jsonify({"status": "error", "message": f"Le flux '{flux_id}' existe déjà."}), 400

        nouveau_flux = {
            "id": flux_id,
            "nom": flux_nom,
            "url": flux_url,
            "codec": flux_codec
        }
        if flux_bitrate:
            nouveau_flux["bitrate"] = int(flux_bitrate)
        if flux_detail:
            nouveau_flux["detail"] = flux_detail

        config['flux_disponibles'].append(nouveau_flux)

        # Si c'est le premier flux, le définir comme actif
        if not config['flux_actif'] and len(config['flux_disponibles']) == 1:
            config['flux_actif'] = flux_id

        with open(FLUX_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return jsonify({"status": "ok", "message": f"Flux '{flux_nom}' ajouté.", "config": config})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@flux_bp.route('/api/flux_config/remove', methods=['POST'])
@login_requis
def api_flux_config_remove():
    """Supprime un flux de la liste des flux disponibles."""
    data = request.get_json(force=True)
    flux_id = data.get('id', '').strip()
    if not flux_id:
        return jsonify({"status": "error", "message": "ID de flux requis."}), 400

    try:
        config = {"flux_actif": "", "flux_disponibles": []}
        if os.path.exists(FLUX_CONFIG_PATH):
            with open(FLUX_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

        anciens = config.get('flux_disponibles', [])
        nouveaux = [f for f in anciens if f.get('id') != flux_id]
        if len(nouveaux) == len(anciens):
            return jsonify({"status": "error", "message": f"Flux '{flux_id}' non trouvé."}), 404

        config['flux_disponibles'] = nouveaux

        # Si le flux supprimé était le flux actif, basculer vers le premier disponible
        if config['flux_actif'] == flux_id:
            config['flux_actif'] = nouveaux[0]['id'] if nouveaux else ''

        with open(FLUX_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return jsonify({"status": "ok", "message": f"Flux '{flux_id}' supprimé.", "config": config})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@flux_bp.route('/api/flux_cors_check')
@login_requis
def api_flux_cors_check():
    """Vérifie les headers CORS du flux Icecast via SSH Debian 3 (qui a internet)."""
    flux_id = request.args.get('flux', '').strip()
    if not flux_id:
        return jsonify({"status": "error", "message": "Paramètre 'flux' requis."}), 400

    # Trouver l'URL du flux dans la config
    try:
        config = {"flux_actif": "", "flux_disponibles": []}
        if os.path.exists(FLUX_CONFIG_PATH):
            with open(FLUX_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

        flux_url = None
        for f in config.get('flux_disponibles', []):
            if f.get('id') == flux_id:
                flux_url = f.get('url')
                break

        if not flux_url:
            return jsonify({"status": "error", "message": f"Flux '{flux_id}' non trouvé dans la config."}), 404

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur lecture config : {e}"}), 500

    # Vérifier CORS via SSH sur Debian 3 (qui a accès internet)
    rc, stdout, stderr = _ssh_debian3(f"curl -sI '{flux_url}' 2>/dev/null | head -20")
    if rc != 0:
        return jsonify({"status": "error", "message": f"SSH inaccessible : {stderr}"})

    headers_raw = stdout
    has_cors = 'access-control-allow-origin' in headers_raw.lower()
    cors_header_value = ""
    content_type = ""

    for line in headers_raw.split('\n'):
        line_lower = line.lower().strip()
        if line_lower.startswith('access-control-allow-origin'):
            cors_header_value = line.split(':', 1)[1].strip() if ':' in line else ""
        if line_lower.startswith('content-type'):
            content_type = line.split(':', 1)[1].strip() if ':' in line else ""

    return jsonify({
        "status": "ok",
        "flux_id": flux_id,
        "url": flux_url,
        "cors_ok": has_cors,
        "cors_header": cors_header_value,
        "content_type": content_type,
        "headers_raw": headers_raw,
        "message": "CORS autorisé ✓" if has_cors else "CORS NON configuré ✗ — Ajoutez <http-headers> dans icecast.xml"
    })


# ══════════════════════════════════════════
# PROXY STREAM ICECAST (via Debian 3 → VPS OVH)
# ══════════════════════════════════════════
# Le serveur Flask tourne sur Windows (pas d'internet).
# Pour proxy le flux Icecast vers le navigateur, on passe par
# Debian 3 (192.168.1.30) qui a internet et peut atteindre le VPS OVH.
# On utilise SSH + curl pour streamer le flux chunk-by-chunk.

@flux_bp.route('/api/stream_proxy/<flux_id>')
@login_requis
def api_stream_proxy(flux_id):
    """Proxy le flux Icecast vers le navigateur via Debian 3 (qui a internet).
    Le flux est streamé chunk-by-chunk via SSH+curl pour ne pas saturer la mémoire."""

    # Trouver l'URL du flux dans la config
    try:
        config = {"flux_actif": "", "flux_disponibles": []}
        if os.path.exists(FLUX_CONFIG_PATH):
            with open(FLUX_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

        flux_url = None
        flux_codec = "audio/aac"
        for fl in config.get('flux_disponibles', []):
            if fl.get('id') == flux_id:
                flux_url = fl.get('url')
                codec = fl.get('codec', '').upper()
                if 'MP3' in codec:
                    flux_codec = 'audio/mpeg'
                elif 'OGG' in codec and 'OPUS' in codec:
                    flux_codec = 'audio/ogg'
                elif 'OGG' in codec:
                    flux_codec = 'audio/ogg'
                elif 'FLAC' in codec:
                    flux_codec = 'audio/flac'
                else:
                    flux_codec = 'audio/aac'  # AAC par défaut (HE-AAC v2 inclus)
                break

        if not flux_url:
            return jsonify({"status": "error", "message": f"Flux '{flux_id}' non trouvé."}), 404

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur lecture config : {e}"}), 500

    # Lancer curl via SSH vers Debian 3 (qui a accès internet)
    try:
        process = sp.Popen(
            [
                "ssh", f"{DEBIAN_3_SSH_USER}@{DEBIAN_3_IP}",
                f"curl -sN --max-time 30 -H 'Icy-MetaData: 1' '{flux_url}'"
            ],
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            bufsize=0,  # Pas de buffering pour le streaming
            creationflags=_SUBPROCESS_CREATIONFLAGS  # Masque la fenêtre console sous Windows
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"Impossible de lancer SSH : {e}"}), 502

    def generate():
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        except Exception:
            pass
        finally:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                pass

    return Response(
        generate(),
        mimetype=flux_codec,
        headers={
            'Cache-Control': 'no-cache, no-store',
            'Access-Control-Allow-Origin': '*',
            'X-Proxy-Source': 'AIRVS-Dashboard-via-Debian3',
            'X-Stream-Codec': flux_codec
        }
    )
