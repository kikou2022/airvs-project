# -*- coding: utf-8 -*-
"""
rotation_azura.py — Phase 21.3 : cœur du module Rotation éditoriale (côté worker).

Contenu (consolidation volontaire de 3 modules de la spec en 1 fichier —
voir README 21.3 §Choix d'implémentation) :
  1. ResolveurMapping      — résolution bidirectionnelle song RadioDJ ↔ media
                             AzuraCast avec cache persistant mapping_radiodj_azura
                             (spec Annexe A.5).
  2. MoteurRotation        — lecture règles/songs RadioDJ, scoring (spec Phase 8),
                             candidats (Phase 9), groupement versions (R12),
                             exécution soft/hard avec coordination S6/S7 (Phase 10).
  3. journaliser_operation — écriture journal_operations_azura (spec Module 3).
  4. Miroir                — demande + drainage de la régénération db.json
                             (mémo §5.1 : après toute opération modifiant comments).

Le module est AUTOCONTENU : il ne importe PAS worker_ubuntu.py (risque
d'import circulaire) — il reçoit db_config en paramètre et reproduit
localement les helpers de normalisation/flags (sémantique Phase 19.2 identique).

Hotfix 2026.11.13-E : lire_songs_radiodj() reconstruit son SELECT d'après
les colonnes réellement présentes dans radiodb.songs (noms variant selon la
version RadioDJ : play_count/count_played/played_count ; colonnes parfois
absentes). Un signal indisponible est désactivé au scoring au lieu de
provoquer l'erreur 1054 « Unknown column ».

Déployé à côté de worker_ubuntu.py sur Ubuntu .17.
"""

import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from datetime import datetime, timedelta

import pymysql

try:
    from azuracast_api import get_api_for_station, AzuraCastAPIError
except ImportError:  # déploiement partiel — l'appelant gère
    get_api_for_station = None
    AzuraCastAPIError = Exception


# ═══════════════════════════════════════════════════════════════════════════
# Helpers locaux (sémantique identique à worker_ubuntu.py, Phase 19.2)
# ═══════════════════════════════════════════════════════════════════════════

def normaliser_pour_recherche(texte):
    """Normalisation pour comparaison floue (accents, casse, ponctuation)."""
    if not texte:
        return ""
    texte = str(texte).strip().lower()
    texte = ''.join(
        c for c in unicodedata.normalize('NFD', texte)
        if unicodedata.category(c) != 'Mn'
    )
    for caractere in "()[]&'+-":
        texte = texte.replace(caractere, " ")
    texte = re.sub(r'\b(?:et|and|x)\b', ' ', texte, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', texte).strip()


def basename(path):
    """Extrait le basename d'un path Windows ou Linux."""
    if not path:
        return ""
    if '\\' in path:
        return path.rsplit('\\', 1)[-1]
    if '/' in path:
        return path.rsplit('/', 1)[-1]
    return path


def decomposer_flag(flag):
    """Extrait {station_id: état} d'un flag AZURA_*_S* (Phase 19.2).

    'AZURA_ACTIVE_S6_S7' → {6: 'ACTIVE', 7: 'ACTIVE'}
    'IN_AZURACAST' (legacy) → {6: 'PUSHED', 7: 'PUSHED'} (compat R0)
    Flag inconnu/vide → {}.
    """
    if not flag:
        return {}
    flag = str(flag).strip()
    if flag == 'IN_AZURACAST':
        return {6: 'PUSHED', 7: 'PUSHED'}
    m = re.match(r'^AZURA_(ACTIVE|RETIRED|PUSHED|REMOVED)((?:_S\d+)*)$', flag)
    if not m:
        return {}
    etat = m.group(1)
    stations = {}
    for sid in re.findall(r'S(\d+)', m.group(2)):
        stations[int(sid)] = etat
    return stations


def composer_flag(etat, station_ids):
    """Compose 'AZURA_<ETAT>_S{id1}_S{id2}...' (identique worker Phase 19.2)."""
    parts = ['AZURA', etat] + [f'S{sid}' for sid in sorted(set(station_ids))]
    return '_'.join(parts)


def recalcule_flag_apres_op(flag_actuel, station_id, nouvel_etat):
    """Recompose le flag multi-stations après une opération sur UNE station.

    Ex : ('AZURA_ACTIVE_S6_S7', 7, 'RETIRED') → 'AZURA_ACTIVE_S6'
         (S6 reste ACTIVE, S7 passe RETIRED).
    Si la station n'apparaissait pas, elle est ajoutée avec nouvel_etat.
    """
    etats = decomposer_flag(flag_actuel)
    etats[int(station_id)] = nouvel_etat
    # Priorité : un seul état par station (valeur déjà par station ici).
    par_etat = {}
    for sid, et in etats.items():
        par_etat.setdefault(et, []).append(sid)
    # Fusion priorité ACTIVE > RETIRED > PUSHED > REMOVED :
    # la recomposition fine (19.2) reste la référence au prochain resync ;
    # ici on compose par groupes d'état (comportement identique au resync).
    for etat_prio in ('ACTIVE', 'RETIRED', 'PUSHED', 'REMOVED'):
        if etat_prio in par_etat:
            return composer_flag(etat_prio, par_etat[etat_prio])
    return flag_actuel or ''


# ═══════════════════════════════════════════════════════════════════════════
# 3. Journalisation (spec Module 3, simplifié)
# ═══════════════════════════════════════════════════════════════════════════

def journaliser_operation(db_config, type_op, station_id=None,
                          media_id_azura=None, song_id_radiodj=None,
                          triggered_by='manual', task_id=None,
                          trigger_source=None, details=None,
                          reversible=True):
    """Insère une ligne dans journal_operations_azura (non bloquant).

    Returns: id de l'entrée, ou None si table absente/erreur.
    """
    try:
        db = pymysql.connect(**db_config)
        cur = db.cursor()
        cur.execute(
            "INSERT INTO journal_operations_azura "
            "(type_op, station_id, media_id_azura, song_id_radiodj, "
            " triggered_by, task_id, trigger_source, details, reversible) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (type_op, station_id, media_id_azura, song_id_radiodj,
             triggered_by, task_id, trigger_source,
             json.dumps(details, ensure_ascii=False, default=str) if details else None,
             1 if reversible else 0)
        )
        db.commit()
        op_id = cur.lastrowid
        cur.close()
        db.close()
        return op_id
    except Exception:
        return None  # Table absente (déploiement partiel) → non bloquant


# ═══════════════════════════════════════════════════════════════════════════
# 4. Miroir — demandes + drainage de la régénération db.json (mémo §5.1)
# ═══════════════════════════════════════════════════════════════════════════

def demander_regen_miroir(db_config, raison='operation_azura'):
    """Marque le miroir comme à régénérer (table mirror_regen_state, 1 ligne).

    Appelé après TOUTE opération qui modifie radiodb.songs.comments :
    rotation soft/hard, retrait/suppression AzuraCast, resync avec changements,
    push (dashboard azuracast.py). Non bloquant : table absente → ignore.
    """
    try:
        db = pymysql.connect(**db_config)
        cur = db.cursor()
        cur.execute(
            "UPDATE mirror_regen_state "
            "SET pending = 1, date_demande = NOW(), last_raison = %s "
            "WHERE id = 1",
            (str(raison)[:255],)
        )
        if cur.rowcount == 0:
            # Ligne absente (script 005 pas encore passé) → créer.
            cur.execute(
                "INSERT IGNORE INTO mirror_regen_state "
                "(id, pending, date_demande, last_raison) VALUES (1, 1, NOW(), %s)",
                (str(raison)[:255],)
            )
        db.commit()
        cur.close()
        db.close()
        return True
    except Exception:
        return False


def lire_config_rotation(chemin_config=None):
    """Lit la section rotation du config.json du worker (à côté du module).

    Clés reconnues (section 'automation') :
      - rotation_hard_autorise   : bool, défaut False (cadrage item 6 en
        réflexion → hard refusé, fallback soft, tant que non validé)
      - mirror_regen_actif       : bool, défaut True
      - mirror_regen_host        : défaut 'sebastien@azuracast.2026.airvs.fr'
      - mirror_regen_port        : défaut '2022' (canal SSH BatchMode existant)
      - mirror_regen_export_path : chemin du script export_db_mirror_V4.py
        SUR .30 — défaut '' (VIDE = déclenchement désactivé, zéro risque ;
        à renseigner après vérification du chemin réel sur .30)
      - mirror_regen_debounce_min : défaut 10 (anti-spam exports)
      - mirror_regen_retry_min    : défaut 30 (backoff après échec SSH)
    Clés de scoring (section 'rotation') : ratio_rotation (0.10),
      duree_protection_jours (30), seuil_play_count_min (5),
      seuil_anciennete_jours (60), categories_elegibles ([] = toutes),
      dossiers_proteges ([]), nb_max_candidats (2000).

    Returns: dict {'automation': {...}, 'rotation': {...}} avec défauts comblés.
    """
    cfg = {'automation': {}, 'rotation': {}}
    try:
        if chemin_config is None:
            chemin_config = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         'config.json')
        if os.path.exists(chemin_config):
            with open(chemin_config, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data.get('automation'), dict):
                cfg['automation'] = data['automation']
            if isinstance(data.get('rotation'), dict):
                cfg['rotation'] = data['rotation']
    except Exception:
        pass  # config.json absent/illisible → défauts

    auto = cfg['automation']
    auto.setdefault('rotation_hard_autorise', False)
    auto.setdefault('mirror_regen_actif', True)
    auto.setdefault('mirror_regen_host', 'sebastien@azuracast.2026.airvs.fr')
    auto.setdefault('mirror_regen_port', '2022')
    auto.setdefault('mirror_regen_export_path', '')  # vide = désactivé
    auto.setdefault('mirror_regen_debounce_min', 10)
    auto.setdefault('mirror_regen_retry_min', 30)

    rot = cfg['rotation']
    rot.setdefault('ratio_rotation', 0.10)
    rot.setdefault('duree_protection_jours', 30)
    rot.setdefault('seuil_play_count_min', 5)
    rot.setdefault('seuil_anciennete_jours', 60)
    rot.setdefault('categories_elegibles', [])  # vide = toutes
    rot.setdefault('dossiers_proteges', [])
    rot.setdefault('nb_max_candidats', 2000)
    return cfg


def _miroir_ssh_exec(cfg_auto, timeout=600):
    """Exécute export_db_mirror_V4.py sur .30 via SSH BatchMode (canal existant).

    Returns: (True, message) ou (False, message).
    """
    chemin = (cfg_auto.get('mirror_regen_export_path') or '').strip()
    if not chemin:
        return False, "mirror_regen_export_path vide (déclenchement désactivé)"
    host = cfg_auto.get('mirror_regen_host') or 'sebastien@azuracast.2026.airvs.fr'
    port = str(cfg_auto.get('mirror_regen_port') or '2022')
    cmd = [
        'ssh', '-p', port,
        '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'StrictHostKeyChecking=accept-new',
        host,
        f"python3 {chemin}",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0:
            return True, f"export exécuté sur {host}:{port} ({chemin})"
        return False, (f"ssh rc={res.returncode} : "
                       f"{(res.stderr or res.stdout or '')[-300:]}")
    except FileNotFoundError:
        return False, "binaire ssh introuvable sur .17"
    except subprocess.TimeoutExpired:
        return False, f"timeout {timeout}s (export lourd ou .30 injoignable)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def drainer_regen_miroir(db_config, cfg=None, print_fn=print):
    """Un passage du drainer : si pending → déclenche l'export sur .30.

    Appelé par le thread de fond du worker (toutes les 60 s) — JAMAIS dans la
    boucle principale (SSH + export ≈ 1-2 min ne doivent pas retarder les
    tâches). Coalescing : pending est un drapeau, pas une file — les demandes
    multiples pendant un export sont fusionnées (un seul export après).

    States : ok / erreur_ssh / erreur_script / non_configure / en_cours.
    """
    if cfg is None:
        cfg = lire_config_rotation()
    auto = cfg['automation']
    if not auto.get('mirror_regen_actif', True):
        return

    try:
        db = pymysql.connect(**db_config)
        cur = db.cursor(pymysql.cursors.DictCursor)
        cur.execute("SELECT * FROM mirror_regen_state WHERE id = 1")
        etat = cur.fetchone()
        cur.close()
        db.close()
    except Exception as e:
        print_fn(f"[MIROIR] Table état illisible (non bloquant) : {e}")
        return
    if not etat or not etat.get('pending'):
        return
    if etat.get('last_statut') == 'en_cours':
        return  # un export est déjà en vol (thread unique)

    # Debounce : si une regen OK a eu lieu APRÈS la dernière demande → couvert.
    maintenant = datetime.now()
    last_regen = etat.get('last_regen')
    date_demande = etat.get('date_demande')
    if (last_regen and date_demande and date_demande <= last_regen
            and (maintenant - last_regen).total_seconds()
                < int(auto.get('mirror_regen_debounce_min', 10)) * 60):
        _miroir_set_state(db_config, pending=0, statut='ok',
                          message="déjà couvert par regen récente (debounce)")
        return
    # Backoff après échec : retenter seulement après retry_min.
    if (etat.get('last_statut') in ('erreur_ssh', 'erreur_script')
            and last_regen
            and (maintenant - last_regen).total_seconds()
                < int(auto.get('mirror_regen_retry_min', 30)) * 60):
        return  # on attend le backoff, pending reste à 1

    _miroir_set_state(db_config, statut='en_cours',
                      message=f"regen demandée ({etat.get('last_raison')})")
    ok, message = _miroir_ssh_exec(auto)
    if ok:
        _miroir_set_state(db_config, pending=0, statut='ok',
                          date_regen=True, message=message, incr_ok=True)
        journaliser_operation(db_config, 'mirror_regen', triggered_by='cron',
                              reversible=False,
                              details={'resultat': 'ok', 'message': message})
        print_fn(f"[MIROIR] Régénération db.json OK : {message}")
    else:
        _miroir_set_state(db_config, statut='erreur_ssh', message=message)
        journaliser_operation(db_config, 'mirror_regen', triggered_by='cron',
                              reversible=False,
                              details={'resultat': 'erreur', 'message': message})
        print_fn(f"[MIROIR] Échec régénération (pending conservé, backoff "
                 f"{auto.get('mirror_regen_retry_min')} min) : {message}")


def _miroir_set_state(db_config, pending=None, statut=None, message=None,
                      date_regen=False, incr_ok=False):
    """Met à jour mirror_regen_state (non bloquant)."""
    try:
        sets, params = [], []
        if pending is not None:
            sets.append("pending = %s")
            params.append(1 if pending else 0)
        if statut is not None:
            sets.append("last_statut = %s")
            params.append(statut)
        if message is not None:
            sets.append("last_message = %s")
            params.append(str(message)[:500])
        if date_regen:
            sets.append("last_regen = NOW()")
        if incr_ok:
            sets.append("nb_regen_ok = nb_regen_ok + 1")
        if not sets:
            return
        params.append(1)
        db = pymysql.connect(**db_config)
        cur = db.cursor()
        cur.execute(f"UPDATE mirror_regen_state SET {', '.join(sets)} WHERE id = %s",
                    params)
        db.commit()
        cur.close()
        db.close()
    except Exception:
        pass


def boucle_miroir_thread(db_config, intervalle_s=60):
    """Point d'entrée du thread de fond du worker (daemon) : draine les
    demandes de régénération miroir toutes les `intervalle_s` secondes."""
    while True:
        try:
            drainer_regen_miroir(db_config)
        except Exception as e:
            try:
                print(f"[MIROIR] Erreur drainer : {e}")
            except Exception:
                pass
        time.sleep(intervalle_s)


def demarrer_thread_miroir(db_config, intervalle_s=60):
    """Démarre le thread daemon du drainer miroir (appelé par worker main())."""
    t = threading.Thread(target=boucle_miroir_thread,
                         args=(db_config, intervalle_s), daemon=True,
                         name='miroir-regen')
    t.start()
    return t


# ═══════════════════════════════════════════════════════════════════════════
# 1. ResolveurMapping (spec Annexe A.5)
# ═══════════════════════════════════════════════════════════════════════════

CONFIANCE_METHODES = {
    'path_exact': 1.0,
    'path_normalized': 0.95,
    'artist_title_exact': 0.85,
    'artist_title_normalized': 0.75,
    'song_id_azura': 0.90,
    'manual': 1.0,
    'cache': 1.0,
}


class ResolveurMapping:
    """Résolution bidirectionnelle RadioDJ ↔ AzuraCast avec cache persistant.

    Stratégie (du plus fiable au plus approximatif) :
      1. Cache mapping_radiodj_azura (actif=TRUE)
      2. path_exact → 3. path_normalized → 4. artist_title_exact
      → 5. artist_title_normalized
    Chaque résolution alimente le cache (résolution paresseuse, spec A.9).
    """

    def __init__(self, db_config):
        self.db_config = db_config

    # ── Cache ──

    def chercher_cache(self, song_id, station_id):
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor(pymysql.cursors.DictCursor)
            cur.execute(
                "SELECT * FROM mapping_radiodj_azura "
                "WHERE song_id_radiodj = %s AND station_id = %s "
                "ORDER BY date_derniere_verif DESC LIMIT 1",
                (song_id, station_id)
            )
            row = cur.fetchone()
            cur.close()
            db.close()
            return row
        except Exception:
            return None

    def chercher_cache_par_media(self, media_id, station_id):
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor(pymysql.cursors.DictCursor)
            cur.execute(
                "SELECT * FROM mapping_radiodj_azura "
                "WHERE media_id_azura = %s AND station_id = %s "
                "ORDER BY date_derniere_verif DESC LIMIT 1",
                (media_id, station_id)
            )
            row = cur.fetchone()
            cur.close()
            db.close()
            return row
        except Exception:
            return None

    def inserer_cache(self, song, media_azura, station_id, methode):
        """Upsert d'une correspondance (UNIQUE song_id+station_id). Non bloquant."""
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor()
            cur.execute(
                """INSERT INTO mapping_radiodj_azura
                   (date_creation, date_derniere_verif,
                    song_id_radiodj, artist_radiodj, title_radiodj, album_radiodj,
                    path_radiodj, basename_radiodj,
                    media_id_azura, station_id, path_azura, unique_id_azura,
                    methode_matching, confiance, actif)
                   VALUES (NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                   ON DUPLICATE KEY UPDATE
                    date_derniere_verif = NOW(),
                    media_id_azura = VALUES(media_id_azura),
                    path_azura = VALUES(path_azura),
                    unique_id_azura = VALUES(unique_id_azura),
                    methode_matching = VALUES(methode_matching),
                    confiance = VALUES(confiance),
                    actif = TRUE,
                    raison_inactivation = NULL""",
                (song.get('ID'), (song.get('artist') or '')[:255],
                 (song.get('title') or '')[:255], (song.get('album') or '')[:255],
                 (song.get('path') or '')[:500],
                 basename(song.get('path') or '')[:255],
                 media_azura.get('id'), station_id,
                 (media_azura.get('path') or '')[:500],
                 (media_azura.get('unique_id') or '')[:64],
                 methode, CONFIANCE_METHODES.get(methode, 0.5))
            )
            db.commit()
            cur.close()
            db.close()
            return True
        except Exception:
            return False

    def alimenter_cache_batch(self, paires, station_id):
        """Upsert en masse : paires = [(song_dict, media_dict, methode), ...].

        Utilisé par le bootstrap MAPPING_BOOTSTRAP et par les candidats de
        preview. Une seule connexion pour tout le lot.
        Returns: nb lignes écrites.
        """
        if not paires:
            return 0
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor()
            for song, media, methode in paires:
                cur.execute(
                    """INSERT INTO mapping_radiodj_azura
                       (date_creation, date_derniere_verif,
                        song_id_radiodj, artist_radiodj, title_radiodj, album_radiodj,
                        path_radiodj, basename_radiodj,
                        media_id_azura, station_id, path_azura, unique_id_azura,
                        methode_matching, confiance, actif)
                       VALUES (NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                       ON DUPLICATE KEY UPDATE
                        date_derniere_verif = NOW(),
                        media_id_azura = VALUES(media_id_azura),
                        path_azura = VALUES(path_azura),
                        unique_id_azura = VALUES(unique_id_azura),
                        methode_matching = VALUES(methode_matching),
                        confiance = VALUES(confiance),
                        actif = TRUE,
                        raison_inactivation = NULL""",
                    (song.get('ID'), (song.get('artist') or '')[:255],
                     (song.get('title') or '')[:255], (song.get('album') or '')[:255],
                     (song.get('path') or '')[:500],
                     basename(song.get('path') or '')[:255],
                     media.get('id'), station_id,
                     (media.get('path') or '')[:500],
                     (media.get('unique_id') or '')[:64],
                     methode, CONFIANCE_METHODES.get(methode, 0.5))
                )
            db.commit()
            cur.close()
            db.close()
            return len(paires)
        except Exception:
            return 0

    def invalider_correspondance(self, song_id=None, media_id=None,
                                 station_id=None, raison=''):
        """Marque actif=FALSE (sans supprimer — historique conservé)."""
        conditions, params = [], []
        if song_id:
            conditions.append("song_id_radiodj = %s")
            params.append(song_id)
        if media_id:
            conditions.append("media_id_azura = %s")
            params.append(media_id)
        if station_id:
            conditions.append("station_id = %s")
            params.append(station_id)
        if not conditions:
            return
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor()
            cur.execute(
                f"UPDATE mapping_radiodj_azura SET actif = FALSE, "
                f"raison_inactivation = %s WHERE {' AND '.join(conditions)}",
                [raison] + params
            )
            db.commit()
            cur.close()
            db.close()
        except Exception:
            pass

    def nettoyer_cache_inactif(self, jours=90):
        """Supprime les correspondances inactives depuis plus de N jours."""
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor()
            cur.execute(
                "DELETE FROM mapping_radiodj_azura "
                "WHERE actif = FALSE AND date_derniere_verif < "
                "DATE_SUB(NOW(), INTERVAL %s DAY)",
                (jours,)
            )
            nb = cur.rowcount
            db.commit()
            cur.close()
            db.close()
            return nb
        except Exception:
            return 0

    def statistiques_matching(self):
        """Stats qualité du matching (endpoint /api/rotation/mapping/stats)."""
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor(pymysql.cursors.DictCursor)
            cur.execute(
                """SELECT methode_matching,
                          COUNT(*) AS total,
                          SUM(CASE WHEN actif THEN 1 ELSE 0 END) AS actifs,
                          ROUND(AVG(confiance), 2) AS confiance_moyenne
                   FROM mapping_radiodj_azura
                   GROUP BY methode_matching"""
            )
            stats = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*) AS actifs FROM mapping_radiodj_azura "
                "WHERE actif = TRUE"
            )
            total_actifs = (cur.fetchone() or {}).get('actifs', 0)
            cur.execute("SELECT COUNT(*) AS n FROM radiodb.songs WHERE song_type = 0")
            total_songs = (cur.fetchone() or {}).get('n', 0)
            cur.close()
            db.close()
            taux = round(100.0 * total_actifs / total_songs, 1) if total_songs else 0.0
            return {
                'par_methode': stats,
                'total_actifs': total_actifs,
                'total_songs_radiodj': total_songs,
                'taux_couverture_pct': taux,
            }
        except Exception as e:
            return {'erreur': str(e)}

    # ── Résolution unitaire song → media (cache puis fallback réseau) ──

    def song_radiodj_vers_media_azura(self, song, station_id,
                                      media_rows=None, ecrire_cache=True):
        """Résout un song RadioDJ → media AzuraCast.

        Args:
            song: dict (ID, artist, title, album, path) — déjà lu par l'appelant
                  si possible (évite un SELECT par appel).
            media_rows: liste des médias AzuraCast de la station SI l'appelant
                        les a déjà (list_media_all) — sinon fetch réseau.
        Returns: {'media_id_azura', 'path_azura', 'methode', 'confiance'} ou None.
        """
        cache = self.chercher_cache(song.get('ID'), station_id)
        if cache and cache.get('actif'):
            return {
                'media_id_azura': cache['media_id_azura'],
                'path_azura': cache.get('path_azura'),
                'methode': 'cache',
                'confiance': cache.get('confiance') or 1.0,
            }

        if media_rows is None:
            media_rows = self._lister_media_azura(station_id)
            if not media_rows:
                return None

        match, methode = self.matcher_song(song, media_rows)
        if match and ecrire_cache:
            self.inserer_cache(song, match, station_id, methode)
        if match:
            return {
                'media_id_azura': match.get('id'),
                'path_azura': match.get('path'),
                'methode': methode,
                'confiance': CONFIANCE_METHODES.get(methode, 0.5),
            }
        return None

    # ── Matching en 4 passes (identique à la stratégie du resync worker) ──

    def matcher_song(self, song, media_rows):
        """Retourne (media_dict, methode) ou (None, None). 4 passes."""
        bn_rd = basename(song.get('path') or '').lower().strip()
        # Passe 1 : path exact
        if bn_rd:
            for m in media_rows:
                if basename(m.get('path') or '').lower().strip() == bn_rd:
                    return m, 'path_exact'
        # Passe 2 : path normalisé
        bn_norm = normaliser_pour_recherche(basename(song.get('path') or ''))
        if bn_norm:
            for m in media_rows:
                if normaliser_pour_recherche(basename(m.get('path') or '')) == bn_norm:
                    return m, 'path_normalized'
        # Passe 3 : artist + title exact
        artist, title = (song.get('artist') or '').strip(), (song.get('title') or '').strip()
        if artist and title:
            cle = f"{artist} - {title}"
            for m in media_rows:
                cle_m = (f"{(m.get('artist') or '').strip()} - "
                         f"{(m.get('title') or '').strip()}")
                if cle_m == cle:
                    return m, 'artist_title_exact'
        # Passe 4 : artist + title normalisé
        if artist and title:
            cle_norm = normaliser_pour_recherche(f"{artist} - {title}")
            if cle_norm:
                for m in media_rows:
                    cle_m = normaliser_pour_recherche(
                        f"{m.get('artist') or ''} - {m.get('title') or ''}")
                    if cle_m and cle_m == cle_norm:
                        return m, 'artist_title_normalized'
        return None, None

    def _lister_media_azura(self, station_id):
        if get_api_for_station is None:
            return None
        api = get_api_for_station(station_id)
        all_media = api.list_media_all()
        rows = all_media if isinstance(all_media, list) \
            else (all_media or {}).get('rows', [])
        return rows

    # ── Bootstrap initial (spec A.11) ──

    def bootstrap_station(self, station_id, log_fn=None):
        """Remplit le cache pour une station (matching existant basename+at).

        Returns: dict stats {medias, songs, matches, par_methode}.
        """
        rows = self._lister_media_azura(station_id)
        if rows is None:
            raise RuntimeError("API AzuraCast indisponible")
        if log_fn:
            log_fn(f"[MAPPING] Station {station_id} : {len(rows)} médias AzuraCast")

        db = pymysql.connect(**self.db_config)
        cur = db.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT ID, artist, title, album, `path`, comments "
            "FROM radiodb.songs WHERE song_type = 0"
        )
        songs = cur.fetchall()
        cur.close()
        db.close()
        if log_fn:
            log_fn(f"[MAPPING] Station {station_id} : {len(songs)} songs RadioDJ")

        # Index AzuraCast par basename exact puis normalisé, et par at normalisé
        idx_bn, idx_bn_norm, idx_at = {}, {}, {}
        for m in rows:
            bn = basename(m.get('path') or '').lower().strip()
            if bn and bn not in idx_bn:
                idx_bn[bn] = m
            bn_n = normaliser_pour_recherche(basename(m.get('path') or ''))
            if bn_n and bn_n not in idx_bn_norm:
                idx_bn_norm[bn_n] = m
            at = normaliser_pour_recherche(
                f"{m.get('artist') or ''} - {m.get('title') or ''}")
            if at and at not in idx_at:
                idx_at[at] = m

        paires, par_methode = [], {}
        for s in songs:
            match = methode = None
            bn = basename(s.get('path') or '').lower().strip()
            if bn and bn in idx_bn:
                match, methode = idx_bn[bn], 'path_exact'
            else:
                bn_n = normaliser_pour_recherche(basename(s.get('path') or ''))
                if bn_n and bn_n in idx_bn_norm:
                    match, methode = idx_bn_norm[bn_n], 'path_normalized'
                else:
                    at = normaliser_pour_recherche(
                        f"{s.get('artist') or ''} - {s.get('title') or ''}")
                    if at and at in idx_at:
                        match, methode = idx_at[at], 'artist_title_normalized'
            if match:
                paires.append((s, match, methode))
                par_methode[methode] = par_methode.get(methode, 0) + 1

        nb = self.alimenter_cache_batch(paires, station_id)
        if log_fn:
            log_fn(f"[MAPPING] Station {station_id} : {nb} correspondance(s) "
                   f"écrite(s) {par_methode}")
        return {
            'station_id': station_id,
            'medias_azura': len(rows),
            'songs_radiodj': len(songs),
            'matches': nb,
            'par_methode': par_methode,
        }


# ── Hotfix 2026.11.13-E : colonnes optionnelles de radiodb.songs ──────────
# Les noms varient selon la version RadioDJ (ex. play_count / count_played /
# played_count) et certaines colonnes peuvent être absentes. Même principe
# que _COLONNES_OPTIONNELLES_CANDIDATS du worker / azuracast.py, mais ciblé
# explicitement sur le schéma « radiodb » : le moteur lit FROM radiodb.songs
# alors que la détection DATABASE() du worker ne couvre que son schéma par
# défaut — d'où une détection dédiée ici.
_COLONNES_SONGS_RADIO_CANDIDATS = {
    'weight':      ['weight'],
    'play_count':  ['play_count', 'count_played', 'played_count'],
    'date_played': ['date_played', 'last_played', 'date_last_played'],
    'date_added':  ['date_added'],
}
_colonnes_songs_radio_cache = None  # peuplé au 1er appel (durée de vie process)


def _detecter_colonnes_songs_radio(db_config):
    """Détecte les colonnes optionnelles de radiodb.songs via
    INFORMATION_SCHEMA (1 requête par process, résultat en cache module).

    Returns: {cle_logique: nom_sql_trouve_ou_None} — cle à None si aucune
    colonne candidate n'existe dans la table.
    """
    global _colonnes_songs_radio_cache
    if _colonnes_songs_radio_cache is not None:
        return _colonnes_songs_radio_cache
    resultat = {k: None for k in _COLONNES_SONGS_RADIO_CANDIDATS}
    try:
        db = pymysql.connect(**db_config)
        cur = db.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'radiodb' AND TABLE_NAME = 'songs'"
        )
        colonnes_reelles = {r[0] for r in cur.fetchall()}
        cur.close()
        db.close()
        for cle, candidats in _COLONNES_SONGS_RADIO_CANDIDATS.items():
            for c in candidats:
                if c in colonnes_reelles:
                    resultat[cle] = c
                    break
    except Exception:
        pass  # détection impossible → tout None : SELECT avec fallback NULL
    _colonnes_songs_radio_cache = resultat
    return resultat


# ═══════════════════════════════════════════════════════════════════════════
# 2. MoteurRotation (spec Module 2, Phases 8/9/10 + R12)
# ═══════════════════════════════════════════════════════════════════════════

class MoteurRotation:
    """Calcul des candidats au retrait + exécution des rotations.

    Le mode 'hard' (suppression physique) est REFUSÉ par défaut
    (cadrage Phase 21 question 6 « encore en réflexion ») :
    config.json → automation.rotation_hard_autorise = true pour l'ouvrir,
    et même alors un média partagé S6/S7 retombe en soft (coordination).
    """

    def __init__(self, db_config, config=None):
        self.db_config = db_config
        self.config = config or lire_config_rotation()
        self.resolveur = ResolveurMapping(db_config)

    # ── Lecture RadioDJ ──

    def lire_categories_eligibles(self, noms_eligibles):
        """Retourne l'ensemble des id_subcat éligibles à partir des NOMS de
        catégories RadioDJ (join subcategory → category). Graceful : tables
        absentes ou liste vide → None (= toutes éligibles)."""
        if not noms_eligibles:
            return None
        try:
            db = pymysql.connect(**self.db_config)
            cur = db.cursor()
            cur.execute(
                "SELECT sc.id FROM subcategory sc "
                "JOIN category c ON c.id = sc.id_cat "
                "WHERE c.name IN (%s)" % ','.join(['%s'] * len(noms_eligibles)),
                [str(n).strip() for n in noms_eligibles]
            )
            ids = {r[0] for r in cur.fetchall()}
            cur.close()
            db.close()
            return ids or None  # aucune correspondance → toutes (sécurité)
        except Exception:
            return None

    def lire_songs_radiodj(self, log_fn=None):
        """Lit les songs RadioDJ utiles au scoring (52k lignes, 1 requête).

        Hotfix 2026.11.13-E : SELECT reconstruit d'après les colonnes
        réellement présentes dans radiodb.songs (détection
        INFORMATION_SCHEMA, cf. _detecter_colonnes_songs_radio). Colonne
        absente → NULL calculé + signal désactivé au scoring : l'erreur
        1054 « Unknown column 'play_count' » ne peut plus survenir.
        """
        cols = _detecter_colonnes_songs_radio(self.db_config)
        if log_fn:
            manquantes = [k for k, v in cols.items() if not v]
            if manquantes:
                log_fn("[PREVIEW] radiodb.songs sans colonne(s) "
                       + ", ".join(manquantes)
                       + " → signal(s) désactivé(s) au scoring")

        # Alias systématique « AS <cle_logique> » : sans lui le DictCursor
        # retournerait le nom physique (ex. count_played) et le scoring
        # (song.get('play_count')) ne verrait pas la valeur.
        def _sel(cle):
            c = cols.get(cle)
            return ("`" + c + "` AS " + cle) if c else ("NULL AS " + cle)

        db = pymysql.connect(**self.db_config)
        cur = db.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT ID, artist, title, album, `path`, "
            + _sel('weight') + ", "
            + _sel('play_count') + ", "
            + _sel('date_played') + ", "
            + _sel('date_added') + ", "
            + "id_subcat, comments FROM radiodb.songs WHERE song_type = 0"
        )
        songs = cur.fetchall()
        cur.close()
        db.close()
        # Signaux réellement disponibles, lus par calculer_score_titre()
        self._signaux_scoring = {
            'weight': bool(cols.get('weight')),
            'play_count': bool(cols.get('play_count')),
            'date_played': bool(cols.get('date_played')),
        }
        return songs

    # ── Scoring (spec Phase 8) ──

    def calculer_score_titre(self, song, params):
        """Score de priorité AU RETRAIT. Élevé = candidat à sortir.

        Blocs (spec) :
          weight=0 → +30 ; weight<3 → +15
          play_count=0 → +20 ; play_count < seuil → +10
          date_played nulle → +20 ; ancienneté > seuil → +25
          (l'âge RETIRED vient avec l'auto-purge, lot 21.4)
        Hotfix 2026.11.13-E : chaque bloc est conditionné au signal réellement
        présent dans radiodb.songs (self._signaux_scoring, cf.
        lire_songs_radiodj) — un signal indisponible est ignoré (ni points ni
        raison trompeuse), les autres gardent leur valeur.
        Returns: {'score': int, 'raisons': [str]}
        """
        signaux = getattr(self, '_signaux_scoring', None) or \
            {'weight': True, 'play_count': True, 'date_played': True}
        score, raisons = 0, []
        if signaux.get('weight'):
            weight = song.get('weight') or 0
            if weight == 0:
                score += 30
                raisons.append("weight=0 (jamais prioritaire)")
            elif weight < 3:
                score += 15
                raisons.append(f"weight faible ({weight})")

        if signaux.get('play_count'):
            pc = song.get('play_count') or 0
            seuil_pc = int(params.get('seuil_play_count_min', 5))
            if pc == 0:
                score += 20
                raisons.append("play_count=0 (jamais diffusé RadioDJ)")
            elif pc < seuil_pc:
                score += 10
                raisons.append(f"play_count < {seuil_pc}")

        if signaux.get('date_played'):
            dp = song.get('date_played')
            seuil_anc = int(params.get('seuil_anciennete_jours', 60))
            if dp:
                anciennete = (datetime.now() - dp).days
                if anciennete > seuil_anc:
                    score += 25
                    raisons.append(f"pas diffusé depuis {anciennete}j")
            else:
                score += 20
                raisons.append("date_played nulle (jamais joué)")

        return {'score': score, 'raisons': raisons}

    # ── Candidats (spec Phase 9) ──

    def calculer_candidats_rotation(self, station_id, params=None, log_fn=None):
        """Liste les candidats au retrait d'une station (SANS rien exécuter).

        Workflow :
          1. list_media_all() sur la station (pagination GF2 — 21.1)
          2. songs RadioDJ (1 requête) + matching 4 passes bidirectionnel
             → alimente mapping_radiodj_azura au passage
          3. exclusions : dossiers protégés, protection récence
             (uploaded_at AzuraCast sinon date_added RadioDJ), catégories
          4. scoring + groupement versions (R12) + tri score décroissant
          5. ratio : plafond nb_max_candidats (défaut, remplace le ratio
             strict de la spec pour rester lisible à l'écran — le ratio
             sert de garde total côté config)
        Returns: dict complet (stats + groupes) prêt pour rotation_previews.
        """
        cfg = self.config['rotation'] if 'rotation' in self.config \
            else lire_config_rotation()['rotation']
        p = dict(cfg)
        if params:
            p.update({k: v for k, v in params.items() if v is not None})

        rows = self.resolveur._lister_media_azura(station_id)
        if rows is None:
            raise RuntimeError("API AzuraCast indisponible (get_api_for_station)")
        if log_fn:
            log_fn(f"[PREVIEW] Station {station_id} : {len(rows)} médias AzuraCast")

        songs = self.lire_songs_radiodj(log_fn=log_fn)
        if log_fn:
            log_fn(f"[PREVIEW] {len(songs)} songs RadioDJ (song_type=0)")

        # Matching batch : index AzuraCast (basename exact / norm / at norm)
        idx_bn, idx_bn_norm, idx_at = {}, {}, {}
        for m in rows:
            bn = basename(m.get('path') or '').lower().strip()
            if bn and bn not in idx_bn:
                idx_bn[bn] = m
            bn_n = normaliser_pour_recherche(basename(m.get('path') or ''))
            if bn_n and bn_n not in idx_bn_norm:
                idx_bn_norm[bn_n] = m
            at = normaliser_pour_recherche(
                f"{m.get('artist') or ''} - {m.get('title') or ''}")
            if at and at not in idx_at:
                idx_at[at] = m

        dossiers_proteges = [d.strip().upper() for d in
                             (p.get('dossiers_proteges') or []) if str(d).strip()]
        subcats_eligibles = self.lire_categories_eligibles(
            p.get('categories_elegibles') or [])
        protection = int(p.get('duree_protection_jours', 30))
        maintenant = datetime.now()
        seuil_protection = maintenant - timedelta(days=protection)

        paires_cache = []
        candidats = []
        nb_proteges = nb_exclus_cat = nb_dossier = 0

        for s in songs:
            flag = s.get('comments') or ''
            etats = decomposer_flag(flag)
            # Candidats = titres présents sur CETTE station (tout état sauf REMOVED)
            etat_station = etats.get(int(station_id))
            if etat_station is None or etat_station == 'REMOVED':
                continue

            bn = basename(s.get('path') or '').lower().strip()
            media = None
            methode = None
            if bn and bn in idx_bn:
                media, methode = idx_bn[bn], 'path_exact'
            else:
                bn_n = normaliser_pour_recherche(basename(s.get('path') or ''))
                if bn_n and bn_n in idx_bn_norm:
                    media, methode = idx_bn_norm[bn_n], 'path_normalized'
                else:
                    at = normaliser_pour_recherche(
                        f"{s.get('artist') or ''} - {s.get('title') or ''}")
                    if at and at in idx_at:
                        media, methode = idx_at[at], 'artist_title_normalized'

            if media:
                paires_cache.append((s, media, methode))

            path_up = (s.get('path') or '').upper()
            if any(d in path_up for d in dossiers_proteges):
                nb_dossier += 1
                continue
            if subcats_eligibles is not None and \
                    s.get('id_subcat') not in subcats_eligibles:
                nb_exclus_cat += 1
                continue

            # Protection récence : uploaded_at AzuraCast sinon date_added RadioDJ
            date_ref = None
            uploaded = media.get('uploaded_at') if media else None
            if isinstance(uploaded, (int, float)) and uploaded > 0:
                date_ref = datetime.fromtimestamp(uploaded)
            elif s.get('date_added'):
                date_ref = s['date_added']
            if date_ref and date_ref > seuil_protection:
                nb_proteges += 1
                continue

            sc = self.calculer_score_titre(s, p)
            if sc['score'] <= 0:
                continue  # aucun motif de retrait

            anciennete_j = None
            if s.get('date_played'):
                anciennete_j = (maintenant - s['date_played']).days
            candidats.append({
                'song_id': s['ID'],
                'media_id': (media or {}).get('id'),
                'artist': s.get('artist') or '',
                'title': s.get('title') or '',
                'album': s.get('album') or '',
                'path': s.get('path') or '',
                'flag': flag,
                'weight': s.get('weight'),
                'play_count': s.get('play_count'),
                'anciennete_jours': anciennete_j,
                'score': sc['score'],
                'raisons': sc['raisons'],
                'media_trouve': bool(media),
                'methode_matching': methode,
            })

        # Alimente le cache mapping (bootstrap incrémental gratuit)
        nb_cache = self.resolveur.alimenter_cache_batch(paires_cache, station_id)

        candidats.sort(key=lambda c: -c['score'])
        nb_max = int(p.get('nb_max_candidats', 2000))
        candidats_plaf = candidats[:nb_max]

        groupes = self.grouper_candidats_par_version(candidats_plaf)

        result = {
            'station_id': station_id,
            'parametres': {
                'ratio_rotation': p.get('ratio_rotation'),
                'duree_protection_jours': p.get('duree_protection_jours'),
                'seuil_play_count_min': p.get('seuil_play_count_min'),
                'seuil_anciennete_jours': p.get('seuil_anciennete_jours'),
                'categories_elegibles': p.get('categories_elegibles') or [],
                'dossiers_proteges': p.get('dossiers_proteges') or [],
                'nb_max_candidats': nb_max,
            },
            'config_effective': {
                'rotation_hard_autorise':
                    bool(self.config['automation'].get('rotation_hard_autorise')),
                'mode_defaut': 'soft',
                'note_hard': ("mode hard refusé tant que le cadrage question 6 "
                              "(exécution hard) n'est pas validée"),
            },
            'stats': {
                'medias_azura': len(rows),
                'songs_radiodj': len(songs),
                'matches_mapping': len(paires_cache),
                'cache_ecrit': nb_cache,
                'proteges_recence': nb_proteges,
                'exclus_dossier_protege': nb_dossier,
                'exclus_categorie': nb_exclus_cat,
                'candidats_total': len(candidats),
                'candidats_affiches': len(candidats_plaf),
                'nb_groupes': len(groupes),
                # Hotfix 2026.11.13-E : signaux songs indisponibles (colonnes
                # absentes de radiodb.songs) — visibles dans le [STATS] du log
                'signaux_songs_indisponibles': [
                    k for k, v in
                    (getattr(self, '_signaux_scoring', {}) or {}).items()
                    if not v
                ],
            },
            'groupes': groupes,
            'date_generation': maintenant.strftime('%Y-%m-%d %H:%M:%S'),
        }
        if log_fn:
            log_fn(
                f"[PREVIEW] {len(candidats)} candidat(s) "
                f"({len(groupes)} groupes après R12) — cache mapping +{nb_cache} — "
                f"protégés:{nb_proteges} dossiers:{nb_dossier} catégories:{nb_exclus_cat}"
            )
        return result

    # ── Groupement versions (R12, spec Phase 12) ──

    def grouper_candidats_par_version(self, candidats):
        """Groupe par (artist, title) normalisé — score_groupe = max versions."""
        groupes = {}
        for c in candidats:
            cle = normaliser_pour_recherche(f"{c['artist']} - {c['title']}")
            if cle not in groupes:
                groupes[cle] = {
                    'cle_groupe': cle,
                    'artist': c['artist'],
                    'title': c['title'],
                    'versions': [],
                }
            groupes[cle]['versions'].append(c)
        out = []
        for g in groupes.values():
            g['score_groupe'] = max(v['score'] for v in g['versions'])
            g['nb_versions'] = len(g['versions'])
            raisons = []
            for v in g['versions']:
                for r in v['raisons']:
                    if r not in raisons:
                        raisons.append(r)
            g['raisons_groupe'] = raisons[:8]
            out.append(g)
        out.sort(key=lambda x: -x['score_groupe'])
        return out

    # ── Exécution (spec Phase 10 + A.6) ──

    def executer_rotation(self, station_id, song_ids, mode='soft',
                          task_id=None, log_fn=None, trigger_source=None):
        """Exécute la rotation sur une liste de song_ids RadioDJ.

        mode='soft'  : retrait de toutes les playlists + flag AZURA_RETIRED_S*
        mode='hard'  : suppression du média + flag AZURA_REMOVED_S*
                       → refusé (fallback soft) sauf si
                       automation.rotation_hard_autorise = true, et un média
                       partagé avec l'autre station retombe toujours en soft.

        Returns: dict {success, erreurs, non_trouves, fallback_soft, details}.
        """
        hard_demande = (str(mode).lower() == 'hard')
        hard_autorise = bool(
            self.config['automation'].get('rotation_hard_autorise'))
        mode_effectif = 'soft'
        if hard_demande and hard_autorise:
            mode_effectif = 'hard'

        if get_api_for_station is None:
            raise RuntimeError("Module azuracast_api indisponible")
        api = get_api_for_station(station_id)

        resultats = {
            'success': 0, 'erreurs': 0, 'non_trouves': 0,
            'fallback_soft': 0, 'mode_demande': mode, 'mode_effectif': mode_effectif,
            'details': [],
        }

        # Lire les songs (une requête pour toutes)
        db = pymysql.connect(**self.db_config)
        cur = db.cursor(pymysql.cursors.DictCursor)
        ph = ','.join(['%s'] * len(song_ids))
        cur.execute(
            f"SELECT ID, artist, title, album, `path`, comments "
            f"FROM radiodb.songs WHERE ID IN ({ph})",
            list(song_ids)
        )
        songs = {s['ID']: s for s in cur.fetchall()}
        cur.close()
        db.close()

        rows_azura = self.resolveur._lister_media_azura(station_id)
        if rows_azura is None:
            rows_azura = []

        for song_id in song_ids:
            song = songs.get(song_id)
            if not song:
                resultats['non_trouves'] += 1
                resultats['details'].append(
                    {'song_id': song_id, 'erreur': 'song RadioDJ introuvable'})
                continue

            match = self.resolveur.song_radiodj_vers_media_azura(
                song, station_id, media_rows=rows_azura)
            if not match:
                resultats['non_trouves'] += 1
                resultats['details'].append(
                    {'song_id': song_id,
                     'erreur': 'pas de correspondance AzuraCast (mapping)'})
                continue

            media_id = match['media_id_azura']
            try:
                mode_pour_ce_media = mode_effectif
                if mode_pour_ce_media == 'hard':
                    partagé = self._media_partage_autre_station(
                        song, station_id, rows_azura)
                    if partagé:
                        mode_pour_ce_media = 'soft'
                        resultats['fallback_soft'] += 1
                        if log_fn:
                            log_fn(
                                f"[ROTATION] song {song_id} partagé S6/S7 → "
                                f"fallback soft (coordination Phase 10)")

                if mode_pour_ce_media == 'soft':
                    api.update_media(media_id, playlists=[])
                    nouveau_flag = recalcule_flag_apres_op(
                        song.get('comments'), station_id, 'RETIRED')
                    self._maj_flag(song_id, nouveau_flag)
                    journaliser_operation(
                        self.db_config, 'rotation_soft',
                        station_id=station_id, media_id_azura=media_id,
                        song_id_radiodj=song_id, triggered_by='preview_accept',
                        task_id=task_id, trigger_source=trigger_source,
                        details={'methode_matching': match['methode'],
                                 'flag_avant': song.get('comments'),
                                 'flag_apres': nouveau_flag})
                else:
                    api.delete_media(media_id)
                    self.resolveur.invalider_correspondance(
                        song_id=song_id, station_id=station_id,
                        raison='azura_deleted')
                    nouveau_flag = recalcule_flag_apres_op(
                        song.get('comments'), station_id, 'REMOVED')
                    self._maj_flag(song_id, nouveau_flag)
                    journaliser_operation(
                        self.db_config, 'rotation_hard',
                        station_id=station_id, media_id_azura=media_id,
                        song_id_radiodj=song_id, triggered_by='preview_accept',
                        task_id=task_id, trigger_source=trigger_source,
                        details={'methode_matching': match['methode'],
                                 'flag_avant': song.get('comments'),
                                 'flag_apres': nouveau_flag})

                resultats['success'] += 1
                if log_fn:
                    log_fn(f"[ROTATION] song {song_id} ({song.get('artist')} - "
                           f"{song.get('title')}) : {mode_pour_ce_media} OK "
                           f"(media {media_id}, flag → {nouveau_flag})")

            except AzuraCastAPIError as e:
                resultats['erreurs'] += 1
                resultats['details'].append(
                    {'song_id': song_id, 'media_id': media_id,
                     'erreur': str(e)})
                if log_fn:
                    log_fn(f"[ROTATION] ERREUR song {song_id} "
                           f"(media {media_id}) : {e}")
            except Exception as e:
                resultats['erreurs'] += 1
                resultats['details'].append(
                    {'song_id': song_id, 'media_id': media_id,
                     'erreur': f"{type(e).__name__}: {e}"})
                if log_fn:
                    log_fn(f"[ROTATION] ERREUR song {song_id} : {e}")

        return resultats

    # ── Coordination S6/S7 (spec Phase 10) ──

    def _media_partage_autre_station(self, song, station_id, rows_azura):
        """True si le fichier est partagé avec l'AUTRE station (6 ↔ 7).

        Heuristique robuste :
          1. flag combiné (S6_S7) → partagé ;
          2. correspondance active sur l'autre station dans le mapping.
        """
        etats = decomposer_flag(song.get('comments'))
        autres = [sid for sid in etats if int(sid) != int(station_id)]
        if autres:
            return True
        # Sinon : mapping actif sur l'autre station ?
        cache = self.resolveur.chercher_cache(song.get('ID'),
                                              6 if int(station_id) == 7 else 7)
        if cache and cache.get('actif'):
            return True
        return False

    def _maj_flag(self, song_id, nouveau_flag):
        """Écrit le flag recomposé dans radiodb.songs.comments."""
        db = pymysql.connect(**self.db_config)
        cur = db.cursor()
        cur.execute("UPDATE radiodb.songs SET comments = %s WHERE ID = %s",
                    (nouveau_flag, song_id))
        db.commit()
        cur.close()
        db.close()
