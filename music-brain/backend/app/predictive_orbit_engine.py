"""
Music Brain: Predictive Discovery & Lossless Orbit Engine (Pillar 3)
Mines YouTube Music InnerTube recommendation graph using surging velocity seeds,
deduplicates against the vault, and probes Soulseek for lossless FLAC copies.
"""

import os
import re
import json
import time
import sqlite3
import unicodedata
import urllib.request
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from ytmusicapi import YTMusic

DB_PATH = "/opt/music-brain/music_brain.db"
SLSKD_API_URL = "http://localhost:5030/api/v0"


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_orbit_table(db: sqlite3.Connection):
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS predicted_orbit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT UNIQUE,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT,
                duration_sec INTEGER DEFAULT 0,
                thumb_url TEXT,
                seed_track_id INTEGER,
                seed_track_title TEXT,
                vibe_label TEXT,
                flac_available INTEGER DEFAULT 0,
                flac_peer_info TEXT,
                status TEXT DEFAULT 'recommended',
                created_at TEXT,
                FOREIGN KEY (seed_track_id) REFERENCES tracks(id) ON DELETE SET NULL
            );
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_orbit_status ON predicted_orbit(status);")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-zA-Z0-9\s]', ' ', norm).lower().strip()


def slskd_req(endpoint: str, method: str = "GET", data=None):
    url = f"{SLSKD_API_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except Exception:
        return None


def probe_soulseek_flac(artist: str, title: str, duration_sec: int) -> Optional[Dict[str, Any]]:
    """Probes Soulseek slskd to check if a duration-matched lossless FLAC is available."""
    q_art = normalize_text(artist)
    q_title = normalize_text(title)
    if not q_art or not q_title:
        return None

    # Clean query
    query = f"{q_art} {q_title}".strip()
    start = slskd_req("/searches", method="POST", data={"searchText": query})
    if not start or "id" not in start:
        return None
    sid = start["id"]

    # Quick wait (up to 8 seconds for fast peer responses)
    t0 = time.time()
    while time.time() - t0 < 8:
        time.sleep(1.5)
        s_info = slskd_req(f"/searches/{sid}") or {}
        if s_info.get("isComplete") or "Completed" in s_info.get("state", ""):
            break

    resps = slskd_req(f"/searches/{sid}/responses") or []
    if not resps:
        return None

    title_words = [w for w in q_title.split() if len(w) > 2]

    for peer in resps:
        u = peer.get("username")
        for f in peer.get("files", []):
            fn = f.get("filename", "")
            fn_norm = normalize_text(fn)
            if not fn_norm.endswith("flac") or f.get("isLocked", False):
                continue

            # Must contain main title words
            if title_words and not any(w in fn_norm for w in title_words):
                continue

            # Strict duration match if duration provided
            if duration_sec > 0:
                p_dur = int(f.get("length", 0))
                if p_dur > 0 and abs(p_dur - duration_sec) > 3.0:
                    continue

            return {
                "username": u,
                "filename": fn,
                "size_mb": round(f.get("size", 0) / (1024 * 1024), 1),
                "duration_sec": f.get("length", 0)
            }

    return None


def generate_predictive_orbit(probe_slsk: bool = True) -> List[Dict[str, Any]]:
    """
    Selects top velocity seeds and discovers ~20 fresh recommendation nodes
    from YouTube Music InnerTube, deduplicating strictly against the vault.
    """
    db = get_db()
    init_orbit_table(db)
    cur = db.cursor()

    # Step 1: Select top 3 surging seeds + 1 top short-term obsession seed
    surging_rows = cur.execute("""
        SELECT t.id, t.title, t.artist, t.source_id, a.affinity_velocity
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE a.affinity_velocity > 0 AND t.source_id IS NOT NULL AND t.source_id != ''
        ORDER BY a.affinity_velocity DESC
        LIMIT 3;
    """).fetchall()

    obsession_rows = cur.execute("""
        SELECT t.id, t.title, t.artist, t.source_id, a.affinity_score_short
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE t.source_id IS NOT NULL AND t.source_id != ''
        ORDER BY a.affinity_score_short DESC
        LIMIT 1;
    """).fetchall()

    seed_tracks = []
    seen_seeds = set()
    for r in list(surging_rows) + list(obsession_rows):
        if r["source_id"] not in seen_seeds:
            seen_seeds.add(r["source_id"])
            seed_tracks.append({
                "id": r["id"],
                "title": r["title"],
                "artist": r["artist"],
                "source_id": r["source_id"]
            })

    if not seed_tracks:
        print("[PredictiveOrbit] No active seeds with source_id found.")
        return []

    # Step 2: Build existing vault lookup set (by source_id and normalized title+artist)
    existing_source_ids = set()
    existing_titles = set()
    for r in cur.execute("SELECT source_id, title, artist FROM tracks").fetchall():
        if r["source_id"]:
            existing_source_ids.add(r["source_id"])
        norm_key = f"{normalize_text(r['artist'])}::{normalize_text(r['title'])}"
        existing_titles.add(norm_key)

    # Step 3: Query YouTube Music InnerTube watch playlists
    yt = YTMusic()
    candidates: List[Dict[str, Any]] = []
    seen_cand_ids = set()

    for seed in seed_tracks:
        vid = seed["source_id"]
        try:
            print(f"[PredictiveOrbit] Mining seed: '{seed['title']}' by {seed['artist']} ({vid})...")
            watch = yt.get_watch_playlist(videoId=vid, limit=20)
            trks = watch.get("tracks", [])

            for t in trks:
                cand_vid = t.get("videoId")
                cand_title = t.get("title")
                cand_artists = [a.get("name") for a in t.get("artists", []) if a.get("name")]
                cand_artist = ", ".join(cand_artists) if cand_artists else "Unknown Artist"
                cand_album = t.get("album", {}).get("name") if isinstance(t.get("album"), dict) else None
                cand_dur = t.get("length") or t.get("duration_seconds") or 0

                # Convert duration text '3:45' to seconds if needed
                if isinstance(cand_dur, str) and ":" in cand_dur:
                    parts = cand_dur.split(":")
                    cand_dur = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0

                if not cand_vid or cand_vid in seen_cand_ids:
                    continue
                if cand_vid in existing_source_ids:
                    continue

                # Fuzzy title+artist deduplication
                norm_key = f"{normalize_text(cand_artist)}::{normalize_text(cand_title)}"
                if norm_key in existing_titles:
                    continue

                # Authentic studio cover URL
                thumbs = t.get("thumbnail", [])
                thumb_url = thumbs[-1].get("url") if thumbs else None
                if thumb_url and "=w" in thumb_url:
                    thumb_url = re.sub(r'=w\d+-h\d+.*$', '=w500-h500-l90-rj', thumb_url)

                candidates.append({
                    "source_id": cand_vid,
                    "title": cand_title,
                    "artist": cand_artist,
                    "album": cand_album,
                    "duration_sec": int(cand_dur),
                    "thumb_url": thumb_url,
                    "seed_track_id": seed["id"],
                    "seed_track_title": seed["title"],
                    "vibe_label": f"Inspired by {seed['artist']}"
                })
                seen_cand_ids.add(cand_vid)

                if len(candidates) >= 20:
                    break
        except Exception as e:
            print(f"[PredictiveOrbit] InnerTube error for seed {vid}: {e}")

        if len(candidates) >= 20:
            break

    # Step 4: Proactive Soulseek probe for top 5 candidates
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    if probe_slsk:
        print(f"[PredictiveOrbit] Probing Soulseek for lossless FLAC availability on top {min(5, len(candidates))} candidates...")
        for c in candidates[:5]:
            match = probe_soulseek_flac(c["artist"], c["title"], c["duration_sec"])
            if match:
                c["flac_available"] = 1
                c["flac_peer_info"] = json.dumps(match)
                print(f"  ⚡ Found Lossless FLAC: {c['title']} by {c['artist']} from peer {match['username']} ({match['size_mb']} MB)")
            else:
                c["flac_available"] = 0
                c["flac_peer_info"] = None

    # Step 5: Save into predicted_orbit table
    with db:
        db.execute("DELETE FROM predicted_orbit WHERE status = 'recommended';")
        for c in candidates:
            db.execute("""
                INSERT INTO predicted_orbit (
                    source_id, title, artist, album, duration_sec, thumb_url,
                    seed_track_id, seed_track_title, vibe_label, flac_available,
                    flac_peer_info, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'recommended', ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    flac_available = excluded.flac_available,
                    flac_peer_info = excluded.flac_peer_info,
                    created_at = excluded.created_at
            """, (
                c["source_id"], c["title"], c["artist"], c.get("album"),
                c.get("duration_sec", 0), c.get("thumb_url"),
                c.get("seed_track_id"), c.get("seed_track_title"), c.get("vibe_label"),
                c.get("flac_available", 0), c.get("flac_peer_info"), now_iso
            ))

    db.close()
    print(f"[PredictiveOrbit] Successfully generated and stored {len(candidates)} predicted candidates.")
    return candidates


if __name__ == "__main__":
    print("Executing Predictive Orbit Generation (Pillar 3)...")
    res = generate_predictive_orbit(probe_slsk=True)
    print(f"Generated {len(res)} candidates:")
    for i, c in enumerate(res[:8], 1):
        flac_tag = " [⚡ FLAC AVAILABLE]" if c.get("flac_available") else ""
        print(f"  {i}. {c['title']} by {c['artist']}{flac_tag} ({c['vibe_label']})")
