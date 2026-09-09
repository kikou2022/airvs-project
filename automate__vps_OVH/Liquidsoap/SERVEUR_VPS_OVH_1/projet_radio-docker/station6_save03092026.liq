# CHAINE STATION 6 — Conteneur Docker independant
# Flux independant AzuraCast 6 traite par TTS v4
# Fallback musique locale si AzuraCast 6 est en maintenance

set("log.file.path","/var/log/liquidsoap/station6.log")

# Telnet (port different du conteneur secours, accessible depuis le host VPS)
set("server.telnet", true)
set("server.telnet.bind_addr", "0.0.0.0")
set("server.telnet.port", 1237)
set("server.telnet.idle_timeout", -1)
set("server.telnet.max_connections", 10)

MUSIQUE = "/music"

###############################################################################
#                     SOURCE AZURACAST 6 + FALLBACK MUSIQUE                   #
###############################################################################

azuracast6 = input.http(
  id="azuracast6_docker",
  "https://azuracast.2026.airvs.fr:8000/radio.mp3",
  max_buffer=10.,
  on_error=fun(msg) -> log("STATION 6 HORS LIGNE : #{msg}")
)

# Fallback : si AzuraCast 6 est en maintenance, playlist locale
musique_s6 = amplify(3.5, playlist(reload=3600, id="musique_s6", MUSIQUE))
musique_s6 = bass_boost(musique_s6, gain=3.0)

source_s6 = fallback(
  [azuracast6, musique_s6, blank()],
  track_sensitive=false,
  replay_metadata=true,
  id="source_s6"
)

###############################################################################
#                     TRAITEMENT AUDIO TTS V4                                 #
###############################################################################

def process_airvs_audio(s) =
  s = ladspa.gate(s, threshold = -60.0, attack = 0.15, hold = 1.0, decay = 200.0, range = -25.0)
  s = normalize(target = 0., window = 0.03, gain_min = -12., gain_max = 0., s)
  s = ladspa.sc4(rms_peak=0.3, attack_time = 0.5, release_time = 3., threshold_level = -36.0, ratio=1., makeup_gain = 6., s)
  s = ladspa.matrixspatialiser(width=16, s)
  s = ladspa.tap_equalizer(band_1_freq=70., band_1_gain=9., s)
  s = ladspa.tap_equalizer(band_1_freq=150., band_1_gain=5., s)
  s = ladspa.tap_equalizer(band_2_gain=2., s)
  s = ladspa.tap_equalizer(band_5_gain=1., s)
  s = ladspa.tap_equalizer(band_7_gain=2., s)
  s = ladspa.tap_equalizer(band_8_gain=2., s)
  s = compress.multiband(s, [
    {frequency=200., attack=3.5, release=30., ratio=3., threshold=-10., gain=2.},
    {frequency=1000., attack=2.25, release=40., ratio=2., threshold=-13., gain=2.},
    {frequency=3500., attack=2.25, release=40., ratio=3., threshold=-9., gain=2.},
    {frequency=6500., attack=2.25, release=70., ratio=2., threshold=-6., gain=1.5},
    {frequency=20000., attack=1.25, release=90., ratio=2., threshold=-4., gain=1.},
  ])
  s = compress.multiband(s, [
    {frequency=200., attack=5., release=50., ratio=2., threshold=-4., gain=0.},
    {frequency=20000., attack=2., release=70., ratio=4., threshold=-2., gain=-2.}
  ])
  s = ladspa.tap_deesser(threshold_level=-5., frequency=20000., s)
  s = ladspa.tap_limiter(limit_level = -5., s)
  s
end
###############################################################################
#                     SORTIE STREAM6                                          #
###############################################################################

radio_az6 = process_airvs_audio(source_s6)

output.icecast(%mp3(bitrate=128),
  host = "icecast-vps-ovh.airvs.fr",
  port = 8000,
  name = "AIRVS Station 6",
  description = "Flux independant Station 6 — traite par TTS v4",
  genre = "INCROYABLE",
  password = "idylle2013@SL",
  mount = "stream6",
  mksafe(buffer(radio_az6))
)