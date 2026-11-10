"""Answer sanitizing for CAPCOM.

The model occasionally leaks reasoning as literal <thinking> tags despite the
system prompt, and answers produced before the disclosure was retired may
still carry its line. sanitize_answer strips both deterministically — prompts
request good behavior, this module guarantees it. Pure stdlib so tests can
import it without the strands/bedrock-agentcore SDKs.
"""

from __future__ import annotations

import re

# Closed <thinking>...</thinking> blocks, possibly multiline.
_THINKING_BLOCK = re.compile(r"<thinking\b[^>]*>.*?</thinking\s*>", re.IGNORECASE | re.DOTALL)
# Stray or unclosed thinking tags left after block removal.
_THINKING_TAG = re.compile(r"</?thinking\b[^>]*>", re.IGNORECASE)
# The retired disclosure line; tolerant of dash width and apostrophe variant.
_DISCLOSURE = re.compile(
    r"\(\s*general knowledge\s*[—–-]\s*not from starbase['\u2019´]?s live data\s*\)",
    re.IGNORECASE,
)
_SPACES = re.compile(r"[ \t]{2,}")
_BLANKS = re.compile(r"\n{3,}")


def sanitize_answer(text: str) -> str:
    """Remove reasoning tags and the retired disclosure line; keep plain text."""
    cleaned = _DISCLOSURE.sub(" ", _THINKING_TAG.sub("", _THINKING_BLOCK.sub("", text)))
    cleaned = "\n".join(_SPACES.sub(" ", line) for line in cleaned.split("\n"))
    return _BLANKS.sub("\n\n", cleaned).strip()
