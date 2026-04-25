"""
AI Client Module.

Provides a single interface to call AI models via OpenRouter API.
"""

import json
import logging
import requests

from modules.config import Config

logger = logging.getLogger("highlight_extractor")

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "google/gemini-2.0-flash-001"


class AIClientError(Exception):
    """Raised when AI backend fails."""
    pass


def call_ai(prompt: str, system_prompt: str = "", temperature: float = 0.7, max_tokens: int = 4096) -> str:
    """
    Call AI via OpenRouter.

    Args:
        prompt: The user prompt / main instruction.
        system_prompt: Optional system-level instruction.
        temperature: Creativity level (0.0 = deterministic, 1.0 = creative).
        max_tokens: Maximum response length.

    Returns:
        The AI-generated text response.

    Raises:
        AIClientError: If the API call fails.
    """
    if not Config.has_openrouter():
        raise AIClientError(
            "OPENROUTER_API_KEY not configured. Set it in .env"
        )

    try:
        result = _call_openrouter(prompt, system_prompt, temperature, max_tokens)
        if result:
            return result
        raise AIClientError("Empty response from OpenRouter")
    except AIClientError:
        raise
    except Exception as e:
        raise AIClientError(f"OpenRouter failed: {e}")


def _call_openrouter(prompt: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
    """Call OpenRouter API (OpenAI-compatible)."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/youtube-automation",
        "X-Title": "YouTube Automation Pipeline",
    }

    logger.debug(f"Calling OpenRouter API ({OPENROUTER_MODEL})...")
    response = requests.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=120)

    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text[:300]}")

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise Exception("No choices in OpenRouter response")

    text = choices[0].get("message", {}).get("content", "")
    if not text:
        raise Exception("Empty response from OpenRouter")

    logger.debug(f"OpenRouter response: {len(text)} chars")
    return text
