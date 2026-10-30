# WARNING!!!
AI-generated software slop, use at your own risk!

# Apple Podcast Downloader

A CLI tool to archive Apple-hosted (HLS/Widevine) podcasts and exclusive episodes. 

Apple wraps its exclusive or paid podcast content in Widevine DRM and serves it via HLS streams, making it impossible to download with standard RSS-based tools. This utility handles the license acquisition, decryption, and metadata tagging automatically.

## Features

- **Batch downloading**: Download an entire podcast feed or specific episodes.
- **Flexible episode selection**: Use Python-style slicing (e.g., `1..5`, `-5..`, `1,3,5`).
- **Automatic metadata tagging**: Embeds title, artist, album, cover art, and description into the `.m4a` file.
- **Sequential numbering**: Files are named chronologically (`001 - Name.m4a`), even if the show has a messed-up episode numbering.
- **Live progress bar**: Shows download speed, percentage, and ETA (requires `pywinpty` on Windows).
- **Resumable**: Simply re-run the command to skip already downloaded episodes.

## Prerequisites

1. **Python 3.10+**
2. **External binaries** (must be in your system `PATH`):
   - [`n_m3u8DL-RE`](https://github.com/nilaoda/N_m3u8DL-RE/releases)
   - `mp4decrypt` (from [Bento4](https://www.bento4.com/downloads/)) OR `shaka-packager`
   - [`ffmpeg`](https://ffmpeg.org/download.html)
3. **A Widevine L3 device file (`.wvd`)** to acquire DRM keys.

## Installation

The recommended way to install the tool is via `pipx`, which creates an isolated environment and makes the `apple-podcast` command available globally.

```bash
git clone https://github.com/yourusername/apple-podcast-downloader.git
cd apple-podcast-downloader
pipx install .
```

## Setup

Before downloading, you need to configure your Apple tokens and the path to your `.wvd` file. The tool will guide you through it:

```bash
apple-podcast configure
```

You will be asked to provide:
1. **Authorization Token** (starts with `Bearer ...`)
2. **Media User Token** (starts with `0.`)
3. **Path to your `.wvd` file**

*How to get the tokens:*
1. Open Chrome and go to [podcasts.apple.com](https://podcasts.apple.com).
2. Start playing any episode.
3. Open DevTools (F12) -> **Network** tab.
4. Filter for `streaming/start`.
5. Click the request, find the `authorization` and `x-apple-music-user-token` headers in the Request Headers section, and copy their values.

The configuration will be saved to your system's standard config directory (e.g., `%APPDATA%\apple-podcast-downloader\.env` on Windows).

## Usage

### Download a whole show
```bash
apple-podcast fetch "https://podcasts.apple.com/us/podcast/my-cool-proprietary-podcast/id7777777777"
```

### Download specific episodes
```bash
# Download the first 5 episodes
apple-podcast fetch "<url>" --episodes 1..5

# Download the last 5 episodes
apple-podcast fetch "<url>" --episodes -5..

# Download a specific episode from its page URL
apple-podcast fetch "https://podcasts.apple.com/us/podcast/self-complacent-episode/id7777777777?i=1000000000042"
```

### Download from a raw m3u8 URL
If you already have the direct HLS playlist URL (with `drmUsed=widevine`), you can use the `dl` command:
```bash
apple-podcast dl "https://play.itunes.apple.com/WebObjects/MZPlay.woa/podcast/hls/playlist.m3u8?..."
```

### Advanced Options
- `--quality N`: Select audio quality (0 = best, default).
- `--out DIR`: Specify an output directory.
- `--no-metadata`: Skip embedding ID3 tags (cover art, title, etc.).
- `--force`: Re-download episodes even if they already exist.
- `--dry-run`: List episodes without actually downloading them.

## Disclaimer

This tool is provided for educational and personal archival purposes only. Downloading DRM-protected content may violate Apple's Terms of Service or copyright laws in your jurisdiction. Use it at your own risk. The authors are not responsible for any misuse or legal consequences.