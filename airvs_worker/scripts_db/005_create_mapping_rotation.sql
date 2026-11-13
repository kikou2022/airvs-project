-- ============================================================================
-- Phase 21.3 — Script 005 : résolveur bidirectionnel + stockage des previews
-- Cadrage : CADRAGE_Phase21_rotation_editoriale_2026-11-11.md (§5, lot 21.3)
-- Spec    : SPEC_TECHNIQUE_IMPLEMENTATION.md — Annexe A.4.1 + Module 2 (Phase 9)
--
-- ⚠️  Ces tables sont créées AUTOMATIQUEMENT (idempotent) au démarrage du
--     worker 21.3 (_assurer_schema_rotation_socle, base airvs_dashboard).
--     Ce script sert de référence / application manuelle uniquement.
--
-- Application manuelle (si besoin) :
--   mysql -u root airvs_dashboard < 005_create_mapping_rotation.sql
-- ============================================================================

USE airvs_dashboard;

-- ────────────────────────────────────────────────────────────────────────────
-- Table mapping_radiodj_azura — résolveur bidirectionnel (spec Annexe A.4)
-- song_id RadioDJ ↔ media_id AzuraCast, avec méthode de matching + confiance.
-- Alimentée par : bootstrap MAPPING_BOOTSTRAP, resync SYNC_AZURA_STATUS
-- (sur changements de flag), et chaque résolution à la volée.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mapping_radiodj_azura (
  id INT AUTO_INCREMENT PRIMARY KEY,
  date_creation DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  date_derniere_verif DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  -- Côté RadioDJ
  song_id_radiodj INT NOT NULL COMMENT 'ID dans radiodb.songs',
  artist_radiodj VARCHAR(255) NOT NULL,
  title_radiodj VARCHAR(255) NOT NULL,
  album_radiodj VARCHAR(255),
  path_radiodj VARCHAR(500) NOT NULL COMMENT 'Chemin Windows d''origine',
  basename_radiodj VARCHAR(255) NOT NULL COMMENT 'Nom de fichier seul',

  -- Côté AzuraCast
  media_id_azura INT NOT NULL COMMENT 'ID dans station_media AzuraCast',
  station_id INT NOT NULL COMMENT '6 ou 7',
  song_id_azura VARCHAR(32) COMMENT 'Hash 32 chars calculé par AzuraCast',
  path_azura VARCHAR(500) COMMENT 'Chemin relatif dans AzuraCast',
  unique_id_azura VARCHAR(64) COMMENT 'unique_id AzuraCast',

  -- Qualité du matching
  methode_matching ENUM(
    'path_exact',
    'path_normalized',
    'artist_title_exact',
    'artist_title_normalized',
    'song_id_azura',
    'manual'
  ) NOT NULL,
  confiance FLOAT DEFAULT 1.0 COMMENT '0.0 à 1.0 (1.0 = path_exact)',

  -- État
  actif BOOLEAN DEFAULT TRUE COMMENT 'FALSE si la correspondance n''est plus valide',
  raison_inactivation VARCHAR(255),

  INDEX idx_song_radiodj (song_id_radiodj),
  INDEX idx_media_azura (media_id_azura, station_id),
  INDEX idx_station (station_id),
  INDEX idx_actif (actif),
  INDEX idx_basename (basename_radiodj),
  INDEX idx_song_id_azura (song_id_azura),
  UNIQUE KEY uniq_song_station (song_id_radiodj, station_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ────────────────────────────────────────────────────────────────────────────
-- Table rotation_previews — résultats des previews ROTATION_PREVIEW.
-- Le worker y stocke le JSON complet des candidats (trop volumineux pour
-- log_resultat) ; le dashboard le relit pour afficher le tableau.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rotation_previews (
  id INT AUTO_INCREMENT PRIMARY KEY,
  date_creation DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  task_id INT COMMENT 'Tâche ROTATION_PREVIEW associée',
  station_id INT NOT NULL COMMENT '6 ou 7',
  parametres JSON COMMENT 'Params utilisés (ratio, protection, seuils...)',
  config_effective JSON COMMENT 'Config effective côté worker (GF7 + defaults)',
  resultats LONGTEXT COMMENT 'JSON : groupes de candidats + stats',
  nb_candidats INT DEFAULT 0,
  nb_groupes INT DEFAULT 0,
  statut ENUM('en_cours', 'pret', 'erreur') DEFAULT 'en_cours',
  INDEX idx_task (task_id),
  INDEX idx_station (station_id),
  INDEX idx_date (date_creation)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ────────────────────────────────────────────────────────────────────────────
-- Table mirror_regen_state — demandes de régénération du miroir (mémo §5.1).
-- Une seule ligne (id=1) : pending=1 → le thread de fond du worker déclenche
-- export_db_mirror_V4.py sur .30 via SSH (canal BatchMode existant :2022).
-- Écrit par le worker (rotation/retrait/suppression/resync) et par le
-- dashboard (pushes azuracast.py) ; lu par le drainer du worker .17.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mirror_regen_state (
  id INT PRIMARY KEY DEFAULT 1,
  pending TINYINT NOT NULL DEFAULT 0,
  date_demande DATETIME,
  last_raison VARCHAR(255),
  last_regen DATETIME,
  last_statut ENUM('ok', 'erreur_ssh', 'erreur_script', 'non_configure', 'en_cours') DEFAULT NULL,
  last_message VARCHAR(500),
  nb_regen_ok INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Ligne d'état unique (idempotent)
INSERT INTO mirror_regen_state (id, pending) VALUES (1, 0)
  ON DUPLICATE KEY UPDATE id = id;
