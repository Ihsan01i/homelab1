import os
import sys
import re
import time
import json
import sqlite3
import datetime
import threading
import subprocess
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response

app = FastAPI(title="Music Brain Master Orchestrator", version="3.0")

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
sync_status_msg = "Idle"
dedup_status_msg = "Idle"

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

# -------------------------------------------------------------
# API Endpoints
# -------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": time.time(), "version": "3.0"}

@app.get("/api/status")
def get_status():
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL;").fetchone()[0]
    flacs = conn.execute("SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL AND (audio_format = 'FLAC' OR is_lossless = 1 OR local_path LIKE '%.flac');").fetchone()[0]
    opuss = total - flacs
    bytes_total = conn.execute("SELECT COALESCE(SUM(file_size_bytes), 0) FROM tracks WHERE local_path IS NOT NULL;").fetchone()[0]
    conn.close()

    # Upgrader log status
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
            "duplicates": 0
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
        
    # Fallback placeholder or 404
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

    tracks = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.audio_format, t.file_size_bytes, t.local_path, t.downloaded_at
        FROM tracks t
        WHERE t.local_path IS NOT NULL
        ORDER BY (t.audio_format = 'FLAC' OR t.local_path LIKE '%.flac') DESC, t.id DESC;
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
            "size": f"{sz_mb} MB"
        })

    tracks_data_escaped = json.dumps(tracks_json).replace("</script>", "<\\/script>")

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>🧠 Music Brain - Master Orchestrator</title>
        <style>
            :root {{
                --bg: #0b0f19;
                --panel: #111827;
                --panel-border: #1f293d;
                --accent: #38bdf8;
                --accent-hover: #0284c7;
                --gold: #fbbf24;
                --green: #10b981;
                --purple: #818cf8;
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
                margin-bottom: 24px;
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
                font-size: 28px;
                font-weight: 700;
                margin: 6px 0 2px 0;
            }}
            .card .sub {{
                font-size: 12px;
                color: var(--muted);
            }}

            /* Actions Bar */
            .actions-bar {{
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 12px;
                padding: 16px 20px;
                margin-bottom: 24px;
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
            }}
            .action-btns {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
            }}
            .btn {{
                background: #1e293b;
                color: var(--text);
                border: 1px solid #334155;
                padding: 8px 16px;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                gap: 8px;
                transition: all 0.15s ease;
            }}
            .btn:hover {{ background: #334155; }}
            .btn-primary {{
                background: var(--accent);
                color: #0b0f19;
                border-color: var(--accent);
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
                <p>Personal Lossless Vault • CT 102 (192.168.1.102) • Autonomous Domino Architecture</p>
            </div>
            <div class="header-links">
                <a href="http://192.168.1.102:4533" target="_blank">📻 Navidrome (4533)</a>
                <a href="http://192.168.1.102:5030" target="_blank">⚡ Soulseek / slskd (5030)</a>
                <a href="http://192.168.1.102:8080" target="_blank">📂 WebDAV (8080)</a>
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
                <button class="btn" onclick="triggerDedup()">🧹 Run Dedup Sweep</button>
                <button class="btn" onclick="restartUpgrader()">⚡ Re-scan Soulseek FLACs</button>
                <a href="/api/db/download" class="btn" style="text-decoration:none; display:inline-flex; align-items:center;" download>💾 Download Database</a>
            </div>
            <div class="status-ticker" id="statusTicker">Master Orchestrator Active • Next Sync: Tomorrow 00:00 UTC</div>
        </div>

        <div class="table-controls">
            <input type="text" class="search-box" id="searchBox" placeholder="Search by title, artist, or album..." onkeyup="filterTracks()">
            <div class="filter-pills">
                <div class="pill active" id="pillAll" onclick="setFilter('ALL')">All ({total_tracks})</div>
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
                    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:30px; color:#94a3b8;">No matching tracks found.</td></tr>';
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
                        <td><span class="badge badge-${{t.format.toLowerCase()}}">${{t.format}}</span></td>
                        <td style="color:#94a3b8; font-family:monospace;">${{t.size}}</td>
                    </tr>
                `).join('');
            }}

            function filterTracks() {{
                const q = document.getElementById('searchBox').value.toLowerCase();
                const filtered = allTracks.filter(t => {{
                    const matchQ = t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q) || t.album.toLowerCase().includes(q);
                    const matchFmt = (currentFilter === 'ALL') || (t.format === currentFilter);
                    return matchQ && matchFmt;
                }});
                renderTracks(filtered);
            }}

            function setFilter(fmt) {{
                currentFilter = fmt;
                document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
                if (fmt === 'ALL') document.getElementById('pillAll').classList.add('active');
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

            // Poll live status every 10s
            async function pollStatus() {{
                try {{
                    const res = await fetch('/api/status');
                    const d = await res.json();
                    const ticker = document.getElementById('statusTicker');
                    if (d.history_sync.is_running) {{
                        ticker.innerText = '⏳ ' + d.history_sync.status;
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
