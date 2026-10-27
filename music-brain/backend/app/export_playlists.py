import os
import re
import sqlite3

DB_PATH = "/opt/music-brain/music_brain.db"
MUSIC_DIR = "/opt/music-brain/music"
PLAYLISTS_DIR = os.path.join(MUSIC_DIR, "playlists")

def sanitize_name(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    return cleaned.strip() or "playlist"

def export_all_playlists():
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Find distinct playlist discovery sources
    sources = conn.execute("""
        SELECT DISTINCT discovery_source 
        FROM discovery_events 
        WHERE discovery_source LIKE 'ytm_playlist:%' OR discovery_source IN ('ytm_liked', 'ytm_library');
    """).fetchall()

    exported_count = 0

    for s in sources:
        source_name = s["discovery_source"]
        if source_name.startswith("ytm_playlist:"):
            pl_display = source_name.replace("ytm_playlist:", "")
        elif source_name == "ytm_liked":
            pl_display = "Liked Songs"
        elif source_name == "ytm_library":
            pl_display = "Library Songs"
        else:
            pl_display = source_name

        safe_filename = sanitize_name(pl_display) + ".m3u8"
        playlist_file = os.path.join(PLAYLISTS_DIR, safe_filename)

        # Get tracks in order
        query = """
            SELECT t.title, t.artist, t.duration_ms, t.local_path
            FROM discovery_events d
            JOIN tracks t ON d.track_id = t.id
            WHERE d.discovery_source = ? AND t.local_path IS NOT NULL AND t.download_status = 'completed'
            ORDER BY d.id ASC;
        """
        rows = conn.execute(query, (source_name,)).fetchall()

        if not rows:
            continue

        with open(playlist_file, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for r in rows:
                title = r["title"] or "Unknown"
                artist = r["artist"] or "Unknown"
                sec = int((r["duration_ms"] or 0) / 1000)
                rel_path = os.path.relpath(r["local_path"], PLAYLISTS_DIR).replace("\\", "/")
                f.write(f"#EXTINF:{sec},{artist} - {title}\n")
                f.write(f"{rel_path}\n")

        exported_count += 1
        print(f"Exported playlist '{pl_display}' ({len(rows)} cached tracks) -> {playlist_file}")

    conn.close()
    print(f"Total playlists exported: {exported_count}")

if __name__ == "__main__":
    export_all_playlists()
