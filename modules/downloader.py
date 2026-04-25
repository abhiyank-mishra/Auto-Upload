"""
Video Downloader Module.

Handles fetching video metadata and downloading video segments.
Strategy:
  - For single clips: try direct yt-dlp section download, fallback to full+trim
  - For multi-clips: download full video ONCE, extract all clips via ffmpeg (fastest)
"""

import os
import re
import sys
import json
import logging
import subprocess
import shutil
import threading
import time
from typing import Any

from colorama import Fore, Style

logger = logging.getLogger("highlight_extractor")


def check_dependencies() -> dict[str, str | None]:
    """Check that required external tools are installed."""
    tools = {}
    for name in ["yt-dlp", "ffmpeg"]:
        path = shutil.which(name)
        tools[name] = path
        if path:
            logger.debug(f"{name} found: {path}")
        else:
            logger.error(f"{name} not found.")
    return tools


def fetch_video_info(url: str) -> dict[str, Any]:
    """Fetch video metadata without downloading using yt-dlp."""
    logger.info("📡 Fetching video metadata...")
    cmd = ["yt-dlp", "--dump-json", "--no-download", "--no-playlist", "--no-warnings", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0:
            err = result.stderr.strip()
            if "Private video" in err or "Sign in" in err:
                raise ValueError("This video is private or requires authentication.")
            elif "Video unavailable" in err:
                raise ValueError("This video is unavailable.")
            else:
                raise RuntimeError(f"yt-dlp error: {err}")

        info = json.loads(result.stdout)
        title = info.get("title", "Unknown")
        duration = info.get("duration", 0)
        views = info.get("view_count", 0)

        logger.info(f"📹 Title:    {title}")
        logger.info(f"⏱️  Duration: {_format_duration(duration)}")
        logger.info(f"👁️  Views:    {views:,}" if views else "👁️  Views:    N/A")
        logger.info(f"📊 Heatmap:  {'Available ✅' if info.get('heatmap') else 'Not available ❌'}")
        logger.info(f"📑 Chapters: {'Available ✅' if info.get('chapters') else 'Not available ❌'}")
        return info

    except subprocess.TimeoutExpired:
        raise RuntimeError("Timed out while fetching video info (60s limit).")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse yt-dlp output: {e}")


def _get_concurrent_fragments():
    try:
        from modules.config import Config
        return Config.CONCURRENT_FRAGMENTS
    except Exception:
        return 4


# ─── Real-time Progress Display ──────────────────────────────────────────────

def _stream_process_output(process, label="Downloading"):
    """Read and display subprocess output in real-time with a progress bar."""
    progress_re = re.compile(r'(\d+\.?\d*)%')
    speed_re = re.compile(r'(?:at|speed)\s+([\d.]+\S*/s)')
    eta_re = re.compile(r'ETA\s+(\S+)')
    last_draw = [0.0]

    def _read(stream):
        try:
            for raw in iter(stream.readline, ''):
                line = raw.strip()
                if not line:
                    continue
                pct_m = progress_re.search(line)
                if pct_m:
                    pct = float(pct_m.group(1))
                    now = time.time()
                    # Throttle redraws to max 5/sec to avoid console flooding
                    if now - last_draw[0] < 0.2 and pct < 100:
                        continue
                    last_draw[0] = now
                    spd = speed_re.search(line)
                    eta = eta_re.search(line)
                    _draw_progress_bar(pct, spd.group(1) if spd else "",
                                       eta.group(1) if eta else "", label)
                elif "[download]" in line.lower() or "[merger]" in line.lower():
                    sys.stdout.write(f"\r{' ' * 90}\r")
                    icon = '⬇️' if 'download' in line.lower() else '🔗'
                    sys.stdout.write(f"  {Fore.CYAN}{icon}{Style.RESET_ALL} {line[:80]}\n")
                    sys.stdout.flush()
                elif "frame=" in line:
                    now = time.time()
                    if now - last_draw[0] < 0.5:
                        continue
                    last_draw[0] = now
                    sys.stdout.write(f"\r  {Fore.YELLOW}✂️  {Style.RESET_ALL} {line[:80]}")
                    sys.stdout.flush()
        except (ValueError, OSError):
            pass

    t1 = threading.Thread(target=_read, args=(process.stdout,), daemon=True)
    t2 = threading.Thread(target=_read, args=(process.stderr,), daemon=True)
    t1.start()
    t2.start()
    process.wait()
    t1.join(timeout=3)
    t2.join(timeout=3)
    sys.stdout.write(f"\r{' ' * 90}\r")
    sys.stdout.flush()


def _draw_progress_bar(percent, speed="", eta="", label="Downloading"):
    bar_w = 30
    filled = int(bar_w * percent / 100)
    empty = bar_w - filled
    if percent >= 80:
        col = Fore.GREEN
    elif percent >= 40:
        col = Fore.YELLOW
    else:
        col = Fore.CYAN
    bar = f"{col}{'█' * filled}{Fore.WHITE + Style.DIM}{'░' * empty}{Style.RESET_ALL}"
    parts = [f"{percent:5.1f}%"]
    if speed:
        parts.append(f"⚡{speed}")
    if eta:
        parts.append(f"⏳{eta}")
    sys.stdout.write(f"\r  ⬇️  [{bar}] {' │ '.join(parts)}  ")
    sys.stdout.flush()


# ─── Download Functions ───────────────────────────────────────────────────────

def download_segment(url, start_time, end_time, output_path, video_info=None):
    """Download a specific segment of the video (single-clip mode)."""
    duration = end_time - start_time
    logger.info(f"⬇️  Downloading segment: {_fmt(start_time)} → {_fmt(end_time)} ({duration:.0f}s)")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    base, ext = os.path.splitext(output_path)
    if not ext:
        ext = ".mp4"
        output_path = base + ext

    # Strategy 1: Direct section download (no --concurrent-fragments to avoid hang)
    logger.info("  📌 Strategy 1: Direct segment download (fast)...")
    if _try_segment_download(url, start_time, end_time, output_path):
        actual = _find_downloaded_file(os.path.dirname(output_path), output_path)
        if actual:
            if actual != output_path:
                try:
                    os.rename(actual, output_path)
                except OSError:
                    output_path = actual
            logger.info(f"✅ Segment downloaded ({_file_size(output_path)})")
            return output_path

    # Strategy 2: Full download + FFmpeg trim
    logger.info("  📌 Strategy 2: Full download + FFmpeg trim...")
    return _download_and_trim(url, start_time, end_time, output_path)


def download_multi_clips(url, peaks, output_dir, video_info=None):
    """
    Download multiple clips efficiently.

    Strategy: Download full video ONCE, then extract all clips via ffmpeg.
    This is much faster than multiple partial downloads for 3-5 clips.
    """
    from modules.analyzer import EngagementPeak
    os.makedirs(output_dir, exist_ok=True)

    results = {}
    frags = _get_concurrent_fragments()
    temp_dir = os.path.join(output_dir, ".temp_download")
    os.makedirs(temp_dir, exist_ok=True)
    temp_video = os.path.join(temp_dir, "full_video.mp4")

    try:
        # Download full video once (fast with concurrent fragments)
        logger.info("  ⬇️  Downloading full video (one-time, for multi-clip extraction)...")
        dl_cmd = [
            "yt-dlp", "--no-playlist",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--concurrent-fragments", str(frags),
            "-o", temp_video, "--newline", "--no-warnings", url
        ]
        p = subprocess.Popen(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
        _stream_process_output(p, label="Full Video")
        if p.returncode != 0:
            raise RuntimeError("Full download failed")

        actual = _find_downloaded_file(temp_dir, temp_video)
        if not actual:
            raise RuntimeError("Downloaded file not found")

        logger.info(f"  📦 Full video downloaded: {_file_size(actual)}")

        # Extract each clip via ffmpeg (very fast, no re-download)
        for i, peak in enumerate(peaks, 1):
            clip_path = os.path.join(output_dir, f"clip{i}_raw.mp4")
            logger.info(f"\n  ✂️  Extracting clip {i}/{len(peaks)}: {_fmt(peak.start_time)} → {_fmt(peak.end_time)}")
            dur = peak.end_time - peak.start_time

            # Use stream copy for speed — exact keyframes aren't critical for 60s clips
            trim_cmd = [
                "ffmpeg", "-y",
                "-ss", str(peak.start_time),
                "-i", actual,
                "-t", str(dur),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                clip_path
            ]
            tp = subprocess.Popen(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace")
            _stream_process_output(tp, label=f"Clip {i}")

            if tp.returncode == 0 and os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                logger.info(f"  ✅ Clip {i} extracted ({_file_size(clip_path)})")
                results[i] = clip_path
            else:
                # Fallback: re-encode if stream copy failed
                logger.info(f"  ⚠️  Stream copy failed for clip {i}, re-encoding...")
                trim_cmd_enc = [
                    "ffmpeg", "-y",
                    "-ss", str(peak.start_time),
                    "-i", actual,
                    "-t", str(dur),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    "-avoid_negative_ts", "make_zero",
                    clip_path
                ]
                tp2 = subprocess.Popen(trim_cmd_enc, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8", errors="replace")
                _stream_process_output(tp2, label=f"Clip {i}")
                if tp2.returncode == 0 and os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                    logger.info(f"  ✅ Clip {i} extracted ({_file_size(clip_path)})")
                    results[i] = clip_path
                else:
                    logger.error(f"  ❌ Clip {i} extraction failed")

    except Exception as e:
        logger.error(f"  ❌ Multi-clip download failed: {e}")
    finally:
        # Always clean up full video
        if os.path.exists(temp_dir):
            logger.info("  🧹 Cleaning up temporary files...")
            shutil.rmtree(temp_dir, ignore_errors=True)

    return results


def _try_segment_download(url, start, end, output_path):
    """Try direct yt-dlp segment download (no concurrent fragments to avoid hang)."""
    section = f"*{start:.1f}-{end:.1f}"
    cmd = [
        "yt-dlp", "--no-playlist",
        "--download-sections", section,
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_path, "--newline", "--no-warnings", url
    ]
    try:
        t0 = time.time()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
        _stream_process_output(p, label="Segment")
        elapsed = time.time() - t0
        if p.returncode == 0:
            logger.info(f"  ⏱️  Downloaded in {elapsed:.1f}s")
        return p.returncode == 0
    except Exception as e:
        logger.debug(f"Segment download failed: {e}")
        return False


def _download_and_trim(url, start, end, output_path):
    """Download full video then extract segment via ffmpeg."""
    temp_dir = os.path.join(os.path.dirname(output_path) or ".", ".temp_download")
    os.makedirs(temp_dir, exist_ok=True)
    temp_video = os.path.join(temp_dir, "full_video.mp4")
    frags = _get_concurrent_fragments()
    try:
        logger.info("⬇️  Downloading full video (temporary)...")
        dl_cmd = [
            "yt-dlp", "--no-playlist",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--concurrent-fragments", str(frags),
            "-o", temp_video, "--newline", "--no-warnings", url
        ]
        p = subprocess.Popen(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
        _stream_process_output(p, label="Full Video")
        if p.returncode != 0:
            raise RuntimeError(f"Full download failed (exit code {p.returncode})")

        actual = _find_downloaded_file(temp_dir, temp_video)
        if not actual:
            raise RuntimeError("Downloaded file not found.")
        logger.info(f"📦 Full video: {_file_size(actual)}")

        logger.info(f"✂️  Trimming: {_fmt(start)} → {_fmt(end)}...")
        dur = end - start
        trim_cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-i", actual, "-t", str(dur),
            "-c", "copy", "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero", output_path
        ]
        tp = subprocess.Popen(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace")
        _stream_process_output(tp, label="Trimming")
        if tp.returncode != 0:
            raise RuntimeError("ffmpeg trimming failed")
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("Trimmed output is empty or missing.")
        logger.info(f"✅ Trimmed clip: {_file_size(output_path)}")
        return output_path
    finally:
        if os.path.exists(temp_dir):
            logger.info("🧹 Cleaning up temporary files...")
            shutil.rmtree(temp_dir, ignore_errors=True)


def _find_downloaded_file(directory, expected_path):
    """Find the actual downloaded file (yt-dlp may add suffixes)."""
    if os.path.exists(expected_path):
        return expected_path
    # Check for common yt-dlp output patterns
    base = os.path.splitext(expected_path)[0]
    for ext in ['.mp4', '.mkv', '.webm']:
        if os.path.exists(base + ext):
            return base + ext
    # Search directory for any video file
    for fname in os.listdir(directory):
        fpath = os.path.join(directory, fname)
        if os.path.isfile(fpath) and fname.endswith(('.mp4', '.mkv', '.webm')):
            return fpath
    return None


def _format_duration(seconds):
    if seconds <= 0:
        return "Unknown"
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"


def _fmt(seconds):
    from modules.utils import seconds_to_timestamp
    return seconds_to_timestamp(seconds)


def _file_size(path):
    size = os.path.getsize(path)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
