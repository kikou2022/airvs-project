#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 22.04 ####

#### TRAITEMENT DE SON 1.0 (Décembre 2024) EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Sources Automate : https://mkir.ch/ - Martin Kirchgessner  #### https://github.com/martinkirch

#### Sources Process Son : 
#####https://www.mkpascal.net/ - Pascal MUANG KHOT #### https://github.com/mkpascal/
#####https://gist.github.com/BusterNeece

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022

#### Version 1.3.8 du 12/04/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés

#### Décrochage manuel DEPUIS STUDIO LOCAL VIA RADIODJ ET BUTT SUR UBUNTU HP
#### Décrochage manuel DEPUIS STRASBOURG VIA BAPTISTE ET BUTT
#### Décrochage automatique DEPUIS AZURACAST DISTANT SUR VPS OVH2 ET 3 (VPS OVH3 EN PRIORITÉ)
#### Gestion des jingles en fonction du jour, de la durée, de l'horaire et du contenu avec prise en charge des weekends
#### TOP HORAIRE EN DÉBUT DE CHAQUE HEURE
#### Détection et gestion de doublons joués sur 1 heure
#### Gestion de secours des émissions quotidiennes et hebdomadaires
#### Création d'un programme de secours musical journée 7/7 avec quotas
#### Création d'un programme de secours musical nuit 7/7


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

# Jingles diffusés uniquement en fonction des jours ou de la période de la semaine
JINGLE_JOUR = "/home/ubuntu/projet_radio/Jingles/AIRVS/JINGLE_JOUR/"
#JINGLE_MARDI = "/home/ubuntu/projet_radio/Jingles/AIRVS/JINGLE_JOUR/mardi/"
#JINGLE_MERCREDI = "/home/ubuntu/projet_radio/Jingles/AIRVS/JINGLE_JOUR/mercredi/"

# Jingles diffusés uniquement durant le weekend
JINGLEWE_COURTS = "/home/ubuntu/projet_radio/Jingles/AIRVS/JINGLE_WEEKEND/COURTS/"
JINGLEWE_LONGS = "/home/ubuntu/projet_radio/Jingles/AIRVS/JINGLE_WEEKEND/LONGS/"
JINGLE_WEEKEND = "/home/ubuntu/projet_radio/Jingles/AIRVS/JINGLE_WEEKEND/"

# Jingles diffusés selon leur temporalité la nuit
JINGLES_NUIT = "/home/ubuntu/projet_radio/Jingles/AIRVS/NUIT"
JINGLES_NUIT2 = "/home/ubuntu/projet_radio/Jingles/AIRVS/NUIT2"

# Jingles diffusés selon leur temporalité par tranche de 2h et/ou 30mn selon les tranches horaires
#JINGLES_MATIN = "/home/ubuntu/projet_radio/Jingles/AIRVS/MATIN"
#JINGLES_MATIN2 = "/home/ubuntu/projet_radio/Jingles/AIRVS/MATIN2"

#JINGLES_MATIN3 = "/home/ubuntu/projet_radio/Jingles/AIRVS/MATIN3"
#JINGLES_MATIN3_1 = "/home/ubuntu/projet_radio/Jingles/AIRVS/MATIN3_1"
#JINGLES_MATIN3_2 = "/home/ubuntu/projet_radio/Jingles/AIRVS/MATIN3_2"

#JINGLES_MIDI = "/home/ubuntu/projet_radio/Jingles/AIRVS/MIDI"

#JINGLES_APRESMIDI = "/home/ubuntu/projet_radio/Jingles/AIRVS/APRESMIDI"
#JINGLES_APRESMIDI2 = "/home/ubuntu/projet_radio/Jingles/AIRVS/APRESMIDI2"
#JINGLES_APRESMIDI3 = "/home/ubuntu/projet_radio/Jingles/AIRVS/APRESMIDI3"

#JINGLES_SOIREE = "/home/ubuntu/projet_radio/Jingles/AIRVS/SOIREE"
#JINGLES_SOIREE2 = "/home/ubuntu/projet_radio/Jingles/AIRVS/SOIREE2"

# Jingles diffusés selon leur contenu
JINGLES_PROMO = "/home/ubuntu/projet_radio/Jingles/AIRVS/PROMO"
JINGLES_PROMO2 = "/home/ubuntu/projet_radio/Jingles/AIRVS/PROMO2"
JINGLES_PROMO3 = "/home/ubuntu/projet_radio/Jingles/AIRVS/PROMO3"

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
### on prépare une variable à ré-utiliser dans la grille

nouveautes = emission("nouveautes")
#timzonerap = emission("timzonerap")
#topnouveautes = emission("topnouveautes")
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
#Vendredi
  ({5w16h00-5w18h00}, nouveautes), 
#Samedi
  
#Dimanche
  ({7w11h00-7w12h00}, nouveautes),
  
  ])

################# DECROCHAGE AUTOMATIQUE VIA AZURACAST SUR VPS OVH 3  ####################

# We set-up an HTTP source, to maybe relay another stream.
#
# Note that, because of `start=false`, this source will never play unless
# activated by a "azuracast3.start" command from telnet server. Don't
# forget to launch "azuracast3.stop" after the show.
# If you want to play the stream as soon as it appears online, remove `start=false,

azuracast3 = input.http(id="azuracast3", "https://azuracast.airvs.fr:8000/radio.mp3",
max_buffer=10.)

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


# Application de quotas chansons françaises et internationales
playlist_quota = mksafe(id="playlist_quota", random(
  [        musique, musique_FR],
  weights=[50,      50]
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

######################### Priorité des sources et transitions

#def crossfade_3s(a,b)
#  add(normalize=false,
#      [ fade.in(duration=3.,b),
#        fade.out(duration=3.,a) ])
#end


def crossfade_3s(a,b)
    faded = add(normalize=false, [
        fade.in(duration=3., type="exp", b),
        # fondu "à la main" parce que fade.out ne se déclenche qu'en fin de piste, or là on coupe
        amplify(mkfade(duration=3., start=1., stop=0., type="exp", a) ,a)
    ])
    # la durée ci-dessous est plus longue : comme à la fin du fondu le volume est nul,
    # ça peut aussi vider un éventuel buffer
    add_timeout(10., { source.skip(a) ; -1.})
    faded
end

# blank() à la fin rend la source "grille" safe, même si en pratique fond_musical est sans fin
grille = fallback([azuracast3, azuracast2, externe, emissions, strasbourg, semaine_musicale, blank()],
  transitions=[crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s, crossfade_3s],
  track_sensitive=false, 
  replay_metadata=false,  
  id="grille")

######################### GESTION ET AJOUT DE JINGLES SUR LA GRILLE DE SEMAINE ################################################
# Fonction de gestion des jingles en fonction du jour de la semaine

def jingle(jour)

playlist(reload_mode="watch", mode="random", id=jour, "#{JINGLE_JOUR}#{jour}")

end

### ATTENTION il ne faut appeler jingle() qu'une fois par dossier
### donc pour les jingles diffusés sur différents créneaux et jours
### on prépare une variable à ré-utiliser dans la grille 

lundi = jingle("lundi")
mardi = jingle("mardi")
mercredi = jingle("mercredi")
jeudi = jingle("jeudi")
vendredi = jingle("vendredi")
samedi = jingle("samedi")
dimanche = jingle("dimanche")

#############################################################################################################
## Création des différentes programmations de jingles durant la semaine
# 600 = au moins 10 minutes de délai entre les jingles
# "delay" compte en secondes et c'est forcément un flottant d'où le point obligatoire
jingles = delay(600., id="jingles",
   
  amplify(1.0, 
   
   switch([
## Programmation du lundi au jeudi selon des plages horaires dans la journée
   ({ (1w or 2w or 3w or 4w) and 6h-8h and (2m-28m or 32m-58m) }, jingle("MATIN")),
   ({ (1w or 2w or 3w or 4w) and 8h-10h and (2m-28m or 32m-58m) }, jingle("MATIN2")),
   ({ (1w or 2w or 3w or 4w) and 10h-12h and (2m-28m or 32m-58m) }, jingle("MATIN3")),
   ({ (1w or 2w or 3w or 4w) and 10h-10h30 and (2m-28m or 32m-58m) }, jingle("MATIN3_1")),
   ({ (1w or 2w or 3w or 4w) and 10h30-11h and (2m-28m or 32m-58m) }, jingle("MATIN3_2")),
   ({ (1w or 2w or 3w or 4w) and 12h-14h and (2m-28m or 32m-58m) }, jingle("MIDI")),
   ({ (1w or 2w or 3w or 4w) and 14h-15h and (2m-28m or 32m-58m) }, jingle("APRESMIDI")),
   ({ (1w or 2w or 3w or 4w) and 15h-18h and (2m-28m or 32m-58m) }, jingle("APRESMIDI2")),
   ({ (1w or 2w or 3w or 4w) and 18h-20h and (2m-28m or 32m-58m) }, jingle("APRESMIDI3")),
   ({ (1w or 2w or 3w or 4w) and 20h-22h and (2m-28m or 32m-58m) }, jingle("SOIREE")),
   ({ (1w or 2w or 3w or 4w) and 22h-0h and (2m-28m or 32m-58m) }, jingle("SOIREE2")),

## Programmation exclusivement durant le weekend et selon des plages horaires
   ({ (5w or 6w or 7w) and 10h-11h and (2m-28m or 32m-58m)}, (playlist(JINGLEWE_COURTS, id="jingles_courts", mode="random", reload_mode="watch"))),
   
## Programmation exclusivement durant certains jours de la semaine 7/7 et selon des plages horaires
   #Lundi journée
   ({ 1w and 9h-23h and (2m-28m or 32m-58m) }, lundi),
   #Mardi journée
   ({ 2w and 9h-23h and (2m-28m or 32m-58m)}, mardi),
   #Mercredi journée
   ({ 3w and 9h-23h and (2m-28m or 32m-58m) }, mercredi),
   #Jeudi journée
   ({ 4w and 9h-23h and (2m-28m or 32m-58m) }, jeudi),
   #Vendredi matin
   ({ 5w and 9h-12h and (2m-28m or 32m-58m) }, vendredi),
   #Samedi journée complète
   ({ 6w and 9h-23h and (2m-28m or 32m-58m) }, samedi),
   #Dimanche après-midi et soirée
   ({ 7w and 14h-23h and (2m-28m or 32m-58m) }, dimanche),
   
## Programmation toutes les nuits de la semaine
   ({ 0h-6h }, playlist(JINGLES_NUIT, id="jingles_nuit", mode="random", reload_mode="watch")),
   ({ 0h30-4h }, playlist(JINGLES_NUIT2, id="jingles_nuit2", mode="random", reload_mode="watch")),

## Programmation tous les jours de la semaine 7/7 selon l'horaire dans l'heure complète  
   ({ 20m-40m }, playlist(JINGLES_LONGS, id="jingles_longs", mode="random", reload_mode="watch")),
   ({ 10m-50m }, playlist(JINGLES_PROMO, id="jingles_promo", mode="random", reload_mode="watch")),
   ({ 30m }, playlist(JINGLES_PROMO2, id="jingles_promo2", mode="random", reload_mode="watch")),
   ({ 15m-45m }, playlist(JINGLES_PROMO3, id="jingles_promo3", mode="random", reload_mode="watch")),
   ({ true }, mksafe(playlist(JINGLES_COURTS, id="jingles_courts", mode="random", reload_mode="watch")))
    ])
  )
)
#################################### GESTION ET AJOUT DE JINGLES SUR LA GRILLE DU WEEKEND ########################################
# Fonction de gestion des jingles en fonction du jour du weekend
def jinglewe(jourwe)

playlist(reload_mode="watch", mode="random", id=jourwe, "#{JINGLE_WEEKEND}#{jourwe}")

end

### ATTENTION il ne faut appeler jinglewe() qu'une fois par dossier
### donc pour les jingles diffusés sur différents créneaux et jours
### on prépare une variable à ré-utiliser dans la grille 

weekend = jinglewe("weekend")
vendrediwe = jinglewe("vendrediwe")
samediwe = jinglewe("samediwe")
dimanchewe = jinglewe("dimanchewe")

###################################################################################################
## Création des différentes programmations de jingles durant le weekend
# 780 = au moins 13 minutes de délai entre les jingles
# "delay" compte en secondes et c'est forcément un flottant d'où le point obligatoire
jingleswe = delay(780., id="jingleswe",
   
  amplify(1.0, 
   
   switch([
## Programmation exclusivement durant certains jours du weekend et selon des plages horaires
   #Vendredi matin
   ({ 5w and 6h-9h and (2m-28m or 32m-58m) }, vendrediwe),
   #Vendredi après-midi
   ({ 5w and 14h-23h50 and (2m-28m or 32m-58m) }, weekend),
   #Samedi après-midi
   ({ 6w and 14h-18h and (2m-28m or 32m-58m) }, samediwe),
   #Samedi journée
   ({ 6w and 07h-23h50 and (2m-28m or 32m-58m) }, weekend),
   #Dimanche matin
   ({ 7w and 07h-12h and (2m-28m or 32m-58m) }, weekend),
   #Dimanche après-midi
   ({ 7w and 14h-18h and (2m-28m or 32m-58m) }, dimanchewe),
   #Weekend complet
   ({ (5w or 6w or 7w) and 12h-14h and (2m-28m or 32m-58m)}, playlist(JINGLEWE_LONGS, id="jingles_longs", mode="random", reload_mode="watch")),
   
   #({ true }, mksafe(playlist(JINGLEWE_COURTS, id="jingles_courts", mode="random", reload_mode="watch")))
    ])
  )
)

## Création de la grille finale mixant la musique et les jingles

def crossfade_3s(a,b)
  add(normalize=false,
      [ fade.in(duration=3.,b),
        fade.out(duration=3.,a) ])
end

# à la fin de chaque track, on va piocher un jingle s'il est dispo (après lecture, il n'est pas dispo)
grille_avec_jingle = fallback([jingles, jingleswe, grille, blank()], 
transitions=[crossfade_3s, crossfade_3s, crossfade_3s],
track_sensitive=false,
replay_metadata=false,
id="grille_avec_jingle")

######################### GESTION DES BLANCS ET AJOUT DU BIP HORAIRE AVANT TRAITEMENT DE SON #######################

# s'il y a plus de 2 secondes à -60dB, on zappe à  la piste suivante ! (blank.skip au lieu de skip_blank)
radio = blank.skip(grille_avec_jingle, max_blank=2., threshold=-60.)

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
