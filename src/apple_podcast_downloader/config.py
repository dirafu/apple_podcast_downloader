import os
import shutil
import sys
from pathlib import Path

import platformdirs
import requests
from dotenv import load_dotenv

APP_NAME = "apple-podcast-downloader"


def get_config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME))


def get_default_env_file() -> Path:
    return get_config_dir() / ".env"


def load_env(custom_env_file: str | None = None):
    """Load env vars with priority:
    1. Explicit --env-file
    2. OS environment variables (already in os.environ)
    3. .env in cwd (dev)
    4. .env in user config dir (standard)
    """
    if custom_env_file:
        p = Path(custom_env_file)
        if p.exists():
            load_dotenv(p, override=False)
            return
        print(f"[-] Warning: specified env file not found: {p}", file=sys.stderr)

    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=False)
        return

    default_env = get_default_env_file()
    if default_env.exists():
        load_dotenv(default_env, override=False)


def save_env(auth: str, media_token: str, device_path: str):
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / ".env"

    content = (
        "# Tokens (from DevTools: request .../podcast/hls/license/streaming/start)\n"
        f"APPLE_AUTH=\"{auth}\"\n"
        f"APPLE_MEDIA_TOKEN=\"{media_token}\"\n"
        "\n"
        "# Path to your Widevine L3 device file (.wvd)\n"
        f"APPLE_DEVICE_PATH=\"{device_path}\"\n"
    )
    env_file.write_text(content, encoding="utf-8")
    print(f"\n[+] Config saved to: {env_file}")


def validate_tokens() -> bool:
    auth = os.environ.get("APPLE_AUTH", "").strip()
    token = os.environ.get("APPLE_MEDIA_TOKEN", "").strip()

    if not auth or not token:
        print("[-] Missing APPLE_AUTH or APPLE_MEDIA_TOKEN.")
        return False

    print("    Checking token validity...")
    headers = {
        "accept": "*/*",
        "authorization": auth,
        "media-user-token": token,
        "origin": "https://podcasts.apple.com",
        "referer": "https://podcasts.apple.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-apple-client-version": "2644.7.0-external",
    }
    # Test URL: a known podcast (The Manchester Historian)
    url = "https://amp-api.podcasts.apple.com/v1/catalog/us/podcasts/1595079988?l=en-GB"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            print("[+] Tokens are valid.")
            return True
        if r.status_code in (401, 403):
            print("[-] Tokens are invalid or expired. Please re-authenticate.")
            return False
        print(f"[-] Unexpected response from Apple API: HTTP {r.status_code}")
        return False
    except Exception as e:
        print(f"[-] Failed to reach Apple API: {e}")
        return False


def configure_cli():
    print("=== Apple Podcast Downloader Setup ===")
    print("To get your tokens, open Chrome, go to podcasts.apple.com,")
    print("start playing any episode, and open DevTools (F12) -> Network.")
    print("Filter for 'streaming/start', click the request, and look at the headers.")
    print("Copy the 'authorization' header and the 'x-apple-music-user-token' header.\n")

    auth = input("Enter authorization (Bearer ...): ").strip()
    token = input("Enter x-apple-music-user-token (0....): ").strip()
    device_path = input("Enter path to your .wvd device file: ").strip().strip('"').strip("'")

    if not auth or not token or not device_path:
        print("[-] All fields are required.")
        return

    device_path = os.path.expanduser(device_path)
    if not os.path.isfile(device_path):
        print(f"[-] File not found: {device_path}")
        return

    config_dir = get_config_dir()
    target_path = config_dir / "device.wvd"
    
    # Copy .wvd to config dir for portability
    if os.path.abspath(device_path) != os.path.abspath(str(target_path)):
        choice = input(f"\nCopy .wvd file to {target_path}? [Y/n]: ").strip().lower()
        if choice in ("", "y", "yes"):
            config_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(device_path, target_path)
            device_path = str(target_path)
            print(f"[+] Copied to: {target_path}")

    os.environ["APPLE_AUTH"] = auth
    os.environ["APPLE_MEDIA_TOKEN"] = token
    os.environ["APPLE_DEVICE_PATH"] = device_path

    if validate_tokens():
        save_env(auth, token, device_path)
    else:
        print("[-] Tokens did not validate. Not saving.")
