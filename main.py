#!/usr/bin/env python3
"""
YouTube Shorts Engine — Autonomous 24/7 Content Machine.
=========================================================

Modes:
  1. CHANNEL SERIES MODE (default)
     - Picks random channel → random video → splits into parts → uploads
     - Runs every 24 hours automatically

  2. TRENDING MODE
     - Uses highlight_extractor.py for engagement-based clips

Usage:
  python main.py                              # Start daemon (channel mode, 24hr cycle)
  python main.py --once                       # Run one cycle then exit
  python main.py --mode trending --url URL    # Trending mode for specific URL
  python main.py --drain-queue                # Upload remaining queued clips

Deployment:
  pm2 start main.py --interpreter python3 --name shorts-engine
  # OR: nohup python main.py > engine.log 2>&1 &
"""

import os
import sys
import time
import random
import signal
import argparse
import logging
import traceback
from pathlib import Path
from datetime import datetime, timezone

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent

# Setup logging
def setup_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)-8s] %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(
        level=level, format=fmt, datefmt=datefmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                str(PROJECT_ROOT / "engine.log"),
                encoding="utf-8", mode="a"
            ),
        ]
    )

logger = logging.getLogger("shorts_engine")

# Imports after path setup
from modules.config import Config
from modules import webhook
from modules.state import PipelineState
from modules.channel_manager import (
    load_channels, pick_random_channel_and_video, download_full_video,
)
from modules.video_splitter import split_video
from modules.video_processor import convert_to_shorts


# ── Constants ─────────────────────────────────────────────────────────────
CYCLE_INTERVAL_HOURS = 24
UPLOAD_MIN_DELAY_MIN = 20     # Minimum delay between uploads (minutes)
UPLOAD_MAX_DELAY_MIN = 120    # Maximum delay between uploads (minutes)


def print_engine_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ⚡  YouTube Shorts Engine — Autonomous Mode                ║
║      Pick → Split → Convert → Upload → Repeat Forever        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


# ── Channel Series Pipeline ──────────────────────────────────────────────

def run_channel_pipeline(state: PipelineState):
    """
    Full Channel Series Mode pipeline:
    1. Pick random channel + random unprocessed video
    2. Download full video
    3. Split into parts
    4. Convert each part to vertical Shorts
    5. Queue all parts for upload
    6. Upload with random delays
    """
    logger.info("\n" + "═" * 60)
    logger.info("🎬 CHANNEL SERIES MODE — Starting new cycle")
    logger.info("═" * 60)

    state.update_last_cycle()

    # Step 1: Pick video
    webhook.send_event("video_selection_start", "success",
                       message="Starting video selection")

    channels = load_channels()
    if not channels:
        webhook.send_event("video_selection_start", "error",
                           message="No channels configured",
                           error="channels.json is empty or missing")
        return False

    channel, video = pick_random_channel_and_video(
        channels, state.data["processed_videos"]
    )
    if not video:
        webhook.send_event("video_selection_start", "error",
                           message="No unprocessed videos available",
                           error="All videos across all channels already processed")
        return False

    video_url = video["url"]
    video_id = video["id"]
    video_title = video["title"]

    webhook.send_event("video_selection_complete", "success",
                       video_url=video_url,
                       message=f"Selected: {video_title}")

    # Step 2: Download
    work_dir = str(PROJECT_ROOT / "output" / f"series_{video_id}")
    os.makedirs(work_dir, exist_ok=True)

    webhook.send_event("video_download_start", "success",
                       video_url=video_url,
                       message=f"Downloading: {video_title}")

    source_path = download_full_video(video_url, work_dir)
    if not source_path:
        webhook.send_event("video_download_start", "error",
                           video_url=video_url,
                           message="Download failed",
                           error="yt-dlp download returned no file")
        state.record_error()
        return False

    webhook.send_event("video_download_complete", "success",
                       video_url=video_url,
                       message=f"Download complete: {os.path.getsize(source_path)/(1024*1024):.0f}MB")

    # Step 3: Split into parts
    webhook.send_event("video_split_start", "success",
                       video_url=video_url,
                       message="Splitting video into parts")

    parts = split_video(source_path, work_dir)
    if not parts:
        webhook.send_event("video_split_start", "error",
                           video_url=video_url,
                           message="Video split failed",
                           error="No parts were created")
        state.record_error()
        return False

    total_parts = len(parts)
    webhook.send_event("video_split_complete", "success",
                       video_url=video_url,
                       message=f"Split into {total_parts} parts")

    # Step 4: Convert each part to vertical Shorts + text overlay
    logger.info(f"\n  📱 Converting {total_parts} parts to vertical Shorts...")

    for part in parts:
        idx = part["index"]
        raw_path = part["path"]
        shorts_path = os.path.join(work_dir, f"part{idx:02d}_shorts.mp4")

        # Text overlay: "Title - Part X"
        # Truncate title to fit on screen
        short_title = video_title[:40] + ("..." if len(video_title) > 40 else "")
        title_text = f"{short_title} - Part {idx}"

        webhook.send_event("clip_processing_start", "success",
                           video_url=video_url, clip_index=idx,
                           message=f"Processing part {idx}/{total_parts}")

        result = convert_to_shorts(
            raw_path, shorts_path,
            clip_num=idx, total_clips=total_parts,
            title_text=title_text,
        )

        if result:
            part["shorts_path"] = result
            # Clean up raw part to save disk
            try:
                os.remove(raw_path)
            except OSError:
                pass

            webhook.send_event("clip_processing_complete", "success",
                               video_url=video_url, clip_index=idx,
                               message=f"Part {idx}/{total_parts} converted")
        else:
            webhook.send_event("clip_processing_complete", "error",
                               video_url=video_url, clip_index=idx,
                               message=f"Part {idx} conversion failed",
                               error="FFmpeg conversion returned None")
            state.record_error()

    # Step 5: Queue all converted parts
    converted_parts = [p for p in parts if "shorts_path" in p]
    logger.info(f"\n  📋 Queueing {len(converted_parts)} clips for upload...")

    for part in converted_parts:
        idx = part["index"]
        short_title = video_title[:40] + ("..." if len(video_title) > 40 else "")

        queue_item = {
            "video_id": video_id,
            "video_url": video_url,
            "clip_path": part["shorts_path"],
            "clip_index": idx,
            "total_parts": total_parts,
            "title": f"{short_title} - Part {idx} #shorts",
            "description": (
                f"{video_title} - Part {idx}/{total_parts}\n\n"
                f"#shorts #viral #trending #youtube\n"
            ),
            "tags": ["shorts", "viral", "trending", video_title[:30]],
            "hashtags": ["#shorts", "#viral", "#trending"],
        }
        state.add_to_queue(queue_item)

    # Step 6: Upload queue with random delays
    drain_upload_queue(state)

    # Mark video as processed
    state.mark_video_processed(video_id)

    # Clean up source file
    try:
        os.remove(source_path)
        logger.info("  🗑️  Source file cleaned up")
    except OSError:
        pass

    webhook.send_event("pipeline_complete", "success",
                       video_url=video_url,
                       message=f"Pipeline complete: {len(converted_parts)} parts uploaded")

    # Print stats
    stats = state.get_stats()
    logger.info(f"\n  📊 Stats: {stats['total_videos_processed']} videos, "
                f"{stats['total_clips_uploaded']} clips uploaded, "
                f"{stats['total_errors']} errors")

    return True


# ── Upload Queue Drainer ──────────────────────────────────────────────────

def drain_upload_queue(state: PipelineState):
    """Upload all queued clips with random delays between each."""
    from modules.uploader import upload_all_clips

    queue_size = state.queue_size()
    if queue_size == 0:
        logger.info("  📋 Upload queue is empty")
        return

    logger.info(f"\n  📤 Processing upload queue: {queue_size} clip(s)")

    uploaded_count = 0

    while True:
        item = state.get_next_upload()
        if not item:
            break

        clip_path = item.get("clip_path", "")
        clip_index = item.get("clip_index")
        title = item.get("title", f"Part {clip_index}")
        video_url = item.get("video_url", "")

        if not os.path.exists(clip_path):
            logger.warning(f"  ⚠️  Clip file missing, skipping: {clip_path}")
            state.pop_upload()
            continue

        logger.info(f"\n  📤 Uploading: \"{title}\"")

        webhook.send_event("upload_start", "success",
                           video_url=video_url, clip_index=clip_index,
                           message=f"Uploading: {title}")

        # Build single-clip upload
        clip_paths = {clip_index: clip_path}
        metadata = {clip_index: {
            "title": title,
            "description": item.get("description", ""),
            "tags": item.get("tags", []),
            "hashtags": item.get("hashtags", []),
        }}

        try:
            results = upload_all_clips(clip_paths, metadata)
            result = results.get(clip_index)

            if result:
                yt_url = result.get("url", "")
                logger.info(f"  ✅ Uploaded: {yt_url}")

                webhook.send_event("upload_complete", "success",
                                   video_url=video_url, clip_index=clip_index,
                                   message=f"Uploaded: {yt_url}")

                state.pop_upload()
                state.mark_uploaded(item, yt_url)
                uploaded_count += 1
            else:
                logger.error(f"  ❌ Upload failed for part {clip_index}")
                webhook.send_event("upload_failed", "error",
                                   video_url=video_url, clip_index=clip_index,
                                   message=f"Upload failed for part {clip_index}",
                                   error="Upload returned no result")
                state.pop_upload()  # Don't block queue
                state.record_error()

        except Exception as e:
            logger.error(f"  ❌ Upload error: {e}")
            webhook.send_event("upload_failed", "error",
                               video_url=video_url, clip_index=clip_index,
                               message=f"Upload exception: {e}",
                               error=str(e))
            state.pop_upload()
            state.record_error()

        # Random delay between uploads (20min - 2hr)
        remaining = state.queue_size()
        if remaining > 0:
            delay_min = random.randint(UPLOAD_MIN_DELAY_MIN, UPLOAD_MAX_DELAY_MIN)
            logger.info(f"\n  ⏳ Waiting {delay_min} minutes before next upload "
                        f"({remaining} remaining in queue)...")
            time.sleep(delay_min * 60)

    logger.info(f"\n  ✅ Upload queue drained: {uploaded_count} clips uploaded")


# ── Daemon Loop ───────────────────────────────────────────────────────────

def daemon_loop(state: PipelineState):
    """Run the channel pipeline every CYCLE_INTERVAL_HOURS."""
    logger.info(f"🔄 Daemon mode: running every {CYCLE_INTERVAL_HOURS} hours")
    logger.info(f"   Press Ctrl+C to stop\n")

    # Check if there are queued uploads from a previous run
    if state.queue_size() > 0:
        logger.info(f"  📋 Found {state.queue_size()} clips from previous run, uploading first...")
        drain_upload_queue(state)

    while True:
        try:
            webhook.send_event("scheduler_cycle", "success",
                               message=f"Starting scheduled cycle at {datetime.now()}")

            success = run_channel_pipeline(state)

            if not success:
                logger.warning("  ⚠️  Cycle completed with issues")

            # Sleep until next cycle
            next_run = datetime.now().strftime("%Y-%m-%d %H:%M")
            logger.info(f"\n  💤 Next cycle in {CYCLE_INTERVAL_HOURS}h "
                        f"(currently: {next_run})")
            time.sleep(CYCLE_INTERVAL_HOURS * 3600)

        except KeyboardInterrupt:
            logger.info("\n  ⛔ Shutdown requested")
            break
        except Exception as e:
            logger.error(f"\n  💥 Cycle error: {e}")
            logger.error(traceback.format_exc())
            webhook.send_error(str(e))
            state.record_error()
            # Wait 10 minutes before retry
            logger.info("  ⏳ Waiting 10 minutes before retry...")
            time.sleep(600)


# ── Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="⚡ YouTube Shorts Engine — Autonomous Content Machine"
    )
    parser.add_argument("--mode", choices=["channel", "trending"],
                        default="channel", help="Pipeline mode (default: channel)")
    parser.add_argument("--once", action="store_true",
                        help="Run one cycle then exit (no daemon loop)")
    parser.add_argument("--drain-queue", action="store_true",
                        help="Only upload queued clips, then exit")
    parser.add_argument("--url", type=str,
                        help="YouTube URL (for trending mode)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Debug logging")
    parser.add_argument("--cycle-hours", type=int, default=24,
                        help="Hours between cycles (default: 24)")

    args = parser.parse_args()

    # Setup
    setup_logging(args.verbose)
    print_engine_banner()

    global CYCLE_INTERVAL_HOURS
    CYCLE_INTERVAL_HOURS = args.cycle_hours

    # Config status
    Config.print_status()

    # Initialize webhook
    webhook_url = os.getenv("WEBHOOK_URL", "")
    webhook.configure(webhook_url)

    # Initialize state
    state = PipelineState()
    stats = state.get_stats()
    logger.info(f"   History:         {stats['total_videos_processed']} videos, "
                f"{stats['total_clips_uploaded']} clips")
    logger.info(f"   Queue:           {stats['queue_size']} pending uploads")
    print()

    # Global error handler
    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        error_msg = f"Uncaught exception: {exc_value}"
        logger.critical(error_msg)
        logger.critical(traceback.format_exception(exc_type, exc_value, exc_tb))
        webhook.send_error(error_msg)

    sys.excepthook = handle_exception

    # Route to mode
    try:
        if args.drain_queue:
            logger.info("📤 Drain-queue mode: uploading pending clips...")
            drain_upload_queue(state)

        elif args.mode == "trending":
            if not args.url:
                logger.error("❌ --url required for trending mode")
                sys.exit(1)
            # Use existing highlight_extractor
            logger.info(f"🔥 Trending mode: {args.url}")
            os.system(f'python "{PROJECT_ROOT / "highlight_extractor.py"}" "{args.url}"')

        elif args.once:
            logger.info("🔂 Single-cycle mode")
            run_channel_pipeline(state)

        else:
            # Full daemon mode
            daemon_loop(state)

    except KeyboardInterrupt:
        logger.info("\n⛔ Shutdown complete")
    except Exception as e:
        logger.critical(f"💥 Fatal error: {e}")
        webhook.send_error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
