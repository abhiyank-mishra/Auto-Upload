"""
Channel Manager Module.

Manages the list of YouTube channels, fetches their video catalogs,
and picks random videos for processing (excluding already-processed ones).
"""

import os
import json
import random
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("shorts_engine")

DEFAULT_CHANNELS_FILE = "channels.json"


def load_channels(channels_path: str = None) -> list[dict]:
    """
    Load channel list from JSON file.

    Expected format:
    {
        "channels": [
            {"url": "https://www.youtube.com/@ChannelName", "name": "Channel Name"},
            {"url": "https://www.youtube.com/c/AnotherChannel", "name": "Another Channel"}
        ]
    }
    """
    if not channels_path:
        project_root = Path(__file__).resolve().parent.parent
        channels_path = str(project_root / DEFAULT_CHANNELS_FILE)

    if not os.path.exists(channels_path):
        logger.error(f"  ❌ Channels file not found: {channels_path}")
        logger.error(f"     Create {DEFAULT_CHANNELS_FILE} with your channel URLs")
        return []

    try:
        with open(channels_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        channels = data.get("channels", [])
        logger.info(f"  📋 Loaded {len(channels)} channel(s) from config")
        return channels
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"  ❌ Failed to load channels: {e}")
        return []


def fetch_channel_videos(channel_url: str, max_videos: int = 100) -> list[dict]:
    """
    Fetch all video entries from a YouTube channel using yt-dlp.

    Returns list of dicts: [{"id": "VIDEO_ID", "title": "...", "duration": 123, "url": "..."}]
    """
    logger.info(f"  🔍 Fetching video list from: {channel_url}")

    # Use /videos tab to get uploads
    url = channel_url.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"

    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--playlist-end", str(max_videos),
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]
    if os.path.exists("cookies.txt"):
        cmd.extend(["--cookies", "cookies.txt"])
    cmd.append(url)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )

        if result.returncode != 0:
            logger.error(f"  ❌ yt-dlp failed: {result.stderr[:300]}")
            return []

        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                vid = {
                    "id": entry.get("id", ""),
                    "title": entry.get("title", "Unknown"),
                    "duration": entry.get("duration") or 0,
                    "url": f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                }
                # Only include videos > 3 minutes (worth splitting)
                if vid["duration"] and vid["duration"] > 180:
                    videos.append(vid)
            except json.JSONDecodeError:
                continue

        logger.info(f"  📊 Found {len(videos)} videos (>3min) from channel")
        return videos

    except subprocess.TimeoutExpired:
        logger.error("  ❌ Channel fetch timed out (120s)")
        return []
    except Exception as e:
        logger.error(f"  ❌ Channel fetch error: {e}")
        return []


def pick_random_channel_and_video(channels: list[dict],
                                   processed_ids: list[str]) -> tuple[dict | None, dict | None]:
    """
    Pick a random channel, then a random unprocessed video from it.

    Args:
        channels:      List of channel dicts from load_channels()
        processed_ids: List of video IDs already processed

    Returns:
        (channel_dict, video_dict) or (None, None) if nothing available
    """
    if not channels:
        logger.error("  ❌ No channels configured")
        return None, None

    # Shuffle channels and try each until we find an unprocessed video
    shuffled = random.sample(channels, len(channels))

    for channel in shuffled:
        channel_url = channel.get("url", "")
        channel_name = channel.get("name", channel_url)
        logger.info(f"\n  🎲 Trying channel: {channel_name}")

        videos = fetch_channel_videos(channel_url)
        if not videos:
            logger.warning(f"  ⚠️  No eligible videos found, trying next channel...")
            continue

        # Filter out already-processed videos
        available = [v for v in videos if v["id"] not in processed_ids]
        if not available:
            logger.warning(f"  ⚠️  All videos from {channel_name} already processed, trying next...")
            continue

        # Pick random video
        chosen = random.choice(available)
        logger.info(f"  🎯 Selected: \"{chosen['title']}\" ({chosen['duration']}s)")
        return channel, chosen

    logger.error("  ❌ No unprocessed videos available across all channels")
    return None, None


def download_full_video(video_url: str, output_dir: str) -> str | None:
    """
    Download a full YouTube video to output_dir using yt-dlp.

    Returns path to downloaded file, or None on failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "source.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--no-playlist",
        "--concurrent-fragments", "4",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]
    if os.path.exists("cookies.txt"):
        cmd.extend(["--cookies", "cookies.txt"])
    cmd.append(video_url)

    logger.info(f"  ⬇️  Downloading full video...")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,  # 30 min timeout
            encoding="utf-8", errors="replace",
        )

        if result.returncode != 0:
            logger.error(f"  ❌ Download failed: {result.stderr[:500]}")
            return None

        # Find the downloaded file
        source_file = os.path.join(output_dir, "source.mp4")
        if os.path.exists(source_file):
            size_mb = os.path.getsize(source_file) / (1024 * 1024)
            logger.info(f"  ✅ Downloaded: {size_mb:.1f} MB")
            return source_file

        # Fallback: look for any mp4 in output_dir
        for f in os.listdir(output_dir):
            if f.endswith(".mp4") and f.startswith("source"):
                path = os.path.join(output_dir, f)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                logger.info(f"  ✅ Downloaded: {size_mb:.1f} MB ({f})")
                return path

        logger.error("  ❌ Download completed but file not found")
        return None

    except subprocess.TimeoutExpired:
        logger.error("  ❌ Download timed out (30 min)")
        return None
    except Exception as e:
        logger.error(f"  ❌ Download error: {e}")
        return None
