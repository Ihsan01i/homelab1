#!/usr/bin/env python3
"""
video_rip_cleaner.py - Automated Audio Quality Tiering & Video Rip Replacer
Identifies video rips and substandard audio tracks, searches YouTube Music's
official studio catalog (filter='songs'), replaces video audio with clean studio masters,
or safely deletes unreplaceable video rips per library governance.
"""

import os
import sys
import re
import time
import json
import sqlite3
import argparse
import subprocess
from datetime import datetime
from mutagen.flac import FLAC, Picture
from mutagen.oggopus import OggOpus
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC
from ytmusicapi import YTMusic
import yt_dlp

DB_PATH = "/var/lib/navidrome/navidrome.db"
MUSIC_DIR = "/opt/music-brain/music"
LOG_FILE = "/opt/music-brain/video_rip_cleaner.log"

VIDEO_NOISE_PATTERNS = [
    r'\(official\s+(?:music\s+)?video\)',
    r'\[official\s+(?:music\s+)?video\]',
    r'\(music\s+video\)',
    r'\[music\s+video\]',
    r'\(lyric\s+video\)',
    r'\[lyric\s+video\]',
    r'\(official\s+audio\)',
    r'\[official\s+audio\]',
    r'\bvisualizer\b',
    r'\bofficial\s+music\s+video\b',
    r'\bofficial\s+video\b',
    r'\|\s*official\s+music\s+video\b'
]
COMBINED_NOISE_RE = re.compile('|'.join(VIDEO_NOISE_PATTERNS), re.IGNORECASE)

def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def clean_track_title(title: str, artist: str = "") -> str:
    cleaned = COMBINED_NOISE_RE.sub('', title or "").strip()
    cleaned = re.sub(r'\s*\|\s*.*$', '', cleaned).strip()
    if artist:
        pat = re.compile(rf'^{re.escape(artist)}\s*[-:?_]\s*', re.IGNORECASE)
        cleaned = pat.sub('', cleaned).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or title

def classify_tier(suffix: str, bit_rate: int, is_video_rip: bool) -> int:
    s = (suffix or "").lower()
    b = bit_rate or 0
    if s == 'flac':
        return 1
    if is_video_rip:
        return 4
    if (s == 'mp3' and b >= 256) or (s == 'm4a' and b >= 256):
        return 2
    if s == 'opus' and b >= 120:
        return 3
    return 4

def find_flagged_tracks():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, artist, album, path, suffix, bit_rate, duration FROM media_file WHERE missing = 0")
    rows = cursor.fetchall()
    conn.close()

    candidates = []
    for r in rows:
        fid, title, artist, album, rel_path, suffix, bit_rate, duration = r
        is_video = False
        reasons = []

        if COMBINED_NOISE_RE.search(title or ""):
            is_video = True
            reasons.append("title_noise")
        if COMBINED_NOISE_RE.search(rel_path or "") and suffix != 'flac':
            is_video = True
            reasons.append("path_noise")
        if suffix == 'opus' and bit_rate and bit_rate < 100:
            is_video = True
            reasons.append(f"low_bitrate_{bit_rate}k")

        tier = classify_tier(suffix, bit_rate, is_video)
        if tier == 4:
            candidates.append({
                "id": fid,
                "title": title,
                "artist": artist,
                "album": album,
                "path": os.path.join(MUSIC_DIR, rel_path),
                "rel_path": rel_path,
                "suffix": suffix,
                "bit_rate": bit_rate,
                "duration": duration,
                "reasons": reasons
            })
    return candidates

def replace_with_ytmusic_studio(track: dict, dry_run: bool = False) -> bool:
    clean_title = clean_track_title(track["title"], track["artist"])
    query = f"{track['artist']} {clean_title}".strip()
    log(f"Searching YouTube Music official studio track for: '{query}'")

    if dry_run:
        log(f"[DRY-RUN] Would search YTM and replace {track['path']}")
        return True

    ytm = YTMusic()
    try:
        results = ytm.search(query, filter="songs")
    except Exception as e:
        log(f"YTM search error: {e}")
        return False

    if not results:
        log(f"No official studio song results found for: {query}")
        return False

    target_dur = track["duration"] or 0
    best_candidate = None
    for res in results:
        if res.get("resultType") != "song":
            continue
        dur_str = res.get("duration") or ""
        parts = dur_str.split(":")
        cand_sec = 0
        if len(parts) == 2:
            cand_sec = int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            cand_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

        if target_dur > 0 and cand_sec > 0:
            if abs(cand_sec - target_dur) <= 6.0:
                best_candidate = res
                break
        elif not best_candidate:
            best_candidate = res

    if not best_candidate:
        log(f"No candidate matched duration (target: {target_dur}s)")
        return False

    vid = best_candidate.get("videoId")
    if not vid:
        return False

    ytm_url = f"https://music.youtube.com/watch?v={vid}"
    matched_title = best_candidate.get('title')
    matched_artist = best_candidate.get('artists', [{}])[0].get('name') if best_candidate.get('artists') else track['artist']
    matched_album = best_candidate.get('album', {}).get('name') if best_candidate.get('album') else track['album']

    log(f"Matched official studio song: '{matched_title}' by {matched_artist} (ID: {vid})")

    tmp_out = f"/tmp/ytm_{vid}.%(ext)s"
    ydl_opts = {
        'format': 'bestaudio[ext=webm]/bestaudio/best',
        'outtmpl': tmp_out,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'opus',
            'preferredquality': '160',
        }],
        'quiet': True,
        'no_warnings': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([ytm_url])
    except Exception as e:
        log(f"yt-dlp download failed: {e}")
        return False

    downloaded_file = f"/tmp/ytm_{vid}.opus"
    if not os.path.exists(downloaded_file):
        log(f"Expected output file not found: {downloaded_file}")
        return False

    try:
        opus_tags = OggOpus(downloaded_file)
        opus_tags["title"] = [matched_title or clean_title]
        opus_tags["artist"] = [matched_artist or track["artist"]]
        opus_tags["album"] = [matched_album or track["album"] or clean_title]
        opus_tags.save()
    except Exception as e:
        log(f"Failed to tag opus file: {e}")

    old_path = track["path"]
    target_path = old_path
    if not target_path.endswith(".opus"):
        target_path = os.path.splitext(old_path)[0] + ".opus"

    try:
        if os.path.exists(old_path) and old_path != target_path:
            os.remove(old_path)
        os.replace(downloaded_file, target_path)
        log(f"Successfully replaced with clean studio master: {target_path}")
        return True
    except Exception as e:
        log(f"Failed replacing file: {e}")
        return False

def purge_track(track: dict, dry_run: bool = False):
    p = track["path"]
    if dry_run:
        log(f"[DRY-RUN] Would delete substandard track: {p}")
        return
    if os.path.exists(p):
        try:
            os.remove(p)
            log(f"Purged substandard track: {p}")
        except Exception as e:
            log(f"Failed to delete {p}: {e}")

def rescan_navidrome():
    log("Triggering Navidrome library scan and purge of missing files...")
    subprocess.run(["systemctl", "restart", "navidrome"], check=False)
    time.sleep(2)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM media_file WHERE missing = 1")
    conn.commit()
    conn.close()
    log("Navidrome rescan & cleanup complete.")

def main():
    parser = argparse.ArgumentParser(description="Clean video rips and upgrade to studio audio")
    parser.add_argument("--action", choices=["audit", "replace", "purge"], default="audit",
                        help="audit: list tracks; replace: upgrade via YTM studio song; purge: delete Tier 4")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without modifying files")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of tracks to process (0 for unlimited)")
    parser.add_argument("--title", type=str, default=None, help="Process a specific track by title substring")
    args = parser.parse_args()

    log(f"Starting Video Rip & Quality Tiering Cleaner (Action: {args.action}, Dry-Run: {args.dry_run})")
    candidates = find_flagged_tracks()
    if args.title:
        candidates = [c for c in candidates if args.title.lower() in c['title'].lower() or args.title.lower() in c['path'].lower()]
    if args.limit > 0:
        candidates = candidates[:args.limit]

    log(f"Found {len(candidates)} Tier 4 substandard/video tracks to process.")

    if args.action == "audit":
        for c in candidates:
            log(f"FLAGGED: {c['artist']} - {c['title']} ({c['suffix']} {c['bit_rate']}k) -> {c['path']} [Reasons: {','.join(c['reasons'])}]")
        return

    if args.action == "replace":
        for c in candidates:
            success = replace_with_ytmusic_studio(c, dry_run=args.dry_run)
            if not success and not args.dry_run:
                log(f"Replacement failed for '{c['title']}'. Purging per policy...")
                purge_track(c, dry_run=args.dry_run)
        if not args.dry_run:
            rescan_navidrome()

    elif args.action == "purge":
        for c in candidates:
            purge_track(c, dry_run=args.dry_run)
        if not args.dry_run:
            rescan_navidrome()

if __name__ == "__main__":
    main()
