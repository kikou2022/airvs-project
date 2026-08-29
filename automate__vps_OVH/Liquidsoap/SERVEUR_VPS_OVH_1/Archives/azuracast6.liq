# routage de flux vers serveur icecast avant traitement de son
routage_az6 = fallback([azuracast6, blank()],
  track_sensitive=false,
  replay_metadata=false,
  id="routage_az6")

# sortie vers flux icecast
output.icecast(%mp3(bitrate=192),
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "UNIQUE", url = "stream11",
        password = "idylle2013@SL",
        mount = "stream11", 
        mksafe(buffer(routage_az6))
    )
