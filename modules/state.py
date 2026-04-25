"""
Pipeline State Persistence.

Saves processed videos, upload queue, and uploaded parts to a JSON file.
Prevents reprocessing and supports resume on restart.
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("shorts_engine")

DEFAULT_STATE_FILE = "state.json"


class PipelineState:
    """Thread-safe persistent state for the pipeline."""

    def __init__(self, state_path: str = None):
        from modules.config import Config
        project_root = Path(__file__).resolve().parent.parent
        self.path = state_path or str(project_root / DEFAULT_STATE_FILE)
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        """Load state from disk."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug(f"State loaded: {len(data.get('processed_videos', []))} videos tracked")
                return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"  ⚠️  Corrupt state file, starting fresh: {e}")

        return {
            "processed_videos": [],     # List of video IDs already processed
            "upload_queue": [],          # Clips waiting to be uploaded
            "uploaded_parts": [],        # Successfully uploaded clips
            "last_cycle": None,          # ISO timestamp of last scheduler cycle
            "stats": {
                "total_videos_processed": 0,
                "total_clips_uploaded": 0,
                "total_errors": 0,
            },
        }

    def _save(self):
        """Persist state to disk (must hold lock)."""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"  ❌ Failed to save state: {e}")

    # ── Video tracking ────────────────────────────────────────────────────

    def is_video_processed(self, video_id: str) -> bool:
        """Check if a video has already been processed."""
        with self._lock:
            return video_id in self.data["processed_videos"]

    def mark_video_processed(self, video_id: str):
        """Mark a video as fully processed."""
        with self._lock:
            if video_id not in self.data["processed_videos"]:
                self.data["processed_videos"].append(video_id)
                self.data["stats"]["total_videos_processed"] += 1
            self._save()

    # ── Upload queue ──────────────────────────────────────────────────────

    def add_to_queue(self, item: dict):
        """
        Add a clip to the upload queue.

        item format:
        {
            "video_id": "source_video_id",
            "video_url": "https://youtube.com/watch?v=...",
            "clip_path": "/path/to/clip.mp4",
            "clip_index": 1,
            "total_parts": 5,
            "title": "Video Title - Part 1",
            "description": "...",
            "tags": [...],
            "hashtags": [...],
            "added_at": "ISO timestamp",
        }
        """
        with self._lock:
            # Dedup check
            existing = {
                (q["video_id"], q["clip_index"])
                for q in self.data["upload_queue"]
            }
            key = (item["video_id"], item["clip_index"])
            if key not in existing:
                item["added_at"] = datetime.now(timezone.utc).isoformat()
                self.data["upload_queue"].append(item)
                self._save()
                return True
            return False

    def get_next_upload(self) -> dict | None:
        """Get the next clip from the queue (FIFO)."""
        with self._lock:
            if self.data["upload_queue"]:
                return self.data["upload_queue"][0]
            return None

    def pop_upload(self) -> dict | None:
        """Remove and return the next clip from the queue."""
        with self._lock:
            if self.data["upload_queue"]:
                item = self.data["upload_queue"].pop(0)
                self._save()
                return item
            return None

    def mark_uploaded(self, item: dict, youtube_url: str):
        """Record a successful upload."""
        with self._lock:
            record = {
                "video_id": item.get("video_id"),
                "clip_index": item.get("clip_index"),
                "title": item.get("title"),
                "youtube_url": youtube_url,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            self.data["uploaded_parts"].append(record)
            self.data["stats"]["total_clips_uploaded"] += 1
            self._save()

    def queue_size(self) -> int:
        """Get current queue size."""
        with self._lock:
            return len(self.data["upload_queue"])

    def record_error(self):
        """Increment error counter."""
        with self._lock:
            self.data["stats"]["total_errors"] += 1
            self._save()

    # ── Scheduler ─────────────────────────────────────────────────────────

    def update_last_cycle(self):
        """Record when the last scheduler cycle ran."""
        with self._lock:
            self.data["last_cycle"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        with self._lock:
            return {
                **self.data["stats"],
                "queue_size": len(self.data["upload_queue"]),
                "last_cycle": self.data.get("last_cycle"),
            }
