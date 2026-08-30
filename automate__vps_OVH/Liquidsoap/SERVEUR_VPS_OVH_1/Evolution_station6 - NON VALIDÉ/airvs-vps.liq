#### SCRIPT LIQUIDSOAP POUR AUTOMATE SUR VPS OVH Ubuntu 24.04 ####

#### TRAITEMENT DE SON 1.0 (Décembre 2024) EN SORTIE DES DIFFÉRENTES SOURCES AVANT DIFFUSION SUR ICECAST  ####

#### Sources Automate : https://mkir.ch/ - Martin Kirchgessner  #### https://github.com/martinkirch
#### Sources Process Son : 
#####https://www.mkpascal.net/ - Pascal MUANG KHOT #### https://github.com/mkpascal/
#####https://gist.github.com/BusterNeece

#### Modifications et adaptations : Sébastien Lelièvre - https://github.com/kikou2022
#### Version 2.1 du 12/12/2025 pour Icecast 2.4.4 et Liquidsoap 2.2.4 - Administration du script par liquidsoap-daemon - Processes son validés
#### Version 2.2 : ajout flux independant Station 6 (radio_az6) traité par tts_v4 → /stream6

#### Décrochage manuel DEPUIS STUDIO LOCAL VIA RADIODJ ET BUTT SUR UBUNTU HP
#### Décrochage manuel DEPUIS STRASBOURG VIA BAPTISTE ET BUTT
#### Décrochage automatique DEPUIS AZURACAST DISTANT SUR VPS OVH 5
#### Détection et gestion de doublons joués sur 1 heure
#### Création d'un programme de secours musical journée 7/7 avec quotas
#### Création d'un programme de secours musical nuit 7/7
#### Enregistrement local de piges en mp3 - 1 fichier créé chaque heure pleine sur plusieurs sources différentes
#### Application de processes audio version 4.0 en sortie de la source finale avant diffusion
# FICHIER DE LOG SI BESOIN HORS LIQUIDSOAP-DAEMON
set("log.file.path","/var/log/liquidsoap/airvs-vps.log")

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
    # On garde la logique de fondu forcé
    faded = add(normalize=false, [
        fade.in(duration=2., type="exp", b),
        # mkfade garde son utilité quand azuracast tombe en plein milieu d'un morceau
        amplify(mkfade(duration=2., start=1., stop=0., type="exp", a), a)
    ])
    faded
end
# blank() à la fin rend la source "grille" safe
grille = fallback(
    [azuracast7, studio1, semaine_musicale, blank()],
    transitions=[
        crossfade_2s, crossfade_2s, crossfade_2s
    ],
    track_sensitive=false, # Ici il protège la semaine_musicale !
    replay_metadata=true,
    id="grille"
)
radio = grille

## PIGES, TRAITEMENT DE SON ET SORTIES VERS FLUX ICECAST

%include "/home/ubuntu/projet_radio/script/piges.liq"

# =============================================================================
# STATION 6 — FLUX INDEPENDANT TRAITE PAR TTS V4
# =============================================================================
# azuracast6 est déjà défini dans entrees.liq
# On le traite par process_airvs_audio() AVANT que tts_v4.liq ne traite radio
# Cela nécessite que tts_v4.liq soit chargé APRES cette ligne (voir plus bas)
# NOTE : process_airvs_audio est définie dans tts_v4.liq
#        on utilise %include "once" pour charger tts_v4.liq une première fois ici
#        afin que la fonction soit disponible, puis on l'inclut à nouveau
#        après pour appliquer le traitement à radio
#
# Alternativement, la définition de la fonction peut être extraite dans un
# fichier séparé (ex: tts_v4_func.liq) pour éviter le double include.
# L'approche retenue ici : on place la création de radio_az6 AVANT tts_v4.liq
# en utilisant %include "once" pour charger la définition de fonction d'abord.

# IMPORTANT : ordre des includes :
#   1. pige.liq          → pige azuracast6 + pige radio (piges inchangées)
#   2. tts_v4.liq        → définit process_airvs_audio() + l'applique à radio
#   3. sorties.liq       → sorties radio + sortie radio_az6 (/stream6)
#
# On crée radio_az6 APRES tts_v4.liq pour avoir accès à process_airvs_audio()

%include "/home/ubuntu/projet_radio/script/tts_v4.liq"

# =============================================================================
# Création du flux Station 6 traité — dérivé d'azuracast6 AVANT fusion dans radio
# =============================================================================
# radio_az6 prend le flux de azuracast6_stream6 (input.http dédié) et lui applique la chaîne tts_v4
# azuracast6_stream6 est un input.http séparé dans entrees.liq pour isoler la clock DSP
# de la clock des piges (pige_az6 utilise azuracast6 original)
radio_az6 = process_airvs_audio(azuracast6_stream6)

%include "/home/ubuntu/projet_radio/script/sorties.liq"
