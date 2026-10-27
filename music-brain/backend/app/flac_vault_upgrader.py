import os
import sys
import re
import time
import json
import sqlite3
import argparse
import subprocess
import urllib.request
import urllib.parse
import shutil
import base64
import unicodedata
from datetime import datetime
from mutagen.flac import FLAC, Picture
from mutagen.oggopus import OggOpus

DB_PATH = "/opt/music-brain/music_brain.db"
MUSIC_DIR = "/opt/music-brain/music"
SLSKD_API_URL = "http://localhost:5030/api/v0"
SLSKD_DOWNLOADS_DIR = "/opt/music-brain/slskd/downloads"
LOG_FILE = "/opt/music-brain/flac_upgrader.log"

def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def normalize_text(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name or "Unknown")
    cleaned = re.sub(r'\s+', " ", cleaned).strip()
    return cleaned[:80] if len(cleaned) > 80 else (cleaned or "Unknown")

def get_primary_artist(artist_str: str) -> str:
    if not artist_str:
        return "Unknown Artist"
    if artist_str.strip().lower() == "again&again":
        return "again&again"
    cleaned = re.sub(r'\s*-\s*Topic\b', '', artist_str, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bTopic\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*VEVO\b', '', cleaned, flags=re.IGNORECASE).strip()
    parts = re.split(r'[,]| feat\.? | ft\.? ', cleaned, flags=re.IGNORECASE)
    return parts[0].strip() or "Unknown Artist"

def extract_music_name(raw_title: str, raw_artist: str) -> str:
    t = (raw_title or "").strip()
    a = (raw_artist or "").strip()
    
    # 1. Strip repeated artist prefix (e.g. "Artist - Title", "Artist: Title")
    parts = [p.strip() for p in re.split(r'[,&]| feat\.? | ft\.? ', a, flags=re.IGNORECASE) if p.strip()]
    for art in [a] + parts:
        if not art:
            continue
        pat = re.compile(rf'^{re.escape(art)}\s*[-:?_]\s*', re.IGNORECASE)
        while pat.search(t):
            t = pat.sub('', t).strip()
            
    # 2. Strip bracketed / parenthetical video & audio noise
    bracket_noise = [
        r'\s*[\(\[](?:official\s+(?:music\s+)?video|official\s+audio|lyric\s+video|lyrics|audio|visualizer|mv|hd|4k|color\s+coded|romaji|sub\s+espanol|extended\s+version|video)[\)\]]',
        r'\s*[\(\[](?:from|soundtrack|ost)\s+.*?[\)\]]',
        r'\s*[\(\[](?:feat\.?|ft\.?)\s+.*?[\)\]]'
    ]
    for bn in bracket_noise:
        t = re.sub(bn, '', t, flags=re.IGNORECASE).strip()
    
    # 3. Strip trailing noise words or separator suffixes
    t = re.sub(r'\s*-\s*(?:official\s+(?:music\s+)?video|official\s+audio|lyrics|audio|mv)\b.*$', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'\s*\|\s*.*$', '', t).strip()
    t = re.sub(r'\b(?:official\s+music\s+video|official\s+video|official\s+audio|lyric\s+video)\b', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'["\']', '', t).strip()
    return t or raw_title

def slskd_req(endpoint: str, method: str = "GET", data=None):
    url = f"{SLSKD_API_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except Exception:
        return None

def extract_authentic_cover(old_opus_path: str, album_dir: str):
    if old_opus_path and os.path.exists(old_opus_path):
        try:
            op = OggOpus(old_opus_path)
            pics = op.get("metadata_block_picture", [])
            if pics:
                pic = Picture(base64.b64decode(pics[0]))
                if pic.data and len(pic.data) > 1000:
                    return pic.data
        except Exception:
            pass

    cover_path = os.path.join(album_dir, "cover.jpg")
    if os.path.exists(cover_path):
        try:
            with open(cover_path, "rb") as f:
                data = f.read()
                if len(data) > 1000:
                    return data
        except Exception:
            pass
    return None

def search_strict_flac_candidates(primary_artist: str, music_name: str, target_sec: float):
    norm_artist = normalize_text(primary_artist).lower()
    norm_music = normalize_text(music_name).lower()
    
    q_art = re.sub(r'[^a-zA-Z0-9\s]', ' ', norm_artist).strip()
    q_music = re.sub(r'[^a-zA-Z0-9\s]', ' ', norm_music).strip()
    query = f"{q_art} {q_music}".strip()
    
    a_words = list(dict.fromkeys([w for w in q_art.split() if len(w) > 2])) or q_art.split()
    stop_words = {"the", "and", "feat", "with", "from", "for", "soundtrack", "version"}
    m_words = list(dict.fromkeys([w for w in q_music.split() if len(w) > 1 and w not in stop_words])) or q_music.split()
    
    start = slskd_req("/searches", method="POST", data={"searchText": query})
    if not start or "id" not in start:
        return []
    sid = start["id"]

    # Wait until slskd finalizes distributed search (isComplete == True or Completed state, up to 32s)
    t0 = time.time()
    while time.time() - t0 < 32:
        time.sleep(2)
        s_info = slskd_req(f"/searches/{sid}") or {}
        if s_info.get("isComplete") or "Completed" in s_info.get("state", ""):
            break

    resps = slskd_req(f"/searches/{sid}/responses") or []
    if not resps:
        return []

    cands = []
    for peer in resps:
        u = peer.get("username")
        has_slot = peer.get("hasFreeUploadSlot", False)
        speed = peer.get("uploadSpeed", 0)
        for f in peer.get("files", []):
            fn = f.get("filename", "")
            fn_norm = normalize_text(fn).lower()
            
            if not fn_norm.endswith(".flac") or f.get("isLocked", False):
                continue
                
            # 1. MUSIC NAME MATCH: Peer filename must contain the clean music name words
            if m_words and not all(w in fn_norm for w in m_words):
                continue
                
            # 2. ARTIST MATCH: Peer path must contain all primary artist words
            if a_words and not all(w in fn_norm for w in a_words):
                continue
                
            # 3. DURATION MATCH: Exact match within ±3.0 seconds
            f_length = f.get("length", 0)
            if f_length <= 0 or abs(f_length - target_sec) > 3.0:
                continue

            cands.append({
                "username": u,
                "filename": fn,
                "size": f.get("size", 0),
                "has_slot": has_slot,
                "speed": speed,
                "length": f_length
            })

    if cands:
        cands.sort(key=lambda x: (1 if x["has_slot"] else 0, x["speed"]), reverse=True)
        seen = set()
        distinct = []
        for c in cands:
            if c["username"] not in seen:
                seen.add(c["username"])
                distinct.append(c)
        log(f"Found {len(distinct)} strict FLAC matches for '{query}' (Artist + Music Name + Duration {target_sec:.1f}s ±3.0s)")
        return distinct
    return []

def download_and_verify_flac(candidates: list, primary_artist: str, album: str, music_name: str, target_sec: float):
    dest_dir = os.path.join(MUSIC_DIR, sanitize_filename(primary_artist), sanitize_filename(album))
    os.makedirs(dest_dir, exist_ok=True)
    dest_flac = os.path.join(dest_dir, f"{sanitize_filename(primary_artist)} - {sanitize_filename(music_name)}.flac")
    
    for i, cand in enumerate(candidates[:5]):
        u = cand["username"]
        fn = cand["filename"]
        sz = cand["size"]
        basename = fn.split("\\")[-1].split("/")[-1]
        log(f"Attempt {i+1}/{min(5, len(candidates))}: Downloading from '{u}' ({sz/(1024*1024):.1f} MB, slot={cand['has_slot']}, peer_len={cand['length']}s)...")
        
        enq = slskd_req(f"/transfers/downloads/{u}", method="POST", data=[{"filename": fn, "size": sz}])
        if not enq or not enq.get("enqueued"):
            continue

        t0 = time.time()
        succeeded = False
        while time.time() - t0 < 60:
            time.sleep(2)
            elapsed = time.time() - t0
            dls = slskd_req("/transfers/downloads") or []
            st = ""
            bytes_tf = 0
            for pu in dls:
                if pu.get("username") == u:
                    for d in pu.get("directories", []):
                        for f in d.get("files", []):
                            if f.get("filename") == fn:
                                st = f.get("state", "")
                                bytes_tf = f.get("bytesTransferred", 0)

            if "Succeeded" in st or ("Completed" in st and "Failed" not in st and "Rejected" not in st):
                succeeded = True
                break
            if any(bad in st for bad in ["Rejected", "Failed", "Cancelled", "Errored"]):
                log(f"Peer '{u}' aborted: {st}. Trying next candidate...")
                break
            if elapsed > 15 and bytes_tf == 0:
                log(f"Peer '{u}' stalled (0 bytes in 15s). Trying next candidate...")
                break

        if succeeded:
            log(f"Transfer finished from '{u}'!")
            for root, _, files in os.walk(SLSKD_DOWNLOADS_DIR):
                for file in files:
                    if file == basename:
                        src_path = os.path.join(root, file)
                        shutil.move(src_path, dest_flac)
                        
                        try:
                            probe = FLAC(dest_flac)
                            actual_dur = probe.info.length
                            if abs(actual_dur - target_sec) > 3.0:
                                log(f"❌ PROBE REJECTED: Decoded FLAC duration {actual_dur:.1f}s != target {target_sec:.1f}s (±3s). Deleting!")
                                os.remove(dest_flac)
                                break
                                
                            log(f"✅ 100% VERIFIED AUTHENTIC FLAC: {actual_dur:.1f}s (target: {target_sec:.1f}s, {probe.info.bits_per_sample}bit/{probe.info.sample_rate}Hz)")
                            return dest_flac
                        except Exception as e:
                            log(f"Probe exception on {dest_flac}: {e}")
                            if os.path.exists(dest_flac):
                                os.remove(dest_flac)
                            break
    return None

def upgrade_track(track: dict):
    tid = track["id"]
    raw_title = track["title"] or "Unknown"
    artist = track["artist"] or "Unknown Artist"
    album = track["album"] or "Singles & EP"
    old_opus = track["local_path"]
    
    primary = get_primary_artist(artist)
    music_name = extract_music_name(raw_title, primary)
    
    safe_prim = sanitize_filename(primary)
    safe_album = sanitize_filename(album)
    dest_dir = os.path.join(MUSIC_DIR, safe_prim, safe_album)
    
    if not old_opus or not os.path.exists(old_opus):
        log(f"[{tid}] Authentic Opus not found on disk. Skipping.")
        return False
        
    try:
        opus_audio = OggOpus(old_opus)
        target_sec = opus_audio.info.length
    except Exception as e:
        log(f"[{tid}] Error reading Opus duration: {e}")
        return False

    log(f"--- [Upgrader] Evaluating [{tid}] {primary} - {music_name} (Raw: '{raw_title}', Target: {target_sec:.1f}s) ---")
    
    try:
        cands = search_strict_flac_candidates(primary, music_name, target_sec)
    except Exception as e:
        log(f"Search exception on [{tid}]: {e}")
        return False
        
    if not cands:
        log(f"No strict FLAC match found on Soulseek. Safely keeping Opus: {music_name}")
        return False
        
    flac_path = download_and_verify_flac(cands, primary, album, music_name, target_sec)
    if flac_path and os.path.exists(flac_path):
        # ALWAYS RETAIN AUTHENTIC COVER ART DIRECTLY FROM THE OPUS FILE
        img_data = extract_authentic_cover(old_opus, dest_dir)
        try:
            fl = FLAC(flac_path)
            fl["TITLE"] = music_name
            fl["ARTIST"] = artist
            fl["ALBUMARTIST"] = primary
            fl["ALBUM"] = album
            if img_data:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.desc = "Front Cover"
                pic.data = img_data
                fl.clear_pictures()
                fl.add_picture(pic)
            fl.save()
        except Exception as e:
            log(f"Tag error: {e}")

        # DELETE OLD OPUS ONLY AFTER FLAC IS 100% IN PLACE
        if os.path.exists(old_opus) and old_opus != flac_path:
            try:
                os.remove(old_opus)
                log(f"Removed superseded Opus: {old_opus}")
            except Exception:
                pass
                
        sz = os.path.getsize(flac_path)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            UPDATE tracks 
            SET audio_format = 'FLAC',
                is_lossless = 1,
                local_path = ?,
                file_size_bytes = ?,
                downloaded_at = CURRENT_TIMESTAMP
            WHERE id = ?;
        """, (flac_path, sz, tid))
        conn.commit()
        conn.close()
        log(f"🎉 UPGRADED TO LOSSLESS FLAC: [{tid}] {primary} - {music_name} ({sz/(1024*1024):.1f} MB)")
        return True
    return False

def run_upgrader(limit: int = None, track_id: int = None, artist_filter: str = None):
    log(f"=== Strict FLAC Upgrader Engine Started (Limit: {limit}, TrackID: {track_id}, Artist: {artist_filter}) ===")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    query = """
        SELECT id, title, artist, album, duration_ms, source_id, local_path
        FROM tracks
        WHERE download_status = 'completed' AND audio_format = 'OPUS'
    """
    params = []
    if track_id:
        query += " AND id = ?"
        params.append(track_id)
    if artist_filter:
        query += " AND artist LIKE ?"
        params.append(f"%{artist_filter}%")
    query += " ORDER BY id ASC;"
    
    tracks = conn.execute(query, params).fetchall()
    conn.close()
    
    if limit:
        tracks = tracks[:limit]
        
    total_tracks = len(tracks)
    log(f"Found {total_tracks} verified Opus tracks eligible for strict lossless FLAC upgrade.")
    upgraded = 0
    retained_opus = 0
    
    for i, t in enumerate(tracks):
        log(f"[{i+1}/{total_tracks}] Processing track ID {t['id']}...")
        success = False
        try:
            success = upgrade_track(dict(t))
        except Exception as e:
            log(f"Unexpected error processing track {t['id']}: {e}")
            
        if success:
            upgraded += 1
        else:
            retained_opus += 1
            
        if (i + 1) % 10 == 0 or (i + 1) == total_tracks:
            pct = ((i + 1) / total_tracks) * 100
            log(f"📊 [Progress: {i+1}/{total_tracks} ({pct:.1f}%)] Upgraded: {upgraded} | Retained Opus: {retained_opus}")
            
        time.sleep(2)
            
    log(f"=== Strict FLAC Upgrade Engine Complete: {upgraded} tracks upgraded, {retained_opus} kept as Opus! ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--id", type=int, default=None)
    parser.add_argument("--artist", type=str, default=None)
    args = parser.parse_args()
    run_upgrader(limit=args.limit, track_id=args.id, artist_filter=args.artist)
