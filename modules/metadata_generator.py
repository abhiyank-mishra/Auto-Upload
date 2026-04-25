"""
Metadata Generator Module.

Uses AI (Gemini/OpenRouter) to generate viral titles, SEO descriptions,
and hashtags for each YouTube Shorts clip.
"""

import os
import json
import logging

logger = logging.getLogger("highlight_extractor")

SYSTEM_PROMPT = """You are a YouTube Shorts metadata expert. Generate viral, engaging metadata for YouTube Shorts clips.

Rules:
- Title must be short, catchy, and max 60 characters
- Description must be SEO-optimized with relevant keywords
- Include credit to the original creator in the description
- Generate 5-10 relevant hashtags
- Output must be valid JSON"""


def generate_clip_metadata(clip_index, total_clips, original_title, original_description,
                           channel_name, clip_start, clip_end, video_duration):
    """
    Generate title, description, and tags for a single clip.

    Args:
        clip_index: 1-based clip index
        total_clips: Total number of clips
        original_title: Original video title
        original_description: Original video description (first 500 chars)
        channel_name: Original channel name for credit
        clip_start: Clip start time in seconds
        clip_end: Clip end time in seconds
        video_duration: Total video duration

    Returns:
        Dict with 'title', 'description', 'tags', 'hashtags' or None if failed.
    """
    from modules.utils import seconds_to_timestamp

    try:
        from modules.ai_client import call_ai, AIClientError
    except ImportError:
        logger.error("  ❌ AI client not available")
        return _fallback_metadata(clip_index, original_title, channel_name)

    desc_snippet = (original_description or "")[:500]
    start_ts = seconds_to_timestamp(clip_start)
    end_ts = seconds_to_timestamp(clip_end)

    prompt = f"""Generate YouTube Shorts metadata for this clip.

ORIGINAL VIDEO INFO:
- Title: {original_title}
- Channel: {channel_name}
- Description snippet: {desc_snippet}
- Clip {clip_index} of {total_clips}: {start_ts} → {end_ts}
- Full video duration: {seconds_to_timestamp(video_duration)}

Generate a JSON object with these exact keys:
{{
  "title": "Short viral title (max 60 chars)",
  "description": "SEO description with credit to @{channel_name}",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3", "#hashtag4", "#hashtag5"]
}}

Output ONLY the JSON object, nothing else."""

    try:
        result = call_ai(prompt, system_prompt=SYSTEM_PROMPT, temperature=0.7, max_tokens=1024)
        metadata = _parse_json_response(result)

        if metadata:
            # Enforce title length
            if len(metadata.get("title", "")) > 60:
                metadata["title"] = metadata["title"][:57] + "..."

            # Ensure credit in description
            if channel_name and channel_name not in metadata.get("description", ""):
                metadata["description"] += f"\n\nOriginal video by @{channel_name}"

            logger.info(f"  ✅ Metadata generated: \"{metadata.get('title', '')}\"")
            return metadata

    except Exception as e:
        logger.warning(f"  ⚠️  AI metadata generation failed: {e}")

    return _fallback_metadata(clip_index, original_title, channel_name)


def generate_all_metadata(peaks, video_info):
    """
    Generate metadata for all clips.

    Args:
        peaks: List of EngagementPeak objects
        video_info: yt-dlp video info dict

    Returns:
        Dict mapping clip_index (1-based) to metadata dict.
    """
    title = video_info.get("title", "Unknown Video")
    description = video_info.get("description", "")
    channel = video_info.get("uploader", video_info.get("channel", "Unknown Creator"))
    duration = video_info.get("duration", 0)

    metadata = {}
    for i, peak in enumerate(peaks, 1):
        logger.info(f"  📝 Generating metadata for clip {i}/{len(peaks)}...")
        meta = generate_clip_metadata(
            clip_index=i, total_clips=len(peaks),
            original_title=title, original_description=description,
            channel_name=channel, clip_start=peak.start_time,
            clip_end=peak.end_time, video_duration=duration
        )
        metadata[i] = meta

    return metadata


def save_metadata(metadata, output_dir):
    """Save metadata to a JSON file."""
    path = os.path.join(output_dir, "metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    logger.info(f"  💾 Metadata saved: {os.path.basename(path)}")
    return path


def _parse_json_response(text):
    """Extract and parse JSON from AI response."""
    import re
    # Try to find JSON object in the response
    text = text.strip()
    # Remove markdown code blocks
    text = re.sub(r'```(?:json)?\s*\n?', '', text)
    text = re.sub(r'```\s*$', '', text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object within the text
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def _fallback_metadata(clip_index, original_title, channel_name):
    """Generate basic metadata without AI."""
    short_title = original_title[:50] if original_title else "Highlight"
    return {
        "title": f"{short_title} - Part {clip_index}",
        "description": (
            f"Highlight clip {clip_index} from \"{original_title}\"\n\n"
            f"Original video by @{channel_name}\n\n"
            f"#shorts #highlights #viral"
        ),
        "tags": ["shorts", "highlights", "viral", "clips"],
        "hashtags": ["#shorts", "#highlights", "#viral"],
    }
