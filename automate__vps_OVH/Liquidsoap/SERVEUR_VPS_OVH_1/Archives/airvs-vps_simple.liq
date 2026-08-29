#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 24.04 ####

#### TRAITEMENT DE SON 1.0 (Décembre 2024) EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Sources Automate : https://mkir.ch/ - Martin Kirchgessner  #### https://github.com/martinkirch

#### Sources Process Son : 
#####https://www.mkpascal.net/ - Pascal MUANG KHOT #### https://github.com/mkpascal/
#####https://gist.github.com/BusterNeece

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022

#### Version 1.4.1 du 22/07/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés

#### Décrochage manuel DEPUIS STUDIO LOCAL VIA RADIODJ ET BUTT SUR UBUNTU HP
#### Décrochage manuel DEPUIS STRASBOURG VIA BAPTISTE ET BUTT
#### Décrochage automatique DEPUIS AZURACAST DISTANT SUR VPS OVH2 ET OVH3 (azuracast4 sur VPS OVH3 EN PRIORITÉ)
#### Gestion simple de jingles quotidiens
#### TOP HORAIRE EN DÉBUT DE CHAQUE HEURE
#### Détection et gestion de doublons joués sur 1 heure
#### Création d'un programme de secours musical journée 24/24 et 7/7 avec quotas

# FICHIER DE LOG SI BESOIN HORS LIQUIDSOAP-DAEMON
#set("log.file.path","/tmp/basic-radio_quotas.log")

# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")    # Limiter à localhost
set("server.telnet.port", 1234)                # Spécifier le port (optionnel)
set("server.telnet.idle_timeout", -1)          # Désactiver le timeout d'inactivité
set("server.telnet.max_connections", 10)       # Limite de connexions simultanées

# Les dossiers ci-dessous seront surveillés par le script : pour mettre ou enlever un morceau à l'antenne
# il faut juste le copier/supprimer dans le dossier ou dans l'un des sous-dossiers.
NUIT = "/home/ubuntu/projet_radio/Musique/BALLADS"
MUSIQUE = "/home/ubuntu/projet_radio/Musique/TOUT"
MUSIQUE_FR = "/home/ubuntu/projet_radio/Musique/FR"

clock = single("/home/ubuntu/projet_radio/Jingles/HORAIRE/BEEP.mp3")

########### Dossiers contenant les jingles par durée, temporalité et contenu

# Jingles diffusés selon leur durée
JINGLES_COURTS = "/home/ubuntu/projet_radio/Jingles/AIRVS/COURTS"
JINGLES_LONGS = "/home/ubuntu/projet_radio/Jingles/AIRVS/LONGS"

# Jingles diffusés selon leur contenu
JINGLES_PROMO = "/home/ubuntu/projet_radio/Jingles/AIRVS/PROMO"
JINGLES_PROMO2 = "/home/ubuntu/projet_radio/Jingles/AIRVS/PROMO2"
JINGLES_PROMO3 = "/home/ubuntu/projet_radio/Jingles/AIRVS/PROMO3"

################# DECROCHAGE AUTOMATIQUE VIA AZURACAST SUR VPS OVH 3  ####################
# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "azuracast3.start" command from telnet server. Don't
# forget to launch "azuracast3.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

azuracast3 = input.http(id="azuracast3", "https://azuracast.airvs.fr:8000/radio.mp3",
max_buffer=10.)

################# DECROCHAGE AUTOMATIQUE VIA AZURACAST SUR VPS OVH 3 ÉTÉ  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "azuracast4.start" command from telnet server. Don't
# forget to launch "azuracast4.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

azuracast4 = input.http(id="azuracast4", "https://azuracast.airvs.fr:8010/radio.mp3",
max_buffer=5.)

################# DECROCHAGE AUTOMATIQUE VIA AZURACAST SUR VPS OVH 2  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "azuracast2.start" command from telnet server. Don't
# forget to launch "azuracast2.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

azuracast2 = input.http(id="azuracast2", "https://azuracast.airvs.site:8000/radio.mp3",
max_buffer=10.)

## DECROCHAGE MANUEL VERS STRASBOURG - BAPTISTE OU YASUTEK SUR BUTT EN MANUEL  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "strasbourg.start" command from telnet server. Don't
# forget to launch "strasbourg.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,`

strasbourg = input.http(id="strasbourg", "https://icecast-vps-ovh.airvs.site/stream8",
  start=false, max_buffer=30.)

## Décrochage manuel DEPUIS STUDIO LOCAL VIA RADIODJ ET BUTT SUR UBUNTU HP ################################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "externe.start" command from telnet server. Don't
# forget to launch "externe.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,`

externe = input.http(id="externe", "https://icecast-vps-ovh.airvs.site/stream9", start=false,
  max_buffer=10.)
########################## Gestion des musiques/quotas/jingles ###################

# On recharge toutes les heures le contenu des playlists, en cas d'ajout/suppression de fichiers
# Suppression des blancs de + 1 seconde

musique         = playlist(reload=3600, id="musique", MUSIQUE)
musique_FR      = playlist(reload=3600, id="musique_FR", MUSIQUE_FR)
musique_nuit = playlist(reload=3600, id="musique_nuit", NUIT)

def vide(a,b) =
  source.fail()
end

# Application de quotas chansons françaises et internationales
playlist_quota = mksafe(id="playlist_quota",  
random(
  [        musique, musique_FR],
  weights=[50,      50],
  transition_length=0.,
  transitions=[vide,vide]
))

# Semaine musicale avec quotas en fond reprend si contenu tranche horaire du soir se trouve vide
semaine_musicale = switch(id="semaine_musicale", [
  ({ (1w or 2w or 3w or 4w or 5w) and 12h-22h30 }, playlist_quota),
  ({ (6w or 7w) and 12h-22h30 }, playlist_quota),

# Programmes de la nuit
  ({ 0h-6h30 }, musique_nuit),

# switch n'est safe que s'il termine sur une source safe
  ({ true }, mksafe(musique)) 
])
######################### MONTAGE GRILLE ANTENNE AVEC LES DIFFÉRENTES SOURCES #################################################################

######################### Priorité des sources et transitions CROSSFADE 2s 

def crossfade_2s(a,b)
    faded = add(normalize=false, [
        fade.in(duration=2., type="exp", b),
        # fondu "à la main" parce que fade.out ne se déclenche qu'en fin de piste, or là on coupe
        amplify(mkfade(duration=2., start=1., stop=0., type="exp", a) ,a)
    ])
    # la durée ci-dessous est plus longue : comme à la fin du fondu le volume est nul,
    # ça peut aussi vider un éventuel buffer
    add_timeout(10., { source.skip(a) ; -1.})
    faded
end

# blank() à la fin rend la source "grille" safe, même si en pratique semaine_musical est sans fin
grille = fallback([azuracast4, azuracast2, externe, azuracast3, strasbourg, semaine_musicale, blank()],
  transitions=[crossfade_2s, crossfade_2s, crossfade_2s, crossfade_2s, crossfade_2s, crossfade_2s],
  track_sensitive=false,
  replay_metadata=false,
  id="grille")

############################## GESTION ET AJOUT DE JINGLES, SPOTS ET BANDE-ANNONCES SUR LA GRILLE DE SEMAINE ################################################
#############################################################################################################
## Création des différentes programmations de jingles durant la semaine
# 600 = au moins 10 minutes de délai entre les jingles
# "delay" compte en secondes et c'est forcément un flottant d'où le point obligatoire
jingles = delay(600., id="jingles",
   
  amplify(1.0, 
   
   switch([

## Programmation tous les jours de la semaine 7/7 selon l'horaire dans l'heure complète  
   ({ 20m-40m }, playlist(JINGLES_LONGS, id="jingles_longs", mode="random", reload_mode="watch")),
   ({ 10m-50m }, playlist(JINGLES_PROMO, id="jingles_promo", mode="random", reload_mode="watch")),
   ({ 30m }, playlist(JINGLES_PROMO2, id="jingles_promo2", mode="random", reload_mode="watch")),
   ({ 15m-45m }, playlist(JINGLES_PROMO3, id="jingles_promo3", mode="random", reload_mode="watch")),
   ({ true }, mksafe(playlist(JINGLES_COURTS, id="jingles_courts", mode="random", reload_mode="watch")))
    ])
  )
)
###################### Création de la grille finale mixant la musique et les jingles ##############################

# à la fin de chaque track, on va piocher un jingle s'il est dispo (après lecture, il n'est pas dispo)
grille_avec_jingle = fallback([jingles, grille], id="grille_avec_jingle", replay_metadata=false, 
transition_length=0.,
transitions=[vide,vide])

######################### GESTION DES BLANCS ET AJOUT DU BIP HORAIRE AVANT TRAITEMENT DE SON #######################

# s'il y a plus de 2 secondes à -60dB, on zappe à  la piste suivante ! (blank.skip au lieu de skip_blank)
radio = blank.skip(grille_avec_jingle, max_blank=2., threshold=-60.)

# ajout du bip horaire au début de chaque heure
radio = add([radio, switch([({ 0m0s }, clock)])])

####################################################################################################################
########################################  PROCESSES AUDIOS VERSION 4  ##############################################
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

##################### SORTIES ET DIFFUSIONS ###########################################

#Paramétrage des différentes sorties matérielles et logicielles

    #output.alsa(radio, id="alsa")

    output.icecast(%vorbis, # ou bien mp3, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_formats.html
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "INCROYABLE", url = "stream3",
        password = "idylle2013@SL",
        mount = "stream3", 
        mksafe(buffer(radio))
    )
    output.icecast(%mp3(bitrate=128), # ou bien vorbis, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_formats.html
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream2",
        password = "idylle2013@SL",
        mount = "stream2", 
        mksafe(buffer(radio))
    )
    output.icecast(%mp3(bitrate=192), # ou bien vorbis, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_form>
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "UNIQUE", url = "stream4",
        password = "idylle2013@SL",
        mount = "stream4", 
        mksafe(buffer(radio))
    )
    output.icecast(%mp3(bitrate=320), # ou bien vorbis, wav, flac, cf. https://www.liquidsoap.info/doc-1.4.0/encoding_form>
        host = "icecast-vps-ovh.airvs.site",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "BADASSE", url = "stream5",
        password = "idylle2013@SL",
        mount = "stream5", 
        mksafe(buffer(radio))
    )
