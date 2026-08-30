################################################# SORTIES VERS DIFFÉRENTS FLUX ICECAST ###########################################

#Paramétrage des différentes sorties matérielles et logicielles

    # output.icecast(%vorbis, 
    #     host = "icecast-vps-ovh.airvs.fr",
    #     port = 8000,
    #     name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "INCROYABLE", url = "stream3",
    #     password = "idylle2013@SL",
    #     mount = "stream3", 
    #     mksafe(buffer(radio))
    # )
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

