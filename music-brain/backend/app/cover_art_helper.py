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

def fetch_musicbrainz_metadata_and_cover(artist: str, title: str, album: str = None) -> tuple[bytes | None, dict]:
    """
    Queries MusicBrainz API for canonical recording metadata and Front Cover Art
    from the Cover Art Archive (CAA).
    Returns (image_bytes_or_none, metadata_dict).
    """
    meta = {
        "mb_track_id": None,
        "album": None,
        "date": None,
        "genre": None
    }
    
    try:
        # Search recordings by primary artist and cleaned track title
        query_kw = {"artist": artist, "recording": title}
        res = musicbrainzngs.search_recordings(limit=5, **query_kw)
        recordings = res.get("recording-list", [])
        if not recordings:
            return None, meta
            
        # Prioritize matching release
        for r in recordings:
            rec_id = r.get("id")
            if not meta["mb_track_id"]:
                meta["mb_track_id"] = rec_id
                
            # Tags / Genres
            tag_list = r.get("tag-list", [])
            if tag_list and not meta["genre"]:
                meta["genre"] = tag_list[0].get("name", "").capitalize()
                
            releases = r.get("release-list", [])
            # Try to match user-provided album name first if present
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
                
                if not meta["album"] and rel_title:
                    meta["album"] = rel_title
                if not meta["date"] and rel_date:
                    meta["date"] = rel_date
                    
                # Query Cover Art Archive
                try:
                    img_bytes = musicbrainzngs.get_image_front(rel_id)
                    if img_bytes:
                        # Normalize and ensure image is valid RGB JPEG
                        im = Image.open(BytesIO(img_bytes))
                        im.load()
                        buf = BytesIO()
                        im.convert("RGB").save(buf, format="JPEG", quality=95)
                        return buf.getvalue(), meta
                except Exception:
                    # 404 Not Found in Cover Art Archive, continue trying next release
                    continue
                    
    except Exception as e:
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

def fetch_strict_cover(artist: str, title: str, album: str, source_id: str, out_path: str) -> tuple[bytes | None, dict]:
    """
    Tier 1: Query MusicBrainz + Cover Art Archive for official high-resolution album front cover and metadata.
    Tier 2: Fall back to high-res YouTube video thumbnail cropped square.
    Saves cover image to out_path.
    """
    # 1. Try MusicBrainz Cover Art Archive
    img_data, meta = fetch_musicbrainz_metadata_and_cover(artist, title, album)
    source_used = "MusicBrainz Cover Art Archive" if img_data else None
    
    # 2. Fallback to YouTube cropped thumbnail
    if not img_data and source_id:
        img_data = fetch_youtube_cropped_cover(source_id)
        if img_data:
            source_used = "YouTube Video Thumbnail (Square Cropped)"
            
    if img_data and out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(img_data)
            
    meta["cover_source"] = source_used
    return img_data, meta
