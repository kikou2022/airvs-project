-- ════════════════════════════════════════════════════════════════════════════
-- 002_create_audit_doublons.sql — Phase 21.1 (spec rotation, table Phase 13)
-- ════════════════════════════════════════════════════════════════════════════
-- Cache des doublons RadioDJ pour les lots 21.5 (audit doublons).
-- Table créée AUSSI automatiquement (idempotent) au démarrage du worker
-- 2026.11.12-A via _assurer_schema_rotation_socle() — ce script sert de
-- référence et pour une application manuelle (MariaDB .30) :
--   mysql -u <user> -p airvs_dashboard < 002_create_audit_doublons.sql
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS audit_doublons_radiodj (
  id INT AUTO_INCREMENT PRIMARY KEY,
  date_audit DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  cle_groupe VARCHAR(255) NOT NULL COMMENT 'Normalisation "artist - title"',
  artist VARCHAR(255) NOT NULL,
  title VARCHAR(255) NOT NULL,
  album VARCHAR(255),

  song_id_radiodj INT NOT NULL COMMENT 'ID dans radiodb.songs',
  path VARCHAR(500),
  file_size INT,
  duration FLOAT,
  id3_complete BOOLEAN DEFAULT FALSE,

  flag_azura VARCHAR(50) COMMENT 'AZURA_*_S6_S7 ou NULL',

  type_doublon ENUM('strict', 'large') NOT NULL COMMENT
    'strict = artist+title+album, large = artist+title',

  action_recommandee ENUM(
    'conserver',
    'nettoyer',
    'auditer',
    'marque_pour_nettoyage'
  ) DEFAULT 'auditer',

  INDEX idx_groupe (cle_groupe),
  INDEX idx_type (type_doublon),
  INDEX idx_action (action_recommandee),
  INDEX idx_song (song_id_radiodj)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
