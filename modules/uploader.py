"""
YouTube Upload Module.

Handles automatic upload of clips to YouTube using the YouTube Data API v3.
Uses OAuth 2.0 for authentication — authenticates ONCE and reuses for all clips.
"""

import os
import sys
import logging
import time
import json
import http.client
import httplib2

logger = logging.getLogger("highlight_extractor")

# OAuth2 scopes needed for upload
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

# Maximum retries for resumable upload
MAX_RETRIES = 3
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]


def _get_authenticated_service(credentials_path):
    """Authenticate with YouTube API using OAuth 2.0 (called ONCE)."""
    from googleapiclient.discovery import build
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    from modules.config import Config

    # Store token in the project root (not output dir)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    token_path = os.path.join(project_root, "youtube_token.json")
    creds = None

    # Load existing token
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, [YOUTUBE_UPLOAD_SCOPE])
        except Exception:
            creds = None

    # Refresh or get new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("  🔄 OAuth token refreshed")
            except Exception:
                creds = None

        if not creds:
            logger.info("  🔑 Opening browser for YouTube OAuth authentication...")
            logger.info("     (This is a one-time setup. Token will be saved for future use)")
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, [YOUTUBE_UPLOAD_SCOPE]
            )
            # port=0 lets the OS pick an available port (avoids WinError 10048)
            creds = flow.run_local_server(port=0, open_browser=True)

        # Save token for future use
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info("  💾 OAuth token saved for future uploads")

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=creds)


def _upload_single(youtube, video_path, title, description, tags, privacy_status):
    """Upload a single video using an already-authenticated YouTube service."""
    from googleapiclient.http import MediaFileUpload

    if not os.path.exists(video_path):
        logger.error(f"  ❌ Video file not found: {video_path}")
        return None

    # Build request body
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500] if tags else [],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    # Create upload request with 1MB chunks for progress visibility
    file_size = os.path.getsize(video_path)
    chunk_size = max(256 * 1024, min(5 * 1024 * 1024, file_size // 20))  # ~20 progress updates
    media = MediaFileUpload(video_path, chunksize=chunk_size, resumable=True,
                            mimetype="video/mp4")

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )

    # Execute upload with retry + progress bar
    return _resumable_upload(request, os.path.basename(video_path), file_size)


def _resumable_upload(request, filename, total_bytes):
    """Execute a resumable upload with retry logic and progress bar."""
    response = None
    retry = 0
    size_mb = total_bytes / (1024 * 1024)

    while response is None:
        try:
            status, response = request.next_chunk()

            if status:
                pct = status.progress() * 100
                uploaded_mb = (status.resumable_progress or 0) / (1024 * 1024)
                bar_len = 30
                filled = int(bar_len * status.progress())
                bar = "█" * filled + "░" * (bar_len - filled)
                sys.stdout.write(
                    f"\r  ⬆️  [{bar}] {pct:5.1f}% │ {uploaded_mb:.1f}/{size_mb:.1f} MB  "
                )
                sys.stdout.flush()

            if response:
                # Clear progress bar line
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.flush()
                return response

        except Exception as e:
            error_str = str(e)
            if retry < MAX_RETRIES:
                retry += 1
                wait = 2 ** retry
                sys.stdout.write("\n")
                logger.warning(f"  ⚠️  Upload error (attempt {retry}/{MAX_RETRIES}): {error_str}")
                logger.info(f"  ⏳ Retrying in {wait}s...")
                time.sleep(wait)
            else:
                sys.stdout.write("\n")
                logger.error(f"  ❌ Upload failed after {MAX_RETRIES} retries: {error_str}")
                return None

    return response


def upload_all_clips(clip_paths, metadata_dict, credentials_path=None):
    """
    Upload all clips to YouTube.

    Authenticates ONCE then uploads all clips using the same service.
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.error("  ❌ Google API libraries not installed. Run:")
        logger.error("     pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")
        return {}

    from modules.config import Config

    if not credentials_path:
        credentials_path = Config.get_youtube_secret_path()

    privacy_status = Config.YOUTUBE_DEFAULT_VISIBILITY

    if not os.path.exists(credentials_path):
        logger.error(f"  ❌ OAuth credentials not found: {credentials_path}")
        logger.error("     Download client_secret.json from Google Cloud Console")
        return {}

    # ── Authenticate ONCE ─────────────────────────────────────────────────
    try:
        logger.info("  🔐 Authenticating with YouTube API...")
        youtube = _get_authenticated_service(credentials_path)
        if not youtube:
            logger.error("  ❌ Authentication failed")
            return {}
        logger.info("  ✅ Authenticated successfully!\n")
    except Exception as e:
        logger.error(f"  ❌ Authentication failed: {e}")
        return {}

    # ── Upload all clips using same service ───────────────────────────────
    results = {}
    total = len(clip_paths)

    for idx in sorted(clip_paths.keys()):
        path = clip_paths[idx]
        meta = metadata_dict.get(idx, {})

        if not meta:
            logger.warning(f"  ⚠️  No metadata for clip {idx}, skipping upload")
            continue

        title = meta.get("title", f"Clip {idx}")
        description = meta.get("description", "")
        tags = meta.get("tags", [])

        # Add hashtags to description
        hashtags = meta.get("hashtags", [])
        if hashtags:
            description += "\n\n" + " ".join(hashtags)

        size_mb = os.path.getsize(path) / (1024 * 1024) if os.path.exists(path) else 0
        logger.info(f"  📤 Uploading clip {idx}/{total}: \"{title}\" ({size_mb:.1f} MB)")

        try:
            result = _upload_single(youtube, path, title, description, tags, privacy_status)

            if result:
                video_id = result.get("id", "")
                url = f"https://youtube.com/shorts/{video_id}"
                logger.info(f"  ✅ Uploaded: {url}")
                results[idx] = {"video_id": video_id, "url": url}
            else:
                logger.error(f"  ❌ Clip {idx} upload returned no response")
                results[idx] = None

        except Exception as e:
            logger.error(f"  ❌ Clip {idx} upload failed: {e}")
            results[idx] = None

        # Small delay between uploads to avoid rate limiting
        if idx < total:
            time.sleep(2)

    return results


# ── Standalone upload (for --upload-only mode) ────────────────────────────

def upload_from_folder(folder_path, credentials_path=None):
    """
    Upload all clips from an existing output folder.
    Reads metadata.json for titles/descriptions.
    """
    folder = os.path.abspath(folder_path)
    if not os.path.isdir(folder):
        logger.error(f"  ❌ Folder not found: {folder}")
        return {}

    # Find clip files
    clip_paths = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".mp4") and f.startswith("clip"):
            # Extract clip number
            import re
            match = re.match(r"clip(\d+)", f)
            if match:
                idx = int(match.group(1))
                clip_paths[idx] = os.path.join(folder, f)

    if not clip_paths:
        logger.error(f"  ❌ No clip*.mp4 files found in: {folder}")
        return {}

    logger.info(f"  📁 Found {len(clip_paths)} clip(s) in {folder}")

    # Load metadata
    meta_path = os.path.join(folder, "metadata.json")
    metadata_dict = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Keys might be string numbers
            metadata_dict = {int(k): v for k, v in raw.items()}
            logger.info(f"  📝 Loaded metadata for {len(metadata_dict)} clips")
        except Exception as e:
            logger.warning(f"  ⚠️  Could not load metadata: {e}")

    # Generate fallback metadata for clips without it
    for idx in clip_paths:
        if idx not in metadata_dict:
            metadata_dict[idx] = {
                "title": f"Clip {idx} #shorts",
                "description": "Uploaded via YouTube Shorts Automation Pipeline\n\n#shorts #viral",
                "tags": ["shorts", "viral"],
                "hashtags": ["#shorts", "#viral"],
            }

    return upload_all_clips(clip_paths, metadata_dict, credentials_path)
