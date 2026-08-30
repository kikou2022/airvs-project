# CHAINE SECOURS AAC — Conteneur Docker independant
# Production AAC de l'antenne principale (transcodage de stream4)
# Bascule sur playlist locale si stream4 est indisponible

set("log.file.path","/var/log/liquidsoap/secours.log")

# Telnet (accessible depuis le host VPS via port mapping)
set("server.telnet", true)
set("server.telnet.bind_addr", "0.0.0.0")
set("server.telnet.port", 1236)
set("server.telnet.idle_timeout", -1)
set("server.telnet.max_connections", 10)

MUSIQUE = "/music"

###############################################################################
#                     SECOURS : flux_stream4 + playlist locale                #
###############################################################################

flux_stream4 = input.http(id="flux_stream4", "https://icecast-vps-ovh.airvs.fr/stream4",
max_buffer=20.)

musique = amplify(3.5, playlist(reload=3600, id="musique", MUSIQUE))
musique = bass_boost(musique, gain=3.0)

def crossfade_2s(a,b)
    faded = add(normalize=false, [
        fade.in(duration=2., type="exp", b),
        amplify(mkfade(duration=2., start=1., stop=0., type="exp", a), a)
    ])
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

###############################################################################
#                     SORTIES AAC (3 bitrates)                                 #
###############################################################################

    output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="64k",
    profile="aac_he_v2", afterburner=0)),
        host = "icecast-vps-ovh.airvs.fr",
        port = 8000,
        name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream12",
        password = "idylle2013@SL",
        mount = "stream12",
        mksafe(buffer(radio))
    )
    # output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="48k",
    # profile="aac_he_v2", afterburner=0)),
    #     host = "icecast-vps-ovh.airvs.fr",
    #     port = 8000,
    #     name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream11",
    #     password = "idylle2013@SL",
    #     mount = "stream11",
    #     mksafe(buffer(radio))
    # )
    # output.icecast(%ffmpeg(format="adts", %audio(codec="libfdk_aac", samplerate=44100, channels=2, b="96k",
    # profile="aac_he_v2", afterburner=0)),
    #     host = "icecast-vps-ovh.airvs.fr",
    #     port = 8000,
    #     name = "AIRVS", description = "La Radio en 1 CLIC !", genre = "GENIALE", url = "stream13",
    #     password = "idylle2013@SL",
    #     mount = "stream13",
    #     mksafe(buffer(radio))
    # )
