"""
Utility functions for the YouTube Highlight Extractor.
Handles URL validation, time formatting, sanitization, logging, and CLI display.
"""

import re
import os
import sys
import logging
from datetime import timedelta

from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)


# ─── Logging Setup ───────────────────────────────────────────────────────────

class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds color to log messages based on level."""

    LEVEL_COLORS = {
        logging.DEBUG:    Fore.CYAN,
        logging.INFO:     Fore.GREEN,
        logging.WARNING:  Fore.YELLOW,
        logging.ERROR:    Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, "")
        timestamp = Fore.WHITE + Style.DIM + self.formatTime(record, "%H:%M:%S") + Style.RESET_ALL
        level = color + f"[{record.levelname:<8}]" + Style.RESET_ALL
        message = record.getMessage()
        return f"{timestamp} {level} {message}"


def setup_logger(name: str = "highlight_extractor", verbose: bool = False) -> logging.Logger:
    """Create and configure a colored logger."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(ColoredFormatter())
        logger.addHandler(handler)

    return logger


# ─── URL Validation ──────────────────────────────────────────────────────────

YOUTUBE_URL_PATTERNS = [
    r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
]


def extract_video_id(url: str) -> str | None:
    """Extract the 11-character video ID from various YouTube URL formats."""
    for pattern in YOUTUBE_URL_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def validate_url(url: str) -> tuple[bool, str]:
    """Validate a YouTube URL and return (is_valid, video_id_or_error)."""
    if not url or not url.strip():
        return False, "URL cannot be empty."

    video_id = extract_video_id(url.strip())
    if not video_id:
        return False, f"Invalid YouTube URL: {url}"

    return True, video_id


# ─── Time Formatting ─────────────────────────────────────────────────────────

def seconds_to_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format."""
    td = timedelta(seconds=int(seconds))
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def timestamp_to_seconds(timestamp: str) -> float:
    """Convert HH:MM:SS or MM:SS timestamp to seconds."""
    parts = timestamp.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 1:
        return parts[0]
    raise ValueError(f"Invalid timestamp format: {timestamp}")


# ─── File Sanitization ───────────────────────────────────────────────────────

def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string for use as a filename."""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    sanitized = re.sub(r'[\s_]+', '_', sanitized).strip('_')
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('_')
    return sanitized or "untitled"


# ─── Output Directory ────────────────────────────────────────────────────────

def ensure_output_dir(path: str = "output") -> str:
    """Create output directory if it doesn't exist. Returns absolute path."""
    abs_path = os.path.abspath(path)
    os.makedirs(abs_path, exist_ok=True)
    return abs_path


# ─── CLI Display Components ──────────────────────────────────────────────────

def print_banner():
    """Print the legacy single-clip banner."""
    banner = f"""
{Fore.CYAN}{Style.BRIGHT}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   {Fore.WHITE}🎬  YouTube Highlight Extractor{Fore.CYAN}                            ║
║   {Fore.WHITE + Style.DIM}   Automatically find & clip the most engaging moments{Fore.CYAN + Style.BRIGHT}     ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{Style.RESET_ALL}
"""
    print(banner)


def print_pipeline_banner():
    """Print the full pipeline banner."""
    banner = f"""
{Fore.MAGENTA}{Style.BRIGHT}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   {Fore.WHITE}🚀  YouTube Shorts Automation Pipeline{Fore.MAGENTA}                     ║
║   {Fore.WHITE + Style.DIM}   Extract → Convert → Upload — Fully Automated{Fore.MAGENTA + Style.BRIGHT}            ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{Style.RESET_ALL}
"""
    print(banner)


def print_step(step_num: int, total: int, description: str):
    """Print a formatted step indicator."""
    bar = f"{Fore.CYAN}━" * 50 + Style.RESET_ALL
    print(f"\n{bar}")
    print(f"  {Fore.WHITE}{Style.BRIGHT}Step {step_num}/{total}{Style.RESET_ALL}  {Fore.CYAN}│{Style.RESET_ALL}  {description}")
    print(bar)


def print_result_box(title: str, start: float, end: float, duration: float,
                     confidence: str, method: str, output_path: str):
    """Print a formatted result summary box (legacy single-clip)."""
    seg_str = f"{seconds_to_timestamp(start)} → {seconds_to_timestamp(end)} ({duration:.0f}s)"
    pad = max(0, 30 - len(seg_str))
    print(f"""
{Fore.GREEN}{Style.BRIGHT}╔══════════════════════════════════════════════════════════════╗
║  ✅  HIGHLIGHT EXTRACTED SUCCESSFULLY                        ║
╠══════════════════════════════════════════════════════════════╣{Style.RESET_ALL}
{Fore.GREEN}║{Style.RESET_ALL}  {Fore.WHITE}Video:{Style.RESET_ALL}      {title[:48]:<48} {Fore.GREEN}║{Style.RESET_ALL}
{Fore.GREEN}║{Style.RESET_ALL}  {Fore.WHITE}Segment:{Style.RESET_ALL}    {seg_str}{' ' * pad}{Fore.GREEN}║{Style.RESET_ALL}
{Fore.GREEN}║{Style.RESET_ALL}  {Fore.WHITE}Method:{Style.RESET_ALL}     {method:<48} {Fore.GREEN}║{Style.RESET_ALL}
{Fore.GREEN}║{Style.RESET_ALL}  {Fore.WHITE}Confidence:{Style.RESET_ALL} {confidence:<48} {Fore.GREEN}║{Style.RESET_ALL}
{Fore.GREEN}║{Style.RESET_ALL}  {Fore.WHITE}Saved to:{Style.RESET_ALL}   {os.path.basename(output_path):<48} {Fore.GREEN}║{Style.RESET_ALL}
{Fore.GREEN}{Style.BRIGHT}╚══════════════════════════════════════════════════════════════╝{Style.RESET_ALL}
""")


def print_summary_box(title, channel, num_clips, num_uploads, output_dir,
                      elapsed, clip_details=None, upload_results=None):
    """Print a comprehensive pipeline summary box."""
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

    print(f"""
{Fore.MAGENTA}{Style.BRIGHT}╔═══════════════════════════════════════════════════════════════╗
║  🏁  PIPELINE COMPLETE                                        ║
╠═══════════════════════════════════════════════════════════════╣{Style.RESET_ALL}
{Fore.MAGENTA}║{Style.RESET_ALL}  {Fore.WHITE}Video:{Style.RESET_ALL}     {title[:49]:<49} {Fore.MAGENTA}║{Style.RESET_ALL}
{Fore.MAGENTA}║{Style.RESET_ALL}  {Fore.WHITE}Channel:{Style.RESET_ALL}   {channel[:49]:<49} {Fore.MAGENTA}║{Style.RESET_ALL}
{Fore.MAGENTA}║{Style.RESET_ALL}  {Fore.WHITE}Clips:{Style.RESET_ALL}     {num_clips:<49} {Fore.MAGENTA}║{Style.RESET_ALL}
{Fore.MAGENTA}║{Style.RESET_ALL}  {Fore.WHITE}Uploaded:{Style.RESET_ALL}  {num_uploads:<49} {Fore.MAGENTA}║{Style.RESET_ALL}
{Fore.MAGENTA}║{Style.RESET_ALL}  {Fore.WHITE}Time:{Style.RESET_ALL}      {time_str:<49} {Fore.MAGENTA}║{Style.RESET_ALL}
{Fore.MAGENTA}║{Style.RESET_ALL}  {Fore.WHITE}Output:{Style.RESET_ALL}    {os.path.basename(output_dir):<49} {Fore.MAGENTA}║{Style.RESET_ALL}""")

    if clip_details:
        print(f"""{Fore.MAGENTA}╠═══════════════════════════════════════════════════════════════╣{Style.RESET_ALL}""")
        for idx, peak, path in clip_details:
            ts = f"{seconds_to_timestamp(peak.start_time)} → {seconds_to_timestamp(peak.end_time)}"
            fname = os.path.basename(path) if path else "N/A"
            status = ""
            if upload_results and idx in upload_results and upload_results[idx]:
                status = f" {Fore.GREEN}✅ uploaded{Style.RESET_ALL}"
            elif upload_results and idx in upload_results:
                status = f" {Fore.RED}❌ failed{Style.RESET_ALL}  "
            detail = f"Clip {idx}: {ts} → {fname}{status}"
            # Pad without ANSI
            clean_len = len(f"Clip {idx}: {ts} → {fname}")
            pad = max(0, 49 - clean_len)
            if status:
                print(f"{Fore.MAGENTA}║{Style.RESET_ALL}  {detail}{' ' * (pad-3)}   {Fore.MAGENTA}║{Style.RESET_ALL}")
            else:
                print(f"{Fore.MAGENTA}║{Style.RESET_ALL}  {detail:<49}   {Fore.MAGENTA}║{Style.RESET_ALL}")

    print(f"""{Fore.MAGENTA}{Style.BRIGHT}╚═══════════════════════════════════════════════════════════════╝{Style.RESET_ALL}
""")



if __name__ == "__main__":
    # Logger test
    logger = setup_logger(verbose=True)

    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical message")

    # Banner test
    print_banner()
    print_pipeline_banner()
    print_step(1, 3, "Processing video...")

    # ─── Dummy data for summary box ───

    class Peak:
        def __init__(self, start, end):
            self.start_time = start
            self.end_time = end

    clip_details = [
        (1, Peak(10, 30), "clip1.mp4"),
        (2, Peak(40, 70), "clip2.mp4"),
    ]

    upload_results = {
        1: True,
        2: False
    }

    # ─── FINAL: Summary box call ───
    print_summary_box(
        title="Test Video Title",
        channel="Test Channel",
        num_clips=2,
        num_uploads=1,
        output_dir="output",
        elapsed=125,
        clip_details=clip_details,
        upload_results=upload_results
    )