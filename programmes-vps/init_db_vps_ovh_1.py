#!/usr/bin/env python3
"""
Initialisation de la base SQLite pour l'app programmeurs + shazam externe.

Usage:
    python3 init_db.py [chemin_db]

Par défaut : ../data/programmes.db
Crée le fichier SQLite et les tables si elles n'existent pas.
"""

import sqlite3
import os
import sys
import hashlib
from datetime import datetime
from werkzeug.security import generate_password_hash

def get_db_path():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return os.path.join(os.path.dirname(__file__), '..', 'data', 'programmes.db')

def init_db(db_path: str):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # ============================================================
    # Table : programmateurs
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS programmateurs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'animateur',  -- 'animateur' | 'admin'
            actif INTEGER NOT NULL DEFAULT 1,
            date_creation TEXT NOT NULL DEFAULT (datetime('now')),
            date_derniere_connexion TEXT NULL
        )
    ''')

    # ============================================================
    # Table : soumissions (formulaire programmeurs)
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS soumissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            programmeur_id INTEGER NOT NULL,
            artiste TEXT NOT NULL,
            titre TEXT NOT NULL,
            genre TEXT NULL,
            source TEXT NOT NULL DEFAULT 'Saisie manuelle',
            commentaire TEXT NULL,
            match_result TEXT NULL,          -- JSON : {"passes": [...], "song_id": ...}
            statut TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'synced' | 'processed'
            date_soumission TEXT NOT NULL DEFAULT (datetime('now')),
            date_sync TEXT NULL,
            date_traitement_studio TEXT NULL,
            FOREIGN KEY (programmeur_id) REFERENCES programmateurs(id)
        )
    ''')

    # ============================================================
    # Table : sync_log (traçabilité des échanges worker ↔ VPS)
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sens TEXT NOT NULL,              -- 'pull' | 'ack'
            type TEXT NOT NULL,              -- 'soumissions' | 'shazam_external'
            nb_entrees INTEGER NOT NULL DEFAULT 0,
            ids TEXT NULL,                   -- JSON list des IDs concernés
            date_sync TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')

    # ============================================================
    # Table : shazam_external (relais MacroDroid hors LAN)
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shazam_external (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artiste TEXT NOT NULL,
            titre TEXT NOT NULL,
            animateur TEXT DEFAULT NULL,
            date_shazam TEXT NOT NULL DEFAULT (datetime('now')),
            statut TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'synced'
            date_sync TEXT NULL
        )
    ''')

    # ============================================================
    # Table : rate_limit_log (rate-limiting par IP, fenêtre glissante)
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rate_limit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip  TEXT NOT NULL,
            ts  INTEGER NOT NULL  -- timestamp Unix (strftime('%s', 'now'))
        )
    ''')

    # ============================================================
    # Index pour les performances
    # ============================================================
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_soumissions_programmeur ON soumissions(programmeur_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_soumissions_statut ON soumissions(statut)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_soumissions_cooldown ON soumissions(artiste, titre, date_soumission)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shazam_external_statut ON shazam_external(statut)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shazam_external_cooldown ON shazam_external(artiste, titre, date_shazam)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_log_date ON sync_log(date_sync)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rate_limit_log_ip_ts ON rate_limit_log(ip, ts)')

    # ============================================================
    # Compte admin par défaut (mot de passe : admin — à changer au premier login)
    # ============================================================
    admin_exists = cursor.execute(
        "SELECT id FROM programmateurs WHERE username = 'admin'"
    ).fetchone()

    if not admin_exists:
        default_pw = 'admin'
        pw_hash = generate_password_hash(default_pw)
        cursor.execute(
            "INSERT INTO programmateurs (username, password_hash, display_name, role) VALUES (?, ?, ?, 'admin')",
            ('admin', pw_hash, 'Administrateur')
        )
        print(f"[OK] Compte admin créé (username: admin, mot de passe: {default_pw})")
        print("     >>> CHANGER LE MOT DE PASSE AU PREMIER LOGIN <<<")
    else:
        print("[OK] Compte admin déjà existant, pas de modification")

    conn.commit()

    # ============================================================
    # Vérification
    # ============================================================
    tables = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

    print(f"\nBase SQLite initialisée : {db_path}")
    print(f"Tables créées ({len(tables)}) :")
    for t in tables:
        count = cursor.execute(f"SELECT COUNT(*) FROM [{t['name']}]").fetchone()[0]
        print(f"  - {t['name']} ({count} lignes)")

    conn.close()


if __name__ == '__main__':
    db_path = get_db_path()
    init_db(db_path)
