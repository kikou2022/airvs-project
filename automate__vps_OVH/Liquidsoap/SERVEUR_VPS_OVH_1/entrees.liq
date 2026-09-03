################# PRODUCTIONS VIA AZURACAST 6 ET 7 (À CONSERVER EN AUTOMATIQUE POUR ENREGISTREMENT DE LA PIGE)  ####################

# Source AzuraCast 6 — désactivée, pige migrée vers cron+ffmpeg sur VPS OVH 1 (03/09/2026)
# azuracast6 = input.http(
#   id="azuracast6", 
#   "https://azuracast.2026.airvs.fr:8000/radio.mp3",
#   max_buffer=10.,   
#   on_error=fun(msg) -> log("⚠ AZURACAST 6 HORS LIGNE : #{msg}")
# )

# Source AzuraCast 7
azuracast7 = input.http(
  id="azuracast7", 
  "https://azuracast.2026.airvs.fr:8010/radio.mp3",
  #timeout=3.0,      
  max_buffer=10.,   
  on_error=fun(msg) -> log("⚠️ AZURACAST 7 HORS LIGNE : #{msg}")
)

## Décrochage automatique DEPUIS STUDIO1 sur VPS OVH 1 ################################

studio1 = input.harbor(
  id="studio1",
  "stream9",
  port=8005,
  password="idylle2013@SL",
  user="source",
  on_connect=fun(_) -> log("🎤 STUDIO 1 connecté (harbor)"),
  on_disconnect=fun() -> log("🎤 STUDIO 1 déconnecté (harbor)")
)

# Buffer : 2s de pré-buffer, max 10s
studio1 = buffer(studio1, buffer=5., max=10.)

# NE PAS METTRE mksafe ici - on veut que studio1 soit "not ready" quand déconnecté
# Le fallback utilisera azuracast7 à la place