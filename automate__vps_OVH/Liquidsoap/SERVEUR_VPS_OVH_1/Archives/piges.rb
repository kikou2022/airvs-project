# Réglages de Liquidsoap :
settings.log.file.set(true)
settings.log.file.path.set("/home/sebastien/Musique/Projet_Radio/pige.log")


# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")    # Limiter à localhost
set("server.telnet.port", 1234)                # Spécifier le port (optionnel)
set("server.telnet.idle_timeout", -1)          # Désactiver le timeout d'inactivité
set("server.telnet.max_connections", 10)       # Limite de connexions simultanées


# Sélection du flux à enregistrer
flux = input.http(id="flux", "https://azuracast2.airvs.fr/listen/airvs_principal/radio.mp3",
max_buffer=5.)

# Sélection de l'entrée sonore
entree = input.alsa(device='hw:1,0')

# blank() à la fin rend la source "mix" safe
mix = fallback([flux, entree, blank()],
  #transitions=[crossfade_2s, crossfade_2s],
  track_sensitive=false,
  replay_metadata=false,
  id="mix")

# Les formats de sortie du fichier audio sont définis avec un %, par exemple :
# %flac
# %wav
# %mp3(bitrate=192)
# %mp3.vbr(quality=2, samplerate=48000)

# Création du fichier audio de sortie
output.file(%mp3(bitrate=128),
    { time.string("home/sebastien/Musique/Projet_Radio/pige/%Y-%m-%d/%Hh%M_%S_%z.mp3") },
    #mix,
    mksafe(mix),
    reopen_when = { 0m }
)


