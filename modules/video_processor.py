"""
Video Processor Module — Pro Vertical Conversion.

Converts landscape (16:9) clips to mobile-optimized vertical Shorts (9:16) using:
  1. Blurred background fill (no black bars)
  2. Smart center-weighted partial crop
  3. Bold text overlay (title + part number)
  4. Auto logo overlay (top-right, semi-transparent)
  5. Real-time FFmpeg progress bar
"""

import os
import re
import sys
import platform
import logging
import subprocess
import json
import threading

logger = logging.getLogger("highlight_extractor")

# ── Defaults ──────────────────────────────────────────────────────────────
TARGET_W = 1080
TARGET_H = 1920

# How much of the source width to keep for the foreground (0.0–1.0).
# 0.55 = crop 45% from sides → main content fills ~57% height → rest is blurred bg.
# Lower = more zoom/crop, Higher = smaller foreground + more blurred bg.
CROP_FACTOR = 0.55

# Gaussian blur sigma for the background layer (higher = more blurry)
BLUR_SIGMA = 40

# Logo settings
LOGO_SCALE = 0.045        # 4.5% of output width ≈ 49px
LOGO_OPACITY = 0.85       # 85% opaque
LOGO_MARGIN = 25          # px from edge

# Text overlay settings
TEXT_FONT_SIZE = 42       # Font size in pixels
TEXT_BORDER_W = 3         # Black border thickness for readability
TEXT_Y_POSITION = 0.06    # 6% from top


def _find_system_font() -> str | None:
    """Find a bold system font path for FFmpeg drawtext."""
    candidates = []
    if platform.system() == "Windows":
        candidates = [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
        ]
    elif platform.system() == "Darwin":
        candidates = [
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:  # Linux
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            return path.replace("\\", "/").replace(":", r"\\:")
    return None


def _get_video_info(video_path):
    """Get video width, height, and duration using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-show_format",
            "-select_streams", "v:0",
            video_path
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            fmt = data.get("format", {})
            w, h, dur = None, None, None
            if streams:
                w = streams[0].get("width")
                h = streams[0].get("height")
                dur_str = streams[0].get("duration")
                if dur_str:
                    dur = float(dur_str)
            if not dur and fmt:
                dur_str = fmt.get("duration")
                if dur_str:
                    dur = float(dur_str)
            return w, h, dur
    except Exception as e:
        logger.debug(f"ffprobe failed: {e}")
    return None, None, None


def _ffmpeg_progress_thread(process, total_duration, clip_num, total_clips):
    """Monitor ffmpeg stderr for progress and render a live bar."""
    bar_len = 30
    # Match both standard time=... and out_time=...
    pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d+)")

    while True:
        line = process.stderr.readline()
        if not line:
            break
        match = pattern.search(line)
        if match:
            h, m, s, ms = match.groups()
            # Handle variable length ms (ffmpeg standard is 2 digits, progress is 6 digits)
            current = int(h) * 3600 + int(m) * 60 + int(s) + float(f"0.{ms}")
            if total_duration and total_duration > 0:
                progress = min(current / total_duration, 1.0)
                pct = progress * 100
                filled = int(bar_len * progress)
                bar = "█" * filled + "░" * (bar_len - filled)
                sys.stdout.write(
                    f"\r  📱 Clip {clip_num}/{total_clips}: [{bar}] {pct:5.1f}%  "
                )
                sys.stdout.flush()

    # Clear line
    sys.stdout.write("\r" + " " * 70 + "\r")
    sys.stdout.flush()


def _build_filter_complex(src_w, src_h, logo_path=None, title_text=None):
    """
    Build the FFmpeg filter_complex string.

    Architecture:
    ┌─────────────────────────────────────────┐
    │         [ Title - Part X ]              │
    │  Blurred background (fills 1080x1920)   │
    │  ┌─────────────────────────────────┐    │
    │  │                                 │    │
    │  │   Main content (partial crop)   │    │
    │  │                                 │    │
    │  └─────────────────────────────────┘    │
    │                              [logo] ──► │
    └─────────────────────────────────────────┘
    """
    filters = []

    # ── Layer 1: Blurred background ───────────────────────────────────────
    # Scale source to FILL the 9:16 frame (will crop sides), then blur.
    filters.append(
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},"
        f"gblur=sigma={BLUR_SIGMA}[bg]"
    )

    # ── Layer 2: Foreground (smart partial crop) ──────────────────────────
    if src_w and src_h and src_w > src_h:
        # Landscape source → partial center crop
        crop_w = int(src_w * CROP_FACTOR)
        # Ensure even dimensions
        crop_w = crop_w + (crop_w % 2)
        crop_x = (src_w - crop_w) // 2

        filters.append(
            f"[0:v]crop={crop_w}:{src_h}:{crop_x}:0,"
            f"scale={TARGET_W}:-2[fg]"
        )
        logger.info(f"  ✂️  Smart crop: keeping center {int(CROP_FACTOR*100)}% "
                     f"({crop_w}x{src_h}), sides removed: {crop_x}px each")
    else:
        # Already vertical or square → just scale to fit width
        filters.append(
            f"[0:v]scale={TARGET_W}:-2[fg]"
        )
        logger.info("  📱 Source is vertical/square, scaling to fit")

    # ── Composite: overlay foreground centered on background ──────────────
    has_logo = logo_path and os.path.exists(logo_path)
    has_text = bool(title_text)

    # Determine output label chain: bg+fg → text → logo
    current_label = "comp"
    filters.append(f"[bg][fg]overlay=(W-w)/2:(H-h)/2[{current_label}]")

    # ── Layer 3: Text overlay ─────────────────────────────────────────────
    if has_text:
        font_path = _find_system_font()
        # Escape special characters for FFmpeg drawtext
        safe_text = title_text.replace("'", "\\'").replace('"', '\\"')
        safe_text = safe_text.replace(":", r"\\:").replace("%", r"%%")

        text_filter = f"drawtext=text='{safe_text}'"
        if font_path:
            text_filter += f":fontfile='{font_path}'"
        text_filter += (
            f":fontsize={TEXT_FONT_SIZE}"
            f":fontcolor=white"
            f":borderw={TEXT_BORDER_W}"
            f":bordercolor=black"
            f":x=(w-text_w)/2"               # Center horizontally
            f":y=h*{TEXT_Y_POSITION}"         # Near top
            f":box=1"
            f":boxcolor=black@0.4"
            f":boxborderw=12"
        )

        next_label = "titled" if has_logo else None
        if next_label:
            filters.append(f"[{current_label}]{text_filter}[{next_label}]")
            current_label = next_label
        else:
            filters.append(f"[{current_label}]{text_filter}")

        logger.info(f"  📝 Text overlay: \"{title_text}\"")

    # ── Layer 4: Logo overlay ─────────────────────────────────────────────
    if has_logo:
        logo_w = max(32, int(TARGET_W * LOGO_SCALE))
        opacity_val = LOGO_OPACITY

        filters.append(
            f"[1:v]scale={logo_w}:-1,"
            f"format=rgba,"
            f"colorchannelmixer=aa={opacity_val}[logo]"
        )
        filters.append(
            f"[{current_label}][logo]overlay=W-w-{LOGO_MARGIN}:{LOGO_MARGIN}"
        )
        logger.info(f"  🏷️  Logo overlay: {logo_w}px wide, {int(opacity_val*100)}% opacity, top-right")
    elif not has_text:
        # No logo AND no text: remove the label bracket from comp
        filters[-1] = "[bg][fg]overlay=(W-w)/2:(H-h)/2"

    return ";".join(filters)


def convert_to_shorts(input_path, output_path, target_width=1080, target_height=1920,
                       clip_num=1, total_clips=1, title_text=None):
    """
    Convert a landscape clip to vertical Shorts with blurred background + logo.

    Pipeline:
      1. Create blurred, zoomed background filling 1080x1920
      2. Partial center-crop the foreground (keep ~55% width)
      3. Overlay foreground centered on blurred background
      4. Add bold text overlay if title_text provided
      5. Add semi-transparent logo (if configured)
      6. Encode H.264/AAC at high quality

    Args:
        input_path:   Path to the raw landscape clip
        output_path:  Path for the output vertical Shorts clip
        target_width:  Output width  (default 1080)
        target_height: Output height (default 1920)
        clip_num:     Current clip index (for progress bar)
        total_clips:  Total number of clips (for progress bar)

    Returns:
        Path to the converted video, or None if failed.
    """
    if not os.path.exists(input_path):
        logger.error(f"  ❌ Input not found: {input_path}")
        return None

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Detect source resolution and duration
    src_w, src_h, src_duration = _get_video_info(input_path)
    if not src_w or not src_h:
        logger.warning("  ⚠️  Could not detect dimensions, assuming 1920x1080")
        src_w, src_h = 1920, 1080

    logger.info(f"  📐 Source: {src_w}x{src_h} → Target: {target_width}x{target_height}")

    # Check for logo
    from modules.config import Config
    logo_path = None
    if Config.has_logo():
        logo_path = str(Config.get_logo_path())

    # Build the filter complex
    filter_complex = _build_filter_complex(src_w, src_h, logo_path, title_text=title_text)

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y"]

    # Input 0: source video
    cmd.extend(["-i", input_path])

    # Input 1: logo (if exists)
    if logo_path and os.path.exists(logo_path):
        cmd.extend(["-i", logo_path])

    # Filter complex + encoding
    cmd.extend([
        "-filter_complex", filter_complex,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-r", "30",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-progress", "pipe:2",
        output_path
    ])

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace"
        )

        # Progress monitoring thread
        progress_thread = threading.Thread(
            target=_ffmpeg_progress_thread,
            args=(process, src_duration, clip_num, total_clips),
            daemon=True
        )
        progress_thread.start()

        # Wait for completion (timeout 10 min)
        return_code = process.wait(timeout=600)
        progress_thread.join(timeout=5)

        if return_code == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            out_w, out_h, _ = _get_video_info(output_path)
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"  ✅ Shorts ready: {out_w}x{out_h}, {size_mb:.1f} MB "
                         f"(blurred bg + {'logo' if logo_path else 'no logo'})")
            return output_path
        else:
            logger.error(f"  ❌ Conversion failed (exit code: {return_code})")
            # Dump last few lines of stderr for debugging
            return None

    except subprocess.TimeoutExpired:
        process.kill()
        logger.error("  ❌ Conversion timed out (10 min limit)")
        return None
    except Exception as e:
        logger.error(f"  ❌ Conversion error: {e}")
        return None
