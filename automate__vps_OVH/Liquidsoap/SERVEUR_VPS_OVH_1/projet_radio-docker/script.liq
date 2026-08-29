# FICHIER DE LOG SI BESOIN HORS LIQUIDSOAP-DAEMON
set("log.file.path","/var/log/liquidsoap/basic.log")

# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")
set("server.telnet.port", 1234)
set("server.telnet.idle_timeout", -1)
set("server.telnet.max_connections", 10)

# Les dossiers ci-dessous seront surveillés par le script
MUSIQUE = "/music"

###############################################################################
#                     CHAÎNE 1 : SECOURS (inchangé)                           #
###############################################################################

flux_stream4 = input.http(id="flux_stream4", "https://icecast-vps-ovh.airvs.fr/stream4",
max_buffer=20.)

################# PROGRAMME DE SECOURS MUSICAL JOURNÉE 7/7 AVEC QUOTAS ####################
musique         = amplify(3.5, playlist(reload=3600, id="musique", MUSIQUE))
musique = bass_boost(musique, gain=3.0)

######################### Priorité des sources et transitions CROSSFADE 2s 
def crossfade_2s(a,b)
    faded = add(normalize=false, [
        fade.in(duration=2., type="exp", b),
        amplify(mkfade(duration=2., start=1., stop=0., type="exp", a) ,a)
    ])
    add_timeout(10., { source.skip(a) ; -1.})
    faded
end

radio = fallback([
    flux_stream4, 
    musique, 
    blank()],
    transitions=[crossfade_2s, crossfade_2s],
  track_sensitive=false,
  replay_metadata=true,
  id="radio")

radio = blank.skip(radio, max_blank=2., threshold=-60.)

####################### SORTIES SECOURS (AAC) ###########################################
    output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="64k", 
    profile="aac_he_v2", afterburner=0)), 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream12",
        password = "idylle2013@SL",
        mount = "stream12", 
        mksafe(buffer(radio))
    )
    output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="48k", 
    profile="aac_he_v2", afterburner=0)), 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream11",
        password = "idylle2013@SL",
        mount = "stream11", 
        mksafe(buffer(radio))
    )
    output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="96k", 
    profile="aac_he_v2", afterburner=0)), 
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream13",
        password = "idylle2013@SL",
        mount = "stream13", 
        mksafe(buffer(radio))
    )

###############################################################################
#            CHAÎNE 2 : STATION 6 — FLUX INDÉPENDANT AVEC TTS V4             #
###############################################################################

azuracast6 = input.http(
  id="azuracast6_docker",
  "https://azuracast.2026.airvs.fr:8000/radio.mp3",
  max_buffer=10.,
  on_error=fun(msg) -> log("STATION 6 HORS LIGNE : #{msg}")
)

# Fonction de traitement audio TTS v4 (identique au script principal)
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

radio_az6 = process_airvs_audio(azuracast6)

output.icecast(%mp3(bitrate=128),
  host = "icecast-vps-ovh.airvs.fr",
  port = 8000,
  name = "AIRVS Station 6",
  description = "Flux independant Station 6 — traité par TTS v4",
  genre = "INCROYABLE",
  password = "idylle2013@SL",
  mount = "stream6",
  mksafe(buffer(radio_az6))
)