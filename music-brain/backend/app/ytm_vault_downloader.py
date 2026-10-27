import os
import sys
import re
import time
import json
import sqlite3
import argparse
import subprocess
import base64
from mutagen.flac import Picture
from mutagen.oggopus import OggOpus

from cover_art_helper import clean_artist_names, clean_track_title, fetch_strict_cover

DB_PATH = "/opt/music-brain/music_brain.db"
MUSIC_DIR = "/opt/music-brain/music"
YTDLP_BIN = "/opt/music-brain/.venv/bin/yt-dlp"
LOG_FILE = "/opt/music-brain/ytm_downloader.log"

def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name or "Unknown")
    cleaned = re.sub(r'\s+', " ", cleaned).strip()
    return cleaned[:80] if len(cleaned) > 80 else (cleaned or "Unknown")

def download_track(track: dict):
    tid = track["id"]
    raw_artist = track["artist"] or "Unknown Artist"
    raw_title = track["title"] or "Unknown Title"
    source_id = track["source_id"]
    
    full_art, primary = clean_artist_names(raw_artist)
    clean_title = clean_track_title(raw_title, primary)
    
    # Do NOT put singles into a monolithic "Singles & EP" folder
    raw_album = track["album"]
    if not raw_album or raw_album == "Singles & EP":
        album_name = clean_title
    else:
        album_name = raw_album
        
    safe_prim = sanitize_filename(primary)
    safe_album = sanitize_filename(album_name)
    safe_title = sanitize_filename(clean_title)
    
    dest_dir = os.path.join(MUSIC_DIR, safe_prim, safe_album)
    os.makedirs(dest_dir, exist_ok=True)
    dest_opus = os.path.join(dest_dir, f"{safe_prim} - {safe_title}.opus")
    cov_path = os.path.join(dest_dir, "cover.jpg")
    
    if os.path.exists(dest_opus) and os.path.getsize(dest_opus) > 100000:
        log(f"[{tid}] Already exists on disk: {dest_opus}")
        return True

    log(f"--- Ingesting [{tid}] {primary} - {clean_title} (YTM Opus) ---")
    urls = []
    if source_id:
        urls.append(f"https://www.youtube.com/watch?v={source_id}")
    urls.append(f"ytsearch1:{primary} {clean_title} audio")
    
    downloaded = False
    for u in urls:
        cmd = [
            YTDLP_BIN,
            "-x",
            "--audio-format", "opus",
            "--audio-quality", "0",
            "--no-playlist",
            "-o", dest_opus,
            u
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if res.returncode == 0 and os.path.exists(dest_opus) and os.path.getsize(dest_opus) > 50000:
                downloaded = True
                break
        except Exception:
            continue
            
    if not downloaded:
        log(f"? Failed to download [{tid}] {clean_title}")
        return False

    # Fetch verified cover art (strict iTunes or authentic YTM center-crop)
    img_data = fetch_strict_cover(primary, clean_title, album_name, source_id, cov_path)
    
    # Read actual audio duration & tag
    try:
        audio = OggOpus(dest_opus)
        exact_dur_sec = audio.info.length
        audio["title"] = clean_title
        audio["artist"] = full_art
        audio["albumartist"] = primary
        audio["album"] = album_name
        if img_data:
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Front Cover"
            pic.data = img_data
            b64 = base64.b64encode(pic.write()).decode('ascii')
            audio["metadata_block_picture"] = [b64]
        audio.save()
    except Exception as e:
        log(f"Tag error on {dest_opus}: {e}")
        exact_dur_sec = None

    sz = os.path.getsize(dest_opus)
    dur_ms = int(exact_dur_sec * 1000) if exact_dur_sec else None
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE tracks 
        SET download_status = 'completed',
            audio_format = 'OPUS',
            is_lossless = 0,
            local_path = ?,
            file_size_bytes = ?,
            duration_ms = coalesce(?, duration_ms),
            artist = ?,
            album = ?,
            downloaded_at = CURRENT_TIMESTAMP,
            download_error = NULL
        WHERE id = ?;
    """, (dest_opus, sz, dur_ms, full_art, album_name, tid))
    conn.commit()
    conn.close()
    
    log(f"? Ingested authentic Opus [{tid}] {primary} - {clean_title} ({sz/(1024*1024):.2f} MB, {exact_dur_sec or 0:.1f}s)")
    return True

def run_downloader(limit: int = None):
    log(f"=== Fast YTM Base Ingest Engine Started (Limit: {limit}) ===")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tracks = conn.execute("""
        SELECT id, title, artist, album, duration_ms, source_id, local_path, download_status
        FROM tracks
        WHERE download_status != 'completed' OR local_path IS NULL
        ORDER BY id ASC;
    """).fetchall()
    conn.close()
    
    if limit:
        tracks = tracks[:limit]
        
    log(f"Queued {len(tracks)} pending tracks for fast authentic YTM Opus download.")
    for i, t in enumerate(tracks):
        log(f"[{i+1}/{len(tracks)}] Processing...")
        download_track(dict(t))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    run_downloader(limit=args.limit)
