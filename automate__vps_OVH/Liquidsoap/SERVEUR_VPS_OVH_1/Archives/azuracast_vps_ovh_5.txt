# routage de flux vers serveur icecast avant traitement de son

routage_az7 = fallback([azuracast7, blank()],
  track_sensitive=false,
  replay_metadata=false,
  id="routage_az7")

# sortie de az7 vers flux icecast
output.icecast(%mp3(bitrate=192),
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "FLUX AZURACAST_7", genre = "UNIQUE", url = "stream12",
        password = "idylle2013@SL",
        mount = "stream12", 
        mksafe(buffer(routage_az7))
    )