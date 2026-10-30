#!/usr/bin/env python3
# fetch.py
#
# Batch download of an Apple Podcasts show or a single episode.

import os
import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

from .dl import (
    AMP_BASE,
    USER_AGENT,
    check_external_deps,
    download_episode,
    sanitize_filename,
)


def _get_amp_headers() -> dict:
    auth = os.environ.get("APPLE_AUTH", "").strip()
    token = os.environ.get("APPLE_MEDIA_TOKEN", "").strip()
    cookie = os.environ.get("APPLE_COOKIE", "").strip()
    
    h = {
        "accept": "*/*",
        "accept-language": "en-GB,en;q=0.9",
        "authorization": auth,
        "media-user-token": token,
        "origin": "https://podcasts.apple.com",
        "referer": "https://podcasts.apple.com/",
        "user-agent": USER_AGENT,
        "x-apple-client-version": "2644.7.0-external",
    }
    if cookie:
        h["cookie"] = cookie
    return h


def _amp_get(url: str) -> dict:
    r = requests.get(url, headers=_get_amp_headers(), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"amp-api HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _pick_hls_url(offers: list[dict]) -> str | None:
    for off in offers or []:
        if off.get("hlsUrl"):
            return off["hlsUrl"]
    return None


def parse_url(url: str) -> tuple[str, str, str | None]:
    parsed = urlparse(url)
    m = re.search(r"/([a-z]{2})/", parsed.path)
    storefront = m.group(1) if m else "us"
    m = re.search(r"id(\d+)", parsed.path)
    if not m:
        raise ValueError("cannot find show id in URL")
    show_id = m.group(1)
    episode_id = (parse_qs(parsed.query).get("i") or [None])[0]
    return storefront, show_id, episode_id


def parse_episode_range(s: str, total: int) -> list[int]:
    """Parse an index range like '1..5', '-5..', '10..', '1,3,5' into 0-based indices.
    Supports Python-like negative indexing: '5', '-1', '1..5', '-5..', '-5..-1', '10..', '1,3,5'
    """
    if not s or s.lower() == "all":
        return list(range(total))
    s = s.strip()

    def to_index(val_str: str) -> int | None:
        if not val_str:
            return None
        idx = int(val_str)
        if idx < 0:
            return total + idx
        return idx - 1

    if "," in s:
        indices = []
        for p in s.split(","):
            try:
                idx = to_index(p.strip())
                if idx is not None and 0 <= idx < total:
                    indices.append(idx)
            except ValueError:
                pass
        return sorted(list(set(indices)))

    # Range like '1..5', '-5..', '10..', '-5..-1'
    m = re.match(r'^(-?\d*)\.\.(-?\d*)$', s)
    if m:
        start_str, end_str = m.groups()
        start = to_index(start_str) if start_str else 0
        end = to_index(end_str) if end_str else total - 1
        
        start = max(0, min(start, total - 1))
        end = max(0, min(end, total - 1))
        
        if start <= end:
            return list(range(start, end + 1))
        else:
            return list(range(start, end - 1, -1))
            
    # Single episode index (e.g., '5' or '-1')
    try:
        idx = to_index(s)
        if idx is not None and 0 <= idx < total:
            return [idx]
        return []
    except ValueError:
        raise ValueError(f"invalid range: {s}")


def _episode_sort_key(e: dict):
    dt = e.get("release_date") or ""
    en = e.get("episode_number")
    return (dt, en if en is not None else 10**9)


def fetch_show_episodes(storefront: str, show_id: str) -> tuple[str, list[dict]]:
    show_name = None
    episodes: list[dict] = []
    offset = 0
    total = None
    while True:
        url = (
            f"{AMP_BASE}/v1/catalog/{storefront}/podcasts/{show_id}"
            "?include=episodes"
            "&limit[episodes]=100"
            f"&offset[episodes]={offset}"
            "&l=en-GB"
        )
        data = _amp_get(url).get("data", [])
        if not data:
            break
        show = data[0]
        if show_name is None:
            show_name = show["attributes"]["name"]
        rel = show.get("relationships", {}).get("episodes", {})
        chunk = rel.get("data", []) or []
        if total is None:
            total = rel.get("meta", {}).get("total", len(chunk))
        for ep in chunk:
            attrs = ep.get("attributes", {})
            episodes.append({
                "id": ep.get("id"),
                "name": attrs.get("name") or attrs.get("itunesTitle") or ep.get("id"),
                "episode_number": attrs.get("episodeNumber"),
                "release_date": attrs.get("releaseDateTime"),
                "hls_url": _pick_hls_url(attrs.get("offers")),
                "artist": attrs.get("artistName", ""),
                "genre": (attrs.get("genreNames") or [""])[0],
                "description": attrs.get("description", {}).get("standard", ""),
                "cover_url": (attrs.get("artwork", {}).get("url", "") or "").replace("{w}x{h}bb.{f}", "600x600bb.jpg"),
            })
        if len(episodes) >= (total or 0) or not chunk:
            break
        offset += len(chunk)

    episodes.sort(key=_episode_sort_key)
    
    if not any(ep.get("hls_url") for ep in episodes):
        raise RuntimeError(
            "This podcast does not provide HLS streams (hlsUrl is missing). "
            "It is likely a standard RSS-based podcast with direct MP3 links. "
            "This tool only supports Apple-hosted (DRM-protected) content. "
            "For standard RSS podcasts, please use tools like yt-dlp or gPodder."
        )
        
    return show_name or show_id, episodes


def fetch_single_episode(storefront: str, episode_id: str) -> tuple[str, dict]:
    url = (
        f"{AMP_BASE}/v1/catalog/{storefront}/podcast-episodes/{episode_id}"
        "?extend=fullDescription&extend[podcasts]=feedUrl"
        "&include=participants,podcast&l=en-GB"
    )
    data = _amp_get(url).get("data", [])
    if not data:
        raise RuntimeError(f"amp-api: episode {episode_id} not found")
    ep = data[0]
    attrs = ep.get("attributes", {})
    show_name = None
    try:
        show_data = ep["relationships"]["podcast"]["data"][0]
        show_name = show_data["attributes"]["name"]
    except (KeyError, IndexError):
        show_name = "podcast"
    
    hls_url = _pick_hls_url(attrs.get("offers"))
    if not hls_url:
        raise RuntimeError(
            "This episode does not provide an HLS stream (hlsUrl is missing). "
            "It is likely a standard RSS-based podcast with direct MP3 links. "
            "This tool only supports Apple-hosted (DRM-protected) content."
        )
        
    return show_name, {
        "id": ep.get("id"),
        "name": attrs.get("name") or attrs.get("itunesTitle") or ep.get("id"),
        "episode_number": attrs.get("episodeNumber"),
        "release_date": attrs.get("releaseDateTime"),
        "hls_url": hls_url,
        "artist": attrs.get("artistName", ""),
        "genre": (attrs.get("genreNames") or [""])[0],
        "description": attrs.get("description", {}).get("standard", ""),
        "cover_url": (attrs.get("artwork", {}).get("url", "") or "").replace("{w}x{h}bb.{f}", "600x600bb.jpg"),
    }


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    url = args[0]
    quality, out_dir, episode_arg = 0, None, None
    force = dry_run = no_metadata = False
    verbose: bool | None = None

    i = 1
    while i < len(args):
        a = args[i]
        if a == "--quality" and i + 1 < len(args):
            quality = int(args[i + 1]); i += 2
        elif a == "--out" and i + 1 < len(args):
            out_dir = args[i + 1]; i += 2
        elif a == "--episodes" and i + 1 < len(args):
            episode_arg = args[i + 1]; i += 2
        elif a == "--force":
            force = True; i += 1
        elif a == "--dry-run":
            dry_run = True; i += 1
        elif a == "--verbose":
            verbose = True; i += 1
        elif a == "--quiet":
            verbose = False; i += 1
        elif a == "--no-metadata":
            no_metadata = True; i += 1
        else:
            print(f"[-] unknown argument: {a}"); sys.exit(1)

    if "play.itunes.apple.com" in url and "playlist.m3u8" in url:
        print("[-] this is an m3u8 URL. Use 'apple-podcast dl' instead.")
        sys.exit(1)

    if not dry_run:
        try:
            check_external_deps()
        except RuntimeError as e:
            print(f"[-] {e}")
            sys.exit(1)

    try:
        storefront, show_id, episode_id = parse_url(url)
    except ValueError as e:
        print(f"[-] {e}"); sys.exit(1)

    print(f"[+] storefront={storefront}  show_id={show_id}"
          + (f"  episode_id={episode_id}" if episode_id else ""))

    if episode_id:
        try:
            show_name, all_episodes = fetch_show_episodes(storefront, show_id)
            idx = next((i for i, ep in enumerate(all_episodes) if ep["id"] == episode_id), None)
            if idx is not None:
                episodes = [(idx + 1, all_episodes[idx])]
            else:
                raise RuntimeError("Episode not found in the main feed")
        except Exception:
            show_name, episode = fetch_single_episode(storefront, episode_id)
            episodes = [(0, episode)]
    else:
        show_name, all_episodes = fetch_show_episodes(storefront, show_id)
        
        try:
            selected_indices = parse_episode_range(episode_arg, len(all_episodes))
        except ValueError as e:
            print(f"[-] {e}"); sys.exit(1)
            
        episodes = [(idx + 1, all_episodes[idx]) for idx in selected_indices]
        
        if episode_arg:
            print(f"[+] selected {len(episodes)} out of {len(all_episodes)} episodes")

    print(f"[+] show: {show_name}  episodes to download: {len(episodes)}")

    if out_dir is None:
        out_dir = sanitize_filename(show_name)
    print(f"[+] output dir: {out_dir}\n")

    if verbose is None:
        verbose = len(episodes) == 1

    if dry_run:
        for orig_idx, ep in episodes:
            print(f"  [{orig_idx:03d}] {ep['name']}")
        return

    os.makedirs(out_dir, exist_ok=True)
    ok = skipped = errors = 0
    total = len(episodes)

    for n, (orig_idx, ep) in enumerate(episodes, 1):
        display_name = ep["name"]
        file_name = f"{orig_idx:03d} - {ep['name']}"
        
        metadata = {
            "title": ep["name"],
            "album": show_name,
            "artist": ep.get("artist", ""),
            "date": (ep.get("release_date") or "").split("T")[0],
            "genre": ep.get("genre", ""),
            "description": ep.get("description", ""),
            "cover_url": ep.get("cover_url", ""),
            "track_index": orig_idx,
            "original_episode_number": ep.get("episode_number"),
            "total_episodes": total
        }

        print(f"[{n}/{total}] {display_name}")
        try:
            path, status = download_episode(
                ep["hls_url"] if ep.get("hls_url") else "",
                quality_index=quality,
                out_dir=out_dir,
                name=file_name,
                force=force,
                dry_run=False,
                verbose=verbose,
                metadata=metadata,
                no_metadata=no_metadata,
            )
        except KeyboardInterrupt:
            print("\n[!] interrupted by user")
            break
        except Exception as e:
            print(f"    ! {e}")
            errors += 1
            continue

        if status == "done" and path:
            print(f"    + {os.path.basename(path)}")
            ok += 1
        elif status == "skipped":
            skipped += 1
        else:
            errors += 1

    print(f"\n[+] finished: ok={ok}  skipped={skipped}  errors={errors}")


if __name__ == "__main__":
    main()