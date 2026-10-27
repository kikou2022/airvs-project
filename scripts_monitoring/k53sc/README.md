# AIRVS Player — K53SC (192.168.1.37, stream6)

Adaptation du `airvs_player.py` du DELL (stream12) pour le K53SC Ubuntu 24.04.
Différences vs DELL:

| Élément | DELL (.30) | K53SC (.37) |
|---|---|---|
| Stream | stream12 (AAC 64k) | stream6 (MP3 128k, TTS v4) |
| Carte son ALSA | `usb_card` (USB) | `hw:CARD=PCH,DEV=0` (intégrée ALC269VB) |
| DOSSIER_RESEAU | `/home/sebastien/radio_bridge` (local .30) | `/srv/radio_bridge/monitoring/stream6` (local .37) |
| SOCKET_PATH | `/tmp/mpv_radio_socket` | `/tmp/mpv_radio_socket_k53sc` (suffixe pour coexister) |
| DB | mysql.connector 8.0.31 (Debian 12, Python 3.11) — `read_timeout: 10`, SSL actif | mysql.connector 8.0.15 (Ubuntu 24.04, Python 3.12) — `read_timeout` retiré (non supporté avant 8.0.22), `ssl_disabled: True` (Python 3.12 a remové `ssl.wrap_socket` que 8.0.15 utilise) |
| Service systemd | `airvs-radio.service` (User=sebastien) | `airvs_player_k53sc.service` (User=ubuntu) |

## Déploiement

```bash
sudo mkdir -p /opt/airvs_player
sudo chown ubuntu:ubuntu /opt/airvs_player
sudo cp airvs_player_k53sc.py /opt/airvs_player/airvs_player.py
sudo cp flux_radio_k53sc.json /opt/airvs_player/flux_radio.json
sudo cp airvs_player_k53sc.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable airvs_player_k53sc.service
sudo systemctl start airvs_player_k53sc.service
