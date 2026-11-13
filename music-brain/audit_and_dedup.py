#!/usr/bin/env python3
import sqlite3
import re
import os
import shutil
import subprocess
from collections import defaultdict

NAVI_DB = "/var/lib/navidrome/navidrome.db"
MB_DB = "/opt/music-brain/music_brain.db"
MUSIC_DIR = "/opt/music-brain/music"
PLAYLISTS_DIR = "/opt/music-brain/music/playlists"
NAVIDROME_BIN = "/opt/navidrome/navidrome"
NAVIDROME_CONF = "/etc/navidrome/navidrome.toml"

def clean_noise(text):
    if not text:
        return ""
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = re.sub(r'"[^"]*"', '', text)
    text = re.sub(r'“[^”]*”', '', text)
    text = re.sub(r'[\(\[](?:from\s+.*?|soundtrack|ost|single|ep)[\)\]]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[\(\[](?:feat\.?|ft\.?)\s+.*?[\)\]]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[\(\[](?:official\s+)?(?:music\s+video|video|mv|visualizer|audio|lyric\s+video|lyrics|slowed|tiktok\s+mashup|no\s+rap|tiktok\s+version|color\s+coded.*)[\)\]]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\|\|.*', '', text)
    text = re.sub(r'[^\w\s]', ' ', text, flags=re.UNICODE).lower()
    return re.sub(r'\s+', ' ', text).strip()

def score_track(t):
    score = 0
    suf = t['suffix'].lower()
    path = t['path'].lower()
    album = (t['album'] or "").lower()
    title = (t['title'] or "").lower()
    artist = (t['artist'] or "").lower()
    
    if suf == 'flac':
        score += 1000
    elif suf == 'opus':
        score += 500
        
    bad_channels = [
        '7clouds', 'unique vibes', 'chilltracks', 'superblyrics', 'rachel gardner', 
        'ilocuu', 'latinnow', 'a paradise bird', 'the vibe guide', 'the vibe music', 
        'syrebralvibes', 'taj tracks', '7th heaven', 'ashutosh garg', 'blissful mind', 
        '1million dance studio', 'airwave trends', 'aura melodies', 'billion stars', 
        'chino', 'creative chaos', 'dip n eat', 'ethereal music', 'eurovision', 
        'petals', 'rap city', 'rare vibes', 'sunshineee', 'wavemusic', 
        'alexsierroffic', 'carlyraejepsen', 'lilnasx'
    ]
    if any(bc in path or bc in artist for bc in bad_channels):
        score -= 500
        
    if 'lyrics' in path or 'lyrics' in title or 'lyrics' in album:
        score -= 300
    if re.search(r'[𐀀-􏿿]', t['title'] or ""):
        score -= 100
    if 'soundtrack' in album or 'motion picture' in album:
        score += 200
        
    return score

def run_dedup():
    conn = sqlite3.connect(NAVI_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, title, artist, album, path, duration, suffix, bit_rate FROM media_file WHERE missing = 0")
    rows = c.fetchall()

    def get_interps(r):
        a = r['artist'] or ""
        t = r['title'] or ""
        pairs = set()
        pairs.add((clean_noise(a), clean_noise(t)))
        if " - " in t:
            parts = t.split(" - ", 1)
            pairs.add((clean_noise(parts[0]), clean_noise(parts[1])))
        if "||" in t:
            parts = t.split("||", 1)
            pairs.add((clean_noise(parts[1]), clean_noise(parts[0])))
        return [(x, y) for x, y in pairs if x and y]

    track_list = [{"row": dict(r), "interps": get_interps(r)} for r in rows]

    clusters_map = defaultdict(set)
    for i in range(len(track_list)):
        for j in range(i + 1, len(track_list)):
            t1 = track_list[i]['row']
            t2 = track_list[j]['row']
            dur1 = t1['duration'] or 0
            dur2 = t2['duration'] or 0
            diff = abs(dur1 - dur2)
            if diff > 6.0:
                continue

            matched = False
            for a1, title1 in track_list[i]['interps']:
                if len(title1) < 3:
                    continue
                for a2, title2 in track_list[j]['interps']:
                    if len(title2) < 3:
                        continue
                    if title1 == title2 and (a1 == a2 or a1 in a2 or a2 in a1):
                        matched = True
                        break
                if matched:
                    break

            if matched:
                clusters_map[title1].add(t1['id'])
                clusters_map[title1].add(t2['id'])

    if not clusters_map:
        print("[Audit & Dedup] Vault is 100% clean: 0 duplicate clusters found.")
        conn.close()
        return 0

    print(f"[Audit & Dedup] Found {len(clusters_map)} duplicate clusters to resolve...")
    mb_conn = sqlite3.connect(MB_DB)
    mb_c = mb_conn.cursor()

    purged = 0
    id_to_row = {r['id']: dict(r) for r in rows}

    for cluster_name, ids in clusters_map.items():
        cluster = [id_to_row[i] for i in ids if i in id_to_row]
        if len(cluster) < 2:
            continue

        scored = sorted([(score_track(t), t) for t in cluster], key=lambda x: x[0], reverse=True)
        winner = scored[0][1]
        losers = [x[1] for x in scored[1:]]

        win_id = winner['id']
        win_path = os.path.join(MUSIC_DIR, winner['path'])
        mb_win = mb_c.execute("SELECT id FROM tracks WHERE local_path LIKE ?", (f"%{winner['path']}%",)).fetchone()
        mb_win_id = mb_win[0] if mb_win else None

        for loser in losers:
            l_id = loser['id']
            loser_path = os.path.join(MUSIC_DIR, loser['path'])
            print(f"  Deduping: '{loser['artist']} - {loser['title']}' -> '{winner['artist']} - {winner['title']}'")

            # Merge annotations
            l_ann = c.execute("SELECT play_count FROM annotation WHERE item_id = ?", (l_id,)).fetchall()
            for ann in l_ann:
                w_ann = c.execute("SELECT id, play_count FROM annotation WHERE item_id = ?", (win_id,)).fetchone()
                if w_ann:
                    new_pc = (w_ann['play_count'] or 0) + (ann['play_count'] or 0)
                    c.execute("UPDATE annotation SET play_count = ? WHERE id = ?", (new_pc, w_ann['id']))
                else:
                    c.execute("UPDATE annotation SET item_id = ? WHERE item_id = ?", (win_id, l_id))
            c.execute("DELETE FROM annotation WHERE item_id = ?", (l_id,))

            # Transfer playlists
            l_pts = c.execute("SELECT playlist_id FROM playlist_tracks WHERE media_file_id = ?", (l_id,)).fetchall()
            for pt in l_pts:
                already = c.execute("SELECT 1 FROM playlist_tracks WHERE playlist_id = ? AND media_file_id = ?", (pt['playlist_id'], win_id)).fetchone()
                if not already:
                    c.execute("UPDATE playlist_tracks SET media_file_id = ? WHERE playlist_id = ? AND media_file_id = ?", (win_id, pt['playlist_id'], l_id))
                else:
                    c.execute("DELETE FROM playlist_tracks WHERE playlist_id = ? AND media_file_id = ?", (pt['playlist_id'], l_id))

            # Update MB tracks
            mb_loser = mb_c.execute("SELECT id FROM tracks WHERE local_path LIKE ?", (f"%{loser['path']}%",)).fetchone()
            if mb_loser:
                l_mb_id = mb_loser[0]
                if mb_win_id:
                    mb_c.execute("UPDATE discovery_events SET track_id = ? WHERE track_id = ?", (mb_win_id, l_mb_id))
                    mb_c.execute("DELETE FROM tracks WHERE id = ?", (l_mb_id,))
                else:
                    mb_c.execute("UPDATE tracks SET local_path = ? WHERE id = ?", (win_path, l_mb_id))

            # Update m3u8 files
            if os.path.exists(PLAYLISTS_DIR):
                for pf in os.listdir(PLAYLISTS_DIR):
                    if pf.endswith('.m3u8'):
                        full_p = os.path.join(PLAYLISTS_DIR, pf)
                        try:
                            with open(full_p, 'r', encoding='utf-8') as f:
                                cont = f.read()
                            rel_loser = os.path.relpath(loser_path, PLAYLISTS_DIR).replace('\\', '/')
                            rel_win = os.path.relpath(win_path, PLAYLISTS_DIR).replace('\\', '/')
                            if rel_loser in cont:
                                cont = cont.replace(rel_loser, rel_win)
                                with open(full_p, 'w', encoding='utf-8') as f:
                                    f.write(cont)
                        except Exception:
                            pass

            # Remove physical file
            if os.path.exists(loser_path):
                try:
                    os.remove(loser_path)
                except Exception:
                    pass

            c.execute("DELETE FROM media_file WHERE id = ?", (l_id,))
            purged += 1

    conn.commit()
    conn.close()
    mb_conn.commit()
    mb_conn.close()

    print(f"[Audit & Dedup] Purged {purged} duplicate files.")
    try:
        mb_c.execute("INSERT INTO system_logs (module, level, message) VALUES ('VAULT_AUDIT', 'SUCCESS', ?)", (f"Audit & Dedup pass completed: {purged} duplicate files purged.",))
        mb_conn.commit()
    except Exception:
        pass
    try:
        subprocess.run([NAVIDROME_BIN, "scan", "--configfile", NAVIDROME_CONF], capture_output=True)
    except Exception:
        pass
    return purged

if __name__ == '__main__':
    run_dedup()
