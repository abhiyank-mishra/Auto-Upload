#!/usr/bin/env python3
"""
YouTube Shorts Automation Pipeline
====================================

Takes a single YouTube URL and automatically:

Also supports --upload-only to resume uploading from a previous run.
  1. Detects 3-5 high-engagement segments
  2. Downloads and clips each segment
  3. Converts clips to vertical Shorts format (9:16)
  4. Generates viral metadata via AI (Gemini/OpenRouter)
  5. Uploads all clips to YouTube as Shorts
  6. Notifies upon completion

Usage:
  python highlight_extractor.py <youtube_url>
  python highlight_extractor.py <youtube_url> --clips 5
  python highlight_extractor.py <youtube_url> --no-upload
  python highlight_extractor.py --upload-only output/My_Video_Folder
  python highlight_extractor.py <youtube_url> --legacy   (single-clip mode)

Dependencies:
  pip install -r requirements.txt
  External: yt-dlp, ffmpeg (must be in PATH)
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path

# Fix Windows console Unicode encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from modules.utils import (
    validate_url, sanitize_filename, ensure_output_dir,
    setup_logger, print_banner, print_step, print_result_box,
    seconds_to_timestamp, print_pipeline_banner, print_summary_box,
)
from modules.analyzer import find_peak_engagement, find_multiple_peaks
from modules.downloader import (
    fetch_video_info, download_segment, download_multi_clips, check_dependencies,
)
from modules.config import Config

from colorama import Fore, Style

logger = logging.getLogger("highlight_extractor")


# ═══════════════════════════════════════════════════════════════════════════════
#  FULL PIPELINE: Extract → Shorts → Upload
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_pipeline(
    url: str,
    output_dir: str = "output",
    clip_duration: float = 60.0,
    min_clips: int = 3,
    max_clips: int = 5,
    enable_upload: bool = True,
    enable_metadata: bool = True,
    verbose: bool = False,
) -> dict:
    """
    Run the complete Shorts automation pipeline.

    Steps: Metadata → Analyze → Download → Shorts Convert → AI Metadata → Upload
    """
    # Calculate total steps dynamically
    total_steps = 4  # metadata + analyze + download + shorts conversion
    if enable_metadata:
        total_steps += 1
    if enable_upload:
        total_steps += 1

    current_step = 0
    pipeline_start = time.time()
    summary = {"url": url, "clips": {}, "uploads": {}, "errors": []}

    # ── Step 1: Fetch Video Metadata ──────────────────────────────────────
    current_step += 1
    print_step(current_step, total_steps, "Fetching video metadata")

    is_valid, result = validate_url(url)
    if not is_valid:
        logger.error(f"❌ {result}")
        return summary

    video_id = result
    logger.info(f"🔗 Video ID: {video_id}")

    try:
        video_info = fetch_video_info(url)
    except (ValueError, RuntimeError) as e:
        logger.error(f"❌ {e}")
        summary["errors"].append(str(e))
        return summary

    title = video_info.get("title", "Unknown Video")
    duration = video_info.get("duration", 0)
    channel = video_info.get("uploader", video_info.get("channel", "Unknown"))

    if duration <= 0:
        logger.error("❌ Cannot determine video duration.")
        return summary

    # Create output folder
    safe_title = sanitize_filename(title, max_length=50)
    video_output_dir = os.path.join(ensure_output_dir(output_dir), safe_title)
    os.makedirs(video_output_dir, exist_ok=True)
    logger.info(f"📁 Output folder: {video_output_dir}")

    # ── Step 2: Analyze Engagement (Multi-Peak) ───────────────────────────
    current_step += 1
    print_step(current_step, total_steps, "Analyzing engagement data (multi-peak)")

    peaks = find_multiple_peaks(
        video_info=video_info,
        video_id=video_id,
        clip_duration=clip_duration,
        min_clips=min_clips,
        max_clips=max_clips,
    )

    for i, peak in enumerate(peaks, 1):
        conf = _confidence_label(peak.score)
        logger.info(
            f"  📍 Clip {i}: {seconds_to_timestamp(peak.start_time)} → "
            f"{seconds_to_timestamp(peak.end_time)} | {peak.method} | {conf}"
        )

    # ── Step 3: Download All Clips ────────────────────────────────────────
    current_step += 1
    print_step(current_step, total_steps, f"Downloading {len(peaks)} clips")

    clip_paths = download_multi_clips(url, peaks, video_output_dir, video_info)

    if not clip_paths:
        logger.error("❌ No clips were downloaded successfully.")
        summary["errors"].append("Download failed for all clips")
        return summary

    logger.info(f"✅ Downloaded {len(clip_paths)}/{len(peaks)} clips")

    # ── Step 4: Convert to Shorts Format (9:16) ──────────────────────────
    current_step += 1
    print_step(current_step, total_steps, "Converting clips to Shorts format (9:16)")

    from modules.video_processor import convert_to_shorts

    final_clip_paths = {}
    total_clips = len(clip_paths)
    for idx, clip_path in clip_paths.items():
        shorts_path = os.path.join(video_output_dir, f"clip{idx}_shorts.mp4")
        logger.info(f"\n  📱 Clip {idx}/{total_clips}: Converting to vertical...")

        result = convert_to_shorts(clip_path, shorts_path, clip_num=idx, total_clips=total_clips)
        if result:
            final_clip_paths[idx] = result
            # Remove the raw horizontal clip to save space
            try:
                os.remove(clip_path)
                # Rename shorts file to clean name
                clean_path = os.path.join(video_output_dir, f"clip{idx}.mp4")
                os.rename(result, clean_path)
                final_clip_paths[idx] = clean_path
            except OSError:
                final_clip_paths[idx] = result
        else:
            logger.warning(f"  ⚠️  Clip {idx}: Shorts conversion failed, keeping original")
            final_clip_paths[idx] = clip_path

    logger.info(f"✅ {len(final_clip_paths)} clips converted to Shorts format")
    summary["clips"] = {
        i: {"path": p, "peak": peaks[i - 1]}
        for i, p in final_clip_paths.items()
    }

    # ── Step 5: Generate AI Metadata ──────────────────────────────────────
    metadata_dict = {}
    if enable_metadata:
        current_step += 1
        print_step(current_step, total_steps, "Generating clip metadata via AI")

        if Config.has_openrouter():
            from modules.metadata_generator import generate_all_metadata, save_metadata

            metadata_dict = generate_all_metadata(peaks, video_info)
            save_metadata(metadata_dict, video_output_dir)
        else:
            logger.warning("⚠️  OPENROUTER_API_KEY not configured. Using fallback metadata.")
            from modules.metadata_generator import _fallback_metadata

            for i in range(1, len(peaks) + 1):
                metadata_dict[i] = _fallback_metadata(i, title, channel)

    # ── Step 6: Upload to YouTube ─────────────────────────────────────────
    upload_results = {}
    if enable_upload:
        current_step += 1
        print_step(current_step, total_steps, "Uploading clips to YouTube")

        if Config.has_youtube_credentials():
            from modules.uploader import upload_all_clips

            upload_results = upload_all_clips(final_clip_paths, metadata_dict)
            summary["uploads"] = upload_results
        else:
            logger.warning("⚠️  Upload skipped: YouTube OAuth credentials not found")
            logger.info("   Place client_secret.json in the project root to enable uploads")

    # ── Pipeline Complete ─────────────────────────────────────────────────
    elapsed = time.time() - pipeline_start

    successful_clips = len(final_clip_paths)
    successful_uploads = sum(1 for v in upload_results.values() if v)

    print_summary_box(
        title=title,
        channel=channel,
        num_clips=successful_clips,
        num_uploads=successful_uploads,
        output_dir=video_output_dir,
        elapsed=elapsed,
        clip_details=[
            (i, peaks[i - 1], final_clip_paths.get(i, ""))
            for i in sorted(final_clip_paths.keys())
        ],
        upload_results=upload_results,
    )

    # Final notification
    if successful_uploads > 0:
        logger.info(
            f"\n🎉 {successful_uploads} clips uploaded successfully. "
            f"Check your YouTube channel."
        )
    elif enable_upload and not Config.has_youtube_credentials():
        logger.info(f"\n📦 {successful_clips} clip(s) ready in: {video_output_dir}")
        logger.info("   Configure YouTube OAuth credentials to enable auto-upload.")
    else:
        logger.info(f"\n📦 {successful_clips} clip(s) saved to: {video_output_dir}")

    return summary


# ═══════════════════════════════════════════════════════════════════════════════
#  LEGACY SINGLE-CLIP MODE (backward compatible)
# ═══════════════════════════════════════════════════════════════════════════════

def process_single_video(url, output_dir="output", clip_duration=60.0, verbose=False):
    """Process a single YouTube video: analyze, download, clip (legacy mode)."""
    total_steps = 3
    start_time = time.time()

    # Step 1: Fetch metadata
    print_step(1, total_steps, "Fetching video metadata")
    is_valid, result = validate_url(url)
    if not is_valid:
        logger.error(f"❌ {result}")
        return None

    video_id = result
    logger.info(f"🔗 Video ID: {video_id}")

    try:
        video_info = fetch_video_info(url)
    except (ValueError, RuntimeError) as e:
        logger.error(f"❌ {e}")
        return None

    title = video_info.get("title", "Unknown Video")
    duration = video_info.get("duration", 0)
    if duration <= 0:
        logger.error("❌ Cannot determine video duration.")
        return None
    if duration < 30:
        clip_duration = duration

    # Step 2: Analyze engagement
    print_step(2, total_steps, "Analyzing engagement data")
    peak = find_peak_engagement(
        video_info=video_info, video_id=video_id, clip_duration=clip_duration
    )

    actual_duration = peak.end_time - peak.start_time
    confidence_label = _confidence_label(peak.score)

    logger.info("")
    logger.info(f"  {Fore.WHITE}{Style.BRIGHT}📍 Detected Peak:{Style.RESET_ALL}")
    logger.info(f"     Time Range:  {seconds_to_timestamp(peak.start_time)} → {seconds_to_timestamp(peak.end_time)}")
    logger.info(f"     Duration:    {actual_duration:.0f}s")
    logger.info(f"     Method:      {peak.method}")
    logger.info(f"     Confidence:  {confidence_label}")
    logger.info(f"     Details:     {peak.details}")
    logger.info("")

    # Step 3: Download & clip
    print_step(3, total_steps, "Downloading and clipping highlight")
    safe_title = sanitize_filename(title)
    output_filename = f"{safe_title}_highlight.mp4"
    abs_output_dir = ensure_output_dir(output_dir)
    output_path = os.path.join(abs_output_dir, output_filename)

    counter = 1
    while os.path.exists(output_path):
        output_filename = f"{safe_title}_highlight_{counter}.mp4"
        output_path = os.path.join(abs_output_dir, output_filename)
        counter += 1

    try:
        saved_path = download_segment(
            url=url, start_time=peak.start_time, end_time=peak.end_time,
            output_path=output_path, video_info=video_info,
        )
    except Exception as e:
        logger.error(f"❌ Download/clip failed: {e}")
        return None

    elapsed = time.time() - start_time
    logger.info(f"⏱️  Total time: {elapsed:.1f}s")

    print_result_box(
        title=title, start=peak.start_time, end=peak.end_time,
        duration=actual_duration, confidence=confidence_label,
        method=peak.method, output_path=saved_path,
    )
    return saved_path


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _confidence_label(score: float) -> str:
    """Convert confidence score to a colored label."""
    if score >= 0.7:
        return f"{Fore.GREEN}HIGH ({score:.0%}){Style.RESET_ALL}"
    elif score >= 0.4:
        return f"{Fore.YELLOW}MEDIUM ({score:.0%}){Style.RESET_ALL}"
    elif score >= 0.2:
        return f"{Fore.RED}LOW ({score:.0%}){Style.RESET_ALL}"
    else:
        return f"{Fore.RED}{Style.DIM}ESTIMATED ({score:.0%}){Style.RESET_ALL}"


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="highlight_extractor",
        description=(
            "🚀 YouTube Shorts Automation Pipeline\n"
            "Extract → Convert to Shorts → Upload — Fully Automated"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python highlight_extractor.py https://youtube.com/watch?v=VIDEO_ID\n"
            "  python highlight_extractor.py URL --clips 5 --no-upload\n"
            "  python highlight_extractor.py URL --legacy  (single-clip, no AI)\n"
        ),
    )

    parser.add_argument("urls", nargs="*", help="YouTube video URL(s)")
    parser.add_argument(
        "--batch", "-b", type=str, metavar="FILE",
        help="Text file with YouTube URLs (one per line)",
    )
    parser.add_argument(
        "--duration", "-d", type=float, default=60.0, metavar="SECONDS",
        help="Target clip duration (default: 60, range: 30-120)",
    )
    parser.add_argument(
        "--clips", type=int, default=None, metavar="N",
        help="Number of clips to extract (3-5, default: auto)",
    )
    parser.add_argument(
        "--output", "-o", type=str, default="output", metavar="DIR",
        help="Output directory (default: ./output)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    # Pipeline toggles
    parser.add_argument(
        "--legacy", action="store_true",
        help="Legacy single-clip mode (no Shorts conversion, no AI, no upload)",
    )
    parser.add_argument("--no-upload", action="store_true", help="Skip YouTube upload")
    parser.add_argument("--no-metadata", action="store_true", help="Skip AI metadata")
    parser.add_argument(
        "--upload-only", type=str, metavar="FOLDER",
        help="Upload clips from an existing output folder (skip extraction)",
    )

    args = parser.parse_args()
    setup_logger(verbose=args.verbose)

    # Print appropriate banner
    if args.legacy:
        print_banner()
    else:
        print_pipeline_banner()

    # ── Upload-only mode ──────────────────────────────────────────────────
    if args.upload_only:
        Config.print_status()
        if not Config.has_youtube_credentials():
            logger.error("❌ YouTube OAuth credentials not found. Cannot upload.")
            sys.exit(1)

        logger.info(f"📤 Upload-only mode: {args.upload_only}")
        print_step(1, 1, "Uploading clips to YouTube")

        from modules.uploader import upload_from_folder
        results = upload_from_folder(args.upload_only)

        successful = sum(1 for v in results.values() if v)
        logger.info(f"\n🎉 Uploaded {successful}/{len(results)} clips")
        for idx, r in sorted(results.items()):
            if r:
                logger.info(f"  ✅ Clip {idx}: {r.get('url', '')}")
            else:
                logger.error(f"  ❌ Clip {idx}: Upload failed")
        sys.exit(0)

    # Check dependencies
    deps = check_dependencies()
    missing = [name for name, path in deps.items() if path is None]
    if missing:
        logger.error(f"❌ Missing required tools: {', '.join(missing)}")
        logger.error("   yt-dlp:  pip install yt-dlp")
        logger.error("   ffmpeg:  https://ffmpeg.org/download.html")
        sys.exit(1)

    # Show config status (full pipeline only)
    if not args.legacy:
        Config.print_status()

    # Validate clip duration
    clip_duration = max(30.0, min(120.0, args.duration))
    if clip_duration != args.duration:
        logger.warning(f"⚠️  Clip duration clamped to {clip_duration}s (valid: 30-120)")

    # Collect URLs
    urls = list(args.urls) if args.urls else []
    if args.batch:
        batch_file = Path(args.batch)
        if not batch_file.exists():
            logger.error(f"❌ Batch file not found: {args.batch}")
            sys.exit(1)
        with open(batch_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
        logger.info(f"📋 Loaded {len(urls)} URLs from {args.batch}")

    if not urls:
        parser.print_help()
        print(f"\n{Fore.RED}Error: No YouTube URLs provided.{Style.RESET_ALL}")
        sys.exit(1)

    # Deduplicate
    seen = set()
    unique_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)
    urls = unique_urls

    # Determine clip range
    min_clips = args.clips if args.clips else Config.MIN_CLIPS
    max_clips = args.clips if args.clips else Config.MAX_CLIPS
    min_clips = max(1, min(5, min_clips))
    max_clips = max(min_clips, min(5, max_clips))

    logger.info(
        f"🎯 Processing {len(urls)} video(s) | "
        f"Clips: {min_clips}-{max_clips} | Duration: {clip_duration}s"
    )

    # Process each URL
    for i, url in enumerate(urls):
        if len(urls) > 1:
            print(f"\n{'═' * 60}")
            print(f"  {Fore.CYAN}{Style.BRIGHT}Video {i + 1}/{len(urls)}{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{url}{Style.RESET_ALL}")
            print(f"{'═' * 60}")

        if args.legacy:
            process_single_video(
                url=url, output_dir=args.output,
                clip_duration=clip_duration, verbose=args.verbose,
            )
        else:
            run_full_pipeline(
                url=url, output_dir=args.output, clip_duration=clip_duration,
                min_clips=min_clips, max_clips=max_clips,
                enable_upload=not args.no_upload,
                enable_metadata=not args.no_metadata,
                verbose=args.verbose,
            )


if __name__ == "__main__":
    main()
