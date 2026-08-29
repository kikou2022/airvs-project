# FICHIER DE LOG SI BESOIN HORS LIQUIDSOAP-DAEMON
set("log.file.path","/var/log/liquidsoap/basic.log")

# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")    # Limiter à localhost
set("server.telnet.port", 1234)                # Spécifier le port (optionnel)
set("server.telnet.idle_timeout", -1)          # Désactiver le timeout d'inactivité
set("server.telnet.max_connections", 10)       # Limite de connexions simultanées

# Les dossiers ci-dessous seront surveillés par le script : pour mettre ou enlever un morceau à l'antenne
# il faut juste le copier/supprimer dans le dossier ou dans l'un des sous-dossiers.
#NUIT = "/home/ubuntu/projet_radio/Musique/BALLADS"
MUSIQUE = "/home/ubuntu/projet_radio/Musique/TOUT"
#MUSIQUE_FR = "/home/ubuntu/projet_radio/Musique/FR"

# On recharge toutes les heures le contenu des playlists, en cas d'ajout/suppression de fichiers
# Suppression des blancs de + 1 seconde

radio        = playlist(reload=3600, id="musique", MUSIQUE)


######################### GESTION DES BLANCS ET AJOUT DU BIP HORAIRE AVANT TRAITEMENT DE SON #######################

# s'il y a plus de 2 secondes à -60dB, on zappe à  la piste suivante ! (blank.skip au lieu de skip_blank)
radio = blank.skip(radio, max_blank=2., threshold=-60.)

################################################# SORTIES VERS DIFFÉRENTS FLUX ICECAST ###########################################

#Paramétrage des différentes sorties matérielles et logicielles

    output.icecast(%fdkaac(channels=2,bitrate=64), 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream12",
        password = "idylle2013@SL",
        mount = "stream12", 
        mksafe(buffer(radio))
    )
    output.icecast(%vorbis, # ou bien mp3, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_formats.html
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "INCROYABLE", url = "stream14",
        password = "idylle2013@SL",
        mount = "stream14", 
        mksafe(buffer(radio))
    )
