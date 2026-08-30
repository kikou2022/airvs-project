####################################################################################################################
########################################  PROCESSES AUDIOS VERSION 4  ##############################################
####################################################################################################################
###### Application de processes audio sur l'ensemble des sources rassemblées dans #radio###### 
###### Version fonction réutilisable — appliquée à radio ET à radio_az6 #############

# =============================================================================
# FONCTION DE TRAITEMENT AUDIO REUTILISABLE
# =============================================================================
# Chaîne LADSPA complète : gate → normalize → SC4 → spatialiser → EQ 8 bandes
#   → multiband compresseur 5 bandes → multiband compresseur 2 bandes
#   → de-esser → limiter
#
# Usage :
#   radio = process_airvs_audio(radio)
#   radio_az6 = process_airvs_audio(azuracast6)
# =============================================================================
def process_airvs_audio(s) =

  # Gate — supprime le bruit de fond en dessous du seuil
  s = ladspa.gate(s, threshold = -60.0, attack = 0.15, hold = 1.0, decay = 200.0, range = -25.0)

  # Wideband AGC + Pre-processing Compresseur SC4
  s = normalize(target = 0., window = 0.03, gain_min = -12., gain_max = 0., s)
  s = ladspa.sc4(rms_peak=0.3, attack_time = 0.5, release_time = 3., threshold_level = -36.0, ratio=1., makeup_gain = 6., s)

  # Stereo Expander
  s = ladspa.matrixspatialiser(width=16, s)

  ##################   EQUALIZER 8 BANDES   ###############################
  # Bass EQ TAP
  s = ladspa.tap_equalizer(band_1_freq=70., band_1_gain=9., s)
  s = ladspa.tap_equalizer(band_1_freq=150., band_1_gain=5., s)
  # Middle EQ TAP
  s = ladspa.tap_equalizer(band_2_gain=2., s)
  s = ladspa.tap_equalizer(band_5_gain=1., s)
  # High EQ TAP
  s = ladspa.tap_equalizer(band_7_gain=2., s)
  s = ladspa.tap_equalizer(band_8_gain=2., s)

  ###################  COMPRESSEURS / LIMITEURS   ##############################

  # 5 Bands Compress/Limit
  s = compress.multiband(s, [
    {frequency=200., attack=3.5, release=30., ratio=3., threshold=-10., gain=2.},
    {frequency=1000., attack=2.25, release=40., ratio=2., threshold=-13., gain=2.},
    {frequency=3500., attack=2.25, release=40., ratio=3., threshold=-9., gain=2.},
    {frequency=6500., attack=2.25, release=70., ratio=2., threshold=-6., gain=1.5},
    {frequency=20000., attack=1.25, release=90., ratio=2., threshold=-4., gain=1.},
  ])

  # 2 Bands Compress/Limit
  s = compress.multiband(s, [
    {frequency=200., attack=5., release=50., ratio=2., threshold=-4., gain=0.},
    {frequency=20000., attack=2., release=70., ratio=4., threshold=-2., gain=-2.}
  ])

  # De-esser TAP
  s = ladspa.tap_deesser(threshold_level=-5., frequency=20000., s)
  # Limiter final
  s = ladspa.tap_limiter(limit_level = -5., s)

  s
end

# =============================================================================
# APPLICATION DU TRAITEMENT AU FLUX PRINCIPAL
# =============================================================================
# radio_az6 est traité dans airvs-vps.liq AVANT ce include (voir airvs-vps.liq)
# Seul radio est traité ici
radio = process_airvs_audio(radio)
