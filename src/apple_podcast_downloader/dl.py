#!/usr/bin/env python3
# dl.py
#
# Library + CLI for downloading a single Apple Podcasts episode by m3u8 URL.

import base64
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH

# ---------- CONSTANTS ----------

LICENSE_URL = (
    "https://play.itunes.apple.com/WebObjects/MZPlay.woa"
    "/podcast/hls/license/streaming/start"
)
AMP_BASE = "https://amp-api.podcasts.apple.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
WIDEVINE_UUID = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"


# ---------- HELPERS ----------

def _get_device_path() -> str:
    return os.environ.get("APPLE_DEVICE_PATH", "").strip()


def _get_auth_headers() -> dict:
    auth = os.environ.get("APPLE_AUTH", "").strip()
    token = os.environ.get("APPLE_MEDIA_TOKEN", "").strip()
    if not auth or not token:
        raise RuntimeError("Missing APPLE_AUTH or APPLE_MEDIA_TOKEN. Run 'apple-podcast configure'.")
    return {
        "authorization": auth,
        "x-apple-music-user-token": token,
        "x-apple-renewal": "true",
        "accept": "application/json",
        "content-type": "application/json",
        "origin": "https://podcasts.apple.com",
        "referer": "https://podcasts.apple.com/",
        "user-agent": USER_AGENT,
    }


def check_external_deps():
    """Check for required external binaries and raise RuntimeError if missing."""
    missing = []
    
    if not shutil.which("n_m3u8DL-RE"):
        missing.append("n_m3u8DL-RE (https://github.com/nilaoda/N_m3u8DL-RE/releases)")
        
    if not shutil.which("mp4decrypt") and not shutil.which("shaka-packager"):
        missing.append("mp4decrypt (https://www.bento4.com/downloads/) or shaka-packager (https://github.com/shaka-project/shaka-packager)")
        
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg (https://ffmpeg.org/download.html)")
        
    if missing:
        msg = "Missing external dependencies required for downloading and decrypting:\n"
        for m in missing:
            msg += f"    * {m}\n"
        msg += "Please install them and add them to your PATH."
        raise RuntimeError(msg)


def _validate_env():
    path = _get_device_path()
    if not path:
        raise RuntimeError(
            "APPLE_DEVICE_PATH is not set. Run 'apple-podcast configure' "
            "or add APPLE_DEVICE_PATH=/path/to/device.wvd to your .env file."
        )
    if not os.path.exists(path):
        raise RuntimeError(f".wvd not found: {path}")


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "")
    name = name.strip().rstrip(".")
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "untitled"


def _normalize_hls_url(url: str) -> str:
    if "drmUsed=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + "drmUsed=widevine&xapsub=accepts-css"


def _parse_attr_list(s: str) -> dict:
    attrs = {}
    parts, cur, in_q = [], "", False
    for c in s:
        if c == '"':
            in_q = not in_q
            cur += c
        elif c == "," and not in_q:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    if cur:
        parts.append(cur)
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            attrs[k.strip()] = v.strip().strip('"')
    return attrs


def _parse_master(text: str, base_url: str) -> list[dict]:
    variants = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = _parse_attr_list(line[len("#EXT-X-STREAM-INF:"):])
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
                j += 1
            if j < len(lines):
                variants.append({
                    "bandwidth": int(attrs.get("BANDWIDTH") or 0),
                    "avg_bandwidth": int(attrs.get("AVERAGE-BANDWIDTH") or 0),
                    "codecs": attrs.get("CODECS", ""),
                    "score": float(attrs.get("SCORE") or 0),
                    "url": urljoin(base_url, lines[j].strip()),
                })
                i = j
        i += 1
    return variants


def _extract_widevine_pssh(variant_text: str) -> str | None:
    for line in variant_text.splitlines():
        if not line.startswith("#EXT-X-KEY:"):
            continue
        if WIDEVINE_UUID not in line:
            continue
        m = re.search(r'URI="([^"]+)"', line)
        if not m:
            continue
        uri = m.group(1)
        if ";base64," in uri:
            return uri.split(";base64,", 1)[1]
    return None


def _get_widevine_key(pssh_b64: str, adam_id: str) -> tuple[str, str]:
    device = Device.load(_get_device_path())
    cdm = Cdm.from_device(device)
    session_id = cdm.open()
    try:
        pssh = PSSH(pssh_b64)
        challenge = cdm.get_license_challenge(session_id, pssh)
        payload = {
            "version": 1,
            "license-requests": [{
                "id": 2,
                "adam-id": adam_id,
                "challenge": base64.b64encode(challenge).decode(),
                "key-system": "com.widevine.alpha",
                "uri": f"data:text/plain;base64,{pssh_b64}",
            }],
        }
        
        r = requests.post(LICENSE_URL, json=payload, headers=_get_auth_headers(), timeout=30)
        r.raise_for_status()
        data = r.json()
        lic = None
        for key in ("license-responses", "license-requests"):
            for item in data.get(key, []) or []:
                if item.get("license"):
                    if item.get("status", 0) != 0:
                        raise RuntimeError(f"status={item.get('status')}: {item}")
                    lic = item["license"]
                    break
            if lic:
                break
        if not lic:
            raise RuntimeError(f"no license in response: {data}")
        cdm.parse_license(session_id, lic)
        for k in cdm.get_keys(session_id):
            if k.type == "CONTENT":
                return k.kid.hex, k.key.hex()
        raise RuntimeError("no CONTENT key in license")
    finally:
        cdm.close(session_id)

# ---------- METADATA ----------

def _apply_metadata(file_path: str, meta: dict):
    try:
        from mutagen.mp4 import MP4, MP4Cover
    except ImportError:
        print("    ! mutagen not installed, skipping metadata")
        return

    try:
        audio = MP4(file_path)
        
        if meta.get("title"): audio["\xa9nam"] = meta["title"]
        if meta.get("album"): audio["\xa9alb"] = meta["album"]
        if meta.get("artist"): audio["\xa9ART"] = meta["artist"]
        if meta.get("date"): audio["\xa9day"] = meta["date"]
        if meta.get("genre"): audio["\xa9gen"] = meta["genre"]
        if meta.get("description"): audio["desc"] = meta["description"]
        
        track_idx = meta.get("track_index")
        total_eps = meta.get("total_episodes", 0)
        if track_idx is not None:
            audio["trkn"] = [(int(track_idx), int(total_eps))]
            
        orig_ep = meta.get("original_episode_number")
        if orig_ep is not None:
            audio["\xa9grp"] = f"Apple Episode: {orig_ep}"

        cover_url = meta.get("cover_url")
        if cover_url:
            try:
                r = requests.get(cover_url, timeout=15)
                if r.status_code == 200:
                    audio["covr"] = [MP4Cover(r.content, imageformat=MP4Cover.FORMAT_JPEG)]
            except Exception:
                pass
                
        audio.save()
    except Exception as e:
        print(f"    ! failed to apply metadata: {e}")


# ---------- PROGRESS (PTY-based) ----------

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
_PROGRESS_RE = re.compile(
    r'(\d+)/(\d+)\s+([\d.]+)%'
    r'(?:\s+[\d.]+[KMG]?B/[\d.]+[KMG]?B)?'
    r'(?:\s+([\d.]+[KMG]?Bps))?'
    r'(?:\s+([\d:]+))?'
)

def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _format_progress(m) -> str:
    segs, total, pct = m.group(1), m.group(2), m.group(3)
    speed = m.group(4)
    eta = m.group(5)
    out = f"{pct}% ({segs}/{total}"
    if speed:
        out += f", {speed}"
    if eta:
        out += f", ETA {eta}"
    out += ")"
    return out


def _run_with_progress(cmd: list[str], prefix: str = "    ") -> tuple[bool, list[str]]:
    is_tty = sys.stdout.isatty()
    last_len = [0]
    recent: list[str] = []
    buf = ""

    def emit(msg: str, newline: bool = False):
        if is_tty and not newline:
            pad = " " * max(0, last_len[0] - len(msg))
            sys.stdout.write("\r" + msg + pad)
            sys.stdout.flush()
            last_len[0] = len(msg)
        else:
            print(msg)

    def cleanup():
        if is_tty and last_len[0]:
            sys.stdout.write("\r" + " " * last_len[0] + "\r")
            sys.stdout.flush()
            last_len[0] = 0

    def handle_line(line: str):
        line = line.strip()
        if not line:
            return
        m = _PROGRESS_RE.search(line)
        if m:
            msg = f"{prefix}downloading audio stream... {_format_progress(m)}"
            if is_tty:
                emit(msg)
        else:
            recent.append(line)
            if len(recent) > 20:
                recent.pop(0)

    def flush():
        nonlocal buf
        parts = re.split(r"[\r\n]", buf)
        for line in parts[:-1]:
            handle_line(line)
        buf = parts[-1]

    if is_tty:
        emit(f"{prefix}downloading audio stream...")
    else:
        print(f"{prefix}downloading audio stream...")

    if sys.platform == "win32":
        try:
            from winpty import PtyProcess
        except ImportError:
            cleanup()
            print(f"{prefix}(install pywinpty for live progress: pip install pywinpty)")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            tail = (proc.stdout or "").splitlines()[-20:]
            return proc.returncode == 0, tail

        try:
            pty_proc = PtyProcess.spawn(cmd, dimensions=(40, 120))
        except Exception as e:
            cleanup()
            print(f"{prefix}(pty spawn failed: {e}; falling back)")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            tail = (proc.stdout or "").splitlines()[-20:]
            return proc.returncode == 0, tail

        exit_code = 0
        try:
            while True:
                try:
                    data = pty_proc.read(4096)
                    if not data:
                        break
                    buf += _strip_ansi(data)
                    flush()
                except EOFError:
                    break
                except Exception:
                    if not pty_proc.isalive():
                        break
                    time.sleep(0.05)
        finally:
            flush()
            cleanup()
            try:
                exit_code = pty_proc.exitstatus or 0
            except Exception:
                exit_code = 0
            try:
                pty_proc.terminate()
            except Exception:
                pass
        return exit_code == 0, recent[-5:]

    import pty
    import select

    master, slave = pty.openpty()
    proc = subprocess.Popen(cmd, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    try:
        while proc.poll() is None:
            try:
                r, _, _ = select.select([master], [], [], 0.1)
            except (OSError, ValueError):
                break
            if r:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += _strip_ansi(chunk.decode("utf-8", "replace"))
                flush()
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += _strip_ansi(chunk.decode("utf-8", "replace"))
            flush()
    finally:
        flush()
        cleanup()
        try:
            os.close(master)
        except OSError:
            pass
    return proc.wait() == 0, recent[-5:]

# ---------- PUBLIC API ----------

def list_qualities(hls_url: str) -> tuple[list[dict], str | None]:
    hls_url = _normalize_hls_url(hls_url)
    sess = requests.Session()
    sess.headers.update({"user-agent": USER_AGENT})
    r = sess.get(hls_url, timeout=30)
    r.raise_for_status()
    variants = _parse_master(r.text, hls_url)
    variants.sort(key=lambda v: v["avg_bandwidth"] or v["bandwidth"], reverse=True)
    qs = parse_qs(urlparse(hls_url).query)
    adam_id = (qs.get("a") or [None])[0]
    return variants, adam_id


def download_episode(
    hls_url: str,
    quality_index: int = 0,
    out_dir: str = ".",
    name: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = True,
    metadata: dict | None = None,
    no_metadata: bool = False,
) -> tuple[str | None, str]:
    _validate_env()
    hls_url = _normalize_hls_url(hls_url)

    if not verbose:
        print("    parsing master playlist...")
    variants, adam_id = list_qualities(hls_url)
    if not variants:
        raise RuntimeError("no variants in master playlist")
    if not adam_id:
        raise RuntimeError("no a= parameter in URL")

    idx = min(max(quality_index, 0), len(variants) - 1)
    variant = variants[idx]
    bw = variant["avg_bandwidth"] or variant["bandwidth"]

    if verbose:
        print(f"[+] master: {hls_url}")
        print(f"[+] variant: {bw} bps  {variant['codecs']}")
    else:
        print(f"    selected quality: {bw} bps ({variant['codecs']})")

    if not name:
        qs = parse_qs(urlparse(hls_url).query)
        ep_id = (qs.get("id") or [None])[0]
        name = f"podcast_{ep_id}" if ep_id else "episode"
    base = sanitize_filename(name)

    out_dir = out_dir or "."
    os.makedirs(out_dir, exist_ok=True)

    for ext in (".m4a", ".mp4"):
        p = os.path.join(out_dir, base + ext)
        if os.path.exists(p):
            if not force:
                if p.endswith(".mp4"):
                    try:
                        os.replace(p, p[:-4] + ".m4a")
                        p = p[:-4] + ".m4a"
                    except OSError:
                        pass
                if verbose:
                    print(f"    = already exists: {os.path.basename(p)}")
                return None, "skipped"

    if dry_run:
        if verbose:
            print(f"    [dry-run] {variant['url']}")
        return None, "dry_run"

    check_external_deps()

    if not verbose:
        print("    fetching variant playlist...")
    sess = requests.Session()
    sess.headers.update({"user-agent": USER_AGENT})
    var_text = sess.get(variant["url"], timeout=30).text
    pssh_b64 = _extract_widevine_pssh(var_text)
    if not pssh_b64:
        raise RuntimeError("no Widevine PSSH in variant playlist")
    if verbose:
        print(f"[+] PSSH: {pssh_b64}")

    if verbose:
        print("[+] requesting license...")
    else:
        print("    getting widevine key...")
    kid_hex, key_hex = _get_widevine_key(pssh_b64, adam_id)
    if verbose:
        print(f"[+] KID: {kid_hex}")
        print(f"[+] KEY: {key_hex}")

    cmd = [
        "n_m3u8DL-RE", variant["url"],
        "-M", "format=mp4",
        "--save-name", base,
        "--save-dir", out_dir,
        "--key", f"{kid_hex}:{key_hex}",
    ]

    if verbose:
        print("[+] " + " ".join(cmd))
        subprocess.run(cmd, check=True)
    else:
        ok, recent = _run_with_progress(cmd)
        if not ok:
            tail = "\n".join(recent[-5:]) if recent else "(no output)"
            raise RuntimeError(f"n_m3u8DL-RE failed:\n{tail}")

    mp4 = os.path.join(out_dir, base + ".mp4")
    m4a = os.path.join(out_dir, base + ".m4a")
    
    final_path = None
    if os.path.exists(mp4):
        try:
            os.replace(mp4, m4a)
            final_path = m4a
        except OSError:
            final_path = mp4
    elif os.path.exists(m4a):
        final_path = m4a
    else:
        raise RuntimeError(f"output file not found: {mp4}")

    if metadata and not no_metadata:
        if not verbose:
            print("    applying metadata...")
        _apply_metadata(final_path, metadata)

    return final_path, "done"


def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    url = args[0]
    quality = None
    name = None
    out_dir = "."
    force = False
    dry_run = False
    no_metadata = False

    i = 1
    while i < len(args):
        a = args[i]
        if a == "--quality" and i + 1 < len(args):
            quality = int(args[i + 1]); i += 2
        elif a == "--name" and i + 1 < len(args):
            name = args[i + 1]; i += 2
        elif a == "--out" and i + 1 < len(args):
            out_dir = args[i + 1]; i += 2
        elif a == "--force":
            force = True; i += 1
        elif a == "--dry-run":
            dry_run = True; i += 1
        elif a == "--no-metadata":
            no_metadata = True; i += 1
        else:
            print(f"[-] unknown argument: {a}"); sys.exit(1)

    if "play.itunes.apple.com" not in url or "playlist.m3u8" not in url:
        print("[-] not an m3u8 URL. Use 'apple-podcast fetch' for show/episode pages")
        sys.exit(1)

    try:
        check_external_deps()
    except RuntimeError as e:
        print(f"[-] {e}")
        sys.exit(1)

    variants, adam_id = list_qualities(url)
    if not variants:
        print("[-] no variants in master playlist")
        sys.exit(1)

    if quality is None:
        print("\nAvailable qualities:")
        for n, v in enumerate(variants):
            bw = v["avg_bandwidth"] or v["bandwidth"]
            print(f"  [{n}] {bw:>7} bps  score={v['score']:.3f}  {v['codecs']}")
        while True:
            try:
                choice = int(input("\nSelect index: "))
                if 0 <= choice < len(variants):
                    break
            except ValueError:
                pass
            print("  invalid")
    else:
        choice = quality

    if name is None:
        default = f"podcast_{(parse_qs(urlparse(url).query).get('id') or ['episode'])[0]}"
        name = input(f"\nFile name [{default}]: ").strip() or default

    try:
        path, status = download_episode(
            url, quality_index=choice, out_dir=out_dir, name=name,
            force=force, dry_run=dry_run, verbose=True,
            no_metadata=no_metadata,
        )
    except Exception as e:
        print(f"[-] {e}")
        sys.exit(1)

    if status == "done" and path:
        print(f"\n[+] done: {path}")
    elif status == "skipped":
        print("\n[=] skipped (file already exists)")
