"""
Video Splitter Module.

Splits a full-length video into sequential parts of 60-110 seconds each.
Uses stream-copy for speed (no re-encoding during split).
"""

import os
import json
import random
import logging
import subprocess

logger = logging.getLogger("shorts_engine")

# Duration range for each part (seconds)
MIN_PART_DURATION = 60
MAX_PART_DURATION = 110


def get_video_duration(video_path: str) -> float | None:
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            dur_str = data.get("format", {}).get("duration")
            if dur_str:
                return float(dur_str)
    except Exception as e:
        logger.debug(f"ffprobe duration failed: {e}")
    return None


def _calculate_split_points(total_duration: float,
                             min_dur: int = MIN_PART_DURATION,
                             max_dur: int = MAX_PART_DURATION) -> list[tuple[float, float]]:
    """
    Calculate split points with randomized durations.

    Returns list of (start_time, end_time) tuples.
    """
    splits = []
    current = 0.0

    while current < total_duration:
        remaining = total_duration - current

        # If remaining is less than minimum, merge with previous
        if remaining < min_dur:
            if splits:
                prev_start, _ = splits[-1]
                splits[-1] = (prev_start, total_duration)
            break

        # Randomize duration within range
        dur = random.uniform(min_dur, min(max_dur, remaining))

        # If remaining after this clip would be too short, take all remaining
        if remaining - dur < min_dur:
            dur = remaining

        end = min(current + dur, total_duration)
        splits.append((current, end))
        current = end

    return splits


def split_video(source_path: str, output_dir: str,
                min_duration: int = MIN_PART_DURATION,
                max_duration: int = MAX_PART_DURATION) -> list[dict]:
    """
    Split a video into sequential parts.

    Uses stream-copy (no re-encoding) for maximum speed.
    Each part gets a randomized duration between min_duration and max_duration.

    Args:
        source_path:  Path to the full source video
        output_dir:   Directory to save the parts
        min_duration: Minimum part duration (seconds)
        max_duration: Maximum part duration (seconds)

    Returns:
        List of dicts: [{"index": 1, "path": "/path/to/part1.mp4", "start": 0, "end": 75, "duration": 75}]
    """
    if not os.path.exists(source_path):
        logger.error(f"  ❌ Source not found: {source_path}")
        return []

    os.makedirs(output_dir, exist_ok=True)

    # Get total duration
    total_duration = get_video_duration(source_path)
    if not total_duration:
        logger.error("  ❌ Could not determine video duration")
        return []

    logger.info(f"  ⏱️  Video duration: {total_duration:.0f}s ({total_duration/60:.1f} min)")

    # Calculate split points
    splits = _calculate_split_points(total_duration, min_duration, max_duration)
    total_parts = len(splits)
    logger.info(f"  ✂️  Splitting into {total_parts} parts ({min_duration}-{max_duration}s each)")

    parts = []
    for idx, (start, end) in enumerate(splits, 1):
        duration = end - start
        part_path = os.path.join(output_dir, f"part{idx:02d}_raw.mp4")

        logger.info(f"  📎 Part {idx}/{total_parts}: "
                     f"{_fmt_time(start)} → {_fmt_time(end)} ({duration:.0f}s)")

        # Use ffmpeg stream-copy for speed
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", source_path,
            "-t", str(duration),
            "-c", "copy",              # Stream copy — no re-encode
            "-avoid_negative_ts", "1",
            "-movflags", "+faststart",
            part_path,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )

            if result.returncode == 0 and os.path.exists(part_path) and os.path.getsize(part_path) > 0:
                size_mb = os.path.getsize(part_path) / (1024 * 1024)
                parts.append({
                    "index": idx,
                    "path": part_path,
                    "start": start,
                    "end": end,
                    "duration": duration,
                    "size_mb": round(size_mb, 1),
                })
            else:
                logger.error(f"  ❌ Part {idx} split failed")

        except subprocess.TimeoutExpired:
            logger.error(f"  ❌ Part {idx} split timed out")
        except Exception as e:
            logger.error(f"  ❌ Part {idx} split error: {e}")

    logger.info(f"  ✅ Split complete: {len(parts)}/{total_parts} parts created")
    return parts


def _fmt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
