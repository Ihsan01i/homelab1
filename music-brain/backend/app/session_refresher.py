import os
import sys
import time
import json
import hashlib
import subprocess

BASE_DIR = "/opt/music-brain"
PROFILE_DIR = os.path.join(BASE_DIR, ".browser_profile")
COOKIES_TXT = "/tmp/cookies_extracted.txt"
BROWSER_JSON = os.path.join(BASE_DIR, "browser.json")
YTDLP_BIN = os.path.join(BASE_DIR, ".venv/bin/yt-dlp")
CHROMIUM_BIN = "/usr/bin/chromium"

def wake_headless_browser(seconds: int = 8):
    """
    Spawns Headless Chromium for a few seconds to let Google client-side JavaScript
    automatically refresh rolling session tokens (__Secure-1PSIDTS) in SQLite.
    Zero user interaction required.
    """
    if not os.path.exists(PROFILE_DIR) or not os.path.exists(CHROMIUM_BIN):
        return

    cmd = [
        CHROMIUM_BIN,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        f"--user-data-dir={PROFILE_DIR}",
        "https://music.youtube.com"
    ]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(seconds)
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    except Exception as e:
        print(f"[SessionRefresher] Headless wake notice: {e}")

def refresh_session_from_profile(wake_first: bool = True) -> bool:
    """
    Extracts live Google/YouTube session cookies from the persistent local
    Chromium profile on CT 102 and writes a fully valid browser.json with
    proper triple SAPISIDHASH signatures.
    """
    if not os.path.exists(PROFILE_DIR):
        return False

    if wake_first:
        wake_headless_browser(seconds=8)

    cmd = [
        YTDLP_BIN,
        "--cookies-from-browser", f"chromium:{PROFILE_DIR}",
        "--cookies", COOKIES_TXT,
        "--simulate", "https://music.youtube.com"
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"[SessionRefresher] Failed to extract cookies from profile: {e}")
        return False

    if not os.path.exists(COOKIES_TXT):
        return False

    cookies = {}
    try:
        with open(COOKIES_TXT, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain, flag, path, secure, expiry, name, val = parts[:7]
                    if ".youtube.com" in domain or "youtube.com" in domain:
                        cookies[name] = val
    finally:
        try:
            os.remove(COOKIES_TXT)
        except Exception:
            pass

    sapisid = cookies.get("SAPISID", "")
    if not sapisid:
        return False

    ts = str(int(time.time()))
    origin = "https://music.youtube.com"
    sapisid1p = cookies.get("__Secure-1PAPISID", sapisid)
    sapisid3p = cookies.get("__Secure-3PAPISID", sapisid)

    h0 = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    h1 = hashlib.sha1(f"{ts} {sapisid1p} {origin}".encode()).hexdigest()
    h3 = hashlib.sha1(f"{ts} {sapisid3p} {origin}".encode()).hexdigest()

    auth_str = f"SAPISIDHASH {ts}_{h0}_u SAPISID1PHASH {ts}_{h1}_u SAPISID3PHASH {ts}_{h3}_u"

    cookie_order = [
        "VISITOR_INFO1_LIVE", "LOGIN_INFO", "PREF", "YSC", "HSID", "SSID", "APISID", "SAPISID",
        "__Secure-1PAPISID", "__Secure-3PAPISID", "SID", "__Secure-1PSID", "__Secure-3PSID",
        "__Secure-1PSIDTS", "__Secure-3PSIDTS", "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC"
    ]

    ordered_cookies = []
    for k in cookie_order:
        if k in cookies:
            ordered_cookies.append(f"{k}={cookies[k]}")
    for k, v in cookies.items():
        if k not in cookie_order:
            ordered_cookies.append(f"{k}={v}")

    cookie_str = "; ".join(ordered_cookies)

    browser_data = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/json",
        "X-Goog-AuthUser": "0",
        "x-origin": "https://music.youtube.com",
        "authorization": auth_str,
        "cookie": cookie_str
    }

    with open(BROWSER_JSON, "w", encoding="utf-8") as f:
        json.dump(browser_data, f, indent=2)

    return True

if __name__ == "__main__":
    if refresh_session_from_profile():
        print("Successfully refreshed browser.json from local Chromium profile.")
    else:
        print("Failed to refresh session.")
