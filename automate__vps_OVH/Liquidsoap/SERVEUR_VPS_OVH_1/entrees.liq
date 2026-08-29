################# PRODUCTIONS VIA AZURACAST 6 ET 7 (À CONSERVER EN AUTOMATIQUE POUR ENREGISTREMENT DE LA PIGE)  ####################

# Source AzuraCast 6
azuracast6 = input.http(
  id="azuracast6", 
  "https://azuracast.2026.airvs.fr:8000/radio.mp3",
  #timeout=3.0,      
  max_buffer=10.,   
  on_error=fun(msg) -> log("⚠️ AZURACAST 6 HORS LIGNE : #{msg}")
)

# Source AzuraCast 7
azuracast7 = input.http(
  id="azuracast7", 
  "https://azuracast.2026.airvs.fr:8010/radio.mp3",
  #timeout=3.0,      
  max_buffer=10.,   
  on_error=fun(msg) -> log("⚠️ AZURACAST 7 HORS LIGNE : #{msg}")
)

## Décrochage manuel DEPUIS STUDIO1 sur VPS OVH 1 ################################

studio1 = input.http(
  id="studio1", 
  "https://icecast-vps-ovh.airvs.fr/stream9", 
  #start=false,
  #timeout=3.0,      
  max_buffer=15.,   
  on_error=fun(msg) -> log("⚠️ STUDIO 1 HORS LIGNE : #{msg}")
)
