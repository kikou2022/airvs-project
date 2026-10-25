import os
import re
import json
import queue
import threading
import logging
import subprocess as sp
import pymysql
from pathlib import Path, PureWindowsPath
from dotenv import load_dotenv

load_dotenv()


# ══════════════════════════════════════════
# 1. CONFIGURATION SQL UNIVERSELLE
# ══════════════════════════════════════════
DB_CONFIG = {
    'host':         os.getenv("DB_HOST", "localhost"),
    'port':         3306,
    'user':         os.getenv("DB_USER", "airvs_user"),
    'password':     os.getenv("DB_PASSWORD", "idylle@SL2026!"),
    'database':     os.getenv("DB_NAME", "airvs_dashboard"),
    'charset':      'utf8mb4',
    'collation':    'utf8mb4_general_ci',
    'cursorclass':  pymysql.cursors.DictCursor
}

def get_db_connection():
    """Cree et retourne une connexion MySQL propre."""
    try:
        return pymysql.connect(**DB_CONFIG)
    except pymysql.Error as err:
        print(f"[ERREUR DB] Connexion impossible : {err}")
        return None


# ══════════════════════════════════════════
# 2. TRADUCTION DE CHEMINS (Windows -> Linux)
# ══════════════════════════════════════════
MOUNT_MAP = {
    "D:\\": "/mnt/projet_radio2",
    "E:\\": "/mnt/projet_radio3",
    "K\\": "/mnt/projet_radio3",
    "M:\\": "/mnt/musique_172_go",
    "L:\\": "/mnt/musique_47_go",
    "N:\\": "/media/sebastien/musique_debian",
    "U:\\": "/mnt/stockage_160go/",
    "Y:\\": "/mnt/stockage_80go",
    "Z:\\": "/mnt/externe",
}
DOSSIERS_A_IGNORER = {"projet_radio"}


def windows_vers_linux(chemin_windows: str) -> Path:
    """Convertit un chemin Windows (D:\\Musique\\...) en chemin Linux SSHFS (/mnt/...)."""
    p = PureWindowsPath(chemin_windows)
    lecteur = p.drive + "\\"
    if lecteur not in MOUNT_MAP:
        raise ValueError(f"Lecteur inconnu '{lecteur}' pour : {chemin_windows}")

    mount = MOUNT_MAP[lecteur]
    parties_sans_lecteur = p.parts[1:]
    if parties_sans_lecteur and parties_sans_lecteur[0] in DOSSIERS_A_IGNORER:
        parties_sans_lecteur = parties_sans_lecteur[1:]
    return Path(mount).joinpath(*parties_sans_lecteur)


# ══════════════════════════════════════════
# 3. NETTOYAGE DE TEXTE (Metadonnees)
# ══════════════════════════════════════════
SEPARATEURS_ARTISTE = r'[;,/&]|feat\.?|ft\.?|vs\.?'


def nettoyer_artiste_pour_recherche(raw_artist: str) -> str:
    """Nettoie 'Artiste feat. Quelqu'un' en 'Artiste' (pour les requetes SQL LIKE)."""
    if not raw_artist:
        return ""
    artistes_propres = re.split(SEPARATEURS_ARTISTE, raw_artist, flags=re.IGNORECASE)
    artist = artistes_propres[0].strip()
    return re.sub(r'\s+', ' ', artist)


def sanitiser_nom_fichier(nom: str) -> str:
    """Supprime les caracteres interdits pour les noms de dossier/fichier."""
    if not nom:
        return ""
    return re.sub(r'[\\/*?:"<>|]', "", nom).strip()


# ══════════════════════════════════════════
# 4. DETECTION DE VERSIONS (Remix / Live)
# ══════════════════════════════════════════
VERSION_REGEX = re.compile(
    r'(?i)\b(remix|remastered?|remaster|edit|version|mix|live|acoustic|demo|'
    r're-?recorded|extended|instrumental|radio\s*edit|club\s*mix|dub|original\s*mix)\b'
)


def is_version(title: str) -> bool:
    """Retourne True si le titre est un remix, un live, un remaster, etc."""
    if not title:
        return False
    return bool(VERSION_REGEX.search(title))


# ═══════════════════════════════════════════════════════════════════════
# 5. LOGGER AIRVS (persistant + console)
# Extrait de app.py (Phase 1.2)
# ═══════════════════════════════════════════════════════════════════════
from logging.handlers import RotatingFileHandler

_log_dir = Path(__file__).parent / 'logs'
_log_dir.mkdir(exist_ok=True)
_logger = logging.getLogger('airvs')
_logger.setLevel(logging.INFO)
_fh = RotatingFileHandler(_log_dir / 'airvs.log', maxBytes=2*1024*1024, backupCount=3, encoding='utf-8')
_fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
_logger.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
_logger.addHandler(_ch)


# ═══════════════════════════════════════════════════════════════════════
# 6. ETAT PARTAGE (sync atelier)
# Extrait de app.py (Phase 1.2) — utilise par les routes atelier + helpers
# ═══════════════════════════════════════════════════════════════════════
task_queue = queue.Queue()
stop_flag = threading.Event()


# ═══════════════════════════════════════════════════════════════════════
# 7. HELPERS TRANSVERSAUX (appeles par plusieurs blueprints)
# ═══════════════════════════════════════════════════════════════════════

def _charger_config_json():
    """Lit config.json a cote de app.py. Retourne {} si fichier absent/corrompu."""
    from config import CONFIG_JSON_PATH
    try:
        if not os.path.exists(CONFIG_JSON_PATH):
            return {}
        with open(CONFIG_JSON_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[CONFIG] Erreur lecture {CONFIG_JSON_PATH} : {e}")
        return {}


def _ssh_debian3(commande):
    """Execute une commande SSH sur Debian 3 et retourne (returncode, stdout, stderr)."""
    from config import DEBIAN_3_SSH_USER, DEBIAN_3_IP, _SUBPROCESS_CREATIONFLAGS
    try:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
       f"{DEBIAN_3_SSH_USER}@{DEBIAN_3_IP}", commande]
        result = sp.run(cmd, capture_output=True, text=True,
                        encoding='utf-8', errors='replace',
                        timeout=10, creationflags=_SUBPROCESS_CREATIONFLAGS)
        # Defensif : stdout/stderr peuvent etre None dans certains cas limites
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except sp.TimeoutExpired:
        return -1, "", "Timeout SSH (10s)"
    except Exception as e:
        return -1, "", str(e)


def _assurer_schema_airvs_avance():
    """Verifie/cree les tables airvs_grille_avance, airvs_historique_avance,
    airvs_pool_titres et airvs_pool_auto_validation.

    Effectue aussi la migration depuis l'ancienne terminologie 'couleur' si
    des tables/colonnes/type_action anciens sont detectes en base.

    Appelee de facon defensive avant chaque endpoint pour tolerer une base non migree.

    IMPORTANT : Cette fonction ne doit JAMAIS lever d'exception
    ni retourner False si la DB est accessible. Chaque bloc est wrappe dans
    son propre try/except pour qu'une erreur sur un bloc ne casse pas les
    autres. L'erreur est logguee sur stdout (visible dans les logs Flask).
    """
    schema_errors = []
    try:
        db = get_db_connection()
        if not db:
            return False
        # IMPORTANT : get_db_connection() configure cursorclass=DictCursor GLOBALEMENT.
        # Cette fonction utilise cursor.fetchone()[0] (acces par index) dans
        # les blocs RENAME / CHANGE COLUMN ci-dessous, ce qui leve KeyError: 0
        # sur un DictCursor. On force donc pymysql.cursors.Cursor (curseur tuple).
        cursor = db.cursor(pymysql.cursors.Cursor)

        def _safe(label, sql, params=None, commit_after=False):
            try:
                cursor.execute(sql, params or ())
                if commit_after:
                    db.commit()
                return True
            except Exception as e:
                msg = f"{label} : {type(e).__name__}: {e}"
                schema_errors.append(msg)
                print(f"[Schema] {msg}")
                return False

        # ── 1. Migration depuis l'ancienne terminologie 'couleur' ──
        for old, new in [
            ('airvs_grille_couleur', 'airvs_grille_avance'),
            ('airvs_historique_couleur', 'airvs_historique_avance'),
        ]:
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = %s",
                    (old,)
                )
                if cursor.fetchone()[0] > 0:
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = DATABASE() AND table_name = %s",
                        (new,)
                    )
                    if cursor.fetchone()[0] == 0:
                        cursor.execute(f"RENAME TABLE {old} TO {new}")
                        print(f"[Migration] {old} → {new}")
            except Exception as e:
                msg = f"RENAME {old}→{new} : {type(e).__name__}: {e}"
                schema_errors.append(msg)
                print(f"[Schema] {msg}")

        for table, old_col, new_col in [
            ('airvs_grille_avance', 'type_couleur', 'type_critere'),
            ('airvs_grille_avance', 'valeur_couleur', 'valeur_critere'),
        ]:
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                    (table, old_col)
                )
                if cursor.fetchone()[0] > 0:
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                        (table, new_col)
                    )
                    if cursor.fetchone()[0] == 0:
                        if old_col == 'type_couleur':
                            cursor.execute(
                                f"ALTER TABLE {table} "
                                f"CHANGE COLUMN {old_col} {new_col} "
                                f"ENUM('artist','genre','subcategory','category','titre','keyword','pool') NOT NULL"
                            )
                        else:
                            cursor.execute(
                                f"ALTER TABLE {table} "
                                f"CHANGE COLUMN {old_col} {new_col} VARCHAR(255) NOT NULL"
                            )
                        print(f"[Migration] {table}.{old_col} → {table}.{new_col}")
            except Exception as e:
                msg = f"CHANGE COLUMN {table}.{old_col}→{new_col} : {type(e).__name__}: {e}"
                schema_errors.append(msg)
                print(f"[Schema] {msg}")

        _safe("UPDATE type_action COULEUR→AVANCE",
              "UPDATE taches_planifiees SET type_action='PROGRAMMATION_AVANCE' "
              "WHERE type_action='PROGRAMMATION_COULEUR'")
        _safe("UPDATE type_action IMPORT_COULEUR→AVANCE",
              "UPDATE taches_planifiees SET type_action='IMPORT_SHEETS_AVANCE' "
              "WHERE type_action='IMPORT_SHEETS_COULEUR'")

        # ── 2. Creation / verification des tables ──
        _safe("CREATE TABLE airvs_grille_avance", """
            CREATE TABLE IF NOT EXISTS airvs_grille_avance (
              id INT AUTO_INCREMENT PRIMARY KEY,
              jour_semaine TINYINT DEFAULT 0,
              heure TIME NOT NULL,
              date_debut DATE DEFAULT NULL,
              date_fin DATE DEFAULT NULL,
              station_id TINYINT NOT NULL DEFAULT 7,
              type_critere ENUM('artist','genre','subcategory','category','titre','keyword','pool') NOT NULL,
              valeur_critere VARCHAR(255) NOT NULL,
              titres_ids JSON DEFAULT NULL
                COMMENT 'Si type_critere=titre: liste de song_ids explicites',
              nb_titres INT NOT NULL DEFAULT 20,
              mode_playlist ENUM('ajouter_existantes','creer_nouvelle','remplacer')
                  NOT NULL DEFAULT 'ajouter_existantes',
              playlist_ids JSON DEFAULT NULL,
              nom_nouvelle_playlist VARCHAR(255) DEFAULT NULL,
              anti_repetition_jours INT NOT NULL DEFAULT 7,
              dossier_cible VARCHAR(255) NOT NULL DEFAULT 'imports_push',
              actif TINYINT NOT NULL DEFAULT 1,
              dry_run TINYINT NOT NULL DEFAULT 1,
              plage_h_debut TIME DEFAULT '06:00:00',
              plage_h_fin TIME DEFAULT '23:00:00',
              dernier_lancement DATETIME DEFAULT NULL,
              commentaire TEXT,
              date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
              INDEX idx_grille_planif (actif, jour_semaine, heure),
              INDEX idx_grille_event (actif, date_debut, date_fin)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        _safe("MODIFY type_critere ENUM +pool",
              "ALTER TABLE airvs_grille_avance "
              "MODIFY COLUMN type_critere "
              "ENUM('artist','genre','subcategory','category','titre','keyword','pool') NOT NULL")

        _safe("ADD COLUMN titres_ids",
              "ALTER TABLE airvs_grille_avance "
              "ADD COLUMN IF NOT EXISTS titres_ids JSON DEFAULT NULL "
              "COMMENT 'Si type_critere=titre: liste de song_ids explicites'")

        _safe("CREATE TABLE airvs_historique_avance", """
            CREATE TABLE IF NOT EXISTS airvs_historique_avance (
              id INT AUTO_INCREMENT PRIMARY KEY,
              grille_id INT NOT NULL,
              song_id INT NOT NULL,
              station_id TINYINT NOT NULL,
              date_lancement DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              tache_id INT DEFAULT NULL,
              mode_playlist VARCHAR(20) DEFAULT NULL,
              playlist_cible VARCHAR(255) DEFAULT NULL,
              INDEX idx_song_date (song_id, date_lancement),
              INDEX idx_grille_date (grille_id, date_lancement),
              INDEX idx_station_date (station_id, date_lancement)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        _safe("CREATE TABLE airvs_pool_titres", """
            CREATE TABLE IF NOT EXISTS airvs_pool_titres (
              id              INT AUTO_INCREMENT PRIMARY KEY,
              id_pool         VARCHAR(80)  NOT NULL,
              sheet_alias     VARCHAR(40)  NOT NULL,
              onglet          VARCHAR(60)  NOT NULL DEFAULT '',
              semaine         VARCHAR(10)  NULL,
              song_id         INT          NULL DEFAULT 0,
              artist          VARCHAR(255),
              title           VARCHAR(255),
              year            VARCHAR(10),
              album           VARCHAR(255),
              date_validation DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              statut          ENUM('valide','manquant','rejete','doublon') NOT NULL DEFAULT 'valide',
              source          VARCHAR(50)  NOT NULL DEFAULT 'inconnu',
              feuille         VARCHAR(50)  DEFAULT NULL,
              ligne           INT          DEFAULT NULL,
              jour            VARCHAR(10)  DEFAULT NULL,
              artiste_saisi   VARCHAR(255) NOT NULL DEFAULT '',
              titre_saisi     VARCHAR(255) NOT NULL DEFAULT '',
              annee_saisie    INT          DEFAULT NULL,
              album_saisi     VARCHAR(255) DEFAULT NULL,
              infos_saisi     TEXT         DEFAULT NULL,
              score_match     INT          DEFAULT NULL,
              motif_rejet     VARCHAR(255) DEFAULT NULL,
              valide_par      VARCHAR(100) DEFAULT NULL,
              INDEX idx_pool_statut (id_pool, statut),
              INDEX idx_song (song_id),
              INDEX idx_date_val (date_validation),
              INDEX idx_source (source)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Migration song_id NOT NULL -> NULL DEFAULT 0
        try:
            dcursor = db.cursor(pymysql.cursors.DictCursor)
            dcursor.execute(
                "SELECT COLUMN_DEFAULT, IS_NULLABLE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'song_id'"
            )
            row = dcursor.fetchone()
            if row and row.get('IS_NULLABLE') == 'NO':
                cursor.execute(
                    "ALTER TABLE airvs_pool_titres "
                    "MODIFY COLUMN song_id INT NULL DEFAULT 0"
                )
                print("[Schema] airvs_pool_titres.song_id : NOT NULL -> NULL DEFAULT 0 (migration)")
            dcursor.close()
        except Exception as e:
            msg = f"MODIFY song_id NULL DEFAULT 0 : {type(e).__name__}: {e}"
            schema_errors.append(msg)
            print(f"[Schema] {msg}")

        # Migration onglet DEFAULT ''
        try:
            dcursor = db.cursor(pymysql.cursors.DictCursor)
            dcursor.execute(
                "SELECT COLUMN_DEFAULT FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'onglet'"
            )
            row = dcursor.fetchone()
            if row and row.get('COLUMN_DEFAULT') is None:
                cursor.execute(
                    "ALTER TABLE airvs_pool_titres "
                    "MODIFY COLUMN onglet VARCHAR(60) NOT NULL DEFAULT ''"
                )
                print("[Schema] airvs_pool_titres.onglet : DEFAULT '' ajoute (migration)")
            dcursor.close()
        except Exception as e:
            msg = f"MODIFY onglet DEFAULT '' : {type(e).__name__}: {e}"
            schema_errors.append(msg)
            print(f"[Schema] {msg}")

        # Etendre ENUM statut pour inclure 'rejete' et 'doublon'
        try:
            dcursor = db.cursor(pymysql.cursors.DictCursor)
            dcursor.execute(
                "SELECT COLUMN_TYPE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'statut'"
            )
            row = dcursor.fetchone()
            if row:
                col_type = (row.get('COLUMN_TYPE') or '').lower()
                valeurs_manquantes = [v for v in ("'rejete'", "'doublon'") if v not in col_type]
                if valeurs_manquantes:
                    cursor.execute(
                        "ALTER TABLE airvs_pool_titres "
                        "MODIFY COLUMN statut ENUM('valide','manquant','rejete','doublon') "
                        "NOT NULL DEFAULT 'valide'"
                    )
                    print(f"[Schema] airvs_pool_titres.statut : ENUM etendu avec {valeurs_manquantes}")
            dcursor.close()
        except Exception as e:
            msg = f"MODIFY statut ENUM rejete/doublon : {type(e).__name__}: {e}"
            schema_errors.append(msg)
            print(f"[Schema] {msg}")

        _safe("CREATE TABLE airvs_pool_auto_validation", """
            CREATE TABLE IF NOT EXISTS airvs_pool_auto_validation (
              id                  INT AUTO_INCREMENT PRIMARY KEY,
              sheet_alias         VARCHAR(40)  NOT NULL,
              onglet              VARCHAR(60)  NOT NULL,
              frequence           ENUM('quotidienne','hebdo') NOT NULL DEFAULT 'quotidienne',
              heure               TIME         NOT NULL DEFAULT '03:00:00',
              jour_semaine        TINYINT      NULL,
              actif               TINYINT      NOT NULL DEFAULT 1,
              derniere_execution  DATETIME     NULL,
              date_creation       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              INDEX idx_actif (actif)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        _safe("CREATE TABLE airvs_auto_check_config", """
            CREATE TABLE IF NOT EXISTS airvs_auto_check_config (
              id                  INT AUTO_INCREMENT PRIMARY KEY,
              actif               TINYINT      NOT NULL DEFAULT 0,
              frequence           VARCHAR(20)  NOT NULL DEFAULT '2x_jour',
              horaires            JSON         NOT NULL,
              sheets_surveilles   JSON         NOT NULL,
              derniere_execution  DATETIME     NULL,
              prochaine_execution DATETIME     NULL,
              date_creation       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # ── E1-bis : suspects_validated (memorisation des decisions humaines) ──
        _safe("CREATE TABLE suspects_validated", """
            CREATE TABLE IF NOT EXISTS suspects_validated (
              id                  INT AUTO_INCREMENT PRIMARY KEY,
              filename            VARCHAR(500) NOT NULL COMMENT 'Nom du fichier tel que detecte',
              artist              VARCHAR(500) NOT NULL COMMENT 'Tag artist au moment de la detection',
              title               VARCHAR(500) NOT NULL COMMENT 'Tag title au moment de la detection',
              methode_detection   VARCHAR(50)  NOT NULL COMMENT 'tags / tags_principal / tags_nu',
              matched_song_id     INT          NULL COMMENT 'ID de la chanson matchee en base RadioDJ',
              matched_artist      VARCHAR(500) NULL COMMENT 'Artist du match BDD',
              matched_title       VARCHAR(500) NULL COMMENT 'Title du match BDD',
              version_terms       JSON         NULL COMMENT 'Termes de version detectes',
              decision            ENUM('valide','rejete') NOT NULL COMMENT 'Decision humaine',
              note_editoriale     TEXT         NULL COMMENT 'Commentaire libre',
              validated_by        VARCHAR(100) NULL COMMENT 'Utilisateur dashboard',
              validated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              INDEX idx_artist_title (artist(255), title(255)),
              INDEX idx_decision (decision),
              INDEX idx_filename (filename(255)),
              INDEX idx_validated_at (validated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_general_ci
        """)

        _safe("CREATE TABLE airvs_notifications", """
            CREATE TABLE IF NOT EXISTS airvs_notifications (
              id                  INT AUTO_INCREMENT PRIMARY KEY,
              type_notification   VARCHAR(30)  NOT NULL DEFAULT 'nouvelles_lignes',
              sheet_alias         VARCHAR(40)  NOT NULL,
              message             TEXT         NOT NULL,
              lu                  TINYINT      NOT NULL DEFAULT 0,
              date_creation       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              INDEX idx_lu (lu),
              INDEX idx_date (date_creation)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        _safe("CREATE TABLE airvs_manquants", """
            CREATE TABLE IF NOT EXISTS airvs_manquants (
              id              INT AUTO_INCREMENT PRIMARY KEY,
              artiste         VARCHAR(500) NOT NULL,
              titre           VARCHAR(500) NOT NULL,
              annee           VARCHAR(10)  DEFAULT NULL,
              album           VARCHAR(500) DEFAULT NULL,
              source          ENUM('prefill_sheets','shazam','animateurs') NOT NULL,
              sheet_alias     VARCHAR(40)  DEFAULT NULL,
              onglet          VARCHAR(60)  DEFAULT NULL,
              semaine         VARCHAR(10)  DEFAULT NULL,
              animateur       VARCHAR(100) DEFAULT NULL,
              origine         VARCHAR(255) DEFAULT NULL,
              statut          ENUM('en_attente','importe','resolu') DEFAULT 'en_attente',
              id_import       INT          DEFAULT NULL,
              resolu_le       DATETIME     DEFAULT NULL,
              date_creation   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE INDEX uq_art_titre_src (artiste(255), titre(255), source),
              INDEX idx_statut (statut),
              INDEX idx_source (source)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # ── Migration airvs_manquants : ENUM enrichi + colonnes correction ──
        # R7 + R16 : rejete, rejete_corrigé + correction_artiste/titre/source/id
        try:
            dcursor = db.cursor(pymysql.cursors.DictCursor)
            dcursor.execute(
                "SELECT COLUMN_TYPE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_manquants' "
                "AND column_name = 'statut'"
            )
            row = dcursor.fetchone()
            _statut_type = (row.get('COLUMN_TYPE', '') if row else '').lower()
            if 'rejete_corrig' not in _statut_type:
                cursor.execute(
                    "ALTER TABLE airvs_manquants "
                    "MODIFY COLUMN statut ENUM('en_attente','importe','resolu','rejete','rejete_corrigé') DEFAULT 'en_attente'"
                )
                print("[Schema] airvs_manquants.statut : ENUM enrichi avec rejete + rejete_corrigé (migration)")
            dcursor.close()
        except Exception as e:
            msg = f"MODIFY statut ENUM rejete/rejete_corrigé : {type(e).__name__}: {e}"
            schema_errors.append(msg)
            print(f"[Schema] {msg}")

        # Ajout colonne motif_rejet (si pas déjà présent)
        try:
            dcursor = db.cursor(pymysql.cursors.DictCursor)
            dcursor.execute(
                "SELECT COUNT(*) AS c FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_manquants' "
                "AND column_name = 'motif_rejet'"
            )
            if not dcursor.fetchone().get('c', 0):
                cursor.execute(
                    "ALTER TABLE airvs_manquants ADD COLUMN motif_rejet VARCHAR(255) DEFAULT NULL AFTER resolu_le"
                )
                print("[Schema] airvs_manquants.motif_rejet ajouté (migration)")
            dcursor.close()
        except Exception as e:
            msg = f"ADD motif_rejet : {type(e).__name__}: {e}"
            schema_errors.append(msg)
            print(f"[Schema] {msg}")

        # Ajout colonnes correction (R16)
        for _col, _def in [
            ('correction_artiste', "VARCHAR(500) DEFAULT NULL AFTER motif_rejet"),
            ('correction_titre',   "VARCHAR(500) DEFAULT NULL AFTER correction_artiste"),
            ('correction_source',  "VARCHAR(255) DEFAULT NULL AFTER correction_titre"),
            ('id_correction',      "INT DEFAULT NULL AFTER correction_source"),
        ]:
            try:
                dcursor = db.cursor(pymysql.cursors.DictCursor)
                dcursor.execute(
                    "SELECT COUNT(*) AS c FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'airvs_manquants' "
                    "AND column_name = %s", (_col,)
                )
                if not dcursor.fetchone().get('c', 0):
                    cursor.execute(
                        f"ALTER TABLE airvs_manquants ADD COLUMN {_col} {_def}"
                    )
                    print(f"[Schema] airvs_manquants.{_col} ajouté (migration)")
                dcursor.close()
            except Exception as e:
                msg = f"ADD {_col} : {type(e).__name__}: {e}"
                schema_errors.append(msg)
                print(f"[Schema] {msg}")

        # ── Phase P1 (grille editoriale) ──
        _safe("CREATE TABLE airvs_grille_editoriale", """
            CREATE TABLE IF NOT EXISTS airvs_grille_editoriale (
              id                  INT AUTO_INCREMENT PRIMARY KEY,
              version_label       VARCHAR(100) DEFAULT 'Grille',
              version_actif       TINYINT      DEFAULT 1,
              jour_semaine        TINYINT      NOT NULL COMMENT '1=Lundi .. 7=Dimanche',
              heure_debut         VARCHAR(5)   NOT NULL COMMENT 'format HH:MM',
              heure_fin           VARCHAR(5)   NOT NULL COMMENT 'format HH:MM',
              titre               VARCHAR(200) NOT NULL,
              type                VARCHAR(20)  DEFAULT 'focus',
              description         TEXT         DEFAULT '',
              animateur           VARCHAR(100) DEFAULT '',
              couleur             VARCHAR(7)   DEFAULT '#1DB954',
              parent_id           INT          DEFAULT NULL,
              ordre               INT          DEFAULT 0,
              date_creation       DATETIME     DEFAULT CURRENT_TIMESTAMP,
              date_modification   DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              INDEX idx_ge_version_actif (version_actif),
              INDEX idx_ge_jour_version (jour_semaine, version_actif),
              INDEX idx_ge_parent (parent_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        _safe("CREATE TABLE airvs_grille_versions", """
            CREATE TABLE IF NOT EXISTS airvs_grille_versions (
              id            INT AUTO_INCREMENT PRIMARY KEY,
              label         VARCHAR(100) NOT NULL UNIQUE,
              actif         TINYINT      NOT NULL DEFAULT 0,
              date_creation DATETIME     DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Version par defaut (activee)
        try:
            cursor.execute(
                "INSERT IGNORE INTO airvs_grille_versions (label, actif) VALUES (%s, 1)",
                ('Rentree Septembre 2026',)
            )
            db.commit()
        except Exception as e:
            schema_errors.append(f"INSERT airvs_grille_versions defaut : {e}")
            print(f"[Schema] INSERT grille_versions defaut : {e}")

        try:
            db.commit()
        except Exception as e:
            msg = f"db.commit() : {type(e).__name__}: {e}"
            schema_errors.append(msg)
            print(f"[Schema] {msg}")

        try:
            cursor.close()
        except Exception:
            pass
        db.close()

        _assurer_schema_airvs_avance._last_errors = schema_errors
        return True
    except Exception as e:
        import traceback
        print(f"[ProgrammationAvancee] Schema impossible (fatal) : {type(e).__name__}: {e}")
        traceback.print_exc()
        try:
            _assurer_schema_airvs_avance._last_errors = (
                getattr(_assurer_schema_airvs_avance, '_last_errors', []) +
                [f"FATAL : {type(e).__name__}: {e}"]
            )
        except Exception:
            pass
        return False


# ════════════════════════════════════════════════════════════════════
# v2.3 — Type de la colonne match_passe (airvs_animateurs)
# La table airvs_animateurs est créée par le WORKER (sync VPS) avec
# match_passe TINYINT, tandis que le dashboard écrit des chaînes
# ('trouve_import', 'strict', 'normalisé…', 'non_trouve'). Sur une base
# en mode SQL strict, chaque UPDATE dashboard échoue en erreur 1366
# « Incorrect integer value » — silencieusement avalée par les
# try/except : les sources ne passent jamais « Matché » et le terminal
# d'import affiche « source(s) Matché : 0 » à chaque corrélation.
# On convertit donc la colonne en VARCHAR(50) une fois pour toutes
# (les valeurs numériques du worker 1..8 deviennent '1'..'8', sans
# perte), puis on rattrape les demandes dont la corrélation d'import
# a été avalée pendant la période TINYINT.
# ════════════════════════════════════════════════════════════════════
_match_passe_type_ok = False          # cache process : 1 seule vérification
_match_passe_rattrapage_lance = False  # rattrapage one-shot par process


def _norm_ws(v):
    """v2.4 — Normalise une saisie : tabulations / retours ligne / espaces
    insécables (NBSP) / espaces multiples → espace simple, extrémités
    nettoyées. Rend les comparaisons de couples (artiste, titre)
    insensibles aux caractères invisibles collés aux tags ID3 ou aux
    saisies animateurs (MySQL TRIM ne retire que l'espace ASCII 0x20 :
    une simple tabulation en fin de valeur suffit à faire échouer une
    égalité exacte — cas « Maxime Le Forestier\t » de l'import #28800).
    """
    return re.sub(r'\s+', ' ', str(v or '')).strip()


def _rattrapage_matche_animateurs(logger=None):
    """v2.3 — Rattrapage one-shot (thread daemon) : repasse en
    statut='matche' les demandes animateurs encore 'nouveau' dont le
    couple (artiste, titre) existe DÉJÀ dans RadioDJ (passe stricte
    uniquement — artiste LIKE ET titre LIKE, zéro faux positif, même
    logique que la corrélation des manquants).

    Répare les corrélations d'import avalées tant que match_passe
    était resté TINYINT (ex. : titre importé, manquant clôturé
    « importé », mais « source(s) Matché : 0 » et demande restée
    'nouveau'). Remplace au passage les song_id écrits à la soumission
    par le worker (matches lâches type mots-clés passe 5) par le match
    strict réel quand il existe désormais.
    """
    def _travail():
        db = get_db_connection()
        if not db:
            return
        nb = 0
        try:
            cur = db.cursor()
            cur.execute(
                "SELECT id, artiste, titre FROM airvs_animateurs "
                "WHERE (statut = 'nouveau' OR statut IS NULL) "
                "ORDER BY id ASC LIMIT 500"
            )
            rows = cur.fetchall()
            for r in rows:
                art = _norm_ws(r['artiste'])
                tit = _norm_ws(r['titre'])
                if not art or not tit:
                    continue
                cur.execute(
                    "SELECT s.ID FROM songs s "
                    "WHERE s.song_type = 0 "
                    "AND s.artist LIKE %s AND s.title LIKE %s "
                    "ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1",
                    (f"%{art}%", f"%{tit}%")
                )
                hit = cur.fetchone()
                if not hit:
                    continue
                cur.execute(
                    "UPDATE airvs_animateurs "
                    "SET statut = 'matche', match_song_id = %s, "
                    "match_passe = 'rattrapage_v23' "
                    "WHERE id = %s AND (statut = 'nouveau' OR statut IS NULL)",
                    (hit['ID'], r['id'])
                )
                if cur.rowcount and cur.rowcount > 0:
                    nb += 1
            db.commit()
            cur.close()
            if nb and logger:
                logger.info(
                    f"[v2.3] rattrapage animateurs : {nb} demande(s) "
                    f"passée(s) en 'matche' (corrélation précédemment "
                    f"avalée par le type TINYINT de match_passe)"
                )
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            if logger:
                logger.warning(f"[v2.3] rattrapage animateurs (non bloquant) : {e}")
        else:
            try:
                db.close()
            except Exception:
                pass
            return
        try:
            db.close()
        except Exception:
            pass
    try:
        threading.Thread(target=_travail, daemon=True).start()
    except Exception:
        pass


def assurer_type_match_passe(logger=None):
    """v2.3 — Convertit airvs_animateurs.match_passe en VARCHAR(50) si la
    colonne est encore numérique (TINYINT/INT… créée par le worker VPS).

    Idempotent + mis en cache : après le premier appel réussi du
    process, plus aucune requête (return immédiat). Si une conversion
    a lieu, lance le rattrapage one-shot des demandes animateurs.

    Retourne True si la colonne est (désormais) de type texte.
    """
    global _match_passe_type_ok
    if _match_passe_type_ok:
        return True
    db = get_db_connection()
    if not db:
        return False
    converted = False
    try:
        cur = db.cursor()
        cur.execute(
            "SELECT DATA_TYPE FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            "AND table_name = 'airvs_animateurs' "
            "AND column_name = 'match_passe'"
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            # Table/colonne absente : la migration standard la créera en VARCHAR
            _match_passe_type_ok = True
            return True
        # DictCursor global : accès dict-safe (jamais fetchone()[0])
        dtype = str(list(row.values())[0] or '').lower()
        if dtype in ('varchar', 'char', 'text', 'tinytext',
                     'mediumtext', 'longtext', 'enum'):
            _match_passe_type_ok = True
            return True
        cur = db.cursor()
        cur.execute(
            "ALTER TABLE airvs_animateurs "
            "MODIFY match_passe VARCHAR(50) DEFAULT NULL"
        )
        db.commit()
        cur.close()
        converted = True
        _match_passe_type_ok = True
        if logger:
            logger.warning(
                f"[v2.3] airvs_animateurs.match_passe converti {dtype} → "
                f"VARCHAR(50) — cause racine des « source(s) Matché : 0 » : "
                f"le dashboard écrivait des chaînes ('trouve_import', "
                f"méthodes du moteur) dans une colonne numérique créée "
                f"par le worker VPS → erreur 1366 avalée silencieusement"
            )
        return True
    except Exception as e:
        if logger:
            logger.warning(
                f"[v2.3] conversion match_passe impossible (non bloquant, "
                f"nouvelle tentative à la prochaine corrélation) : {e}")
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass
        if converted:
            _rattrapage_matche_animateurs(logger=logger)


def correler_manquants_radiodj(exclure_cles=None, limite=500, logger=None,
                               maj_sources=True):
    """Corrélation airvs_manquants ↔ bibliothèque RadioDJ (P2).

    v2 — maj_sources=True : quand un manquant est corrélé, la/les ligne(s)
    source (airvs_shazam / airvs_animateurs) qui ont créé ce manquant
    passent en statut='matche' + match_song_id + match_passe='trouve_import'
    (badge « Matché » + ✓ immédiats, sans refaire une passe de matching).

    Pour chaque manquant en_attente, cherche le couple (artiste, titre)
    dans la table songs de RadioDJ avec la passe stricte du moteur de
    matching (LIKE %artiste% AND LIKE %titre%, song_type=0 — la passe 1
    de _match_shazam_songs, zéro faux positif par rapport au matching
    standard).

    Si trouvé : statut='importe', id_import=songs.ID, resolu_le=NOW().
    Le manquant quitte automatiquement la vue « En attente » : la liste
    s'allège au fil des imports, sans action manuelle.

    Args:
        exclure_cles: set de tuples (artiste_lower, titre_lower) à ignorer
                      — ex. les not_found du matching courant, qui viennent
                      d'échouer au même moteur et ne peuvent pas être trouvés
        limite: nb max de manquants en_attente traités par appel
                (anti-lourdeur, les plus anciens d'abord)
        logger: logger optionnel (warnings non bloquants)

    Returns:
        (nb_correles, nb_tests)
    """
    nb_corr = 0
    nb_test = 0
    nb_src = (0, 0)
    # v2.3 — garantit un match_passe textuel avant toute écriture sources
    assurer_type_match_passe(logger=logger)
    db = get_db_connection()
    if not db:
        if logger:
            logger.warning("[correler_manquants] DB indisponible")
        return 0, 0
    try:
        cur = db.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT id, artiste, titre FROM airvs_manquants "
            "WHERE statut = 'en_attente' "
            "ORDER BY date_creation ASC LIMIT %s",
            (int(limite),)
        )
        rows = cur.fetchall()
        for r in rows:
            # v2.4 — _norm_ws : un manquant porteur d'une tabulation ou
            # d'un NBSP interne ne doit plus casser son propre LIKE de
            # recherche (le motif %…% héritait du caractère sale).
            art = _norm_ws(r['artiste'])
            tit = _norm_ws(r['titre'])
            # Strict : les deux champs requis (un champ vide donnerait
            # LIKE '%%' et donc un faux positif garanti)
            if not art or not tit:
                continue
            key = (art.lower(), tit.lower())
            if exclure_cles and key in exclure_cles:
                continue
            nb_test += 1
            cur.execute(
                "SELECT s.ID FROM songs s "
                "WHERE s.song_type = 0 "
                "AND s.artist LIKE %s AND s.title LIKE %s "
                "ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1",
                (f"%{art}%", f"%{tit}%")
            )
            hit = cur.fetchone()
            if hit:
                cur.execute(
                    "UPDATE airvs_manquants SET statut = 'importe', "
                    "id_import = %s, resolu_le = NOW() "
                    "WHERE id = %s AND statut = 'en_attente'",
                    (hit['ID'], r['id'])
                )
                nb_corr += 1
                # v2 — matérialiser le lien côté lignes sources
                if maj_sources:
                    _s = _maj_sources_trouve_import(cur, art, tit, hit['ID'])
                    nb_src = (nb_src[0] + _s[0], nb_src[1] + _s[1])
        db.commit()
        cur.close()
        db.close()
        if logger and (nb_src[0] or nb_src[1]):
            logger.info(
                f"[correler_manquants] {nb_corr} manquant(s) corrélé(s) — "
                f"lignes sources passées en Matché : "
                f"{nb_src[0]} shazam, {nb_src[1]} animateurs"
            )
    except Exception as e:
        if logger:
            logger.warning(f"[correler_manquants] erreur (non bloquante) : {e}")
        try:
            db.close()
        except Exception:
            pass
        return nb_corr, nb_test
    return nb_corr, nb_test


def _maj_sources_trouve_import(cur, artiste, titre, song_id):
    """v2 — Passe les lignes sources d'un couple (artiste, titre) désormais
    présent dans RadioDJ en statut='matche' + match_song_id.

    Appelée par la corrélation quand un titre importé (Maintenance →
    Import & Validation) correspond à un manquant : la demande d'origine
    (airvs_shazam / airvs_animateurs) passe « Matché » (✓) immédiatement,
    sans devoir refaire une passe de matching. match_passe='trouve_import'
    trace l'origine de la correspondance (import manuel ≠ matching auto).

    Sécurité : seules les lignes encore 'nouveau' (ou sans statut) sont
    touchées — jamais les rejets ni les lignes déjà matchées.

    v2.4 : égalité normalisée via _norm_ws (tabulations / NBSP / espaces
    multiples des saisies tolérés — l'égalité SQL TRIM exacte ratait ces
    lignes, TRIM ne retirant que l'espace ASCII 0x20).

    Args:
        cur: curseur DB (commit par l'appelant)
        artiste, titre: couple exact (TRIM/LOWER appliqué en SQL)
        song_id: ID du titre correspondant dans radiodb.songs

    Returns:
        (nb_shazam, nb_animateurs) lignes mises à jour
    """
    nb = [0, 0]
    # v2.4 — entrées normalisées + garde-fou champs vides (un couple vide
    # aurait matché les lignes à artiste/titre vides).
    _art = _norm_ws(artiste)
    _tit = _norm_ws(titre)
    if not _art or not _tit:
        return 0, 0
    _pat_art, _pat_tit = f"%{_art}%", f"%{_tit}%"
    for i, table in enumerate(('airvs_shazam', 'airvs_animateurs')):
        # v2.4 — sélection des candidates par LIKE puis égalité Python
        # normalisée (_norm_ws) : insensible aux tabulations / NBSP /
        # espaces multiples collés aux saisies (l'égalité SQL exacte
        # TRIM ratait ces lignes). Les id retenus sont mis à jour
        # individuellement — plus sûr et traçable qu'un UPDATE d'égalité.
        _ids = []
        try:
            cur.execute(
                f"SELECT id, artiste, titre FROM {table} "
                "WHERE (statut = 'nouveau' OR statut IS NULL) "
                "AND artiste LIKE %s AND titre LIKE %s "
                "LIMIT 200",
                (_pat_art, _pat_tit)
            )
            for _r in cur.fetchall():
                if (_norm_ws(_r['artiste']).lower() == _art.lower()
                        and _norm_ws(_r['titre']).lower() == _tit.lower()):
                    _ids.append(_r['id'])
        except Exception:
            _ids = []
        if not _ids:
            continue
        # v2.3 — UPDATE 1 : statut + match_song_id d'abord (colonnes sûres).
        # Même si la trace match_passe échoue ensuite, le ✓ « Matché » et
        # le lien vers le morceau RadioDJ sont matérialisés — plus jamais
        # de « source(s) Matché : 0 » silencieux à cause d'un type de
        # colonne inadapté.
        try:
            cur.execute(
                f"UPDATE {table} "
                "SET statut = 'matche', match_song_id = %s "
                "WHERE id IN (" + ",".join(['%s'] * len(_ids)) + ") "
                "AND (statut = 'nouveau' OR statut IS NULL)",
                [song_id] + _ids
            )
            nb[i] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        except Exception:
            # Non bloquant : la corrélation du manquant reste valable
            pass
        # v2.3 — UPDATE 2 : trace 'trouve_import' (la colonne peut rester
        # TINYINT côté base worker → peut échouer sans annuler le ✓).
        try:
            cur.execute(
                f"UPDATE {table} "
                "SET match_passe = 'trouve_import' "
                "WHERE id IN (" + ",".join(['%s'] * len(_ids)) + ") "
                "AND statut = 'matche' "
                "AND (match_passe IS NULL OR match_passe = '')",
                _ids
            )
        except Exception:
            # Non bloquant : le ✓ est déjà posé par l'UPDATE 1
            pass
    return nb[0], nb[1]


def correler_import_cible(artiste, titre, logger=None):
    """v2 — Corrélation ciblée d'un titre venant d'être importé.

    Appelée immédiatement après une validation de suspect réussie
    (Maintenance → Import & Validation) : cherche le couple
    (artiste, titre) dans radiodb.songs avec la passe stricte du moteur
    (LIKE %artiste% AND LIKE %titre%, song_type=0 — zéro faux positif)
    et si trouvé :
      1. clôture le(s) manquant(s) en_attente correspondant(s)
         (statut='importe' + id_import + resolu_le) ;
      2. passe la/les ligne(s) source en 'matche' + match_song_id
         + match_passe='trouve_import' (via _maj_sources_trouve_import).

    Si le titre n'est pas encore dans songs (scan FolderSync asynchrone
    de RadioDJ après le RunEvent), trouve=False : l'appelant peut
    re-tenter quelques secondes plus tard. Rien n'est marqué à moitié
    (rollback complet en cas d'erreur).

    Returns:
        dict {'trouve': bool, 'song_id': int|None,
              'manquants_clotures': int,
              'sources_matchees': (nb_shazam, nb_animateurs),
              'manquants_proches': [(artiste, titre), …] (v2.4 — manquants
                  en_attente proches mais couple différent après
                  normalisation, remontés dans le suivi terminal)}
    """
    result = {'trouve': False, 'song_id': None,
              'manquants_clotures': 0, 'sources_matchees': (0, 0),
              'manquants_proches': []}
    # v2.4 — _norm_ws : le couple peut porter des tabulations / NBSP /
    # espaces multiples (tags ID3 de compilations mal étiquetées) qui
    # faisaient échouer toute égalité SQL exacte (TRIM ne retire que
    # l'espace ASCII 0x20).
    art = _norm_ws(artiste)
    tit = _norm_ws(titre)
    if not art or not tit:
        return result
    # v2.3 — garantit un match_passe textuel avant toute écriture sources
    assurer_type_match_passe(logger=logger)
    db = get_db_connection()
    if not db:
        if logger:
            logger.warning("[correler_import_cible] DB indisponible")
        return result
    cur = None
    try:
        cur = db.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT s.ID FROM songs s "
            "WHERE s.song_type = 0 "
            "AND s.artist LIKE %s AND s.title LIKE %s "
            "ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1",
            (f"%{art}%", f"%{tit}%")
        )
        hit = cur.fetchone()
        if not hit:
            db.commit()
            return result
        result['trouve'] = True
        result['song_id'] = hit['ID']
        # 1. Manquants en_attente du couple (toutes sources confondues).
        # v2.4 — sélection LIKE puis égalité Python normalisée (_norm_ws) :
        # les manquants porteurs de caractères invisibles sont désormais
        # clôturés ; les proches refusés (couple réellement différent)
        # sont remontés dans result['manquants_proches'] pour le suivi.
        _ids_mq = []
        _proches = []
        cur.execute(
            "SELECT id, artiste, titre FROM airvs_manquants "
            "WHERE statut = 'en_attente' "
            "AND artiste LIKE %s AND titre LIKE %s "
            "LIMIT 200",
            (f"%{art}%", f"%{tit}%")
        )
        for _r in cur.fetchall():
            if (_norm_ws(_r['artiste']).lower() == art.lower()
                    and _norm_ws(_r['titre']).lower() == tit.lower()):
                _ids_mq.append(_r['id'])
            elif len(_proches) < 3:
                _proches.append(
                    (str(_r['artiste'] or '').strip(),
                     str(_r['titre'] or '').strip())
                )
        if _ids_mq:
            cur.execute(
                "UPDATE airvs_manquants SET statut = 'importe', "
                "id_import = %s, resolu_le = NOW() "
                "WHERE id IN (" + ",".join(['%s'] * len(_ids_mq)) + ") "
                "AND statut = 'en_attente'",
                [hit['ID']] + _ids_mq
            )
            result['manquants_clotures'] = (
                cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            )
        result['manquants_proches'] = _proches
        # 2. Lignes sources → Matché + ✓ immédiat
        result['sources_matchees'] = _maj_sources_trouve_import(
            cur, art, tit, hit['ID']
        )
        db.commit()
        if logger and (result['manquants_clotures']
                       or result['sources_matchees'][0]
                       or result['sources_matchees'][1]):
            logger.info(
                f"[correler_import_cible] {art} - {tit} : "
                f"{result['manquants_clotures']} manquant(s) clôturé(s), "
                f"sources matchées {result['sources_matchees']} "
                f"(song #{hit['ID']})"
            )
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        if logger:
            logger.warning(f"[correler_import_cible] erreur (non bloquante) : {e}")
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
    return result
