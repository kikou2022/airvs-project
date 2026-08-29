################# DECROCHAGE AUTOMATIQUE VIA AZURACAST SUR VPS OVH 4 (À CONSERVER EN AUTOMATIQUE POUR ENREGISTREMENT DE LA PIGE)  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "azuracast5.start" command from telnet server. Don't
# forget to launch "azuracast5.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

azuracast5 = input.http(id="azuracast5", "https://azuracast2.airvs.fr:8000/radio.mp3", start=false,
max_buffer=10.)

################# DECROCHAGES AUTOMATIQUES VIA AZURACAST SUR VPS OVH 5 (À CONSERVER EN AUTOMATIQUE POUR ENREGISTREMENT DE LA PIGE)  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "azuracastX.start" command from telnet server. Don't
# forget to launch "azuracastX.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

azuracast7 = input.http(id="azuracast7", "https://azuracast.2026.airvs.fr:8010/radio.mp3",
max_buffer=10.)

## DECROCHAGE MANUEL VERS STRASBOURG - BAPTISTE OU YASUTEK SUR BUTT EN MANUEL  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "strasbourg.start" command from telnet server. Don't
# forget to launch "strasbourg.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,`

#strasbourg = input.http(id="strasbourg", "https://icecast-vps-ovh2.airvs.fr/stream8",
  # start=false, max_buffer=5.)

## Décrochage manuel SECOURS DEPUIS VPS OVH 2 SUR STREAM9 ################################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "studio1.start" command from telnet server. Don't
# forget to launch "studio1.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,`

studio1 = input.http(id="studio1", "https://icecast-vps-ovh.airvs2.fr/stream9", start=false,
  max_buffer=5.)

  ## Décrochage manuel SECOURS DEPUIS VPS OVH 1 SUR STREAM10 ################################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "studio2.start" command from telnet server. Don't
# forget to launch "studio2.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,`

#studio2 = input.http(id="studio2", "https://icecast-vps-ovh.airvs.fr/stream10", start=false,
 # max_buffer=5.)

