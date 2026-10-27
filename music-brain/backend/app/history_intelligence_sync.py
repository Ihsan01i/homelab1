import os
import sys
import re
import time
import json
import sqlite3
import datetime
import subprocess
from ytmusicapi import YTMusic
from mutagen.flac import FLAC, Picture
from mutagen.oggopus import OggOpus

BASE_DIR = "/opt/music-brain"
DB_PATH = os.path.join(BASE_DIR, "music_brain.db")
AUTH_PATH = os.path.join(BASE_DIR, "browser.json")
LOG_PATH = os.path.join(BASE_DIR, "history_sync.log")
MUSIC_DIR = os.path.join(BASE_DIR, "music")
PLAYLISTS_DIR = os.path.join(MUSIC_DIR, "playlists")
YTDLP_BIN = os.path.join(BASE_DIR, ".venv/bin/yt-dlp")

# Import strict cover art helper
sys.path.insert(0, os.path.join(BASE_DIR, "backend/app"))
from cover_art_helper import clean_artist_names, clean_track_title, fetch_strict_cover

def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:\"<>|]', "", name or "Unknown")
    cleaned = re.sub(r'\s+', " ", cleaned).strip()
    return cleaned[:80] if len(cleaned) > 80 else (cleaned or "Unknown")

def download_and_ingest_track(conn, cursor, track_id: int, vid: str, title: str, artist: str, album: str) -> str:
    full_art, primary = clean_artist_names(artist)
    clean_title = clean_track_title(title, primary)
    
    album_name = album if (album and album != "Singles & EP") else clean_title
    safe_prim = sanitize_filename(primary)
    safe_album = sanitize_filename(album_name)
    safe_title = sanitize_filename(clean_title)
    
    dest_dir = os.path.join(MUSIC_DIR, safe_prim, safe_album)
    os.makedirs(dest_dir, exist_ok=True)
    dest_opus = os.path.join(dest_dir, f"{safe_prim} - {safe_title}.opus")
    cov_path = os.path.join(dest_dir, "cover.jpg")
    
    if os.path.exists(dest_opus) and os.path.getsize(dest_opus) > 50000:
        log(f"Track already on disk: {dest_opus}")
        return dest_opus
        
    log(f"Downloading new history track: {primary} - {clean_title} (ID: {vid})...")
    url = f"https://www.youtube.com/watch?v={vid}"
    cmd = [
        YTDLP_BIN,
        "-x",
        "--audio-format", "opus",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", dest_opus,
        url
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if res.returncode != 0 or not os.path.exists(dest_opus):
            log(f"Download failed for {title}: {res.stderr[:200]}")
            return None
    except Exception as e:
        log(f"Download error on {title}: {e}")
        return None
        
    # Fetch authentic square cover art
    img_data = fetch_strict_cover(primary, clean_title, album_name, vid, cov_path)
    
    # Tag file
    try:
        audio = OggOpus(dest_opus)
        audio["title"] = clean_title
        audio["artist"] = full_art
        audio["albumartist"] = primary
        audio["album"] = album_name
        if img_data:
            import base64
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Front Cover"
            pic.data = img_data
            b64 = base64.b64encode(pic.write()).decode('ascii')
            audio["metadata_block_picture"] = [b64]
        audio.save()
    except Exception as e:
        log(f"Tagging error on {dest_opus}: {e}")
        
    sz = os.path.getsize(dest_opus)
    cursor.execute("""
        UPDATE tracks 
        SET download_status = 'completed',
            local_path = ?,
            file_size_bytes = ?,
            audio_format = 'OPUS',
            downloaded_at = CURRENT_TIMESTAMP
        WHERE id = ?;
    """, (dest_opus, sz, track_id))
    conn.commit()
    log(f"Successfully ingested history track: {primary} - {clean_title} ({sz/(1024*1024):.1f} MB)")
    return dest_opus

def export_smart_history_playlists(conn):
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    cursor = conn.cursor()
    
    # 1. Recently Played (Latest 50 distinct tracks from history)
    recent_query = """
        SELECT t.title, t.artist, t.duration_ms, t.local_path
        FROM discovery_events d
        JOIN tracks t ON d.track_id = t.id
        WHERE d.discovery_source = 'ytm_history' AND t.local_path IS NOT NULL AND t.download_status = 'completed'
        GROUP BY t.id
        ORDER BY MAX(d.id) DESC
        LIMIT 50;
    """
    recent_tracks = cursor.execute(recent_query).fetchall()
    if recent_tracks:
        pl_path = os.path.join(PLAYLISTS_DIR, "Recently Played.m3u8")
        with open(pl_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for r in recent_tracks:
                title, artist, dur, lpath = r
                sec = int((dur or 0) / 1000)
                rel = os.path.relpath(lpath, PLAYLISTS_DIR).replace("\\", "/")
                f.write(f"#EXTINF:{sec},{artist} - {title}\n{rel}\n")
        log(f"Exported 'Recently Played' playlist with {len(recent_tracks)} tracks.")

    # 2. Heavy Rotation (Top 50 most frequently played tracks)
    top_query = """
        SELECT t.title, t.artist, t.duration_ms, t.local_path, COUNT(*) as play_count
        FROM discovery_events d
        JOIN tracks t ON d.track_id = t.id
        WHERE t.local_path IS NOT NULL AND t.download_status = 'completed'
        GROUP BY t.id
        ORDER BY play_count DESC
        LIMIT 50;
    """
    top_tracks = cursor.execute(top_query).fetchall()
    if top_tracks:
        pl_path = os.path.join(PLAYLISTS_DIR, "Heavy Rotation.m3u8")
        with open(pl_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for r in top_tracks:
                title, artist, dur, lpath, pcount = r
                sec = int((dur or 0) / 1000)
                rel = os.path.relpath(lpath, PLAYLISTS_DIR).replace("\\", "/")
                f.write(f"#EXTINF:{sec},{artist} - {title}\n{rel}\n")
        log(f"Exported 'Heavy Rotation' playlist with {len(top_tracks)} top tracks.")

def run_history_sync():
    log("=== Starting 8-Hour YouTube Music History & Intelligence Sync ===")
    if not os.path.exists(AUTH_PATH):
        log(f"Error: {AUTH_PATH} not found!")
        return

    try:
        yt = YTMusic(AUTH_PATH)
        history = yt.get_history()
    except Exception as e:
        log(f"Failed to fetch YouTube Music history: {e}")
        return

    log(f"Retrieved {len(history)} items from YouTube Music play history.")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    new_plays_logged = 0
    new_tracks_downloaded = 0

    # Process from oldest to newest to preserve chronological order
    for item in reversed(history):
        vid = item.get("videoId")
        title = (item.get("title") or "").strip()
        if not vid or not title:
            continue

        artists_list = [a["name"] for a in item.get("artists", []) if "name" in a]
        artist = ", ".join(artists_list) if artists_list else "Unknown Artist"
        album = item.get("album", {}).get("name") if item.get("album") else None
        duration = item.get("duration_seconds")

        # Find or create track
        cursor.execute("SELECT id, download_status, local_path FROM tracks WHERE source_id = ?;", (vid,))
        res = cursor.fetchone()
        
        if not res:
            cursor.execute('''
                SELECT id, download_status, local_path FROM tracks
                WHERE LOWER(TRIM(title)) = ? AND LOWER(TRIM(artist)) = ?
                ORDER BY is_lossless DESC, id ASC LIMIT 1;
            ''', (title.lower().strip(), artist.lower().strip()))
            res = cursor.fetchone()
        if not res:
            cursor.execute("""
                INSERT INTO tracks (title, artist, album, duration_ms, source_id, download_status)
                VALUES (?, ?, ?, ?, ?, 'pending');
            """, (title, artist, album, duration * 1000 if duration else None, vid))
            track_id = cursor.lastrowid
            conn.commit()
            download_status = "pending"
            local_path = None
        else:
            track_id, download_status, local_path = res

        # Check if this exact playback event was already recorded recently
        cursor.execute("""
            SELECT id FROM discovery_events 
            WHERE track_id = ? AND discovery_source = 'ytm_history'
            ORDER BY id DESC LIMIT 1;
        """, (track_id,))
        recent_event = cursor.fetchone()
        
        # Always log to discovery_events for recommendation scoring
        cursor.execute("""
            INSERT INTO discovery_events (track_id, discovery_source, context_json, played_at)
            VALUES (?, 'ytm_history', ?, datetime('now'));
        """, (track_id, json.dumps(item)))
        new_plays_logged += 1
        conn.commit()

        # Download if not already completed on disk
        if download_status != 'completed' or not local_path or not os.path.exists(local_path):
            downloaded_path = download_and_ingest_track(conn, cursor, track_id, vid, title, artist, album)
            if downloaded_path:
                new_tracks_downloaded += 1

    # Export smart dynamic playlists
    export_smart_history_playlists(conn)
    conn.close()

    log(f"Sync complete. Logged {new_plays_logged} playback events, downloaded {new_tracks_downloaded} new tracks.")

if __name__ == "__main__":
    run_history_sync()
