#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 22.04 ####

#### DECROCHAGE VERS FLUX EXTÉRIEURS ICECAST, DIFFUSION EMISSIONS, PLAYLISTES ET JINGLES SELON QUOTAS ET GRILLE HORAIRE #### 

##### TESTS EN COURS D'AJOUT DE PLAYLIST NUIT ET DE SOIRÉES 20H-24H

#### TRAITEMENT DE SON EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Sources Automate : https://mkir.ch/ - Martin Kirchgessner  #### https://github.com/martinkirch

#### Sources Process Son : 
#####https://www.mkpascal.net/ - Pascal MUANG KHOT #### https://github.com/mkpascal/
#####https://gist.github.com/BusterNeece

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022

#### Version 1.3.0 du 08/02/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés

#### Création d'un quota NOUVELLES ENTREES sur la semaine complète pondéré à 20%
#### Décrochage manuel DEPUIS AZURACAST EN STUDIO LOCAL SUR UBUNTU HP
#### Décrochage manuel DEPUIS AZURACAST DISTANT SUR VPS OVH 2
#### Gestion des émissions quotidiennes et hebdomadaires
#### Gestion des Jingles selon durée, horaire et contenu
#### Détection et gestion de doublons joués sur 1 heure
#### Création d'un programmme musical matinal 7/7 
#### Création d'un programme musical nuit 7/7
#### TOP HORAIRE EN DÉBUT DE CHAQUE HEURE

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
MATINALE = "/home/ubuntu/projet_radio/Musique/MATIN"
NOUVELLES_ENTREES = "/home/ubuntu/projet_radio/Musique/NOUVELLES_ENTREES"
MUSIQUE = "/home/ubuntu/projet_radio/Musique/TOUT"
MUSIQUE_FR = "/home/ubuntu/projet_radio/Musique/FR"
MUSIQUE_RECENTE = "/home/ubuntu/projet_radio/Musique/AIRPLAY"

clock = single("/home/ubuntu/projet_radio/Jingles/HORAIRE/BEEP.mp3")

# Dossiers contenant les jingles par durée et contenus
JINGLES_COURTS = "/home/ubuntu/projet_radio/Jingles/AIRVS/COURTS"
JINGLES_LONGS = "/home/ubuntu/projet_radio/Jingles/AIRVS/LONGS"
JINGLES_NUIT = "/home/ubuntu/projet_radio/Jingles/AIRVS/NUIT"
JINGLES_SOIREE ="/home/ubuntu/projet_radio/Jingles/AIRVS/SOIREE"
JINGLES_APRESMIDI ="/home/ubuntu/projet_radio/Jingles/AIRVS/APRESMIDI"
JINGLES_MATIN ="/home/ubuntu/projet_radio/Jingles/AIRVS/MATIN"
JINGLES_PROMO = "/home/ubuntu/projet_radio/Jingles/AIRVS/PROMO"

# le répertoire de base contenant les émissions
EMI = "/home/ubuntu/projet_radio/Emissions/"

# Test détection morceaux joués dans l'heure précédente
l = playlog()
def check(r)
  m = request.metadata(r)
  if l.last(m) < 3600. then
    log.info("Rejecting #{m['filename']} (played #{l.last(m)}s ago).")
    false
  else
    l.add(m)
    true
  end
end
############################### EMISSIONS QUOTIDIENNES ET/OU HEBDOMADAIRES ###################

def emission(nom)
  #def force_titre_emission(md) =
    #list.add(("title", nom), md)
  #end
  delay(id="#{nom}_", 10000., # 10000s = ~3h, pour éviter de rejouer l'emission si elle a un créneau long
    
      eat_blank(max_blank=3.0, threshold=-60.0, # en cas d'oubli de silence à la fin
        audio_to_stereo( # au cas où un fichier mono est fourni... évite que le décodeur bute dessus en boucle
          playlist(reload_mode="watch", mode="normal", id=nom, "#{EMI}#{nom}")
        )
      )   
  )
    end

### ATTENTION il ne faut appeler emission() qu'une fois par dossier
### donc pour les emissions diffusées sur plusieurs créneaux
### on prépare une variable à ré-utiliser dans la grille :

nouveautes = emission("nouveautes")
timzonerap = emission("timzonerap")
topnouveautes = emission("topnouveautes")
#emission_4 = emission("Emission_4")

# Construction de la grille semaine pour les émissions

emissions = switch(id="emissions", replay_metadata=false, [

#Lundi
  ({1w17h00-1w18h00}, nouveautes),
#Mardi
  ({2w16h00-2w19h00}, nouveautes),
#Mercredi
  ({3w16h00-3w18h00}, nouveautes), 
#Jeudi
  ({4w16h00-4w18h00}, nouveautes),
  ({4w20h00-4w21h00}, timzonerap),
#Vendredi
  ({5w16h00-5w18h00}, nouveautes),    
#Samedi
  ({6w17h00-6w18h00}, topnouveautes),
#Dimanche
  ({7w11h00-7w12h00}, topnouveautes),
  ({7w23h00-7w24h00}, timzonerap),
  ])

################# DECROCHAGE DEPUIS AZURACAST SUR VPS OVH 2  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "strasbourg.start" command from telnet server. Don't
# forget to launch "strasbourg.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

azuracastweb = input.http(id="azuracastweb", "http://icecast-vps-ovh2.airvs.site:8081/listen/airvs/radio.mp3",
start=false, max_buffer=5.)

## DECROCHAGE DEPUIS STUDIO LOCAL ENVOYÉ VIA AZURACAST SUR UBUNTU HP  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "strasbourg.start" command from telnet server. Don't
# forget to launch "strasbourg.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

studio = input.http(id="studio", "http://90.46.211.181:8000/radio.mp3",
start=false, max_buffer=5.)

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


########################## Gestion des musiques/quotas/jingles ###################

# On recharge toutes les heures le contenu des playlists, en cas d'ajout/suppression de fichiers
# Suppression des blancs de + 1 seconde

musique         = playlist(reload=360, id="musique", MUSIQUE)
musique_FR      = playlist(reload=3600, id="musique_FR", MUSIQUE_FR)
musique_recente = playlist(reload=3600, id="musique_recente", MUSIQUE_RECENTE)
musique_nuit = playlist(reload=360, id="musique_nuit", NUIT)
musique_new = playlist(reload=360, id="musique_new", NOUVELLES_ENTREES)
matinale = playlist(reload=900, id="matinale", MATINALE)

# Application de quotas chansons françaises, nouveautés et fêtes
playlist_quota = mksafe(id="playlist_quota", random(
  [        musique, musique_FR, musique_recente, musique_new],
  weights=[50,      15,         15,        20]
))

# Semaine musicale avec quotas en fond reprend si contenu tranche horaire du soir se trouve vide
semaine_musicale = switch(id="semaine_musicale", [
  ({ (1w or 2w or 3w or 4w or 5w) and 12h-22h30 }, playlist_quota),
  ({ (6w or 7w) and 12h-22h30 }, playlist_quota),

# Programmes du matin
  ({ 7h-12h }, matinale),

# Programmes de la nuit
  ({ 0h-6h30 }, musique_nuit),

# switch n'est safe que s'il termine sur une source safe
  ({ true }, mksafe(musique)) 
])

def crossfade_3s(a,b)
  add(normalize=false,
      [ fade.in(duration=3.,b),
        fade.out(duration=3.,a) ])
end


######################### MONTAGE GRILLE ANTENNE AVEC LES DIFFÉRENTES SOURCES #################################################################

grille = fallback([externe, emissions, strasbourg, azuracastweb, studio, semaine_musicale, blank()],
  transitions=[crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s],
  track_sensitive=false, 
  replay_metadata=false,  
  id="grille")

######################### GESTION ET AJOUT DE JINGLES SUR LA GRILLE ################################################

# 780 = au moins 13 minutes de délai entre les jingles
# "delay" compte en secondes et c'est forcément un flottant d'où le point obligatoire
jingles = delay(780., id="jingles",
   amplify(1.0, 
   
   switch([
   
   ({ 6h-14h }, playlist(JINGLES_MATIN, id="jingles_matin", mode="random", reload_mode="watch")),
   ({ 14h-20h }, playlist(JINGLES_APRESMIDI, id="jingles_apresmidi", mode="random", reload_mode="watch")),
   ({ 20h-0h }, playlist(JINGLES_SOIREE, id="jingles_soiree", mode="random", reload_mode="watch")),
   ({ 0h-6h }, playlist(JINGLES_NUIT, id="jingles_nuit", mode="random", reload_mode="watch")),
   ({ 20m-40m }, playlist(JINGLES_LONGS, id="jingles_longs", mode="random", reload_mode="watch")),
   ({ 10m-50m }, playlist(JINGLES_PROMO, id="jingles_promo", mode="random", reload_mode="watch")),
   ({ true }, playlist(JINGLES_COURTS, id="jingles_courts", mode="random", reload_mode="watch"))
    ])
  )
)
# à la fin de chaque track, on va piocher un jingle s'il est dispo (après lecture, il n'est pas dispo)
grille_avec_jingle = fallback([jingles, grille], id="grille_avec_jingle")

######################### GESTION DES BLANCS ET AJOUT DU BIP HORAIRE AVANT TRAITEMENT DE SON #######################

# s'il y a plus de 2 secondes à -60dB, on zappe à  la piste suivante !
radio = skip_blank(grille_avec_jingle, max_blank=2., threshold=-60.)

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
