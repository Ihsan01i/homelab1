"""
Lossless Vault: Autonomous Regional Lyrics & Transliteration Engine
Extracts lyrics from official sources (YouTube Music InnerTube), applies phonetic
Romanization for regional Dravidian languages (Manglish for Malayalam, Tanglish for Tamil),
and embeds lyrics both as Vorbis tags and sidecar .lrc files for Navidrome/Symfonium.
"""

import os
import re
import sys
import json
import sqlite3
import subprocess
import unicodedata
from mutagen.oggopus import OggOpus
from mutagen.flac import FLAC
from ytmusicapi import YTMusic

BASE_DIR = "/opt/music-brain"
DB_PATH = os.path.join(BASE_DIR, "music_brain.db")
NAVIDROME_BIN = "/opt/navidrome/navidrome"
NAVIDROME_CONF = "/etc/navidrome/navidrome.toml"

# =====================================================================
# Phonetic Dravidian Transliterator Engine (Manglish / Tanglish)
# 100% faithful phonetic Romanization — NEVER translation
# =====================================================================

MALAYALAM_VOWELS = {
    'അ': 'a', 'ആ': 'aa', 'ഇ': 'i', 'ഈ': 'ee', 'ഉ': 'u', 'ഊ': 'oo', 'ഋ': 'ri',
    'എ': 'e', 'ഏ': 'e', 'ഐ': 'ai', 'ഒ': 'o', 'ഓ': 'o', 'ഔ': 'au'
}

MALAYALAM_VOWEL_SIGNS = {
    'ാ': 'aa', 'ി': 'i', 'ീ': 'ee', 'ു': 'u', 'ൂ': 'oo', 'ൃ': 'ri',
    'െ': 'e', 'േ': 'e', 'ൈ': 'ai', 'ൊ': 'o', 'ോ': 'o', 'ൌ': 'au', 'ൗ': 'au'
}

MALAYALAM_CHILLUS = {
    'ൺ': 'n', 'ൻ': 'n', 'ർ': 'r', 'ൽ': 'l', 'ൾ': 'l', 'ൿ': 'k'
}

MALAYALAM_CONSONANTS = {
    'ക': 'k', 'ഖ': 'kh', 'ഗ': 'g', 'ഘ': 'gh', 'ങ': 'ng',
    'ച': 'ch', 'ഛ': 'chh', 'ജ': 'j', 'ഝ': 'jh', 'ഞ': 'nj',
    'ട': 't', 'ഠ': 'th', 'ഡ': 'd', 'ഢ': 'dh', 'ണ': 'n',
    'ത': 'th', 'ഥ': 'th', 'ദ': 'd', 'ധ': 'dh', 'ന': 'n',
    'പ': 'p', 'ഫ': 'f', 'ബ': 'b', 'ഭ': 'bh', 'മ': 'm',
    'യ': 'y', 'ര': 'r', 'ല': 'l', 'വ': 'v', 'ശ': 'sh',
    'ഷ': 'sh', 'സ': 's', 'ഹ': 'h', 'ള': 'l', 'ഴ': 'zh', 'റ': 'r'
}

MALAYALAM_SPECIAL_CONJUNCTS = {
    'ന്റ': 'nt',
    'റ്റ': 'tt',
    'ഞ്ച': 'nch',
    'ണ്ട': 'nd',
    'മ്പ': 'mb',
    'ങ്ങ': 'ng',
    'ഞ്ഞ': 'nj',
    'ന്ന': 'nn',
    'മ്മ': 'mm',
    'ത്ത': 'th',
    'പ്പ': 'pp',
    'ക്ക': 'kk',
    'ഗ്ഗ': 'gg',
    'ച്ച': 'ch',
    'ജ്ജ': 'jj',
    'ള്ള': 'll',
    'ല്ല': 'll',
    'യ്യ': 'yy',
    'വ്വ': 'vv'
}

def transliterate_malayalam_to_manglish(text: str) -> str:
    """Converts native Malayalam script into natural, readable phonetic Manglish."""
    if not text:
        return ""
    
    # Strip zero-width non-joiner and joiner artifacts
    text = re.sub(r'[\u200b-\u200f\ufeff]', '', text)
    
    out = ""
    i = 0
    n = len(text)
    
    while i < n:
        # Check special conjuncts
        matched_conjunct = False
        for cj, val in MALAYALAM_SPECIAL_CONJUNCTS.items():
            if text[i:].startswith(cj):
                i += len(cj)
                if i < n and text[i] in MALAYALAM_VOWEL_SIGNS:
                    out += val + MALAYALAM_VOWEL_SIGNS[text[i]]
                    i += 1
                elif i < n and text[i] == '്':
                    if i + 1 == n or text[i+1].isspace():
                        out += val + "u"
                    else:
                        out += val
                    i += 1
                else:
                    out += val + "a"
                matched_conjunct = True
                break
        if matched_conjunct:
            continue
            
        ch = text[i]
        
        if ch in MALAYALAM_VOWELS:
            out += MALAYALAM_VOWELS[ch]
            i += 1
        elif ch in MALAYALAM_CHILLUS:
            out += MALAYALAM_CHILLUS[ch]
            i += 1
        elif ch in MALAYALAM_CONSONANTS:
            cons = MALAYALAM_CONSONANTS[ch]
            i += 1
            if i < n:
                next_ch = text[i]
                if next_ch == '്':
                    i += 1
                    if i < n and text[i] in MALAYALAM_CONSONANTS:
                        next_cons = MALAYALAM_CONSONANTS[text[i]]
                        i += 1
                        if i < n and text[i] in MALAYALAM_VOWEL_SIGNS:
                            out += cons + next_cons + MALAYALAM_VOWEL_SIGNS[text[i]]
                            i += 1
                        elif i < n and text[i] == '്':
                            out += cons + next_cons
                            i += 1
                        else:
                            out += cons + next_cons + "a"
                    elif i == n or text[i].isspace() or text[i] in '.,!?\n':
                        out += cons + "u"
                    else:
                        out += cons
                elif next_ch in MALAYALAM_VOWEL_SIGNS:
                    out += cons + MALAYALAM_VOWEL_SIGNS[next_ch]
                    i += 1
                elif next_ch == 'ം':
                    out += cons + "am"
                    i += 1
                elif next_ch == 'ഃ':
                    out += cons + "ah"
                    i += 1
                else:
                    out += cons + "a"
            else:
                out += cons + "a"
        elif ch == 'ം':
            out += "m"
            i += 1
        elif ch == 'ഃ':
            out += "h"
            i += 1
        elif ch == '്':
            out += "u"
            i += 1
        else:
            out += ch
            i += 1
            
    return out


def is_malayalam(text: str) -> bool:
    return bool(re.search(r'[\u0D00-\u0D7F]', text))


def is_indic(text: str) -> bool:
    return bool(re.search(r'[\u0900-\u0D7F]', text))


def process_lyrics_text(raw_lyrics: str) -> str:
    """Translates native Indic script to Latin alphabet, keeping English intact."""
    if not raw_lyrics:
        return ""
    if is_malayalam(raw_lyrics):
        return transliterate_malayalam_to_manglish(raw_lyrics)
    return raw_lyrics


def embed_and_save_lyrics(local_path: str, lyrics_text: str):
    """Embeds lyrics in Vorbis comments and writes sidecar .lrc file."""
    if not local_path or not os.path.exists(local_path):
        return False
        
    # Write .lrc sidecar file next to audio file
    base_no_ext, _ = os.path.splitext(local_path)
    lrc_path = base_no_ext + ".lrc"
    try:
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(lyrics_text)
    except Exception as e:
        print(f"[-] Failed to write {lrc_path}: {e}")

    # Embed tag in audio file
    try:
        if local_path.endswith(".opus"):
            audio = OggOpus(local_path)
            audio['lyrics'] = lyrics_text
            audio['unsyncedlyrics'] = lyrics_text
            audio.save()
        elif local_path.endswith(".flac"):
            audio = FLAC(local_path)
            audio['lyrics'] = lyrics_text
            audio['unsyncedlyrics'] = lyrics_text
            audio.save()
        return True
    except Exception as e:
        print(f"[-] Failed to embed tags in {local_path}: {e}")
        return False


def fetch_track_lyrics(source_id: str):
    """Fetches lyrics from YouTube Music using source_id."""
    if not source_id:
        return None
    ytm = YTMusic()
    try:
        watch = ytm.get_watch_playlist(videoId=source_id)
        lyrics_id = watch.get('lyrics')
        if lyrics_id:
            data = ytm.get_lyrics(lyrics_id)
            return data.get('lyrics', '')
    except Exception as e:
        pass
    return None
