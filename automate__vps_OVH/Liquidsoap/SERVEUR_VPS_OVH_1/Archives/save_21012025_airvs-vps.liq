#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 22.04 ####

#### DECROCHAGE VERS FLUX EXTÉRIEURS ICECAST, DIFFUSION EMISSIONS, PLAYLISTES ET JINGLES SELON QUOTAS ET GRILLE HORAIRE #### 

##### TESTS EN COURS D'AJOUT DE PLAYLIST NUIT ET DE SOIRÉES 20H-24H

#### TRAITEMENT DE SON EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Sources Automate : https://mkir.ch/ - Martin Kirchgessner  #### https://github.com/martinkirch

#### Sources Process Son : 
#####https://www.mkpascal.net/ - Pascal MUANG KHOT #### https://github.com/mkpascal/
#####https://gist.github.com/BusterNeece

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022

#### Version 1.2.3 du 08/01/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés

#### Décrochage soiree 19h50-00h00 depuis script VPS2 envoyé sur /stream7 (playlistes horaires par style et décennies)
#### Décrochage apresmidi 16h00-17h59 depuis script VPS2 envoyé sur /stream7
#### Création d'un quota NOUVELLES ENTREES sur la semaine complète pondéré à 20%
#### TOP HORAIRE EN DÉBUT DE CHAQUE HEURE

# FICHIER DE LOG
#set("log.file.path","/tmp/basic-radio_quotas.log")

# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")    # Limiter à localhost
set("server.telnet.port", 1234)                # Spécifier le port (optionnel)
set("server.telnet.idle_timeout", -1)          # Désactiver le timeout d'inactivité
set("server.telnet.max_connections", 10)       # Limite de connexions simultanées

# Les dossiers ci-dessous seront surveillés par le script : pour mettrairvs-vps.liqe ou enlever un morceau à l'antenne
# il faut juste le copier/supprimer dans le dossier ou dans l'un des sous-dossiers.

NUIT = "/home/ubuntu/projet_radio/Musique/BALLADS"

NOUVELLES_ENTREES = "/home/ubuntu/projet_radio/Musique/NOUVELLES_ENTREES"

clock = single("/home/ubuntu/test/Jingles/HORAIRE/BEEP.mp3")

############################ EMISSIONS ######################################

# attention il faut que les répertoires existent au lancement du script, même vides

emissions = switch(id="emissions", [
  ({20h00-23h59}, blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload_mode="watch", mode="normal", id="superemission", "/home/ubuntu/test/Emissions/superemission"))),
  ({9h00-11h00}, blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload_mode="watch", mode="normal", id="matinale", "/home/ubuntu/test/Emissions/matinale"))),
  ({11h20-11h30}, blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload_mode="watch", mode="normal", id="teaser", "/home/ubuntu/test/Emissions/teaser"))),
  ({18h05-19h00}, blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload_mode="watch", mode="normal", id="apero", "/home/ubuntu/test/Emissions/apero"))),
  ({15h00-15h50}, blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload_mode="watch", mode="normal", id="blabla", "/home/ubuntu/test/Emissions/blabla"))),
  ({00h30-02h30}, blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload_mode="watch", id="nuit",        "/home/ubuntu/test/Emissions/nuit")))
  ])

### DÉCROCHAGE 16h00-18h00 APRÈS-MIDI DU LUNDI AU DIMANCHE DEPUIS SCRIPT VPS2 ENVOYÉ SUR /STREAM7  ####################

apresmidi = input.http(id="apresmidi", "https://icecast-vps-ovh.airvs.site/stream7"
)
#exec_at(pred={ 16h00m00s },
# { log(label="server_driver", "Démarrage du flux APRÈS-MIDI"); server.execute("apresmidi.start") }
#)
#exec_at(pred={ 17h59m59s },
#  { log(label="server_driver", "Arrêt du flux APRÈS-MIDI"); server.execute("apresmidi.stop") }
#)

### DECROCHAGE 19h50-00h00 SOIREES DU LUNDI AU DIMANCHE DEPUIS SCRIPT VPS2 ENVOYÉ SUR /STREAM7 ####################

soiree = input.http(id="soiree", "https://icecast-vps-ovh.airvs.site/stream7"
)
exec_at(pred={ 19h50m00s },
  { log(label="server_driver", "Démarrage du flux SOIREE"); server.execute("soiree.start") }
)
exec_at(pred={ 00h00m00s },
  { log(label="server_driver", "Arrêt du flux SOIREE"); server.execute("soiree.stop") }
)

## DECROCHAGE DE JOURNÉE DEPUIS SCRIPT LOCAL STUDIO ENVOYÉ SUR /STREAM5  ####################

journee = input.http(id="journee", "https://icecast-vps-ovh.airvs.site/stream5"
)
exec_at(pred={ 9h00m00s },
  { log(label="server_driver", "Démarrage du flux JOURNEE"); server.execute("journee.start") }
)
exec_at(pred={ 19h00m00s },
  { log(label="server_driver", "Arrêt du flux JOURNEE"); server.execute("journee.stop") }
)

## DECROCHAGE VERS STRASBOURG - BAPTISTE OU YASUTEK SUR BUTT EN MANUEL  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "strasbourg.start" command from telnet server. Don't
# forget to launch "strasbourg.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,`

strasbourg = input.http(id="strasbourg", "https://icecast-vps-ovh.airvs.site/stream8",
  start=false, max_buffer=30.)

################################ SOURCE EXTERNE DE STUDIO EN MANUEL (RADIO DJ sur 7/8, DIRECT MICRO SUR 4, SCRIPTS EXCEPTIONNELS) ################################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "externe.start" command from telnet server. Don't
# forget to launch "externe.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,`

externe = input.http(id="externe", "https://icecast-vps-ovh.airvs.site/stream9",
  max_buffer=30.)


########################## Tambouille de musiques/quotas/jingles ###################

# On recharge toutes les heures le contenu des playlists, en cas d'ajout/suppression de fichiers
# Suppression des blancs de + 1 seconde

musique         = blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload=360, id="musique",         "/home/ubuntu/test/Musique/TOUT/"))

musique_FR      = blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload=3600, id="musique_FR",      "/home/ubuntu/test/Musique/FR/"))

musique_recente = blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload=3600, id="musique_recente", "/home/ubuntu/test/Musique/AIRPLAY/"))

musique_nuit = blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload=360, id="musique_nuit", NUIT))

musique_new = blank.eat(start_blank=true, max_blank=1.0, threshold=-45.0, playlist(reload=360, id="musique_new", NOUVELLES_ENTREES))

# Application de quotas chansons françaises, nouveautés et fêtes
playlist_quota = mksafe(id="playlist_quota", random(
  [        musique, musique_FR, musique_recente, musique_new],
  weights=[50,      15,         15,        20]
))


# Semaine musicale avec quotas en fond reprend si contenu tranche horaire du soir se trouve vide
semaine_musicale = switch(id="semaine_musicale", [
  ({ (1w or 2w or 3w or 4w or 5w) and 6h30-22h30 }, playlist_quota),
  ({ (6w or 7w) and 8h-22h30 }, playlist_quota),
  
# Programmes de la nuit
  ({ 0h-6h30 }, musique_nuit),
  ({ true }, mksafe(musique)) # switch n'est safe que s'il termine sur une source safe
])

def crossfade_3s(a,b)
  add(normalize=false,
      [ fade.in(duration=3.,b),
        fade.out(duration=3.,a) ])
end


######################### MONTAGE GRILLE ANTENNE ############################################

grille = fallback([emissions, externe, strasbourg, journee, apresmidi, soiree, semaine_musicale],
  transitions=[crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s],
  track_sensitive=false, id="grille")

# 780 = au moins 13 minutes de délai entre les jingles
# "delay" compte en secondes et c'est forcément un flottant d'où le point obligatoire
jingles = delay(780., id="jingles",
   amplify(1.0, playlist.safe("/home/ubuntu/test/Jingles/AIRVS", mode="random", reload_mode="watch"))
)

# à la fin de chaque track, on va piocher un jingle s'il est dispo (après lecture, il n'est pas dispo)
grille_avec_jingle = fallback([jingles, grille], id="grille_avec_jingle")

# s'il y a plus de 2 secondes à -50dB, on zappe à  la piste suivante !
radio = skip_blank(grille_avec_jingle, max_blank=2., threshold=-40.)

# ajout du bip horaire au début de chaque heure
radio = add([radio, switch([({ 0m0s }, clock)])])

####################################################################################################################
###########################  PROCESSES AUDIOS VERSION 4  ###########################################################
####################################################################################################################
# Fonction Replay Gain
#radio = amplify(1.,override="replay_gain",radio)

###### Application de processes audio sur l'ensemble des sources rassemblées dans #radio###### 
# Gate
radio = ladspa.gate(radio, threshold = -60.0, attack = 0.15, hold = 1.0, decay = 200.0, range = -25.0)

# Wideband AGC + Pre-processing Compresseur SC4
radio = normalize(target = 0., window = 0.03, gain_min = -12., gain_max = 0., radio)
radio = ladspa.sc4(rms_peak=0.3, attack_time = 0.5, release_time = 3., threshold_level = -36.0, ratio=1., makeup_gain = 6., radio)

# Stereo Expander
radio = ladspa.matrixspatialiser(width=16, radio)

##################   EQUALIZER 8 BANDES   ###############################
# Bass EQ TAP
#radio = ladspa.tap_equalizer(band_1_freq=40., band_1_gain=6., radio)
#radio = ladspa.tap_equalizer(band_2_freq=100., band_2_gain=6., radio)
radio = ladspa.tap_equalizer(band_1_freq=70., band_1_gain=9., radio)
radio = ladspa.tap_equalizer(band_1_freq=150., band_1_gain=5., radio)

#radio = ladspa.tap_equalizer(band_3_freq=200., band_3_gain=6., radio)
# Middle EQ TAP
radio = ladspa.tap_equalizer(band_2_gain=2., radio)
radio = ladspa.tap_equalizer(band_5_gain=1., radio)
#radio = ladspa.tap_equalizer(band_4_freq=500., band_4_gain=-10., radio)
#radio = ladspa.tap_equalizer(band_5_freq=1000., band_5_gain=-10., radio)
#radio = ladspa.tap_equalizer(band_6_freq=3000., band_6_gain=-7., radio)

# High EQ TAP
radio = ladspa.tap_equalizer(band_7_gain=2., radio)
radio = ladspa.tap_equalizer(band_8_gain=2., radio)
#radio = ladspa.tap_equalizer(band_7_freq=8000., band_7_gain=-11., radio)
#radio = ladspa.tap_equalizer(band_8_freq=16000., band_8_gain=-11., radio)

###################  COMPRESSEURS / LIMITEURS   ##############################

# 5 Bands Compress/Limit
radio = compress.multiband(radio, [
  {frequency=200., attack=3.5, release=30., ratio=3., threshold=-10., gain=2.},
  {frequency=1000., attack=2.25, release=40., ratio=2., threshold=-13., gain=2.},
  {frequency=3500., attack=2.25, release=40., ratio=3., threshold=-9., gain=2.},
  {frequency=6500., attack=2.25, release=70., ratio=2., threshold=-6., gain=1.5},
  {frequency=20000., attack=1.25, release=90., ratio=2., threshold=-4., gain=1.},
])

# 2 Bands Compress/Limit
radio = compress.multiband(radio, [
  {frequency=200., attack=5., release=50., ratio=2., threshold=-4., gain=0.},
  {frequency=20000., attack=2., release=70., ratio=4., threshold=-2., gain=-2.}
])

# De-esser TAP
radio = ladspa.tap_deesser(threshold_level=-5., frequency=20000., radio)
# Limiter final
radio = ladspa.tap_limiter(limit_level = -5., radio)

##################### SORTIES ###########################################

#Paramétrage des différentes sorties matérielles et logicielles

    #output.alsa(radio, id="alsa")

    output.icecast(%vorbis, # ou bien mp3, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_formats.html
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "Style", url = "stream3",
        password = "idylle2013@SL",
        mount = "stream3", 
        mksafe(buffer(radio))
    )
    output.icecast(%mp3(bitrate=128), # ou bien vorbis, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_formats.html
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "Style", url = "stream2",
        password = "idylle2013@SL",
        mount = "stream2", 
        mksafe(buffer(radio))
    )
    output.icecast(%mp3(bitrate=192), # ou bien vorbis, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_form>
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "Style", url = "stream4",
        password = "idylle2013@SL",
        mount = "stream4", 
        mksafe(buffer(radio))
    )
