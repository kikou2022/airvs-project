#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 24.04 ####

#### TRAITEMENT DE SON 1.0 (Décembre 2024) EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022

#### Version 1.4.0 du 07/07/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés


#### Décrochage automatique DEPUIS AZURACAST DISTANT SUR VPS OVH2 ET OVH3 (azuracast4 sur VPS OVH3 EN PRIORITÉ)

# FICHIER DE LOG SI BESOIN HORS LIQUIDSOAP-DAEMON
set("log.file.path","/tmp/basic-radio_quotas.log")

# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")    # Limiter à localhost
set("server.telnet.port", 1234)                # Spécifier le port (optionnel)
set("server.telnet.idle_timeout", -1)          # Désactiver le timeout d'inactivité
set("server.telnet.max_connections", 10)       # Limite de connexions simultanées

clock = single("/home/ubuntu/projet_radio/Jingles/HORAIRE/BEEP.mp3")

# Playliste musicale
musique = playlist(reload=3600, "/home/ubuntu/projet_radio/Musique")

# Remote URL 
azuracast2 = mksafe(buffer(buffer=1.,input.http(id="azuracast2", "https://azuracast.airvs.site:8000/radio.mp3")))
azuracast3 = mksafe(buffer(buffer=1.,input.http(id="azuracast3", "https://azuracast.airvs.fr:8000/radio.mp3")))

# Remote URL Schedule Switches Advanced
grille = switch([({0h-7h}, azuracast3), ({7h-24h}, azuracast2)])

# ajout du bip horaire au début de chaque heure
radio = add([grille, switch([({ 0m0s }, clock)])])

##################### SORTIES ET DIFFUSIONS ###########################################

#Paramétrage des différentes sorties matérielles et logicielles

    #output.alsa(radio, id="alsa")

    output.icecast(%mp3(bitrate=128),
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream5",
        password = "idylle2013@SL",
        mount = "stream5", 
        mksafe(buffer(radio))
    )

