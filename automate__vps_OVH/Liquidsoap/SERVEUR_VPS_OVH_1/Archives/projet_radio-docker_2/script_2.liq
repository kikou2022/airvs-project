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
MUSIQUE = "/music"

########################## SOURCES DE DIFFUSION ###############################

# We set-up an HTTP source, to maybe relay another stream.
# If you want to play the stream as soon as it appears online, remove `start=false,

flux_stream4 = input.http(id="flux_stream4", "https://icecast-vps-ovh.airvs.fr/stream4",
max_buffer=20.)
################# PROGRAMME DE SECOURS MUSICAL JOURNÉE 7/7 AVEC QUOTAS ####################

# On recharge toutes les heures le contenu des playlists, en cas d'ajout/suppression de fichiers

musique         = playlist(reload=3600, id="musique", MUSIQUE)


######################### Priorité des sources et transitions CROSSFADE 2s 
def crossfade_2s(a,b)
    faded = add(normalize=false, [
        fade.in(duration=2., type="exp", b),
        # fondu "à la main" parce que fade.out ne se déclenche qu'en fin de piste, or là on coupe
        amplify(mkfade(duration=2., start=1., stop=0., type="exp", a) ,a)
    ])
    # la durée ci-dessous est plus longue : comme à la fin du fondu le volume est nul,
    # ça peut aussi vider un éventuel buffer
    add_timeout(10., { source.skip(a) ; -1.})
    faded
end


# blank() à la fin rend la source "grille" safe, même si en pratique semaine_musical est sans fin
radio = fallback([
    flux_stream4, 
    musique, 
    blank()],
    transitions=[crossfade_2s, crossfade_2s],
  track_sensitive=false,
  replay_metadata=true,
  id="radio")

######################### GESTION DES BLANCS #######################

# s'il y a plus de 2 secondes à -60dB, on zappe à  la piste suivante ! (blank.skip au lieu de skip_blank)
radio = blank.skip(radio, max_blank=2., threshold=-60.)

################################################# SORTIES VERS DIFFÉRENTS FLUX ICECAST ###########################################

#Paramétrage des différentes sorties matérielles et logicielles
    # output.icecast(%fdkaac(channels=2,bitrate=48), 
    #     host = "icecast-vps-ovh.airvs.fr",
    #     port = 8000,
    #     name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream11",
    #     password = "idylle2013@SL",
    #     mount = "stream11", 
    #     mksafe(buffer(radio))
    # )

    output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="64k", 
    profile="aac_he_v2", afterburner=0)), 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream14",
        password = "idylle2013@SL",
        mount = "stream14", 
        mksafe(buffer(radio))
    )
        output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="48k", 
    profile="aac_he_v2", afterburner=0)), 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream15",
        password = "idylle2013@SL",
        mount = "stream15", 
        mksafe(buffer(radio))
    )