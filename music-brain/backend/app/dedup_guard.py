# /opt/music-brain/backend/app/dedup_guard.py
import re
import sqlite3
import os

NAVI_DB = "/var/lib/navidrome/navidrome.db"
MB_DB = "/opt/music-brain/music_brain.db"

def normalize_title(t: str) -> str:
    if not t:
        return ""
    # Strip (From "...") / [From ...] / soundtrack / ost / single / ep
    t = re.sub(r'[\(\[](?:from\s+.*?|soundtrack|ost|single|ep)[\)\]]', '', t, flags=re.IGNORECASE)
    # Strip (feat. ...) / [ft. ...]
    t = re.sub(r'[\(\[](?:feat\.?|ft\.?)\s+.*?[\)\]]', '', t, flags=re.IGNORECASE)
    # Strip (official music video) / (music video) / [mv] / (visualizer) / (audio) / (lyric video)
    t = re.sub(r'[\(\[](?:official\s+)?(?:music\s+video|video|mv|visualizer|audio|lyric\s+video|lyrics)[\)\]]', '', t, flags=re.IGNORECASE)
    # Strip non-alphanumeric unicode
    t = re.sub(r'[^\w\s]', ' ', t, flags=re.UNICODE).lower()
    return re.sub(r'\s+', ' ', t).strip()

def normalize_artist(a: str) -> str:
    if not a:
        return ""
    parts = re.split(r'[,&/]| feat\.? | ft\.? ', a, flags=re.IGNORECASE)
    primary = parts[0].strip().lower()
    primary = re.sub(r'[^\w\s]', ' ', primary, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', primary).strip()

def check_vault_duplicate(artist: str, title: str, duration_sec: float = None) -> dict:
    """
    Returns existing vault track dict if candidate is a duplicate, else None.
    Matching rules:
      1. Primary artist matches
      2. Core title matches (after stripping 'From Movie', 'Single', 'Music Video', etc.)
      3. If duration_sec provided: duration matches within ±4.0 seconds
    """
    norm_a = normalize_artist(artist)
    norm_t = normalize_title(title)
    
    if not norm_a or not norm_t or len(norm_t) < 3:
        return None

    try:
        conn = sqlite3.connect(NAVI_DB)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        if duration_sec is not None and duration_sec > 0:
            min_dur = max(0, duration_sec - 4.5)
            max_dur = duration_sec + 4.5
            c.execute("""
                SELECT id, title, artist, album, path, duration, suffix, bit_rate
                FROM media_file
                WHERE missing = 0 AND duration BETWEEN ? AND ?
            """, (min_dur, max_dur))
        else:
            c.execute("""
                SELECT id, title, artist, album, path, duration, suffix, bit_rate
                FROM media_file
                WHERE missing = 0
            """)
        candidates = c.fetchall()
        conn.close()
        
        for cand in candidates:
            c_a = normalize_artist(cand['artist'])
            c_t = normalize_title(cand['title'])
            
            # Check artist match (exact or substring)
            if c_a == norm_a or c_a in norm_a or norm_a in c_a:
                # Check title match
                if c_t == norm_t:
                    return dict(cand)
                    
        return None
    except Exception as e:
        print(f"[DedupGuard] Error checking duplicate: {e}")
        return None

def check_db_duplicate(mb_cursor, artist: str, title: str, duration_sec: float = None) -> dict:
    """
    Checks tracks table in music_brain.db for matching existing tracks.
    """
    norm_a = normalize_artist(artist)
    norm_t = normalize_title(title)
    if not norm_a or not norm_t or len(norm_t) < 3:
        return None

    mb_cursor.execute("SELECT id, title, artist, album, local_path, duration_ms, download_status FROM tracks WHERE download_status = 'completed'")
    rows = mb_cursor.fetchall()
    for r in rows:
        r_id, r_title, r_artist, r_album, r_path, r_dur, r_status = r
        if not r_title:
            continue
        c_a = normalize_artist(r_artist)
        c_t = normalize_title(r_title)
        if c_a == norm_a or c_a in norm_a or norm_a in c_a:
            if c_t == norm_t:
                if duration_sec and r_dur:
                    r_dur_sec = r_dur / 1000.0
                    if abs(r_dur_sec - duration_sec) <= 4.5:
                        return {"id": r_id, "title": r_title, "artist": r_artist, "album": r_album, "local_path": r_path}
                else:
                    return {"id": r_id, "title": r_title, "artist": r_artist, "album": r_album, "local_path": r_path}
    return None

if __name__ == '__main__':
    # Test case: Sushin Shyam - Aathi Iva Yarraa (From "Scene")
    res = check_vault_duplicate("Sushin Shyam, Rajpriyan", "Aathi Iva Yarraa (From \"Scene\")", 188.0)
    print("Test duplicate check result:", res)
    assert res is not None, "Failed to catch duplicate!"
    print("DedupGuard self-test PASSED successfully!")
