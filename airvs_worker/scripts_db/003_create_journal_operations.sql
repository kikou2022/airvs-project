-- ════════════════════════════════════════════════════════════════════════════
-- 003_create_journal_operations.sql — Phase 21.1 (spec rotation, table Phase 6)
-- ════════════════════════════════════════════════════════════════════════════
-- Journal de toutes les opérations AzuraCast (historique réversible).
-- Dès la Phase 21.1, le worker y journalise les resyncs de flags
-- (type_op = 'flag_resync'). Les autres type_op seront alimentés par les
-- lots 21.3/21.4 (rotation soft/hard, auto_purge…).
-- Table créée AUSSI automatiquement (idempotent) au démarrage du worker
-- 2026.11.12-A — ce script sert de référence / application manuelle :
--   mysql -u <user> -p airvs_dashboard < 003_create_journal_operations.sql
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS journal_operations_azura (
  id INT AUTO_INCREMENT PRIMARY KEY,
  date_op DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  type_op ENUM(
    'push',                  -- Copie RadioDJ → AzuraCast
    'playlist_add',          -- Ajout media → playlist
    'playlist_remove',       -- Retrait media → playlist
    'move',                  -- Déplacement entre playlists
    'delete',                -- Suppression media AzuraCast
    'rename',                -- Renommage/déplacement fichier
    'empty_playlist',        -- Vider playlist
    'rotation_soft',         -- Rotation mode soft (retirer playlists)
    'rotation_hard',         -- Rotation mode hard (supprimer fichiers)
    'auto_purge',            -- RETIRED > 90j → REMOVED
    'flag_force',            -- Override manuel flag AZURA_*_S*
    'flag_resync',           -- Cron resync flags
    'm3u_push'               -- Push depuis M3U (workflow spec section 13)
  ) NOT NULL,

  station_id INT COMMENT '6 ou 7, NULL si global',
  media_id_azura INT COMMENT 'ID média côté AzuraCast',
  song_id_radiodj INT COMMENT 'ID song côté RadioDJ (radiodb.songs.ID)',

  triggered_by ENUM(
    'manual',         -- Action humaine via dashboard
    'cron',           -- Cron automatique
    'auto_purge',     -- Auto-purge automatique
    'preview_accept', -- Validation preview 24h
    'event_hook'      -- Hook event RadioDJ (lot 21.6)
  ) NOT NULL,

  task_id INT COMMENT 'Lien vers taches_planifiees.id',
  trigger_source VARCHAR(255) COMMENT 'dashboard:sebastien, cron:monthly, etc.',

  details JSON COMMENT 'Contexte spécifique (rotation_id, playlist_id, raisons, etc.)',

  reversible BOOLEAN DEFAULT TRUE,
  reversed_by INT COMMENT 'ID de l''opération inverse',
  reversed_at DATETIME,

  INDEX idx_date (date_op),
  INDEX idx_type (type_op),
  INDEX idx_station (station_id),
  INDEX idx_song (song_id_radiodj),
  INDEX idx_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
