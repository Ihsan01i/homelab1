import os
import sys
import re
import time
import json
import sqlite3
import argparse
import mutagen
from mutagen.flac import FLAC
from mutagen.oggopus import OggOpus
import musicbrainzngs

try:
    import acoustid
except ImportError:
    acoustid = None

DB_PATH = "/opt/music-brain/music_brain.db"
MUSIC_DIR = "/opt/music-brain/music"
LOG_FILE = "/opt/music-brain/fingerprinter.log"

# User-configured AcoustID Keys
ACOUSTID_USER_KEY = "aUdI0aOIfo"
# Application client key (register at https://acoustid.org/new-application if desired)
ACOUSTID_CLIENT_KEY = os.environ.get("ACOUSTID_CLIENT_KEY", "")

# Initialize MusicBrainz API Client
musicbrainzngs.set_useragent("MusicBrain", "1.0", "admin@musicbrain.local")
musicbrainzngs.set_rate_limit(limit_or_interval=1.0)

def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def match_acoustid(file_path: str):
    """Attempt AcoustID fingerprint lookup if client key is configured."""
    if not acoustid or not ACOUSTID_CLIENT_KEY:
        return None
    try:
        results = acoustid.match(ACOUSTID_CLIENT_KEY, file_path)
        for score, recording_id, title, artist in results:
            if score >= 0.8:
                return {
                    "source": "AcoustID",
                    "score": score,
                    "recording_id": recording_id,
                    "title": title,
                    "artist": artist
                }
    except Exception as e:
        log(f"AcoustID lookup warning on {file_path}: {e}")
    return None

def match_musicbrainz_direct(title: str, artist: str, target_sec: float = None):
    """Query MusicBrainz Direct API using clean title and artist with duration verification."""
    # Clean noise from title
    clean_title = re.sub(r'\s*[\(\[].*?[\)\]]', '', title).strip()
    clean_artist = re.sub(r'\s*-\s*Topic\b', '', artist, flags=re.IGNORECASE).strip()
    clean_artist = re.split(r'[,]| feat\.? | ft\.? ', clean_artist, flags=re.IGNORECASE)[0].strip()

    try:
        query_parts = []
        if clean_title:
            query_parts.append(f'recording:"{clean_title}"')
        if clean_artist:
            query_parts.append(f'artist:"{clean_artist}"')
        
        query = " AND ".join(query_parts)
        res = musicbrainzngs.search_recordings(query=query, limit=5)
        recordings = res.get("recording-list", [])
        
        best = None
        for r in recordings:
            r_title = r.get("title", "")
            r_artist = r.get("artist-credit-phrase", "")
            r_dur_ms = int(r.get("length", 0) or 0)
            r_dur_sec = r_dur_ms / 1000.0 if r_dur_ms > 0 else 0
            
            # Check duration if target_sec is provided
            if target_sec and r_dur_sec > 0:
                if abs(r_dur_sec - target_sec) > 5.0:
                    continue
                    
            release_title = ""
            release_date = ""
            releases = r.get("release-list", [])
            if releases:
                release_title = releases[0].get("title", "")
                release_date = releases[0].get("date", "")
                
            best = {
                "source": "MusicBrainz",
                "recording_id": r.get("id"),
                "title": r_title,
                "artist": r_artist,
                "album": release_title,
                "date": release_date,
                "tags": [t.get("name") for t in r.get("tag-list", [])[:3]]
            }
            break
            
        return best
    except Exception as e:
        log(f"MusicBrainz search warning for '{title} - {artist}': {e}")
        return None

def enrich_tags(file_path: str, meta: dict, dry_run: bool = False):
    """Enrich file metadata while strictly preserving cover art intact."""
    if not os.path.exists(file_path):
        return False
        
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".flac":
            audio = FLAC(file_path)
            # Retain all existing pictures untouched
            if meta.get("recording_id"):
                audio["MUSICBRAINZ_TRACKID"] = meta["recording_id"]
            if meta.get("date") and not audio.get("DATE"):
                audio["DATE"] = meta["date"]
            if meta.get("tags"):
                existing = audio.get("GENRE", [])
                if not existing:
                    audio["GENRE"] = meta["tags"][0]
            if not dry_run:
                audio.save()
            return True
            
        elif ext == ".opus":
            audio = OggOpus(file_path)
            if meta.get("recording_id"):
                audio["MUSICBRAINZ_TRACKID"] = meta["recording_id"]
            if meta.get("date") and not audio.get("DATE"):
                audio["DATE"] = meta["date"]
            if meta.get("tags"):
                existing = audio.get("GENRE", [])
                if not existing:
                    audio["GENRE"] = meta["tags"][0]
            if not dry_run:
                audio.save()
            return True
    except Exception as e:
        log(f"Error enriching {file_path}: {e}")
    return False

def scan_and_fingerprint(limit: int = None, dry_run: bool = False):
    log(f"=== Headless MusicBrainz / AcoustID Enricher Started (Dry Run: {dry_run}, Limit: {limit}) ===")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tracks = conn.execute("""
        SELECT id, title, artist, album, duration_ms, local_path, audio_format 
        FROM tracks 
        WHERE download_status = 'completed' AND local_path IS NOT NULL
        ORDER BY id ASC;
    """).fetchall()
    conn.close()
    
    if limit:
        tracks = tracks[:limit]
        
    total = len(tracks)
    log(f"Found {total} tracks in vault to evaluate.")
    
    matched = 0
    for i, t in enumerate(tracks):
        tid = t["id"]
        path = t["local_path"]
        title = t["title"] or ""
        artist = t["artist"] or ""
        dur_sec = (t["duration_ms"] or 0) / 1000.0
        
        if not path or not os.path.exists(path):
            continue
            
        # 1. Try AcoustID (if key present)
        meta = match_acoustid(path)
        
        # 2. Fallback to MusicBrainz Direct
        if not meta:
            meta = match_musicbrainz_direct(title, artist, target_sec=dur_sec)
            
        if meta:
            matched += 1
            log(f"[{i+1}/{total}] [{meta['source']}] [{tid}] {artist} - {title} => MBID: {meta.get('recording_id')}, Album: '{meta.get('album', '')}', Date: {meta.get('date', 'N/A')}")
            enrich_tags(path, meta, dry_run=dry_run)
        else:
            if (i + 1) % 50 == 0:
                log(f"[{i+1}/{total}] Evaluated {i+1} tracks ({matched} enriched)...")
                
        time.sleep(0.5) # Gentle rate-limit respect
        
    log(f"=== Enricher Finished: {matched}/{total} tracks enriched with MusicBrainz metadata ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    scan_and_fingerprint(limit=args.limit, dry_run=args.dry_run)
