"""
Configuration Module.

Loads environment variables from .env file and provides
validated configuration for all pipeline components.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger("highlight_extractor")

# Project root directory (where highlight_extractor.py lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Try to load .env file
try:
    from dotenv import load_dotenv
    _env_path = PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
        logger.debug(f"Loaded .env from {_env_path}")
    else:
        load_dotenv()  # Try default locations
except ImportError:
    logger.debug("python-dotenv not installed, using system environment variables only.")


class Config:
    """Centralized configuration loaded from environment variables."""

    # ── AI Services ───────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

    # ── YouTube Upload ────────────────────────────────────────────────────
    YOUTUBE_CLIENT_SECRET_FILE: str = os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
    YOUTUBE_DEFAULT_VISIBILITY: str = os.getenv("YOUTUBE_DEFAULT_VISIBILITY", "unlisted")

    # ── Processing Settings ───────────────────────────────────────────────
    CONCURRENT_FRAGMENTS: int = int(os.getenv("CONCURRENT_FRAGMENTS", "4"))

    # ── Video Processing ──────────────────────────────────────────────────
    LOGO_PATH: str = os.getenv("LOGO_PATH", "")

    # ── Clip Settings ─────────────────────────────────────────────────────
    MIN_CLIPS: int = 3
    MAX_CLIPS: int = 5
    CLIP_DURATION: float = 60.0
    CLIP_DURATION_TOLERANCE: float = 10.0
    MIN_GAP_BETWEEN_CLIPS: float = 30.0

    @classmethod
    def _resolve_secret_path(cls) -> Path:
        """Resolve the YouTube client secret file relative to project root."""
        raw = cls.YOUTUBE_CLIENT_SECRET_FILE
        p = Path(raw)
        if p.is_absolute() and p.exists():
            return p
        # Resolve relative to project root
        resolved = PROJECT_ROOT / raw
        if resolved.exists():
            return resolved
        return p  # Return as-is (will fail the exists check)

    @classmethod
    def has_gemini(cls) -> bool:
        return bool(cls.GEMINI_API_KEY and cls.GEMINI_API_KEY != "your_gemini_api_key_here")

    @classmethod
    def has_openrouter(cls) -> bool:
        return bool(cls.OPENROUTER_API_KEY and cls.OPENROUTER_API_KEY != "your_openrouter_api_key_here")

    @classmethod
    def has_any_ai(cls) -> bool:
        return cls.has_gemini() or cls.has_openrouter()

    @classmethod
    def has_youtube_credentials(cls) -> bool:
        return cls._resolve_secret_path().exists()

    @classmethod
    def get_youtube_secret_path(cls) -> str:
        """Get the resolved, absolute path to the YouTube client secret file."""
        return str(cls._resolve_secret_path())

    @classmethod
    def has_logo(cls) -> bool:
        """Check if a logo file is configured and exists."""
        return bool(cls.LOGO_PATH) and cls.get_logo_path().exists()

    @classmethod
    def get_logo_path(cls) -> Path:
        """Get the resolved path to the logo file."""
        if not cls.LOGO_PATH:
            return Path("")
        p = Path(cls.LOGO_PATH)
        if p.is_absolute() and p.exists():
            return p
        resolved = PROJECT_ROOT / p
        return resolved

    @classmethod
    def reload(cls):
        """Reload configuration from environment."""
        cls.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        cls.OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
        cls.YOUTUBE_CLIENT_SECRET_FILE = os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
        cls.YOUTUBE_DEFAULT_VISIBILITY = os.getenv("YOUTUBE_DEFAULT_VISIBILITY", "public")
        cls.CONCURRENT_FRAGMENTS = int(os.getenv("CONCURRENT_FRAGMENTS", "4"))
        cls.LOGO_PATH = os.getenv("LOGO_PATH", "")

    @classmethod
    def print_status(cls):
        """Log the current configuration status."""
        logger.info("⚙️  Configuration:")
        logger.info(f"   AI (OpenRouter): {'✅ Configured' if cls.has_openrouter() else '❌ Not set'}")
        logger.info(f"   YouTube OAuth:   {'✅ Found' if cls.has_youtube_credentials() else '❌ Not found'}")
        logger.info(f"   Visibility:      {cls.YOUTUBE_DEFAULT_VISIBILITY}")
        logo_status = f'✅ {cls.get_logo_path().name}' if cls.has_logo() else '⚠️  Not set'
        logger.info(f"   Logo overlay:    {logo_status}")
        logger.info(f"   DL Fragments:    {cls.CONCURRENT_FRAGMENTS}")

