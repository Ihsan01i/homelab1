"""
Music Brain: Autonomous Discovery Ingestion Engine (Pillar 3 to Vault & Navidrome)
Physically ingests AI-predicted candidate tracks into the vault, embeds authentic covers,
registers tracks into SQLite, and compiles the live 'Discovery Orbit.m3u8' playlist.
"""

import os
import re
import sys
import json
import time
import sqlite3
import subprocess
import unicodedata
from mutagen.oggopus import OggOpus
from mutagen.flac import Picture
import base64

# Helpers
from cover_art_helper import fetch_strict_cover

BASE_DIR = "/opt/music-brain"
MUSIC_DIR = os.path.join(BASE_DIR, "music")
DISCOVERY_DIR = os.path.join(MUSIC_DIR, "Discovery")
PLAYLIST_DIR = os.path.join(MUSIC_DIR, "playlists")
DB_PATH = os.path.join(BASE_DIR, "music_brain.db")
YTDLP_BIN = os.path.join(BASE_DIR, ".venv/bin/yt-dlp")
NAVIDROME_BIN = "/opt/navidrome/navidrome"
NAVIDROME_CONF = "/etc/navidrome/navidrome.toml"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def sanitize(text: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", text or "Unknown")
    cleaned = re.sub(r'\s+', " ", cleaned).strip()
    return cleaned[:70] if len(cleaned) > 70 else (cleaned or "Unknown")


def get_primary_artist(artist_str: str) -> str:
    if not artist_str:
        return "Unknown Artist"
    parts = re.split(r'[,]| feat\.? | ft\.? ', artist_str, flags=re.IGNORECASE)
    return parts[0].strip() or "Unknown Artist"


def ingest_candidate(c: dict) -> bool:
    """Downloads a single candidate track, tags it, and adds it to SQLite."""
    cand_id = c["id"]
    vid = c["source_id"]
    title = c["title"]
    artist = c["artist"]
    album = c.get("album") or "Discovery Orbit"
    thumb_url = c.get("thumb_url")

    primary = get_primary_artist(artist)
    safe_art = sanitize(primary)
    safe_album = sanitize(album)
    safe_title = sanitize(title)

    track_dir = os.path.join(DISCOVERY_DIR, safe_art, safe_album)
    os.makedirs(track_dir, exist_ok=True)

    dest_opus = os.path.join(track_dir, f"{safe_art} - {safe_title}.opus")
    cov_path = os.path.join(track_dir, "cover.jpg")

    # Download if not already present
    if not os.path.exists(dest_opus) or os.path.getsize(dest_opus) < 40000:
        print(f"[DiscoveryIngest] Downloading '{title}' by {artist} (ID: {vid})...")
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
                print(f"[DiscoveryIngest] Download failed for {title}: {res.stderr[:120]}")
                return False
        except Exception as e:
            print(f"[DiscoveryIngest] Download exception for {title}: {e}")
            return False

    # Authentic Studio Cover Art
    img_data, _ = fetch_strict_cover(primary, title, album, vid, cov_path, thumb_url)

    # Mutagen Tagging
    duration_ms = 0
    try:
        audio = OggOpus(dest_opus)
        audio["title"] = title
        audio["artist"] = artist
        audio["albumartist"] = primary
        audio["album"] = album
        audio["genre"] = "Discovery Orbit"

        if img_data:
            p = Picture()
            p.data = img_data
            p.type = 3
            p.mime = "image/jpeg"
            audio["metadata_block_picture"] = [base64.b64encode(p.write()).decode('ascii')]

        audio.save()
        duration_ms = int(audio.info.length * 1000)
    except Exception as e:
        print(f"[DiscoveryIngest] Tagging warning on {dest_opus}: {e}")

    # Register into SQLite tracks & discovery_events
    file_size = os.path.getsize(dest_opus)
    now_iso = time.strftime('%Y-%m-%d %H:%M:%S')

    db = get_db()
    with db:
        # Check if already in tracks
        existing = db.execute("SELECT id FROM tracks WHERE source_id = ? OR local_path = ?", (vid, dest_opus)).fetchone()
        if existing:
            track_id = existing[0]
            db.execute("""
                UPDATE tracks SET
                    local_path = ?, file_size_bytes = ?, duration_ms = ?,
                    download_status = 'COMPLETED', downloaded_at = ?
                WHERE id = ?
            """, (dest_opus, file_size, duration_ms, now_iso, track_id))
        else:
            cur = db.execute("""
                INSERT INTO tracks (
                    title, artist, album, duration_ms, source_id, created_at,
                    download_status, local_path, file_size_bytes, downloaded_at,
                    audio_format, is_lossless
                ) VALUES (?, ?, ?, ?, ?, ?, 'COMPLETED', ?, ?, ?, 'OPUS', 0)
            """, (title, artist, album, duration_ms, vid, now_iso, dest_opus, file_size, now_iso))
            track_id = cur.lastrowid

        # Update predicted_orbit status
        db.execute("""
            UPDATE predicted_orbit SET
                status = 'in_vault'
            WHERE id = ?
        """, (cand_id,))

        # Add discovery event
        ctx = json.dumps({"source": "predicted_orbit", "seed_track": c.get("seed_track_title")})
        db.execute("""
            INSERT INTO discovery_events (track_id, discovery_source, context_json, played_at)
            VALUES (?, 'predicted_orbit', ?, ?)
        """, (track_id, ctx, now_iso))

    db.close()
    print(f"  ✓ Successfully ingested into vault: {title} - {artist} (Track ID: {track_id})")
    return True


def compile_discovery_playlist():
    """Generates the Discovery Orbit.m3u8 playlist in /opt/music-brain/music/playlists/."""
    db = get_db()
    rows = db.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.duration_ms, t.local_path
        FROM tracks t
        WHERE t.local_path LIKE '%/music/Discovery/%'
          AND t.download_status = 'COMPLETED'
        ORDER BY t.id DESC
        LIMIT 30;
    """).fetchall()
    db.close()

    if not rows:
        print("[DiscoveryIngest] No discovery tracks found to compile.")
        return 0

    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    pls_path = os.path.join(PLAYLIST_DIR, "Discovery Orbit.m3u8")
    tmp_path = pls_path + ".tmp"

    lines = ["#EXTM3U\n"]
    for r in rows:
        p = r["local_path"]
        if os.path.exists(p):
            dur_sec = max(0, int((r["duration_ms"] or 0) / 1000))
            rel = os.path.relpath(p, PLAYLIST_DIR).replace('\\', '/')
            safe_title = (r["title"] or "Unknown").replace('\n', ' ')
            safe_artist = (r["artist"] or "Unknown").replace('\n', ' ')
            lines.append(f"#EXTINF:{dur_sec},{safe_artist} - {safe_title}\n{rel}\n")

    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp_path, pls_path)

    print(f"[DiscoveryIngest] Compiled Discovery Orbit.m3u8 with {len(rows)} tracks.")
    return len(rows)


def run_discovery_ingestion(limit: int = 10):
    """Ingests up to `limit` candidate tracks from predicted_orbit."""
    db = get_db()
    candidates = db.execute("""
        SELECT id, source_id, title, artist, album, thumb_url, seed_track_title
        FROM predicted_orbit
        WHERE status = 'recommended'
        ORDER BY id ASC
        LIMIT ?;
    """, (limit,)).fetchall()
    db.close()

    if not candidates:
        print("[DiscoveryIngest] No recommended candidates waiting in predicted_orbit.")
        return 0

    print(f"[DiscoveryIngest] Ingesting {len(candidates)} predicted candidates into vault...")
    success_count = 0
    for c in candidates:
        if ingest_candidate(dict(c)):
            success_count += 1

    if success_count > 0:
        compile_discovery_playlist()
        print("[DiscoveryIngest] Triggering Navidrome library scan...")
        try:
            subprocess.run([NAVIDROME_BIN, "scan", "--configfile", NAVIDROME_CONF], capture_output=True)
            print("[DiscoveryIngest] Navidrome scan complete.")
        except Exception as e:
            print(f"[DiscoveryIngest] Navidrome scan notice: {e}")

    return success_count


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print(f"Executing Autonomous Discovery Ingestion (Target: {count} tracks)...")
    ingested = run_discovery_ingestion(limit=count)
    print(f"Autonomous Discovery Ingestion Complete: {ingested} tracks ingested and synced!")
