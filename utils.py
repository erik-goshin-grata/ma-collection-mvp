"""
Shared utilities for the M&A Collection MVP pipeline.
"""

from __future__ import annotations

import hashlib
import re


def content_hash(text: str) -> str:
    """Return a SHA-256 hex digest over whitespace-normalised, lowercased text."""
    normalised = re.sub(r"\s+", " ", text).lower().strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
