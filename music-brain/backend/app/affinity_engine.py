"""
Music Brain: Episodic Memory & Affinity Engine (Pillar 1)
Calculates mathematical half-life decay affinity, circadian matrices, and musical eras.
"""

import os
import json
import math
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

DB_PATH = "/opt/music-brain/music_brain.db"
NAVIDROME_DB_PATH = "/var/lib/navidrome/navidrome.db"

# Source weights
WEIGHT_YTM_LIKED = 3.0
WEIGHT_FAVORITE_PLAYLIST = 2.5
WEIGHT_NAMED_PLAYLIST = 2.0
WEIGHT_YTM_LIBRARY = 1.5
WEIGHT_HISTORY_OR_STREAM = 1.0

# Half-life parameters in days
HALF_LIFE_SHORT_DAYS = 45.0   # Current rotation & obsessions
HALF_LIFE_LONG_DAYS = 365.0   # Sustained annual taste & anchors


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_affinity_tables(db: sqlite3.Connection):
    """Creates the Pillar 1 schema tables if they do not exist."""
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS track_affinity (
                track_id INTEGER PRIMARY KEY,
                affinity_score_short REAL DEFAULT 0.0,
                affinity_score_long REAL DEFAULT 0.0,
                affinity_score_all_time REAL DEFAULT 0.0,
                affinity_velocity REAL DEFAULT 0.0,
                total_plays INTEGER DEFAULT 0,
                first_discovered_at TEXT,
                last_played_at TEXT,
                days_since_last_play REAL,
                is_favorite INTEGER DEFAULT 0,
                dominant_circadian_slot TEXT,
                top_era TEXT,
                updated_at TEXT,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            );
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_affinity_short ON track_affinity(affinity_score_short DESC);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_affinity_long ON track_affinity(affinity_score_long DESC);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_affinity_velocity ON track_affinity(affinity_velocity DESC);")

        db.execute("""
            CREATE TABLE IF NOT EXISTS artist_affinity (
                artist_name TEXT PRIMARY KEY,
                affinity_score_short REAL DEFAULT 0.0,
                affinity_score_long REAL DEFAULT 0.0,
                affinity_score_all_time REAL DEFAULT 0.0,
                total_plays INTEGER DEFAULT 0,
                vault_track_count INTEGER DEFAULT 0,
                top_track_id INTEGER,
                updated_at TEXT,
                FOREIGN KEY (top_track_id) REFERENCES tracks(id) ON DELETE SET NULL
            );
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_artist_affinity_short ON artist_affinity(affinity_score_short DESC);")

        db.execute("""
            CREATE TABLE IF NOT EXISTS circadian_patterns (
                track_id INTEGER,
                hour_of_day INTEGER,
                day_of_week INTEGER,
                play_count INTEGER DEFAULT 0,
                affinity_weight REAL DEFAULT 0.0,
                PRIMARY KEY (track_id, hour_of_day, day_of_week),
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            );
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS listening_eras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                era_name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                total_events INTEGER DEFAULT 0,
                unique_tracks INTEGER DEFAULT 0,
                dominant_artists_json TEXT,
                top_tracks_json TEXT,
                bridge_tracks_json TEXT,
                created_at TEXT
            );
        """)


def sync_navidrome_scrobbles(db: sqlite3.Connection):
    """
    Ingests playback scrobbles from Navidrome into discovery_events,
    linking local media_file plays back to music_brain tracks.
    """
    if not os.path.exists(NAVIDROME_DB_PATH):
        return 0

    try:
        navi = sqlite3.connect(NAVIDROME_DB_PATH, timeout=10.0)
        navi_cur = navi.cursor()

        # Get scrobbles with file path
        rows = navi_cur.execute("""
            SELECT s.id, s.submission_time, mf.path
            FROM scrobbles s
            JOIN media_file mf ON s.media_file_id = mf.id
            ORDER BY s.id ASC
        """).fetchall()
        navi.close()
    except Exception as e:
        print(f"[AffinityEngine] Error reading Navidrome DB: {e}")
        return 0

    ingested = 0
    cur = db.cursor()
    for sid, sub_time, mf_path in rows:
        # Check if already synced using context_json tag
        marker = f"navidrome_scrobble_{sid}"
        exists = cur.execute(
            "SELECT 1 FROM discovery_events WHERE context_json LIKE ?",
            (f'%"{marker}"%',)
        ).fetchone()
        if exists:
            continue

        # Match local_path
        track = cur.execute(
            "SELECT id FROM tracks WHERE local_path = ? OR local_path LIKE ?",
            (mf_path, f"%{os.path.basename(mf_path)}")
        ).fetchone()

        if track:
            track_id = track[0]
            # Convert epoch to ISO string
            dt_str = datetime.fromtimestamp(sub_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            ctx = json.dumps({"source_marker": marker, "navidrome_scrobble_id": sid, "file": mf_path})
            cur.execute("""
                INSERT INTO discovery_events (track_id, discovery_source, context_json, played_at)
                VALUES (?, 'navidrome_play', ?, ?)
            """, (track_id, ctx, dt_str))
            ingested += 1

    if ingested > 0:
        db.commit()
        print(f"[AffinityEngine] Synced {ingested} new Navidrome scrobbles into discovery_events.")
    return ingested


def parse_timestamp(ts_val: Any) -> Optional[datetime]:
    """Helper to parse varied ISO timestamps into UTC datetime."""
    if not ts_val:
        return None
    if isinstance(ts_val, (int, float)):
        return datetime.fromtimestamp(ts_val, tz=timezone.utc)
    ts_str = str(ts_val).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


def get_source_weight(source: str) -> float:
    """Returns mathematical weight for a discovery source."""
    if source == 'ytm_liked':
        return WEIGHT_YTM_LIKED
    elif source in ('ytm_playlist:Favorite songs', 'ytm_playlist:2025 recp'):
        return WEIGHT_FAVORITE_PLAYLIST
    elif source and source.startswith('ytm_playlist:'):
        return WEIGHT_NAMED_PLAYLIST
    elif source == 'ytm_library':
        return WEIGHT_YTM_LIBRARY
    return WEIGHT_HISTORY_OR_STREAM


def calculate_affinities(db: sqlite3.Connection, now_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Computes Half-Life Decay Affinity and Circadian metrics across all tracks and artists.
    """
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)

    now_iso = now_dt.strftime('%Y-%m-%d %H:%M:%S')
    cur = db.cursor()

    # Step 1: Pre-fetch all discovery events grouped by track_id
    cur.execute("""
        SELECT track_id, discovery_source, played_at
        FROM discovery_events
        WHERE track_id IS NOT NULL
        ORDER BY played_at ASC
    """)
    events_by_track: Dict[int, List[Dict[str, Any]]] = {}
    for tid, src, played_at in cur.fetchall():
        dt = parse_timestamp(played_at)
        if dt:
            events_by_track.setdefault(tid, []).append({
                'source': src,
                'dt': dt,
                'weight': get_source_weight(src)
            })

    # Step 2: Query all tracks
    tracks = cur.execute("SELECT id, title, artist, album, local_path FROM tracks").fetchall()

    track_affinities_to_upsert = []
    circadian_records = []

    for tid, title, artist, album, local_path in tracks:
        ev_list = events_by_track.get(tid, [])
        total_plays = len(ev_list)

        score_short = 0.0
        score_long = 0.0
        score_all_time = 0.0
        score_recent_72h = 0.0
        score_prev_72h = 0.0

        first_dt: Optional[datetime] = None
        last_dt: Optional[datetime] = None
        is_fav = 0

        # Hourly distribution for circadian slots
        # Slots: Morning (05-11), Midday (12-17), Evening (18-22), Night (23-04)
        slot_counts = {"Morning": 0, "Midday": 0, "Evening": 0, "Night": 0}
        hourly_counts: Dict[tuple, int] = {}

        for ev in ev_list:
            dt: datetime = ev['dt']
            w: float = ev['weight']

            if ev['source'] == 'ytm_liked' or (ev['source'] and 'Favorite' in ev['source']):
                is_fav = 1

            if first_dt is None or dt < first_dt:
                first_dt = dt
            if last_dt is None or dt > last_dt:
                last_dt = dt

            # Days elapsed
            delta_days = max(0.0, (now_dt - dt).total_seconds() / 86400.0)

            # Half-life decay: w * 2^(-delta / tau)
            score_short += w * math.pow(2.0, -delta_days / HALF_LIFE_SHORT_DAYS)
            score_long += w * math.pow(2.0, -delta_days / HALF_LIFE_LONG_DAYS)
            score_all_time += w

            # Velocity: 0 to 3 days vs 3 to 6 days
            if delta_days <= 3.0:
                score_recent_72h += w
            elif delta_days <= 6.0:
                score_prev_72h += w

            # Circadian slot
            h = dt.hour
            d = dt.weekday()
            hourly_counts[(h, d)] = hourly_counts.get((h, d), 0) + 1

            if 5 <= h < 12:
                slot_counts["Morning"] += 1
            elif 12 <= h < 18:
                slot_counts["Midday"] += 1
            elif 18 <= h < 23:
                slot_counts["Evening"] += 1
            else:
                slot_counts["Night"] += 1

        days_since = (now_dt - last_dt).total_seconds() / 86400.0 if last_dt else 999.0
        velocity = score_recent_72h - score_prev_72h

        dominant_slot = max(slot_counts, key=slot_counts.get) if total_plays > 0 else "Midday"

        track_affinities_to_upsert.append((
            tid,
            round(score_short, 4),
            round(score_long, 4),
            round(score_all_time, 4),
            round(velocity, 4),
            total_plays,
            first_dt.strftime('%Y-%m-%d %H:%M:%S') if first_dt else None,
            last_dt.strftime('%Y-%m-%d %H:%M:%S') if last_dt else None,
            round(days_since, 2),
            is_fav,
            dominant_slot,
            None, # top_era updated below
            now_iso
        ))

        for (h, d), cnt in hourly_counts.items():
            circadian_records.append((tid, h, d, cnt, round(cnt * 1.0, 2)))

    # Step 3: Batch upsert track_affinity
    with db:
        db.executemany("""
            INSERT INTO track_affinity (
                track_id, affinity_score_short, affinity_score_long, affinity_score_all_time,
                affinity_velocity, total_plays, first_discovered_at, last_played_at,
                days_since_last_play, is_favorite, dominant_circadian_slot, top_era, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                affinity_score_short = excluded.affinity_score_short,
                affinity_score_long = excluded.affinity_score_long,
                affinity_score_all_time = excluded.affinity_score_all_time,
                affinity_velocity = excluded.affinity_velocity,
                total_plays = excluded.total_plays,
                first_discovered_at = excluded.first_discovered_at,
                last_played_at = excluded.last_played_at,
                days_since_last_play = excluded.days_since_last_play,
                is_favorite = excluded.is_favorite,
                dominant_circadian_slot = excluded.dominant_circadian_slot,
                updated_at = excluded.updated_at
        """, track_affinities_to_upsert)

        # Clear and reload circadian patterns
        db.execute("DELETE FROM circadian_patterns;")
        db.executemany("""
            INSERT INTO circadian_patterns (track_id, hour_of_day, day_of_week, play_count, affinity_weight)
            VALUES (?, ?, ?, ?, ?)
        """, circadian_records)

    # Step 4: Aggregate Artist Affinity
    artist_map: Dict[str, Dict[str, Any]] = {}
    for tid, title, artist, album, _ in tracks:
        # Split primary artist
        primary_artist = artist.split(',')[0].strip() if artist else 'Unknown'
        if primary_artist not in artist_map:
            artist_map[primary_artist] = {
                'score_short': 0.0,
                'score_long': 0.0,
                'score_all_time': 0.0,
                'total_plays': 0,
                'vault_tracks': 0,
                'top_track_id': None,
                'top_track_score': -1.0
            }

        # Find track affinity score
        row = cur.execute(
            "SELECT affinity_score_short, affinity_score_long, affinity_score_all_time, total_plays FROM track_affinity WHERE track_id = ?",
            (tid,)
        ).fetchone()
        if row:
            s_short, s_long, s_all, plays = row
            entry = artist_map[primary_artist]
            entry['score_short'] += s_short
            entry['score_long'] += s_long
            entry['score_all_time'] += s_all
            entry['total_plays'] += plays
            entry['vault_tracks'] += 1

            if s_long > entry['top_track_score']:
                entry['top_track_score'] = s_long
                entry['top_track_id'] = tid

    artist_upserts = []
    for art_name, data in artist_map.items():
        artist_upserts.append((
            art_name,
            round(data['score_short'], 4),
            round(data['score_long'], 4),
            round(data['score_all_time'], 4),
            data['total_plays'],
            data['vault_tracks'],
            data['top_track_id'],
            now_iso
        ))

    with db:
        db.executemany("""
            INSERT INTO artist_affinity (
                artist_name, affinity_score_short, affinity_score_long, affinity_score_all_time,
                total_plays, vault_track_count, top_track_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artist_name) DO UPDATE SET
                affinity_score_short = excluded.affinity_score_short,
                affinity_score_long = excluded.affinity_score_long,
                affinity_score_all_time = excluded.affinity_score_all_time,
                total_plays = excluded.total_plays,
                vault_track_count = excluded.vault_track_count,
                top_track_id = excluded.top_track_id,
                updated_at = excluded.updated_at
        """, artist_upserts)

    # Step 5: Detect & Populate Listening Eras
    recalculate_listening_eras(db)

    print(f"[AffinityEngine] Successfully computed affinity metrics for {len(track_affinities_to_upsert)} tracks and {len(artist_upserts)} artists.")
    return {
        "status": "success",
        "processed_tracks": len(track_affinities_to_upsert),
        "processed_artists": len(artist_upserts),
        "timestamp": now_iso
    }


def recalculate_listening_eras(db: sqlite3.Connection):
    """
    Defines the chronological listening eras from history events,
    tagging dominant artists and identifying bridge tracks.
    """
    cur = db.cursor()

    era_definitions = [
        ("Genesis & Early Discoveries", "2021-01-01", "2021-12-31"),
        ("The Vault Expansion", "2022-01-01", "2022-12-31"),
        ("Acoustic & Indie Interlude", "2023-01-01", "2024-12-31"),
        ("Regional Renaissance & Fusion", "2025-01-01", "2025-12-31"),
        ("The Modern Golden Era", "2026-01-01", "2026-12-31"),
    ]

    with db:
        db.execute("DELETE FROM listening_eras;")

    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    for name, start, end in era_definitions:
        # Get dominant artists in this era
        art_rows = cur.execute("""
            SELECT t.artist, COUNT(*) as cnt
            FROM discovery_events d
            JOIN tracks t ON d.track_id = t.id
            WHERE d.played_at >= ? AND d.played_at <= ?
            GROUP BY t.artist
            ORDER BY cnt DESC
            LIMIT 6
        """, (start, end)).fetchall()
        dom_artists = [{"artist": r[0], "plays": r[1]} for r in art_rows]

        # Get top tracks in this era
        trk_rows = cur.execute("""
            SELECT t.id, t.title, t.artist, COUNT(*) as cnt
            FROM discovery_events d
            JOIN tracks t ON d.track_id = t.id
            WHERE d.played_at >= ? AND d.played_at <= ?
            GROUP BY t.id
            ORDER BY cnt DESC
            LIMIT 6
        """, (start, end)).fetchall()
        top_tracks = [{"id": r[0], "title": r[1], "artist": r[2], "plays": r[3]} for r in trk_rows]

        # Find bridge tracks (tracks first discovered in this era that continued playing in future eras)
        bridge_rows = cur.execute("""
            SELECT t.id, t.title, t.artist, COUNT(*) as future_plays
            FROM discovery_events d
            JOIN tracks t ON d.track_id = t.id
            WHERE d.played_at > ?
              AND t.id IN (
                  SELECT track_id FROM discovery_events
                  WHERE played_at >= ? AND played_at <= ?
              )
            GROUP BY t.id
            ORDER BY future_plays DESC
            LIMIT 4
        """, (end, start, end)).fetchall()
        bridge_tracks = [{"id": r[0], "title": r[1], "artist": r[2], "future_plays": r[3]} for r in bridge_rows]

        stats = cur.execute("""
            SELECT COUNT(*), COUNT(DISTINCT track_id)
            FROM discovery_events
            WHERE played_at >= ? AND played_at <= ?
        """, (start, end)).fetchone()

        total_evs, uniq_trks = stats if stats else (0, 0)

        with db:
            db.execute("""
                INSERT INTO listening_eras (
                    era_name, start_date, end_date, total_events, unique_tracks,
                    dominant_artists_json, top_tracks_json, bridge_tracks_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name, start, end, total_evs, uniq_trks,
                json.dumps(dom_artists), json.dumps(top_tracks), json.dumps(bridge_tracks), now_iso
            ))


def run_full_pipeline():
    """Entry point to initialize schema, sync scrobbles, and compute affinities."""
    db = get_db()
    init_affinity_tables(db)
    sync_navidrome_scrobbles(db)
    res = calculate_affinities(db)
    db.close()
    return res


if __name__ == "__main__":
    print("Executing Music Brain Pillar 1: Episodic Memory & Affinity Engine...")
    result = run_full_pipeline()
    print("Result:", result)
