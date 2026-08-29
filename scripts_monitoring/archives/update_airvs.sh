#!/bin/bash
# Chemin vers le script de mise à jour
LOCAL_SCRIPT="/home/sebastien/Documents/Projets_2026/Projet AIRVS/scripts_monitoring/airvs_player.py"
# Chemin vers le script sur le serveur distant
REMOTE_PATH="sebastien@192.168.1.30:/opt/airvs_player/airvs_player.py"

echo "Mise à jour du script airvs_player.py sur le serveur distant..."
# Utilisation de scp pour copier le fichier vers le serveur distant
scp "$LOCAL_SCRIPT" "$REMOTE_PATH"

echo "Redémarrage du service airvs-radio sur le serveur distant..."
# Connexion SSH pour redémarrer le service
ssh sebastien@192.168.1.30 "sudo systemctl restart airvs-radio.service"