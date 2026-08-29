#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 24.04 ####

#### TRAITEMENT DE SON 1.0 (Décembre 2024) EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022

#### Version 2.0.0 du 07/07/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés


#### Décrochage automatique planifié via AZURACAST DISTANT SUR VPS OVH2 ET OVH3

# FICHIER DE LOG SI BESOIN HORS LIQUIDSOAP-DAEMON
#set("log.file.path","/tmp/basic-radio_quotas.log")

# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")    # Limiter à localhost
set("server.telnet.port", 1234)                # Spécifier le port (optionnel)
set("server.telnet.idle_timeout", -1)          # Désactiver le timeout d'inactivité
set("server.telnet.max_connections", 10)       # Limite de connexions simultanées

clock = single("/home/ubuntu/projet_radio/Jingles/HORAIRE/BEEP.mp3")

# Playliste musicale de secours
musique = playlist(reload=3600, "/home/ubuntu/projet_radio/Musique")

# Remote URL sur Azuracast
azuracast2 = mksafe(buffer(buffer=1.,input.http(id="azuracast2", "https://azuracast.airvs.site:8000/radio.mp3")))
azuracast3 = mksafe(buffer(buffer=1.,input.http(id="azuracast3", "https://azuracast.airvs.fr:8000/radio.mp3")))
azuracast4 = mksafe(buffer(buffer=1.,input.http(id="azuracast4", "https://azuracast.airvs.fr:8010/radio.mp3")))

# Remote URL Schedule Switches Advanced
grille = switch([
    
    ({0h-6h}, azuracast3), 
    ({6h-14h}, azuracast2),
    ({14h-20h}, azuracast3),
    ({20h-22h}, azuracast4),
    ({22h-0h}, azuracast2),
    ({ true }, mksafe(musique))
])

# s'il y a plus de 2 secondes à -60dB, on zappe à  la piste suivante ! (blank.skip au lieu de skip_blank)
radio = blank.skip(grille, max_blank=2., threshold=-60.)

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