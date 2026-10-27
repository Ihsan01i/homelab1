import sqlite3
import os

DB_PATH = "/opt/music-brain/music_brain.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT,
            duration_ms INTEGER,
            source_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS discovery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            discovery_source TEXT NOT NULL,
            context_json TEXT,
            played_at TIMESTAMP,
            FOREIGN KEY (track_id) REFERENCES tracks (id)
        );
    """)

    conn.commit()
    conn.close()
    print("Database initialized successfully at", DB_PATH)

if __name__ == "__main__":
    init_db()
