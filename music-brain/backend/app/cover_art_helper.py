import os
import re
import urllib.request
from PIL import Image

def clean_artist_names(raw_artist: str) -> tuple[str, str]:
    if not raw_artist:
        return "Unknown Artist", "Unknown Artist"
    if raw_artist.strip().lower() == "again&again":
        return "again&again", "again&again"
    a = re.sub(r'\s*-\s*Topic\b', '', raw_artist, flags=re.IGNORECASE)
    a = re.sub(r'\bTopic\b', '', a, flags=re.IGNORECASE)
    a = re.sub(r'\s*VEVO\b', '', a, flags=re.IGNORECASE).strip()
    
    parts = [p.strip() for p in re.split(r'[,;/]|\bfeat\.?\b|\bft\.?\b', a, flags=re.IGNORECASE) if p.strip()]
    if not parts:
        return "Unknown Artist", "Unknown Artist"
    primary = parts[0]
    if primary.lower() in ["a.r. rahman", "a. r. rahman", "ar rahman", "a r rahman"]:
        primary = "A. R. Rahman"
    elif primary.lower() == "shaan rahman":
        primary = "Shaan Rahman"
    elif primary.lower() == "twenty one pilots":
        primary = "Twenty One Pilots"
        
    full_artist = ", ".join(parts)
    return full_artist, primary

def clean_track_title(title: str, primary_artist: str) -> str:
    if not title:
        return "Unknown Title"
    t = title.strip()
    if primary_artist and t.lower().startswith(f"{primary_artist.lower()} - "):
        t = t[len(primary_artist) + 3:].strip()
    t = re.sub(r'\s*\((?:Official\s+(?:Music\s+)?Video|Official\s+Audio|Lyric\s+Video|Audio)\)', '', t, flags=re.IGNORECASE).strip()
    t = re.sub(r'\s*\[(?:Official\s+(?:Music\s+)?Video|Official\s+Audio|Lyric\s+Video|Audio)\]', '', t, flags=re.IGNORECASE).strip()
    return t or title

def fetch_strict_cover(artist, title, album, source_id, out_path):
    if not source_id:
        return None
    urls = [
        f"https://i.ytimg.com/vi_webp/{source_id}/maxresdefault.webp",
        f"https://i.ytimg.com/vi/{source_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{source_id}/hqdefault.jpg"
    ]
    im = None
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                im = Image.open(resp)
                im.load()
                break
        except Exception:
            continue
            
    if not im:
        return None
        
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = im.crop((left, top, left + side, top + side))
    
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cropped.convert("RGB").save(out_path, quality=95)
    
    with open(out_path, "rb") as f:
        return f.read()
