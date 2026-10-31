import os
import re
import urllib.request
from io import BytesIO
from PIL import Image
import musicbrainzngs

musicbrainzngs.set_useragent("MusicBrainVault", "1.0", "https://github.com/Ihsan01i/homelab1")

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

def fetch_ytm_googleusercontent_cover(thumb_url: str) -> bytes | None:
    """
    Fetches the pristine 1200x1200 official release square cover from Google's CDN.
    """
    if not thumb_url or "googleusercontent.com" not in thumb_url:
        return None
    try:
        base_url = thumb_url.split('=')[0]
        hires_url = f"{base_url}=w1200-h1200-l90-rj"
        req = urllib.request.Request(hires_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            im = Image.open(resp)
            im.load()
            w, h = im.size
            if w >= 500 and h >= 500 and 0.95 <= (w / h) <= 1.05:
                buf = BytesIO()
                im.convert("RGB").save(buf, format="JPEG", quality=95)
                return buf.getvalue()
    except Exception:
        pass
    return None

def fetch_musicbrainz_metadata_and_cover(artist: str, title: str, album: str = None) -> tuple[bytes | None, dict]:
    """
    Queries MusicBrainz API for canonical recording metadata and Front Cover Art
    from the Cover Art Archive (CAA) with STRICT release and square aspect ratio validation.
    """
    meta = {
        "mb_track_id": None,
        "album": None,
        "date": None,
        "genre": None
    }
    
    try:
        query_kw = {"artist": artist, "recording": title}
        res = musicbrainzngs.search_recordings(limit=5, **query_kw)
        recordings = res.get("recording-list", [])
        if not recordings:
            return None, meta
            
        for r in recordings:
            rec_id = r.get("id")
            if not meta["mb_track_id"]:
                meta["mb_track_id"] = rec_id
                
            tag_list = r.get("tag-list", [])
            if tag_list and not meta["genre"]:
                meta["genre"] = tag_list[0].get("name", "").capitalize()
                
            releases = r.get("release-list", [])
            # Prioritize exact album name match first
            sorted_releases = sorted(
                releases,
                key=lambda x: (
                    album and album.lower() in (x.get("title") or "").lower(),
                    x.get("status") == "Official"
                ),
                reverse=True
            )
            
            for rel in sorted_releases:
                rel_id = rel.get("id")
                rel_title = rel.get("title")
                rel_date = (rel.get("date") or "")[:4]
                
                # Guard: If album is provided and release title completely mismatches, skip
                if album and album.lower() not in (rel_title or "").lower() and (rel_title or "").lower() not in album.lower():
                    # If this is a regional song with a completely different movie album, do NOT blindly grab its cover
                    continue
                
                if not meta["album"] and rel_title:
                    meta["album"] = rel_title
                if not meta["date"] and rel_date:
                    meta["date"] = rel_date
                    
                try:
                    img_bytes = musicbrainzngs.get_image_front(rel_id)
                    if img_bytes:
                        im = Image.open(BytesIO(img_bytes))
                        im.load()
                        w, h = im.size
                        ratio = w / h if h > 0 else 0
                        # STRICT GUARD: Cover must be square (reject 600x960 movie posters)
                        if 0.95 <= ratio <= 1.05 and w >= 400 and h >= 400:
                            buf = BytesIO()
                            im.convert("RGB").save(buf, format="JPEG", quality=95)
                            return buf.getvalue(), meta
                except Exception:
                    continue
                    
    except Exception:
        pass
        
    return None, meta

def fetch_youtube_cropped_cover(source_id: str) -> bytes | None:
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
    
    buf = BytesIO()
    cropped.convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()

def fetch_strict_cover(artist: str, title: str, album: str, source_id: str, out_path: str, ytm_thumb_url: str = None) -> tuple[bytes | None, dict]:
    """
    Tier 1 (Highest Quality & Authenticity):
      YouTube Music Official Release CDN (googleusercontent.com at =w1200-h1200).
      Guaranteed official 1:1 square release cover from the record label.
    Tier 2:
      MusicBrainz Cover Art Archive (Strictly verified square aspect ratio & album match).
    Tier 3:
      YouTube Video Thumbnail cropped square (fallback for unofficial video uploads).
    """
    img_data = None
    source_used = None
    meta = {"mb_track_id": None, "album": None, "date": None, "genre": None}
    
    # 1. Tier 1: YouTube Music High-Res Official Release Cover
    if ytm_thumb_url and "googleusercontent.com" in ytm_thumb_url:
        img_data = fetch_ytm_googleusercontent_cover(ytm_thumb_url)
        if img_data:
            source_used = "YouTube Music Official Studio Artwork (1200x1200)"

    # 2. Query MusicBrainz for metadata (and cover if Tier 1 missed)
    mb_img, mb_meta = fetch_musicbrainz_metadata_and_cover(artist, title, album)
    meta.update({k: v for k, v in mb_meta.items() if v})
    
    if not img_data and mb_img:
        img_data = mb_img
        source_used = "MusicBrainz Cover Art Archive (Verified Square)"

    # 3. Tier 3: Fallback to YouTube cropped thumbnail
    if not img_data and source_id:
        img_data = fetch_youtube_cropped_cover(source_id)
        if img_data:
            source_used = "YouTube Video Thumbnail (Square Cropped Fallback)"
            
    if img_data and out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(img_data)
            
    meta["cover_source"] = source_used
    return img_data, meta
