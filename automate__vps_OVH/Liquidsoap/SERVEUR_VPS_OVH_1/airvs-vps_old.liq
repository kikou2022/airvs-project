#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 24.04 ####

#### TRAITEMENT DE SON 1.0 (Décembre 2024) EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Sources Automate : https://mkir.ch/ - Martin Kirchgessner  #### https://github.com/martinkirch

#### Sources Process Son : 
#####https://www.mkpascal.net/ - Pascal MUANG KHOT #### https://github.com/mkpascal/
#####https://gist.github.com/BusterNeece

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022
#### Version 2.2 du 12/12/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés

#### Décrochage manuel DEPUIS STUDIO LOCAL VIA RADIODJ ET BUTT SUR UBUNTU HP
#### Décrochage manuel DEPUIS STRASBOURG VIA BAPTISTE ET BUTT
#### Décrochage automatique DEPUIS AZURACAST DISTANT SUR VPS OVH 5
#### Détection et gestion de doublons joués sur 1 heure
#### Création d'un programme de secours musical journée 7/7 avec quotas
#### Création d'un programme de secours musical nuit 7/7
#### Enregistrement local de piges en mp3 - 1 fichier créé chaque heure pleine sur plusieurs sources différentes
#### Application de processes audio version 4.0 en sortie de la source finale avant diffusion
# FICHIER DE LOG SI BESOIN HORS LIQUIDSOAP-DAEMON
set("log.file.path","/tmp/airvs-vps.log")

# Activation du serveur Telnet
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")    # Limiter à localhost
set("server.telnet.port", 1234)                # Spécifier le port (optionnel)
set("server.telnet.idle_timeout", 1)          # Désactiver le timeout d'inactivité
set("server.telnet.max_connections", 10)       # Limite de connexions simultanées

# Les dossiers ci-dessous seront surveillés par le script : pour mettre ou enlever un morceau à l'antenne
# il faut juste le copier/supprimer dans le dossier ou dans l'un des sous-dossiers.
NUIT = "/home/ubuntu/projet_radio/Musique"
MUSIQUE = "/home/ubuntu/projet_radio/Musique"
MUSIQUE_FR = "/home/ubuntu/projet_radio/Musique"

########################## GESTION DES DOUBLONS ###############################
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

########################## SOURCES DE DIFFUSION ###############################
%include "/home/ubuntu/projet_radio/script/entrees.liq"

%include "/home/ubuntu/projet_radio/script/playlistes_locales.liq"

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
grille = fallback([azuracast7, azuracast5, studio1, semaine_musicale, blank()],
  transitions=[crossfade_2s, crossfade_2s, crossfade_2s, crossfade_2s],
  track_sensitive=false,
  replay_metadata=true,
  id="grille")

######################### GESTION DES BLANCS #######################

# s'il y a plus de 2 secondes à -60dB, on zappe à  la piste suivante ! (blank.skip au lieu de skip_blank)
radio = blank.skip(grille, max_blank=2., threshold=-60.)

## PIGES, TRAITEMENT DE SON ET SORTIES VERS FLUX ICECAST

%include "/home/ubuntu/projet_radio/script/piges.liq"

%include "/home/ubuntu/projet_radio/script/tts_v4.liq"

# %include "/home/ubuntu/projet_radio/script/azuracast_vps_ovh_5.liq"

%include "/home/ubuntu/projet_radio/script/sorties.liq"