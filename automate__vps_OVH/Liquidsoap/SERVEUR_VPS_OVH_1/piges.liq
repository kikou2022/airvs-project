# Enregistrement local d'une pige mp3 chaque heure pleine dans /var/www/pige/azuracast5 pour différents flux azuracast
# (à adapter selon le nom du flux azuracast)
# (à adapter selon le répertoire de stockage des piges)
# (à adapter selon le bitrate souhaité)

## PIGE AZURACAST 6
 pige_az6 = fallback([azuracast6, blank()], 
  track_sensitive=false,
  replay_metadata=false,
  id="pige_az6")

# Enregistrer le flux azuracast6 en mp3 96kbps avec timestamp
    output.file(%mp3(bitrate=96),
     { time.string("/var/www/pige/azuracast6/%Y-%m-%d/%Hh%M_%S_%z.mp3") },
     mksafe(pige_az6),
     reopen_when = { 0m }
 )

# PIGE RADIO REALISÉE PAR LE FLUX RADIO PRINCIPAL AIRVS
    output.file(%mp3(bitrate=96),
    { time.string("/var/www/pige/%Y-%m-%d/%Hh%M_%S_%z.mp3") },
    #mix,
    mksafe(radio),
    reopen_when = { 0m }
    )