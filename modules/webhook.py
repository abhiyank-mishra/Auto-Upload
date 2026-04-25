"""
Webhook Logging Module.

Sends structured POST requests to a webhook URL for every pipeline event.
Fixed JSON schema — all fields always present.
"""

import logging
import datetime
import requests
import threading

logger = logging.getLogger("shorts_engine")

# Consistent event names
EVENTS = {
    "video_selection_start": "video_selection_start",
    "video_selection_complete": "video_selection_complete",
    "video_download_start": "video_download_start",
    "video_download_complete": "video_download_complete",
    "video_split_start": "video_split_start",
    "video_split_complete": "video_split_complete",
    "clip_processing_start": "clip_processing_start",
    "clip_processing_complete": "clip_processing_complete",
    "upload_start": "upload_start",
    "upload_complete": "upload_complete",
    "upload_failed": "upload_failed",
    "pipeline_complete": "pipeline_complete",
    "system_error": "system_error",
    "scheduler_cycle": "scheduler_cycle",
}

_webhook_url = None
MAX_RETRIES = 3


def configure(url: str):
    """Set the webhook URL."""
    global _webhook_url
    _webhook_url = url
    if url:
        logger.info(f"   Webhook:         ✅ Configured")
    else:
        logger.info(f"   Webhook:         ⚠️  Not set")


def send_event(event: str, status: str, video_url: str = "",
               clip_index=None, message: str = "", error: str = "undefined"):
    """
    Send a structured webhook event. Non-blocking (runs in background thread).

    Args:
        event:     One of the EVENTS constants
        status:    "success" or "error"
        video_url: Source YouTube URL
        clip_index: Part number (int) or None
        message:   Short human-readable description
        error:     Error message or "undefined"
    """
    payload = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": event,
        "status": status,
        "video_url": video_url or "",
        "clip_index": str(clip_index) if clip_index is not None else "null",
        "message": message,
        "error": error if error else "undefined",
    }

    # Always log locally
    icon = "✅" if status == "success" else "❌"
    logger.info(f"  📡 [{event}] {icon} {message}")

    if not _webhook_url:
        return

    # Send in background thread to avoid blocking pipeline
    thread = threading.Thread(target=_send_with_retry, args=(payload,), daemon=True)
    thread.start()


def _send_with_retry(payload: dict):
    """POST payload to webhook with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                _webhook_url,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code < 400:
                return
            logger.debug(f"Webhook HTTP {resp.status_code} (attempt {attempt})")
        except requests.RequestException as e:
            logger.debug(f"Webhook error (attempt {attempt}): {e}")

        if attempt < MAX_RETRIES:
            import time
            time.sleep(2 ** attempt)

    logger.warning(f"  ⚠️  Webhook delivery failed after {MAX_RETRIES} attempts")


def send_error(error_msg: str, video_url: str = ""):
    """Convenience: send a system_error event."""
    send_event(
        event=EVENTS["system_error"],
        status="error",
        video_url=video_url,
        message=f"System error: {error_msg}",
        error=str(error_msg),
    )
