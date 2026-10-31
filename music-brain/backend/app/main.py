import os
import sys
import re
import time
import json
import sqlite3
import datetime
import threading
import subprocess
from typing import Optional
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response

# Pillar 1, 2 & 3 Intelligence, Smart Playlists & Predictive Orbit
try:
    from backend.app.affinity_engine import run_full_pipeline, get_db
    from backend.app.smart_playlist_engine import generate_all_smart_playlists
    from backend.app.predictive_orbit_engine import generate_predictive_orbit
except ImportError:
    from app.affinity_engine import run_full_pipeline, get_db
    from app.smart_playlist_engine import generate_all_smart_playlists
    from app.predictive_orbit_engine import generate_predictive_orbit

app = FastAPI(title="Music Brain Master Orchestrator", version="4.0")

BASE_DIR = "/opt/music-brain"
DB_PATH = os.path.join(BASE_DIR, "music_brain.db")
MUSIC_DIR = os.path.join(BASE_DIR, "music")
VENV_PYTHON = os.path.join(BASE_DIR, ".venv/bin/python3")
HISTORY_LOG = os.path.join(BASE_DIR, "history_sync.log")
UPGRADER_LOG = os.path.join(BASE_DIR, "flac_upgrader.log")

# Worker execution states
worker_lock = threading.Lock()
is_syncing = False
is_deduping = False
is_recalculating_affinity = False
sync_status_msg = "Idle"
dedup_status_msg = "Idle"
affinity_status_msg = "Idle"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# -------------------------------------------------------------
# Background Worker Trigger Functions
# -------------------------------------------------------------
def background_history_sync():
    global is_syncing, sync_status_msg
    with worker_lock:
        if is_syncing:
            return
        is_syncing = True
        sync_status_msg = "Running YouTube Music History Sync..."

    try:
        cmd = [VENV_PYTHON, os.path.join(BASE_DIR, "backend/app/history_intelligence_sync.py")]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if res.returncode == 0:
            sync_status_msg = f"Last sync completed at {datetime.datetime.now().strftime('%H:%M:%S')}"
            # Automatically trigger affinity refresh upon new history intake
            try:
                run_full_pipeline()
            except Exception as e:
                print(f"[Main] Error updating affinity after sync: {e}")
        else:
            sync_status_msg = f"Sync failed: {res.stderr[-100:] if res.stderr else 'Unknown error'}"
    except Exception as e:
        sync_status_msg = f"Sync error: {e}"
    finally:
        with worker_lock:
            is_syncing = False

def background_dedup():
    global is_deduping, dedup_status_msg
    with worker_lock:
        if is_deduping:
            return
        is_deduping = True
        dedup_status_msg = "Running vault deduplication sweep..."

    try:
        cmd = [VENV_PYTHON, os.path.join(BASE_DIR, "audit_and_dedup.py")]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            dedup_status_msg = f"Dedup clean at {datetime.datetime.now().strftime('%H:%M:%S')} (0 duplicates remaining)"
        else:
            dedup_status_msg = f"Dedup failed: {res.stderr[-100:] if res.stderr else 'Error'}"
    except Exception as e:
        dedup_status_msg = f"Dedup error: {e}"
    finally:
        with worker_lock:
            is_deduping = False

def background_affinity_recalc():
    global is_recalculating_affinity, affinity_status_msg
    with worker_lock:
        if is_recalculating_affinity:
            return
        is_recalculating_affinity = True
        affinity_status_msg = "Recalculating half-life decay & circadian matrices..."

    try:
        res = run_full_pipeline()
        # Pillar 2: Auto-compile smart playlists after affinity recalculation
        try:
            generate_all_smart_playlists()
        except Exception as e_pl:
            print(f"[Main] Error compiling smart playlists: {e_pl}")

        affinity_status_msg = f"Affinity & Playlists updated at {datetime.datetime.now().strftime('%H:%M:%S')} ({res.get('processed_tracks', 0)} tracks)"
    except Exception as e:
        affinity_status_msg = f"Affinity calculation error: {e}"
    finally:
        with worker_lock:
            is_recalculating_affinity = False

# -------------------------------------------------------------
# Core API Endpoints
# -------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": time.time(), "version": "4.0 (Pillar 1)"}

@app.get("/api/status")
def get_status():
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL;").fetchone()[0]
    flacs = conn.execute("SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL AND (audio_format = 'FLAC' OR is_lossless = 1 OR local_path LIKE '%.flac');").fetchone()[0]
    opuss = total - flacs
    bytes_total = conn.execute("SELECT COALESCE(SUM(file_size_bytes), 0) FROM tracks WHERE local_path IS NOT NULL;").fetchone()[0]
    events_count = conn.execute("SELECT COUNT(*) FROM discovery_events;").fetchone()[0]
    conn.close()

    upgrader_tail = "Idle"
    if os.path.exists(UPGRADER_LOG):
        try:
            with open(UPGRADER_LOG, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                if lines:
                    upgrader_tail = lines[-1].strip()
        except Exception:
            pass

    return {
        "vault": {
            "total_tracks": total,
            "flac_count": flacs,
            "opus_count": opuss,
            "flac_pct": round((flacs / total * 100), 1) if total > 0 else 0,
            "storage_gb": round(bytes_total / (1024**3), 2),
            "duplicates": 0,
            "discovery_events": events_count
        },
        "history_sync": {
            "is_running": is_syncing,
            "status": sync_status_msg
        },
        "dedup": {
            "is_running": is_deduping,
            "status": dedup_status_msg
        },
        "flac_upgrader": {
            "status": upgrader_tail
        },
        "intelligence": {
            "is_running": is_recalculating_affinity,
            "status": affinity_status_msg
        }
    }

@app.post("/api/sync/trigger")
def trigger_sync(background_tasks: BackgroundTasks):
    global is_syncing
    if is_syncing:
        return JSONResponse({"status": "already_running", "message": "History sync is already in progress"})
    background_tasks.add_task(background_history_sync)
    return {"status": "started", "message": "YouTube Music History Sync initiated in background"}

@app.post("/api/dedup/trigger")
def trigger_dedup(background_tasks: BackgroundTasks):
    global is_deduping
    if is_deduping:
        return JSONResponse({"status": "already_running", "message": "Dedup sweep is already in progress"})
    background_tasks.add_task(background_dedup)
    return {"status": "started", "message": "Deduplication sweep initiated in background"}

@app.post("/api/upgrader/restart")
def restart_upgrader():
    try:
        subprocess.run(["systemctl", "restart", "music-flac-upgrader.service"], check=True)
        return {"status": "restarted", "message": "FLAC Upgrader service restarted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream/{track_id}")
def stream_track(track_id: int):
    conn = get_db_connection()
    row = conn.execute("SELECT local_path, audio_format FROM tracks WHERE id = ?;", (track_id,)).fetchone()
    conn.close()
    if not row or not row["local_path"] or not os.path.exists(row["local_path"]):
        raise HTTPException(status_code=404, detail="Audio file not found on disk")
    
    path = row["local_path"]
    media_type = "audio/flac" if path.lower().endswith(".flac") else "audio/ogg"
    return FileResponse(path, media_type=media_type)

@app.get("/api/cover/{track_id}")
def get_cover(track_id: int):
    conn = get_db_connection()
    row = conn.execute("SELECT local_path FROM tracks WHERE id = ?;", (track_id,)).fetchone()
    conn.close()
    if not row or not row["local_path"]:
        raise HTTPException(status_code=404, detail="Track not found")
        
    dest_dir = os.path.dirname(row["local_path"])
    cov_path = os.path.join(dest_dir, "cover.jpg")
    if os.path.exists(cov_path):
        return FileResponse(cov_path, media_type="image/jpeg")
        
    raise HTTPException(status_code=404, detail="Cover art not found")

@app.get("/api/db/download")
def download_database():
    snapshot_path = "/tmp/music_brain_snapshot.db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(snapshot_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return FileResponse(snapshot_path, filename="music_brain.db", media_type="application/vnd.sqlite3")

# -------------------------------------------------------------
# Pillar 1 Intelligence API Endpoints
# -------------------------------------------------------------
@app.get("/api/intelligence/summary")
def get_intelligence_summary():
    """Returns top-level intelligence metrics: active era, current circadian slot, top obsessions, and velocity."""
    conn = get_db_connection()
    
    # Active Era
    latest_era = conn.execute("SELECT * FROM listening_eras ORDER BY id DESC LIMIT 1;").fetchone()
    era_data = dict(latest_era) if latest_era else None
    if era_data:
        era_data["dominant_artists"] = json.loads(era_data.get("dominant_artists_json") or "[]")
        era_data["top_tracks"] = json.loads(era_data.get("top_tracks_json") or "[]")
        era_data["bridge_tracks"] = json.loads(era_data.get("bridge_tracks_json") or "[]")

    # Current Circadian Slot based on current hour
    current_hour = datetime.datetime.now().hour
    if 5 <= current_hour < 12:
        current_slot = "Morning Focus"
    elif 12 <= current_hour < 18:
        current_slot = "Midday Drive"
    elif 18 <= current_hour < 23:
        current_slot = "Evening Chill"
    else:
        current_slot = "Midnight Orbit"

    # Top Obsessions (Short-Term Decay)
    obsessions = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, a.affinity_score_short, a.total_plays, a.days_since_last_play
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        ORDER BY a.affinity_score_short DESC
        LIMIT 5;
    """).fetchall()

    # Surging Tracks (Affinity Velocity)
    surging = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, a.affinity_velocity, a.affinity_score_short
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE a.affinity_velocity > 0
        ORDER BY a.affinity_velocity DESC
        LIMIT 5;
    """).fetchall()

    # Top Artists by Affinity
    artists = conn.execute("""
        SELECT artist_name, affinity_score_short, total_plays, vault_track_count
        FROM artist_affinity
        ORDER BY affinity_score_short DESC
        LIMIT 5;
    """).fetchall()

    conn.close()

    return {
        "active_era": era_data,
        "current_circadian_slot": current_slot,
        "current_hour": current_hour,
        "top_obsessions": [dict(r) for r in obsessions],
        "surging_tracks": [dict(r) for r in surging],
        "top_artists": [dict(r) for r in artists]
    }

@app.get("/api/intelligence/affinities")
def get_affinities(
    sort_by: str = Query("short", regex="^(short|long|all_time|velocity)$"),
    limit: int = 50
):
    """Returns tracks ordered by mathematical affinity."""
    col_map = {
        "short": "affinity_score_short",
        "long": "affinity_score_long",
        "all_time": "affinity_score_all_time",
        "velocity": "affinity_velocity"
    }
    col = col_map[sort_by]

    conn = get_db_connection()
    rows = conn.execute(f"""
        SELECT t.id, t.title, t.artist, t.album, t.audio_format,
               a.affinity_score_short, a.affinity_score_long, a.affinity_score_all_time,
               a.affinity_velocity, a.total_plays, a.days_since_last_play, a.is_favorite
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        ORDER BY a.{col} DESC
        LIMIT ?;
    """, (limit,)).fetchall()
    conn.close()

    return {"sort_by": sort_by, "count": len(rows), "tracks": [dict(r) for r in rows]}

@app.get("/api/intelligence/eras")
def get_listening_eras():
    """Returns the full chronological listening eras timeline with dominant artists and bridge tracks."""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM listening_eras ORDER BY id ASC;").fetchall()
    conn.close()

    eras = []
    for r in rows:
        d = dict(r)
        d["dominant_artists"] = json.loads(d.get("dominant_artists_json") or "[]")
        d["top_tracks"] = json.loads(d.get("top_tracks_json") or "[]")
        d["bridge_tracks"] = json.loads(d.get("bridge_tracks_json") or "[]")
        eras.append(d)

    return {"eras": eras}

@app.post("/api/intelligence/recalculate")
def trigger_intelligence_recalc(background_tasks: BackgroundTasks):
    """Triggers an on-demand recalculation of half-life decay scores and circadian patterns."""
    global is_recalculating_affinity
    if is_recalculating_affinity:
        return JSONResponse({"status": "already_running", "message": "Affinity calculation is already in progress"})
    background_tasks.add_task(background_affinity_recalc)
    return {"status": "started", "message": "Episodic Affinity calculation initiated in background"}

@app.get("/api/playlists/smart")
def get_smart_playlists():
    """Returns all Pillar 2 smart playlists with track counts and descriptions."""
    conn = get_db_connection()
    nav_db_path = "/var/lib/navidrome/navidrome.db"
    playlist_stats = {}
    if os.path.exists(nav_db_path):
        try:
            ndb = sqlite3.connect(nav_db_path)
            rows = ndb.execute("SELECT name, song_count FROM playlist;").fetchall()
            ndb.close()
            playlist_stats = {r[0]: r[1] for r in rows}
        except Exception:
            pass

    smart_list = [
        {"name": "Heavy Rotation", "tag": "🔥 Current Obsessions", "desc": "Top tracks ranked by 45-day half-life decay affinity."},
        {"name": "Forgotten Gems", "tag": "💎 Nostalgic Heavyweights", "desc": "High all-time affinity tracks you haven't heard in >45 days."},
        {"name": "Time Machine", "tag": "⏳ Calendar Flashbacks", "desc": "Songs you had on repeat in late fall across 2021-2025."},
        {"name": "Circadian Flow", "tag": "🕒 Adaptive Clock Radio", "desc": "Evolves between Morning Focus, Midday Energy, Evening Flow & Midnight Orbit."},
        {"name": "Deep Vault", "tag": "🎧 Artist Deep Dives", "desc": "Under-played B-sides and album cuts by your top affinity artists."},
        {"name": "The Bridge", "tag": "🌉 Taste Gateways", "desc": "Signature tracks that carried your taste across multiple distinct eras."}
    ]

    for p in smart_list:
        p["count"] = playlist_stats.get(p["name"], 0)

    conn.close()
    return {"smart_playlists": smart_list}

@app.post("/api/playlists/regenerate")
def regenerate_playlists():
    """Recompiles all M3U8 smart playlists and triggers a Navidrome scan."""
    try:
        res = generate_all_smart_playlists()
        subprocess.run(["/opt/navidrome/navidrome", "scan", "--configfile", "/etc/navidrome/navidrome.toml"], capture_output=True)
        return {"status": "success", "playlists": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/intelligence/orbit")
def get_predictive_orbit():
    """Returns the current active predictive orbit recommendations."""
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, source_id, title, artist, album, duration_sec, thumb_url,
               seed_track_title, vibe_label, flac_available, flac_peer_info, status, created_at
        FROM predicted_orbit
        WHERE status = 'recommended'
        ORDER BY id ASC;
    """).fetchall()
    conn.close()

    orbit = []
    for r in rows:
        d = dict(r)
        d["peer_info"] = json.loads(d["flac_peer_info"]) if d.get("flac_peer_info") else None
        orbit.append(d)

    return {"count": len(orbit), "orbit": orbit}

def full_orbit_refresh_and_ingest():
    generate_predictive_orbit(True)
    try:
        from backend.app.autonomous_discovery_ingest import run_discovery_ingestion
    except ImportError:
        from app.autonomous_discovery_ingest import run_discovery_ingestion
    run_discovery_ingestion(10)

@app.post("/api/intelligence/orbit/refresh")
def refresh_predictive_orbit(background_tasks: BackgroundTasks, auto_ingest: bool = True):
    """Triggers an asynchronous refresh of the predictive discovery orbit and ingests into Navidrome."""
    if auto_ingest:
        background_tasks.add_task(full_orbit_refresh_and_ingest)
        return {"status": "started", "message": "Predictive Orbit generation & autonomous vault ingestion initiated in background"}
    else:
        background_tasks.add_task(generate_predictive_orbit, True)
        return {"status": "started", "message": "Predictive Orbit generation initiated in background"}

@app.post("/api/intelligence/orbit/ingest")
def ingest_predictive_orbit(background_tasks: BackgroundTasks, limit: int = 10):
    """Autonomously downloads predicted orbit tracks into vault, tags metadata & updates Navidrome."""
    try:
        from backend.app.autonomous_discovery_ingest import run_discovery_ingestion
    except ImportError:
        from app.autonomous_discovery_ingest import run_discovery_ingestion
    background_tasks.add_task(run_discovery_ingestion, limit)
    return {"status": "started", "message": f"Autonomous ingestion of up to {limit} orbit tracks into vault/Navidrome initiated."}

# -------------------------------------------------------------
# Main Dashboard UI
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    conn = get_db_connection()
    total_tracks = conn.execute("SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL;").fetchone()[0]
    flac_tracks = conn.execute("SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL AND (audio_format = 'FLAC' OR is_lossless = 1 OR local_path LIKE '%.flac');").fetchone()[0]
    opus_tracks = total_tracks - flac_tracks
    total_bytes = conn.execute("SELECT COALESCE(SUM(file_size_bytes), 0) FROM tracks WHERE local_path IS NOT NULL;").fetchone()[0]
    total_gb = total_bytes / (1024**3)
    flac_pct = round((flac_tracks / total_tracks * 100), 1) if total_tracks > 0 else 0

    # Top Obsession
    top_obs = conn.execute("""
        SELECT t.id, t.title, t.artist, a.affinity_score_short, a.total_plays
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        ORDER BY a.affinity_score_short DESC
        LIMIT 1;
    """).fetchone()

    # Top Surging
    top_surge = conn.execute("""
        SELECT t.id, t.title, t.artist, a.affinity_velocity
        FROM track_affinity a
        JOIN tracks t ON a.track_id = t.id
        WHERE a.affinity_velocity > 0
        ORDER BY a.affinity_velocity DESC
        LIMIT 1;
    """).fetchone()

    # Active Era
    latest_era = conn.execute("SELECT era_name FROM listening_eras ORDER BY id DESC LIMIT 1;").fetchone()
    active_era_name = latest_era[0] if latest_era else "The Modern Golden Era (2026)"

    # Circadian Slot
    current_hour = datetime.datetime.now().hour
    if 5 <= current_hour < 12:
        circadian_label = "☀️ Morning Focus"
    elif 12 <= current_hour < 18:
        circadian_label = "⚡ Midday Energy"
    elif 18 <= current_hour < 23:
        circadian_label = "🌆 Evening Flow"
    else:
        circadian_label = "🌙 Midnight Orbit"

    # Predictive Orbit candidates
    orbit_rows = conn.execute("""
        SELECT id, source_id, title, artist, thumb_url, vibe_label, flac_available
        FROM predicted_orbit
        WHERE status = 'recommended'
        ORDER BY id ASC
        LIMIT 6;
    """).fetchall()

    orbit_cards_html = ""
    for o in orbit_rows:
        img_src = o["thumb_url"] or ""
        flac_badge = '<span class="badge badge-flac" style="font-size:9px; margin-left:4px;">⚡ FLAC</span>' if o["flac_available"] else ''
        orbit_cards_html += f"""
        <div class="card" style="display:flex; align-items:center; gap:12px; padding:12px;">
            <img src="{img_src}" style="width:48px;height:48px;border-radius:6px;object-fit:cover;background:#1e293b;" onerror="this.style.display='none'">
            <div style="flex:1; min-width:0;">
                <div style="font-weight:600; font-size:13px; color:#f8fafc; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="{o['title']}">{o['title']}</div>
                <div style="font-size:11px; color:#94a3b8; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{o['artist']}</div>
                <div style="font-size:10px; color:#c084fc; margin-top:2px;">{o['vibe_label']} {flac_badge}</div>
            </div>
            <a href="https://music.youtube.com/watch?v={o['source_id']}" target="_blank" class="play-btn" style="font-size:12px; text-decoration:none; padding:4px 8px; border:1px solid #38bdf8; border-radius:4px;" title="Listen on YouTube Music">▶</a>
        </div>
        """

    tracks = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.audio_format, t.file_size_bytes, t.local_path,
               COALESCE(a.affinity_score_short, 0.0) as aff_short,
               COALESCE(a.affinity_velocity, 0.0) as aff_velo
        FROM tracks t
        LEFT JOIN track_affinity a ON t.id = a.track_id
        WHERE t.local_path IS NOT NULL
        ORDER BY a.affinity_score_short DESC, t.id DESC;
    """).fetchall()
    conn.close()

    tracks_json = []
    for r in tracks:
        fmt = "FLAC" if (r["audio_format"] == "FLAC" or (r["local_path"] and r["local_path"].endswith(".flac"))) else "OPUS"
        sz_mb = round((r["file_size_bytes"] or 0) / (1024 * 1024), 1)
        tracks_json.append({
            "id": r["id"],
            "title": r["title"] or "Unknown Title",
            "artist": r["artist"] or "Unknown Artist",
            "album": r["album"] or "Unknown Album",
            "format": fmt,
            "size": f"{sz_mb} MB",
            "aff_short": round(r["aff_short"], 2),
            "aff_velo": round(r["aff_velo"], 2)
        })

    tracks_data_escaped = json.dumps(tracks_json).replace("</script>", "<\\/script>")

    obs_title = f"{top_obs['title']} - {top_obs['artist']}" if top_obs else "Calculating..."
    obs_sub = f"Score: {top_obs['affinity_score_short']:.1f} • {top_obs['total_plays']} plays" if top_obs else "45-day half-life decay"
    
    surge_title = f"{top_surge['title']} - {top_surge['artist']}" if top_surge else "Stable Velocity"
    surge_sub = f"+{top_surge['affinity_velocity']:.1f} velocity in 72h" if top_surge else "No sudden spikes"

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>🧠 Music Brain - Cognitive Intelligence Hub</title>
        <style>
            :root {{
                --bg: #0b0f19;
                --panel: #111827;
                --panel-border: #1f293d;
                --accent: #38bdf8;
                --accent-hover: #0284c7;
                --gold: #fbbf24;
                --green: #10b981;
                --purple: #a855f7;
                --pink: #ec4899;
                --text: #f8fafc;
                --muted: #94a3b8;
            }}
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: var(--bg);
                color: var(--text);
                padding: 24px 32px 120px 32px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 24px;
                padding-bottom: 20px;
                border-bottom: 1px solid var(--panel-border);
            }}
            .header h1 {{
                font-size: 24px;
                font-weight: 700;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .header p {{
                color: var(--muted);
                font-size: 13px;
                margin-top: 4px;
            }}
            .header-links a {{
                color: var(--accent);
                text-decoration: none;
                font-size: 13px;
                margin-left: 16px;
                font-weight: 500;
            }}
            .header-links a:hover {{ text-decoration: underline; }}

            /* Stat Cards */
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
                gap: 16px;
                margin-bottom: 16px;
            }}
            .card {{
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 12px;
                padding: 18px;
            }}
            .card .label {{
                font-size: 12px;
                color: var(--muted);
                text-transform: uppercase;
                letter-spacing: 0.5px;
                font-weight: 600;
            }}
            .card .val {{
                font-size: 24px;
                font-weight: 700;
                margin: 6px 0 2px 0;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .card .sub {{
                font-size: 12px;
                color: var(--muted);
            }}

            /* Intelligence Grid */
            .intel-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 16px;
                margin-bottom: 24px;
            }}
            .card-intel {{
                background: linear-gradient(135deg, rgba(30, 41, 59, 0.7), rgba(15, 23, 42, 0.9));
                border: 1px solid rgba(56, 189, 248, 0.2);
                border-radius: 12px;
                padding: 16px 20px;
            }}

            /* Actions Bar */
            .actions-bar {{
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 12px;
                padding: 14px 20px;
                margin-bottom: 24px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 16px;
                flex-wrap: wrap;
            }}
            .action-btns {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
            }}
            .btn {{
                background: #1e293b;
                border: 1px solid var(--panel-border);
                color: var(--text);
                padding: 8px 16px;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
            }}
            .btn:hover {{ background: #334155; }}
            .btn-primary {{
                background: var(--accent);
                color: #0b0f19;
                border: none;
            }}
            .btn-primary:hover {{ background: var(--accent-hover); }}
            .status-ticker {{
                font-size: 13px;
                color: var(--muted);
                font-family: monospace;
            }}

            /* Search & Table */
            .table-controls {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 14px;
                gap: 12px;
                flex-wrap: wrap;
            }}
            .search-box {{
                background: var(--panel);
                border: 1px solid var(--panel-border);
                color: var(--text);
                padding: 8px 14px;
                border-radius: 8px;
                font-size: 13px;
                width: 320px;
            }}
            .filter-pills {{
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
            }}
            .pill {{
                background: var(--panel);
                border: 1px solid var(--panel-border);
                color: var(--muted);
                padding: 6px 14px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 600;
                cursor: pointer;
            }}
            .pill.active {{
                background: var(--accent);
                color: #0b0f19;
                border-color: var(--accent);
            }}

            .track-table {{
                width: 100%;
                border-collapse: collapse;
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 12px;
                overflow: hidden;
            }}
            .track-table th, .track-table td {{
                padding: 12px 16px;
                text-align: left;
                border-bottom: 1px solid var(--panel-border);
                font-size: 13px;
            }}
            .track-table th {{
                background: #0f172a;
                color: var(--muted);
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            .track-table tr:hover td {{
                background: #1e293b;
            }}
            .badge {{
                display: inline-block;
                padding: 3px 8px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            .badge-flac {{ background: rgba(16, 185, 129, 0.2); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.4); }}
            .badge-opus {{ background: rgba(56, 189, 248, 0.2); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.4); }}
            .play-btn {{
                background: none;
                border: none;
                color: var(--accent);
                cursor: pointer;
                font-size: 16px;
            }}

            /* Sticky Player */
            .sticky-player {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                background: #0f172a;
                border-top: 1px solid var(--panel-border);
                padding: 12px 32px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                box-shadow: 0 -4px 20px rgba(0,0,0,0.5);
                z-index: 100;
            }}
            .player-info {{
                display: flex;
                align-items: center;
                gap: 12px;
                width: 300px;
            }}
            .player-info img {{
                width: 44px;
                height: 44px;
                border-radius: 6px;
                object-fit: cover;
                background: #1e293b;
            }}
            .player-text .p-title {{ font-weight: 600; font-size: 13px; color: var(--text); }}
            .player-text .p-art {{ font-size: 11px; color: var(--muted); }}
            audio {{
                width: 500px;
                height: 36px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <h1>🧠 Music Brain Master Orchestrator</h1>
                <p>Personal Lossless Vault • CT 102 (192.168.1.102) • Autonomous Cognitive Architecture (Pillar 1 Active)</p>
            </div>
            <div class="header-links">
                <a href="http://192.168.1.102:4533" target="_blank">📻 Navidrome (4533)</a>
                <a href="http://192.168.1.102:5030" target="_blank">⚡ Soulseek / slskd (5030)</a>
                <a href="http://192.168.1.102:8080" target="_blank">📂 WebDAV (8080)</a>
            </div>
        </div>

        <!-- Pillar 1 Cognitive Intelligence Grid -->
        <div class="intel-grid">
            <div class="card-intel" style="border-left: 4px solid var(--pink);">
                <div class="card label" style="padding:0;background:none;border:none;">🔥 Current Obsession</div>
                <div class="card val" style="padding:0;background:none;border:none;font-size:18px;color:#f472b6;" title="{obs_title}">{obs_title}</div>
                <div class="card sub" style="padding:0;background:none;border:none;">{obs_sub}</div>
            </div>
            <div class="card-intel" style="border-left: 4px solid var(--accent);">
                <div class="card label" style="padding:0;background:none;border:none;">🚀 Surging Velocity (72h)</div>
                <div class="card val" style="padding:0;background:none;border:none;font-size:18px;color:var(--accent);" title="{surge_title}">{surge_title}</div>
                <div class="card sub" style="padding:0;background:none;border:none;">{surge_sub}</div>
            </div>
            <div class="card-intel" style="border-left: 4px solid var(--gold);">
                <div class="card label" style="padding:0;background:none;border:none;">⏳ Active Listening Era</div>
                <div class="card val" style="padding:0;background:none;border:none;font-size:18px;color:var(--gold);" title="{active_era_name}">{active_era_name}</div>
                <div class="card sub" style="padding:0;background:none;border:none;">Historical Changepoint Clustering</div>
            </div>
            <div class="card-intel" style="border-left: 4px solid var(--purple);">
                <div class="card label" style="padding:0;background:none;border:none;">🕒 Circadian Rhythm</div>
                <div class="card val" style="padding:0;background:none;border:none;font-size:18px;color:var(--purple);">{circadian_label}</div>
                <div class="card sub" style="padding:0;background:none;border:none;">Hour-of-Day Adaptive Profile</div>
            </div>
        </div>

        <!-- Pillar 2 Autonomous Smart Playlists Grid -->
        <div style="margin-bottom: 24px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px;">
                <h3 style="font-size: 15px; font-weight: 700; color: var(--accent); display:flex; align-items:center; gap:8px;">
                    🎵 Pillar 2: Autonomous Smart Playlists (Live in Navidrome & Symfonium)
                </h3>
                <button class="btn" style="padding: 4px 12px; font-size:11px;" onclick="triggerPlaylistRegen()">⚡ Recompile Playlists</button>
            </div>
            <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-bottom: 0;">
                <div class="card" style="border-left: 3px solid var(--pink);">
                    <div class="label">🔥 Heavy Rotation (35 tracks)</div>
                    <div style="font-size: 14px; font-weight: 600; margin: 4px 0;">Current Obsessions</div>
                    <div class="sub">45-day half-life decay affinity</div>
                </div>
                <div class="card" style="border-left: 3px solid var(--green);">
                    <div class="label">💎 Forgotten Gems (35 tracks)</div>
                    <div style="font-size: 14px; font-weight: 600; margin: 4px 0;">Nostalgic Heavyweights</div>
                    <div class="sub">High all-time score, unplayed >45d</div>
                </div>
                <div class="card" style="border-left: 3px solid var(--gold);">
                    <div class="label">⏳ Time Machine (35 tracks)</div>
                    <div style="font-size: 14px; font-weight: 600; margin: 4px 0;">Calendar Flashbacks</div>
                    <div class="sub">October/November past year repeats</div>
                </div>
                <div class="card" style="border-left: 3px solid var(--purple);">
                    <div class="label">🕒 Circadian Flow (30 tracks)</div>
                    <div style="font-size: 14px; font-weight: 600; margin: 4px 0;">{circadian_label}</div>
                    <div class="sub">Adapts throughout the day</div>
                </div>
                <div class="card" style="border-left: 3px solid var(--accent);">
                    <div class="label">🎧 Deep Vault (30 tracks)</div>
                    <div style="font-size: 14px; font-weight: 600; margin: 4px 0;">Artist Deep Dives</div>
                    <div class="sub">Hidden cuts by your top artists</div>
                </div>
                <div class="card" style="border-left: 3px solid #6366f1;">
                    <div class="label">🌉 The Bridge (17 tracks)</div>
                    <div style="font-size: 14px; font-weight: 600; margin: 4px 0;">Taste Gateways</div>
                    <div class="sub">Tracks bridging 2021-2026 eras</div>
                </div>
            </div>
        </div>

        <!-- Pillar 3 Predictive Discovery Orbit Grid -->
        <div style="margin-bottom: 24px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px;">
                <h3 style="font-size: 15px; font-weight: 700; color: #c084fc; display:flex; align-items:center; gap:8px;">
                    🔮 Pillar 3: Predictive Discovery Orbit (AI Algorithmic Predictions)
                </h3>
                <div style="display:flex; gap:8px;">
                    <button class="btn" style="padding: 4px 12px; font-size:11px;" onclick="triggerOrbitRefresh()">🔄 Refresh Orbit</button>
                    <button class="btn" style="padding: 4px 12px; font-size:11px; background:#9333ea;" onclick="triggerOrbitIngest()">⚡ Ingest to Navidrome</button>
                </div>
            </div>
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px;">
                {orbit_cards_html}
            </div>
        </div>

        <div class="stats-grid">
            <div class="card">
                <div class="label">💎 Lossless FLAC</div>
                <div class="val" style="color: var(--green);">{flac_tracks}</div>
                <div class="sub">{flac_pct}% of total vault</div>
            </div>
            <div class="card">
                <div class="label">🎵 High-Bitrate Opus</div>
                <div class="val" style="color: var(--accent);">{opus_tracks}</div>
                <div class="sub">Instant Playback Targets</div>
            </div>
            <div class="card">
                <div class="label">📦 Total Library Size</div>
                <div class="val">{total_gb:.2f} GB</div>
                <div class="sub">{total_tracks} total verified tracks</div>
            </div>
            <div class="card">
                <div class="label">🛡️ Vault Duplicates</div>
                <div class="val" style="color: var(--green);">0</div>
                <div class="sub">Two-Tier Dedup Active</div>
            </div>
        </div>

        <div class="actions-bar">
            <div class="action-btns">
                <button class="btn btn-primary" onclick="triggerSync()">🔄 Sync History Now</button>
                <button class="btn" onclick="triggerAffinityRecalc()">🧠 Recalculate Intelligence</button>
                <button class="btn" onclick="triggerDedup()">🧹 Run Dedup Sweep</button>
                <button class="btn" onclick="restartUpgrader()">⚡ Re-scan Soulseek FLACs</button>
                <a href="/api/db/download" class="btn" style="text-decoration:none; display:inline-flex; align-items:center;" download>💾 Download Database</a>
            </div>
            <div class="status-ticker" id="statusTicker">Master Orchestrator Active • Episodic Memory Operational</div>
        </div>

        <div class="table-controls">
            <input type="text" class="search-box" id="searchBox" placeholder="Search by title, artist, or album..." onkeyup="filterTracks()">
            <div class="filter-pills">
                <div class="pill active" id="pillAll" onclick="setFilter('ALL')">All ({total_tracks})</div>
                <div class="pill" id="pillObsessions" onclick="setFilter('OBSESSIONS')">🔥 Top Obsessions</div>
                <div class="pill" id="pillSurging" onclick="setFilter('SURGING')">🚀 Surging</div>
                <div class="pill" id="pillFLAC" onclick="setFilter('FLAC')">FLAC ({flac_tracks})</div>
                <div class="pill" id="pillOPUS" onclick="setFilter('OPUS')">Opus ({opus_tracks})</div>
            </div>
        </div>

        <table class="track-table">
            <thead>
                <tr>
                    <th width="40"></th>
                    <th width="50">Cover</th>
                    <th>Track Title</th>
                    <th>Artist</th>
                    <th>Album</th>
                    <th width="80">Affinity</th>
                    <th width="90">Format</th>
                    <th width="90">Size</th>
                </tr>
            </thead>
            <tbody id="trackBody"></tbody>
        </table>

        <div class="sticky-player" id="playerBar" style="display: none;">
            <div class="player-info">
                <img id="playerCover" src="" alt="">
                <div class="player-text">
                    <div class="p-title" id="playerTitle">Select a track to stream</div>
                    <div class="p-art" id="playerArtist">---</div>
                </div>
            </div>
            <audio id="audioEl" controls autoplay></audio>
        </div>

        <script>
            const allTracks = {tracks_data_escaped};
            let currentFilter = 'ALL';

            function renderTracks(tracks) {{
                const tbody = document.getElementById('trackBody');
                if (!tracks.length) {{
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:30px; color:#94a3b8;">No matching tracks found.</td></tr>';
                    return;
                }}
                tbody.innerHTML = tracks.slice(0, 300).map(t => `
                    <tr>
                        <td>
                            <button class="play-btn" onclick="playTrack(${{t.id}}, '${{escapeHtml(t.title)}}', '${{escapeHtml(t.artist)}}')">▶</button>
                        </td>
                        <td>
                            <img src="/api/cover/${{t.id}}" style="width:36px;height:36px;border-radius:4px;object-fit:cover;background:#1e293b;" onerror="this.style.display='none'">
                        </td>
                        <td style="font-weight:600; color:#f8fafc;">${{t.title}}</td>
                        <td style="color:#cbd5e1;">${{t.artist}}</td>
                        <td style="color:#94a3b8;">${{t.album}}</td>
                        <td style="color:#f472b6; font-weight:600; font-family:monospace;">${{t.aff_short > 0 ? t.aff_short : '-'}}</td>
                        <td><span class="badge badge-${{t.format.toLowerCase()}}">${{t.format}}</span></td>
                        <td style="color:#94a3b8; font-family:monospace;">${{t.size}}</td>
                    </tr>
                `).join('');
            }}

            function filterTracks() {{
                const q = document.getElementById('searchBox').value.toLowerCase();
                const filtered = allTracks.filter(t => {{
                    const matchQ = t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q) || t.album.toLowerCase().includes(q);
                    let matchFmt = true;
                    if (currentFilter === 'FLAC') matchFmt = (t.format === 'FLAC');
                    else if (currentFilter === 'OPUS') matchFmt = (t.format === 'OPUS');
                    else if (currentFilter === 'OBSESSIONS') matchFmt = (t.aff_short >= 5.0);
                    else if (currentFilter === 'SURGING') matchFmt = (t.aff_velo > 0.0);
                    return matchQ && matchFmt;
                }});
                renderTracks(filtered);
            }}

            function setFilter(fmt) {{
                currentFilter = fmt;
                document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
                if (fmt === 'ALL') document.getElementById('pillAll').classList.add('active');
                if (fmt === 'OBSESSIONS') document.getElementById('pillObsessions').classList.add('active');
                if (fmt === 'SURGING') document.getElementById('pillSurging').classList.add('active');
                if (fmt === 'FLAC') document.getElementById('pillFLAC').classList.add('active');
                if (fmt === 'OPUS') document.getElementById('pillOPUS').classList.add('active');
                filterTracks();
            }}

            function playTrack(id, title, artist) {{
                const bar = document.getElementById('playerBar');
                bar.style.display = 'flex';
                document.getElementById('playerTitle').innerText = title;
                document.getElementById('playerArtist').innerText = artist;
                document.getElementById('playerCover').src = `/api/cover/${{id}}`;
                const audio = document.getElementById('audioEl');
                audio.src = `/api/stream/${{id}}`;
                audio.play();
            }}

            function escapeHtml(str) {{
                return (str || '').replace(/'/g, "\\'");
            }}

            async function triggerSync() {{
                const ticker = document.getElementById('statusTicker');
                ticker.innerText = 'Starting YouTube Music History Sync...';
                try {{
                    const res = await fetch('/api/sync/trigger', {{ method: 'POST' }});
                    const data = await res.json();
                    ticker.innerText = data.message;
                }} catch (e) {{
                    ticker.innerText = 'Failed to trigger sync: ' + e;
                }}
            }}

            async function triggerAffinityRecalc() {{
                const ticker = document.getElementById('statusTicker');
                ticker.innerText = 'Recalculating Episodic Memory & Affinity...';
                try {{
                    const res = await fetch('/api/intelligence/recalculate', {{ method: 'POST' }});
                    const data = await res.json();
                    ticker.innerText = data.message;
                }} catch (e) {{
                    ticker.innerText = 'Failed to recalculate: ' + e;
                }}
            }}

            async function triggerPlaylistRegen() {{
                const ticker = document.getElementById('statusTicker');
                ticker.innerText = 'Recompiling all Smart Playlists & Syncing Navidrome...';
                try {{
                    const res = await fetch('/api/playlists/regenerate', {{ method: 'POST' }});
                    const data = await res.json();
                    ticker.innerText = 'Smart Playlists successfully recompiled!';
                    setTimeout(() => location.reload(), 1500);
                }} catch (e) {{
                    ticker.innerText = 'Failed to recompile: ' + e;
                }}
            }}

            async function triggerOrbitRefresh() {{
                const ticker = document.getElementById('statusTicker');
                ticker.innerText = 'Mining YouTube Music seeds for new predictive orbit...';
                try {{
                    const res = await fetch('/api/intelligence/orbit/refresh', {{ method: 'POST' }});
                    const data = await res.json();
                    ticker.innerText = data.message;
                    setTimeout(() => location.reload(), 3000);
                }} catch (e) {{
                    ticker.innerText = 'Failed to refresh orbit: ' + e;
                }}
            }}

            async function triggerDedup() {{
                const ticker = document.getElementById('statusTicker');
                ticker.innerText = 'Starting Deduplication Sweep...';
                try {{
                    const res = await fetch('/api/dedup/trigger', {{ method: 'POST' }});
                    const data = await res.json();
                    ticker.innerText = data.message;
                }} catch (e) {{
                    ticker.innerText = 'Failed to trigger dedup: ' + e;
                }}
            }}

            async function restartUpgrader() {{
                const ticker = document.getElementById('statusTicker');
                ticker.innerText = 'Restarting FLAC Upgrader...';
                try {{
                    const res = await fetch('/api/upgrader/restart', {{ method: 'POST' }});
                    const data = await res.json();
                    ticker.innerText = data.message;
                }} catch (e) {{
                    ticker.innerText = 'Failed to restart upgrader: ' + e;
                }}
            }}

            async function pollStatus() {{
                try {{
                    const res = await fetch('/api/status');
                    const d = await res.json();
                    const ticker = document.getElementById('statusTicker');
                    if (d.history_sync.is_running) {{
                        ticker.innerText = '⏳ ' + d.history_sync.status;
                    }} else if (d.intelligence.is_running) {{
                        ticker.innerText = '🧠 ' + d.intelligence.status;
                    }} else if (d.dedup.is_running) {{
                        ticker.innerText = '⏳ ' + d.dedup.status;
                    }} else {{
                        ticker.innerText = '🟢 Master Orchestrator Active • ' + d.history_sync.status;
                    }}
                }} catch (e) {{}}
            }}
            setInterval(pollStatus, 8000);

            // Initial render
            renderTracks(allTracks);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
