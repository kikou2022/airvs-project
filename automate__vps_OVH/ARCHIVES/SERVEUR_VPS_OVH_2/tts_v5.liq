####################################################################################################################
##############################   PROCESS AUDIO WEB - MUSIQUE (SON DYNAMIQUE)   ####################################
###########################            CIBLE -16 LUFS / GRAVES PRÉSENTS            #################################
####################################################################################################################

# ---------------------------------------------------------------------------
# 1. Gate discret (silence plateau)
# ---------------------------------------------------------------------------
radio = ladspa.gate(
  radio,
  threshold = -62.,
  attack = 0.1,
  hold = 0.6,
  decay = 240.,
  range = -15.
)

# ---------------------------------------------------------------------------
# 2. AGC lent (dynamique globale)
# ---------------------------------------------------------------------------
radio = normalize(
  radio,
  target = -1.0,
  window = 0.07,
  gain_min = -5.,
  gain_max = 2.
)
# ----------------------------------------------------
# Stereo Expander
# ----------------------------------------------------
radio = ladspa.matrixspatialiser(width=16, radio)

# ---------------------------------------------------------------------------
##################   EQUALIZER 8 BANDES   ###############################
# Bass EQ TAP
radio = ladspa.tap_equalizer(band_1_freq=70., band_1_gain=9., radio)
radio = ladspa.tap_equalizer(band_1_freq=150., band_1_gain=5., radio)
# Middle EQ TAP
radio = ladspa.tap_equalizer(band_2_gain=2., radio)
radio = ladspa.tap_equalizer(band_5_gain=1., radio)
# High EQ TAP
radio = ladspa.tap_equalizer(band_7_gain=2., radio)
radio = ladspa.tap_equalizer(band_8_gain=2., radio)

# ---------------------------------------------------------------------------
# 4. Compression multibande
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 5. De-esser (voix et sifflantes légères)
# ---------------------------------------------------------------------------
radio = ladspa.tap_deesser(
  radio,
  threshold_level = -6.,
  frequency = 9000.
)

# ---------------------------------------------------------------------------
# 6. Limiteur simple pour éviter les peaks (FM, DAB, Web)
# ---------------------------------------------------------------------------
radio = ladspa.tap_limiter(
  radio,
  limit_level = -1.0  # Limitation douce à -1 dB
)

####################################################################################################################
#########################################   FIN PROCESS WEB - SON MUSICAL   ######################################
####################################################################################################################
