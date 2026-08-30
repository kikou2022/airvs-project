################################################# SORTIES VERS DIFFÉRENTS FLUX ICECAST ###########################################

#Paramétrage des différentes sorties matérielles et logicielles

# =============================================================================
# SORTIES FLUX PRINCIPAL AIRVS (radio) — inchangées
# =============================================================================
    output.icecast(%vorbis, 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "INCROYABLE", url = "stream3",
        password = "idylle2013@SL",
        mount = "stream3", 
        mksafe(buffer(radio))
    )
    output.icecast(%mp3(bitrate=128), 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream2",
        password = "idylle2013@SL",
        mount = "stream2", 
        mksafe(buffer(radio))
    )
    output.icecast(%mp3(bitrate=320), # stream haute qualité pour diffusion en AAC via script liquidsoap en docker
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "UNIQUE", url = "stream4",
        password = "idylle2013@SL",
        mount = "stream4", 
        mksafe(buffer(radio))
    )

# =============================================================================
# SORTIE FLUX STATION 6 INDEPENDANT (radio_az6)
# =============================================================================
# Flux Station 6 traité par process_airvs_audio(), sorti sur son propre mount
# fallible=true : le mount /stream6 se met en pause si azuracast6 est down (mercredi 6h-23h uniquement)
# start=false : ne tente pas de se connecter au démarrage du script (station 6 pas encore active)
# mksafe : garantit que le source ne crashe pas le script si azuracast6 est absent
    output.icecast(%mp3(bitrate=320),
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS Station 6",
        description = "Flux independant Station 6 — traite par TTS v4",
        genre = "INCROYABLE",
        url = "stream6",
        password = "idylle2013@SL",
        mount = "stream6",
        fallible = true,
        start = false,
        mksafe(buffer(radio_az6))
    )
