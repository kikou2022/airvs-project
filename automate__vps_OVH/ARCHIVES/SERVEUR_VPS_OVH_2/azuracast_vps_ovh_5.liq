# routage de flux vers serveur icecast avant traitement de son
routage_az6 = fallback([azuracast6, blank()],
  track_sensitive=false,
  replay_metadata=false,
  id="routage_az6")

routage_az7 = fallback([azuracast7, blank()],
  track_sensitive=false,
  replay_metadata=false,
  self_sync=false,
  id="routage_az7")

# sortie de az6 vers flux icecast
output.icecast(%mp3(bitrate=192),
        host = "icecast-vps-ovh2.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "FLUX AZURACAST_6", genre = "UNIQUE", url = "stream11",
        password = "idylle2013@SL",
        mount = "stream11", 
        mksafe(buffer(routage_az6))
    )
# sortie de az7 vers flux icecast
output.icecast(%mp3(bitrate=192),
        host = "icecast-vps-ovh2.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "FLUX AZURACAST_7", genre = "UNIQUE", url = "stream12",
        password = "idylle2013@SL",
        mount = "stream12", 
        mksafe(buffer(routage_az7))
    )