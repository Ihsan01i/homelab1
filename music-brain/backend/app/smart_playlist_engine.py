"""
Music Brain: Autonomous Smart Playlist Engine (Pillar 2)
Compiles dynamic, mathematical M3U8 smart playlists directly into Navidrome.
"""

import os
import sqlite3
import datetime
from typing import List, Dict, Any, Tuple

PLAYLIST_DIR = "/opt/music-brain/music/playlists"
MUSIC_DIR = "/opt/music-brain/music"
DB_PATH = "/opt/music-brain/music_brain.db"


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def format_m3u8_entry(title: str, artist: str, duration_ms: int, local_path: str) -> str:
    """Formats a single track into an Extended M3U entry with relative path from PLAYLIST_DIR."""
    dur_sec = max(0, int((duration_ms or 0) / 1000))
    # Relative path from /opt/music-brain/music/playlists to /opt/music-brain/music/... is ../<relative_from_music>
    rel_path = os.path.relpath(local_path, PLAYLIST_DIR).replace('\\', '/')
    safe_title = (title or "Unknown").replace('\n', ' ')
    safe_artist = (artist or "Unknown").replace('\n', ' ')
    return f"#EXTINF:{dur_sec},{safe_artist} - {safe_title}\n{rel_path}\n"


def write_m3u8(playlist_name: str, tracks: List[Dict[str, Any]]) -> str:
    """Writes an M3U8 playlist file atomically and returns file path."""
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    filename = f"{playlist_name}.m3u8"
    file_path = os.path.join(PLAYLIST_DIR, filename)
    tmp_path = file_path + ".tmp"

    lines = ["#EXTM3U\n"]
    valid_count = 0
    for t in tracks:
        p = t.get("local_path")
        if p and os.path.exists(p):
            lines.append(format_m3u8_entry(t.get("title"), t.get("artist"), t.get("duration_ms"), p))
            valid_count += 1

    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    os.replace(tmp_path, file_path)
    return file_path


# -------------------------------------------------------------
# Smart Playlist Generators
# -------------------------------------------------------------

def generate_heavy_rotation(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Flagship 1: Heavy Rotation - Top 35 tracks with highest 45-day half-life affinity."""
    rows = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.duration_ms, t.local_path, a.affinity_score_short
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE t.local_path IS NOT NULL AND a.affinity_score_short > 0.5
        ORDER BY a.affinity_score_short DESC
        LIMIT 35;
    """).fetchall()
    return [dict(r) for r in rows]


def generate_forgotten_gems(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Flagship 2: Forgotten Gems - High all-time affinity songs not played in >= 45 days."""
    rows = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.duration_ms, t.local_path, a.affinity_score_all_time, a.days_since_last_play
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE t.local_path IS NOT NULL
          AND a.total_plays >= 2
          AND a.days_since_last_play >= 45.0
        ORDER BY a.affinity_score_all_time DESC
        LIMIT 35;
    """).fetchall()
    return [dict(r) for r in rows]


def generate_time_machine(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Flagship 3: Vault Time Capsule - Songs discovered in the current month across previous years."""
    now = datetime.datetime.now()
    cur_month = f"{now.month:02d}"
    prev_month = f"{(now.month - 1) if now.month > 1 else 12:02d}"
    cur_year = str(now.year)

    rows = conn.execute("""
        SELECT DISTINCT t.id, t.title, t.artist, t.album, t.duration_ms, t.local_path, d.played_at
        FROM discovery_events d
        JOIN tracks t ON d.track_id = t.id
        WHERE t.local_path IS NOT NULL
          AND (strftime('%m', d.played_at) = ? OR strftime('%m', d.played_at) = ?)
          AND strftime('%Y', d.played_at) < ?
        ORDER BY d.played_at DESC
        LIMIT 35;
    """, (cur_month, prev_month, cur_year)).fetchall()
    return [dict(r) for r in rows]


def generate_circadian_flow(conn: sqlite3.Connection) -> Tuple[str, List[Dict[str, Any]]]:
    """Flagship 4: Circadian Flow - Adaptive slot based on current hour of day."""
    h = datetime.datetime.now().hour
    if 5 <= h < 12:
        slot = "Morning Focus"
    elif 12 <= h < 18:
        slot = "Midday Drive"
    elif 18 <= h < 23:
        slot = "Evening Chill"
    else:
        slot = "Midnight Orbit"

    # Prioritize tracks matching the current circadian slot or highest daytime affinity
    rows = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.duration_ms, t.local_path, a.affinity_score_long
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE t.local_path IS NOT NULL
          AND (a.dominant_circadian_slot = ? OR a.affinity_score_short > 2.0)
        ORDER BY (a.dominant_circadian_slot = ?) DESC, a.affinity_score_long DESC
        LIMIT 30;
    """, (slot.split()[0], slot.split()[0])).fetchall()

    return slot, [dict(r) for r in rows]


def generate_deep_vault(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Flagship 5: Deep Vault - Hidden B-sides and under-played tracks by your top artists."""
    rows = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.duration_ms, t.local_path, art.affinity_score_short
        FROM tracks t
        JOIN artist_affinity art ON (t.artist = art.artist_name OR t.artist LIKE art.artist_name || ',%')
        JOIN track_affinity a ON t.id = a.track_id
        WHERE t.local_path IS NOT NULL
          AND art.affinity_score_short >= 8.0
          AND a.total_plays <= 1
        ORDER BY art.affinity_score_short DESC, RANDOM()
        LIMIT 30;
    """).fetchall()
    return [dict(r) for r in rows]


def generate_the_bridge(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Flagship 6: The Bridge - Tracks that survived multiple eras connecting your past and present."""
    rows = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.duration_ms, t.local_path, a.affinity_score_all_time, a.total_plays
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE t.local_path IS NOT NULL
          AND a.total_plays >= 3
          AND a.first_discovered_at < '2025-01-01'
          AND a.last_played_at >= '2025-06-01'
        ORDER BY a.total_plays DESC, a.affinity_score_all_time DESC
        LIMIT 25;
    """).fetchall()
    return [dict(r) for r in rows]


# -------------------------------------------------------------
# Master Compilation Routine
# -------------------------------------------------------------

def generate_all_smart_playlists() -> Dict[str, Any]:
    """Compiles all autonomous smart playlists into /opt/music-brain/music/playlists/."""
    conn = get_db()
    results = {}

    try:
        # 1. Heavy Rotation
        hr_tracks = generate_heavy_rotation(conn)
        write_m3u8("Heavy Rotation", hr_tracks)
        results["Heavy Rotation"] = {"count": len(hr_tracks), "desc": "Current obsessions (45-day half-life)"}

        # 2. Forgotten Gems
        fg_tracks = generate_forgotten_gems(conn)
        write_m3u8("Forgotten Gems", fg_tracks)
        results["Forgotten Gems"] = {"count": len(fg_tracks), "desc": "High all-time affinity unplayed in >45 days"}

        # 3. Vault Time Capsule
        tm_tracks = generate_time_machine(conn)
        write_m3u8("Time Machine", tm_tracks)
        results["Time Machine"] = {"count": len(tm_tracks), "desc": f"Late fall flashbacks from past years"}

        # 4. Circadian Flow
        slot, cf_tracks = generate_circadian_flow(conn)
        write_m3u8("Circadian Flow", cf_tracks)
        results["Circadian Flow"] = {"count": len(cf_tracks), "slot": slot, "desc": f"Hour-of-day adaptive flow ({slot})"}

        # 5. Deep Vault
        dv_tracks = generate_deep_vault(conn)
        write_m3u8("Deep Vault", dv_tracks)
        results["Deep Vault"] = {"count": len(dv_tracks), "desc": "Hidden cuts by your highest affinity artists"}

        # 6. The Bridge
        tb_tracks = generate_the_bridge(conn)
        write_m3u8("The Bridge", tb_tracks)
        results["The Bridge"] = {"count": len(tb_tracks), "desc": "Gateway tracks spanning 2021-2026 eras"}

    finally:
        conn.close()

    print(f"[SmartPlaylists] Successfully compiled {len(results)} autonomous playlists.")
    return results


if __name__ == "__main__":
    print("Executing Autonomous Smart Playlist Compilation (Pillar 2)...")
    res = generate_all_smart_playlists()
    for name, info in res.items():
        print(f"  ✓ {name}.m3u8 -> {info['count']} tracks ({info['desc']})")
