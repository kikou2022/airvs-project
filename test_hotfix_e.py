# -*- coding: utf-8 -*-
"""Validation du hotfix 2026.11.13-E (rotation_azura.py) — play_count optionnel.

Teste en local (sans MySQL ni réseau) :
  1. la construction du SELECT selon les colonnes détectées (alias / NULL)
  2. les signaux de scoring (_signaux_scoring) et le scoring dégradé
  3. la détection _detecter_colonnes_songs_radio (cache module, branche "trouvé")
  4. la compilation du module
"""
import sys
import types
import py_compile

MODULE_DIR = '/home/z/my-project/download/phase21_3_rotation_editoriale/deploy/ubuntu_17_worker'
sys.path.insert(0, MODULE_DIR)

# 0. Compilation (syntaxe) — n'exécute pas les imports
py_compile.compile(MODULE_DIR + '/rotation_azura.py', doraise=True)
print('OK  py_compile rotation_azura.py')

# 1. Import avec pymysql disponible ou simulé
try:
    import pymysql  # noqa: F401
    print('OK  pymysql réel disponible')
except ImportError:
    stub = types.ModuleType('pymysql')
    stub.connect = lambda **k: None
    stub.cursors = types.SimpleNamespace(DictCursor=object)
    sys.modules['pymysql'] = stub
    print('OK  pymysql simulé (stub)')

import rotation_azura as rz
from datetime import datetime, timedelta

SQLS = []


class FakeCur:
    """Curseur factique : enregistre le SQL, retourne des lignes prédéfinies."""
    def __init__(self, rows=None):
        self.rows = rows or []
    def execute(self, sql, *a, **k):
        SQLS.append(sql)
    def fetchall(self):
        return self.rows
    def close(self):
        pass


class FakeDB:
    def __init__(self, rows=None):
        self._rows = rows
    def cursor(self, *a, **k):
        return FakeCur(self._rows)
    def close(self):
        pass


def nouveau_moteur():
    """MoteurRotation sans __init__ (pas de lecture de config / resolveur)."""
    m = object.__new__(rz.MoteurRotation)
    m.db_config = {'host': 'fake'}
    m.config = {'rotation': {}, 'automation': {}}
    return m


def reset_cache(cols):
    rz._colonnes_songs_radio_cache = cols
    SQLS.clear()


echecs = []


def check(nom, cond):
    print(('OK  ' if cond else 'ECHEC  ') + nom)
    if not cond:
        echecs.append(nom)


# ── Cas A : colonnes trouvées sous alias (ex. count_played) ──
cols_a = {'weight': 'weight', 'play_count': 'count_played',
          'date_played': 'date_played', 'date_added': 'date_added'}
reset_cache(cols_a)
rz.pymysql.connect = lambda **k: FakeDB()
m = nouveau_moteur()
logs = []
songs = m.lire_songs_radiodj(log_fn=logs.append)
sql = SQLS[0]
check('A1 SELECT avec alias count_played', '`count_played` AS play_count' in sql)
check('A2 pas de NULL AS play_count', 'NULL AS play_count' not in sql)
check('A3 FROM radiodb.songs WHERE song_type = 0 conservé',
      sql.endswith('FROM radiodb.songs WHERE song_type = 0'))
check('A4 base avant colonnes dynamiques intacte',
      sql.startswith('SELECT ID, artist, title, album, `path`, '))
check('A5 signaux tous actifs', m._signaux_scoring == {
    'weight': True, 'play_count': True, 'date_played': True})
check('A6 aucun log de colonnes manquantes', not logs)

# Scoring avec tous les signaux : poids 0, jamais joué, vieux titre
sc = m.calculer_score_titre(
    {'weight': 0, 'play_count': 0, 'date_played': datetime(2026, 1, 1)},
    {'seuil_play_count_min': 5, 'seuil_anciennete_jours': 60})
check('A7 score complet = 75 (30+20+25)', sc['score'] == 75 and len(sc['raisons']) == 3)
sc = m.calculer_score_titre(
    {'weight': 5, 'play_count': 99, 'date_played': datetime.now()},
    {'seuil_play_count_min': 5, 'seuil_anciennete_jours': 60})
check('A8 titre sain = 0', sc['score'] == 0 and sc['raisons'] == [])

# ── Cas B : aucune colonne optionnelle (cas réel install .30 suspecté) ──
cols_b = {k: None for k in cols_a}
reset_cache(cols_b)
rz.pymysql.connect = lambda **k: FakeDB()
m = nouveau_moteur()
logs = []
songs = m.lire_songs_radiodj(log_fn=logs.append)
sql = SQLS[0]
check('B1 NULL AS weight', 'NULL AS weight' in sql)
check('B2 NULL AS play_count', 'NULL AS play_count' in sql)
check('B3 NULL AS date_played', 'NULL AS date_played' in sql)
check('B4 NULL AS date_added', 'NULL AS date_added' in sql)
check('B5 plus aucune colonne physique backtickée',
      all(x not in sql for x in ['`weight`', '`play_count`', '`date_played`',
                                 '`date_added`']))
check('B6 signaux tous désactivés', m._signaux_scoring == {
    'weight': False, 'play_count': False, 'date_played': False})
check('B7 log colonnes manquantes émis',
      any('sans colonne(s)' in l and 'weight' in l for l in logs))

# Scoring dégradé : aucun point, aucune raison trompeuse
sc = m.calculer_score_titre(
    {'weight': 0, 'play_count': None, 'date_played': None},
    {'seuil_play_count_min': 5, 'seuil_anciennete_jours': 60})
check('B8 score dégradé = 0 (ni +30, ni +20)', sc['score'] == 0 and sc['raisons'] == [])

# Cas intermédiaire : date_played dispo, play_count absent
cols_c = {'weight': 'weight', 'play_count': None,
          'date_played': 'last_played', 'date_added': 'date_added'}
reset_cache(cols_c)
m = nouveau_moteur()
m.lire_songs_radiodj()
check('C1 signaux partiels', m._signaux_scoring == {
    'weight': True, 'play_count': False, 'date_played': True})
sc = m.calculer_score_titre(
    {'weight': 0, 'play_count': None,
     'date_played': datetime.now() - timedelta(days=120)},
    {'seuil_play_count_min': 5, 'seuil_anciennete_jours': 60})
check('C2 score partiel = 55 (30+25, pas de +20 play_count)',
      sc['score'] == 55 and sc['raisons'] == ['weight=0 (jamais prioritaire)',
                                              'pas diffusé depuis 120j'])

# ── Détection réelle (branche "trouvé") + cache ──
rz._colonnes_songs_radio_cache = None
SQLS.clear()
rz.pymysql.connect = lambda **k: FakeDB(rows=[('ID',), ('play_count',),
                                              ('count_played',), ('last_played',),
                                              ('weight',), ('date_added',)])
cols = rz._detecter_colonnes_songs_radio({'host': 'fake'})
check('D1 détection alias play_count→play_count', cols['play_count'] == 'play_count')
check('D2 détection date_played→last_played', cols['date_played'] == 'last_played')
check('D3 détection weight→weight', cols['weight'] == 'weight')
check('D4 détection mise en cache', rz._colonnes_songs_radio_cache is cols)
# Détection en échec → tout None (graceful)
rz._colonnes_songs_radio_cache = None
def _boom(**k):
    raise Exception('MySQL injoignable')
rz.pymysql.connect = _boom
cols = rz._detecter_colonnes_songs_radio({'host': 'fake'})
check('D5 échec détection → tout None sans lever',
      all(v is None for v in cols.values()))
rz._colonnes_songs_radio_cache = None

print()
if echecs:
    print(f'== {len(echecs)} ECHEC(S) : {echecs}')
    sys.exit(1)
print('== Tous les tests du hotfix E passent ==')
