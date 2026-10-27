from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import sqlite3
import os
import subprocess
import threading

app = FastAPI(title="Music Brain", version="2.5")
DB_PATH = "/opt/music-brain/music_brain.db"
MUSIC_DIR = "/opt/music-brain/music"
VENV_PYTHON = "/opt/music-brain/.venv/bin/python3"
HYBRID_DOWNLOAD_SCRIPT = "/opt/music-brain/backend/app/download_hybrid.py"
LOG_FILE = "/opt/music-brain/download.log"

download_lock = threading.Lock()
is_downloading = False

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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

@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    conn = get_db_connection()
    
    total_tracks = conn.execute("SELECT COUNT(*) FROM tracks;").fetchone()[0]
    downloaded_tracks = conn.execute("SELECT COUNT(*) FROM tracks WHERE download_status = 'completed';").fetchone()[0]
    flac_tracks = conn.execute("SELECT COUNT(*) FROM tracks WHERE download_status = 'completed' AND (is_lossless = 1 OR audio_format = 'FLAC' OR local_path LIKE '%.flac');").fetchone()[0]
    opus_tracks = downloaded_tracks - flac_tracks
    
    total_bytes = conn.execute("SELECT COALESCE(SUM(file_size_bytes), 0) FROM tracks WHERE download_status = 'completed';").fetchone()[0]
    total_mb = total_bytes / (1024 * 1024)
    total_gb = total_mb / 1024
    pct = int((downloaded_tracks / total_tracks * 100)) if total_tracks > 0 else 0
    
    # Load tracks: FLAC lossless first, then recent downloads, then remaining tracks
    tracks = conn.execute("""
        SELECT t.id, t.title, t.artist, t.album, t.download_status, t.file_size_bytes,
               t.audio_format, t.is_lossless, t.local_path,
               d.discovery_source, d.played_at 
        FROM tracks t
        LEFT JOIN (
            SELECT track_id, discovery_source, played_at 
            FROM discovery_events 
            GROUP BY track_id
        ) d ON t.id = d.track_id
        ORDER BY (t.audio_format = 'FLAC' OR t.is_lossless = 1 OR t.local_path LIKE '%.flac') DESC,
                 (t.download_status = 'completed') DESC,
                 t.downloaded_at DESC, 
                 t.id DESC;
    """).fetchall()

    playlists = conn.execute("""
        SELECT discovery_source, COUNT(*) as cnt 
        FROM discovery_events 
        WHERE discovery_source LIKE 'ytm_playlist:%' OR discovery_source IN ('ytm_liked', 'ytm_library')
        GROUP BY discovery_source
        ORDER BY cnt DESC;
    """).fetchall()
    
    conn.close()

    storage_str = f"{total_gb:.2f} GB" if total_gb >= 1.0 else f"{total_mb:.1f} MB"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>🧠 Music Brain - Master Dashboard</title>
        <style>
            :root {{
                --bg: #0b0f19;
                --card-bg: #151d30;
                --card-border: #1e2942;
                --accent: #38bdf8;
                --accent-hover: #0284c7;
                --text-main: #f1f5f9;
                --text-muted: #94a3b8;
                --gold: #fbbf24;
                --success: #10b981;
                --danger: #ef4444;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: var(--bg);
                color: var(--text-main);
                margin: 0;
                padding: 24px;
                padding-bottom: 100px;
            }}
            .container {{ max-width: 1200px; margin: auto; }}
            
            /* Top Navbar */
            .navbar {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 16px;
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                padding: 16px 24px;
                border-radius: 12px;
                margin-bottom: 24px;
            }}
            .brand {{
                display: flex;
                align-items: center;
                gap: 12px;
                text-decoration: none;
                color: #fff;
            }}
            .brand h1 {{ margin: 0; font-size: 1.4rem; }}
            .brand-tag {{
                background: #1e2942;
                border: 1px solid #334155;
                font-size: 0.75rem;
                padding: 2px 8px;
                border-radius: 20px;
                color: var(--accent);
            }}
            .nav-links {{ display: flex; gap: 10px; align-items: center; }}
            .btn {{
                background: var(--accent);
                color: #0b0f19;
                font-weight: 600;
                padding: 8px 16px;
                border-radius: 8px;
                text-decoration: none;
                border: none;
                cursor: pointer;
                font-size: 0.9rem;
                display: inline-flex;
                align-items: center;
                gap: 6px;
                transition: all 0.2s;
            }}
            .btn:hover {{ background: var(--accent-hover); color: #fff; }}
            .btn-secondary {{ background: #1e2942; color: var(--text-main); border: 1px solid #334155; }}
            .btn-secondary:hover {{ background: #334155; }}
            
            /* Audio Format Legend */
            .legend-card {{
                background: linear-gradient(135deg, #151d30, #1e2942);
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 18px 24px;
                margin-bottom: 24px;
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
            }}
            .legend-item {{
                display: flex;
                align-items: center;
                gap: 12px;
            }}
            .legend-title {{
                font-weight: 700;
                font-size: 0.95rem;
                display: block;
            }}
            .legend-desc {{
                font-size: 0.8rem;
                color: var(--text-muted);
            }}

            /* Stats Grid */
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 16px;
                margin-bottom: 24px;
            }}
            .stat-card {{
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 12px;
                padding: 16px 20px;
            }}
            .stat-card .label {{
                font-size: 0.8rem;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                color: var(--text-muted);
                margin-bottom: 4px;
            }}
            .stat-card .value {{
                font-size: 1.6rem;
                font-weight: 700;
                color: #fff;
            }}

            /* Progress */
            .progress-container {{
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 12px;
                padding: 18px 20px;
                margin-bottom: 24px;
            }}
            .progress-header {{ display: flex; justify-content: space-between; margin-bottom: 10px; font-weight: 600; font-size: 0.9rem; }}
            .progress-bar-bg {{ background: #0f172a; border-radius: 999px; height: 12px; overflow: hidden; }}
            .progress-bar-fill {{ background: linear-gradient(90deg, #38bdf8, #10b981); height: 100%; border-radius: 999px; transition: width 0.4s ease; }}

            /* Playlists Chips */
            .playlists-wrap {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }}
            .playlist-chip {{ background: #151d30; border: 1px solid #1e2942; padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; color: #cbd5e1; }}
            .playlist-chip strong {{ color: var(--accent); }}

            /* Table */
            .table-wrap {{
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 12px;
                overflow: hidden;
            }}
            .table-header-bar {{
                padding: 16px 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: #131b2c;
                border-bottom: 1px solid var(--card-border);
            }}
            .search-input {{
                background: #0b0f19;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px 12px;
                color: #fff;
                font-size: 0.9rem;
                width: 280px;
            }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px 18px; text-align: left; border-bottom: 1px solid #1e2942; }}
            th {{ background: #0d1322; color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
            tr:hover {{ background: #1a233a; }}

            /* Badges */
            .badge {{
                display: inline-block;
                padding: 4px 10px;
                border-radius: 6px;
                font-size: 0.78rem;
                font-weight: 700;
                letter-spacing: 0.02em;
            }}
            .badge-flac {{
                background: rgba(251, 191, 36, 0.15);
                color: #fbbf24;
                border: 1px solid rgba(251, 191, 36, 0.3);
            }}
            .badge-opus {{
                background: rgba(56, 189, 248, 0.15);
                color: #38bdf8;
                border: 1px solid rgba(56, 189, 248, 0.3);
            }}
            .badge-pending {{
                background: #334155;
                color: #94a3b8;
            }}

            .play-btn {{
                background: #1e2942;
                border: 1px solid #334155;
                color: #fff;
                border-radius: 50%;
                width: 32px;
                height: 32px;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 0.85rem;
                transition: all 0.2s;
            }}
            .play-btn:hover {{
                background: var(--accent);
                color: #0b0f19;
            }}

            /* Floating Player Bar */
            .player-bar {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                background: #111827;
                border-top: 1px solid #374151;
                padding: 12px 24px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                box-shadow: 0 -10px 25px rgba(0,0,0,0.5);
                z-index: 1000;
            }}
            .player-track-info {{
                font-size: 0.9rem;
                display: flex;
                flex-direction: column;
                gap: 2px;
            }}
            audio {{ height: 36px; min-width: 400px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Persistent Top Navbar with Home Link -->
            <div class="navbar">
                <a href="/" class="brand">
                    <span style="font-size: 1.6rem;">🧠</span>
                    <div>
                        <h1>Music Brain</h1>
                        <span class="brand-tag">Self-Hosted Vault</span>
                    </div>
                </a>
                <div class="nav-links">
                    <a href="/" class="btn btn-secondary">🏠 Home</a>
                    <a href="http://100.83.189.65:4533" target="_blank" class="btn">
                        🎵 Navidrome Streamer
                    </a>
                    <a href="http://100.83.189.65:5030" target="_blank" class="btn btn-secondary">
                        ⚡ Soulseek (slskd)
                    </a>
                </div>
            </div>

            <!-- Audio Format Quality Legend -->
            <div class="legend-card">
                <div class="legend-item">
                    <span class="badge badge-flac" style="font-size: 0.9rem;">🌟 FLAC</span>
                    <div>
                        <span class="legend-title" style="color: var(--gold);">Tier 1: True Studio FLAC (Best Quality)</span>
                        <span class="legend-desc">Genuine 16-bit / 24-bit uncompressed lossless master via Soulseek (900–1411+ kbps).</span>
                    </div>
                </div>
                <div class="legend-item">
                    <span class="badge badge-opus" style="font-size: 0.9rem;">🎵 OPUS</span>
                    <div>
                        <span class="legend-title" style="color: var(--accent);">Tier 2: High-Bitrate Opus (Web Stream)</span>
                        <span class="legend-desc">160 kbps transparent lossy stream from YouTube Music. Compact (~3-4 MB), great for mobile.</span>
                    </div>
                </div>
            </div>

            <!-- Stats Grid -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="label">Total Curated Tracks</div>
                    <div class="value">{total_tracks}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Downloaded in Vault</div>
                    <div class="value" style="color: var(--success);">{downloaded_tracks} / {total_tracks}</div>
                </div>
                <div class="stat-card">
                    <div class="label">🌟 Lossless FLAC Masters</div>
                    <div class="value" style="color: var(--gold);">{flac_tracks}</div>
                </div>
                <div class="stat-card">
                    <div class="label">🎵 OPUS Web Streams</div>
                    <div class="value" style="color: var(--accent);">{opus_tracks}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Storage Used</div>
                    <div class="value">{storage_str}</div>
                </div>
            </div>

            <div class="progress-container">
                <div class="progress-header">
                    <span>Vault Sync Progress</span>
                    <span>{pct}% ({downloaded_tracks} of {total_tracks} tracks stored locally)</span>
                </div>
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" style="width: {pct}%;"></div>
                </div>
            </div>

            <h3 style="margin: 0 0 10px 0; font-size: 0.85rem; text-transform: uppercase; color: var(--text-muted);">Indexed Playlists</h3>
            <div class="playlists-wrap">
    """

    for pl in playlists:
        name = pl["discovery_source"].replace("ytm_playlist:", "").replace("ytm_", "")
        cnt = pl["cnt"]
        html_content += f'<div class="playlist-chip">{name} · <strong>{cnt} tracks</strong></div>'

    html_content += f"""
            </div>

            <div class="table-wrap">
                <div class="table-header-bar">
                    <h3 style="margin: 0;">Tracks Repository (Recent Additions)</h3>
                    <input type="text" id="filterInput" class="search-input" placeholder="Search track, artist, format..." onkeyup="filterTable()">
                </div>
                <table id="tracksTable">
                    <thead>
                        <tr>
                            <th style="width: 40px;">Play</th>
                            <th>Audio Quality / Format</th>
                            <th>Title</th>
                            <th>Artist</th>
                            <th>Album</th>
                            <th>File Size</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    for row in tracks:
        status = row["download_status"] or "pending"
        path = row["local_path"] or ""
        is_flac = (row["audio_format"] == "FLAC") or (row["is_lossless"] == 1) or path.lower().endswith(".flac")
        
        if status == "completed":
            if is_flac:
                badge_html = '<span class="badge badge-flac">🌟 FLAC (Lossless)</span>'
            else:
                badge_html = '<span class="badge badge-opus">🎵 OPUS (160k YTM)</span>'
        else:
            badge_html = '<span class="badge badge-pending">⏳ Pending</span>'

        size_bytes = row["file_size_bytes"] or 0
        size_str = f"{size_bytes / (1024*1024):.1f} MB" if size_bytes > 0 else "-"
        
        play_btn = ""
        track_id = row["id"]
        safe_title_js = (row["title"] or "Unknown").replace("'", "\\'").replace('"', '&quot;')
        safe_artist_js = (row["artist"] or "Unknown").replace("'", "\\'").replace('"', '&quot;')
        if status == "completed" and path:
            play_btn = f'<button class="play-btn" onclick="playAudio(\'/api/stream/{track_id}\', \'{safe_title_js}\', \'{safe_artist_js}\')">▶</button>'
        else:
            play_btn = '<span style="color: #475569;">-</span>'

        html_content += f"""
                        <tr>
                            <td>{play_btn}</td>
                            <td>{badge_html}</td>
                            <td><strong>{row['title'] or 'Unknown'}</strong></td>
                            <td style="color: var(--text-muted);">{row['artist'] or 'Unknown'}</td>
                            <td style="color: var(--text-muted);">{row['album'] or 'Singles & EP'}</td>
                            <td>{size_str}</td>
                        </tr>
        """

    html_content += """
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Floating Instant Audio Player -->
        <div class="player-bar" id="playerBar" style="display: none;">
            <div class="player-track-info">
                <span id="playerTitle" style="font-weight: 700; color: #fff;"></span>
                <span id="playerArtist" style="font-size: 0.8rem; color: #9ca3af;"></span>
            </div>
            <audio id="audioEl" controls autoplay></audio>
        </div>

        <script>
            function filterTable() {
                const input = document.getElementById('filterInput').value.toLowerCase();
                const rows = document.querySelectorAll('#tracksTable tbody tr');
                rows.forEach(row => {
                    const text = row.innerText.toLowerCase();
                    row.style.display = text.includes(input) ? '' : 'none';
                });
            }

            function playAudio(streamUrl, title, artist) {
                const bar = document.getElementById('playerBar');
                const audio = document.getElementById('audioEl');
                const tEl = document.getElementById('playerTitle');
                const aEl = document.getElementById('playerArtist');

                tEl.innerText = title;
                aEl.innerText = artist;
                audio.src = streamUrl;
                bar.style.display = 'flex';
                audio.play();
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/health")
def health_check():
    return {"status": "healthy", "database": DB_PATH}
