import os
import sys
import time
import sqlite3
import datetime
import base64
from io import BytesIO
from PIL import Image
import musicbrainzngs
from mutagen.flac import FLAC, Picture
from mutagen.oggopus import OggOpus

BASE_DIR = "/opt/music-brain"
DB_PATH = os.path.join(BASE_DIR, "music_brain.db")
LOG_PATH = os.path.join(BASE_DIR, "musicbrainz_enricher.log")

sys.path.insert(0, os.path.join(BASE_DIR, "backend/app"))
from cover_art_helper import clean_artist_names, clean_track_title, fetch_musicbrainz_metadata_and_cover

musicbrainzngs.set_useragent("MusicBrainVault", "1.0", "https://github.com/Ihsan01i/homelab1")
musicbrainzngs.set_rate_limit(1.0, 1)

def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass

def run_enrichment():
    log("=== Starting MusicBrainz & Cover Art Archive Batch Enrichment for Recent History Downloads ===")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    query = """
        SELECT DISTINCT t.id, t.title, t.artist, t.album, t.local_path, t.audio_format
        FROM tracks t
        JOIN discovery_events d ON t.id = d.track_id
        WHERE d.discovery_source = 'ytm_history' AND t.local_path IS NOT NULL
        ORDER BY t.id ASC;
    """
    tracks = cursor.execute(query).fetchall()
    total = len(tracks)
    log(f"Found {total} tracks from YouTube Music history to enrich.")

    stats = {
        "processed": 0,
        "mb_matched": 0,
        "caa_covers": 0,
        "kept_covers": 0,
        "errors": 0
    }

    for idx, (tid, title, artist, album, lpath, fmt) in enumerate(tracks, 1):
        actual_path = lpath
        if not os.path.exists(actual_path):
            flac_alt = os.path.splitext(actual_path)[0] + ".flac"
            if os.path.exists(flac_alt):
                actual_path = flac_alt
            else:
                log(f"[{idx}/{total}] Track ID {tid} missing on disk: {actual_path}")
                stats["errors"] += 1
                continue

        full_art, primary = clean_artist_names(artist)
        clean_title = clean_track_title(title, primary)

        try:
            img_bytes, mb_meta = fetch_musicbrainz_metadata_and_cover(primary, clean_title, album)
        except Exception as e:
            log(f"[{idx}/{total}] MusicBrainz query error on '{primary} - {clean_title}': {e}")
            stats["errors"] += 1
            time.sleep(1.0)
            continue

        mbid = mb_meta.get("mb_track_id")
        caa_found = bool(img_bytes)

        if not mbid and not caa_found:
            log(f"[{idx}/{total}] No MusicBrainz match for: {primary} - {clean_title} (Preserving existing metadata & cover)")
            stats["kept_covers"] += 1
            stats["processed"] += 1
            time.sleep(0.5)
            continue

        stats["mb_matched"] += 1

        # Target metadata
        target_album = mb_meta.get("album") or album or clean_title
        target_date = mb_meta.get("date")
        target_genre = mb_meta.get("genre")

        # Save Cover Art Archive image to disk if available
        dest_dir = os.path.dirname(actual_path)
        cov_path = os.path.join(dest_dir, "cover.jpg")
        if caa_found:
            try:
                with open(cov_path, "wb") as f:
                    f.write(img_bytes)
                stats["caa_covers"] += 1
            except Exception:
                pass
        else:
            stats["kept_covers"] += 1

        # Tag audio file
        try:
            if actual_path.lower().endswith(".flac"):
                audio = FLAC(actual_path)
                audio["title"] = clean_title
                audio["artist"] = full_art
                audio["albumartist"] = primary
                audio["album"] = target_album
                if target_date: audio["date"] = target_date
                if target_genre: audio["genre"] = target_genre
                if mbid: audio["musicbrainz_trackid"] = mbid
                if caa_found:
                    pic = Picture()
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Front Cover"
                    pic.data = img_bytes
                    audio.clear_pictures()
                    audio.add_picture(pic)
                audio.save()
            elif actual_path.lower().endswith(".opus"):
                audio = OggOpus(actual_path)
                audio["title"] = clean_title
                audio["artist"] = full_art
                audio["albumartist"] = primary
                audio["album"] = target_album
                if target_date: audio["date"] = target_date
                if target_genre: audio["genre"] = target_genre
                if mbid: audio["musicbrainz_trackid"] = mbid
                if caa_found:
                    pic = Picture()
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Front Cover"
                    pic.data = img_bytes
                    b64 = base64.b64encode(pic.write()).decode('ascii')
                    audio["metadata_block_picture"] = [b64]
                audio.save()

            # Update DB
            cursor.execute("UPDATE tracks SET album = ? WHERE id = ?;", (target_album, tid))
            conn.commit()

            caa_status = "CAA Front Cover Updated" if caa_found else "Existing Cover Preserved"
            log(f"[{idx}/{total}] ✅ Enriched [{tid}] {primary} - {clean_title} | Album: '{target_album}' ({target_date or 'N/A'}) | {caa_status}")
        except Exception as e:
            log(f"[{idx}/{total}] ⚠️ Error tagging [{tid}] {actual_path}: {e}")
            stats["errors"] += 1

        stats["processed"] += 1
        time.sleep(1.0) # Respect rate limits

    conn.close()
    log("=== Enrichment Complete ===")
    log(f"Summary: Processed: {stats['processed']}/{total} | MB Matches: {stats['mb_matched']} | CAA Covers: {stats['caa_covers']} | Kept Covers: {stats['kept_covers']} | Errors: {stats['errors']}")

if __name__ == "__main__":
    run_enrichment()
