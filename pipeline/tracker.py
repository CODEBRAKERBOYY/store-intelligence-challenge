from __future__ import annotations

import hashlib


def visitor_token(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"VIS_{digest}"


def confidence_from_video(size_bytes: int, duration_s: float, index: int) -> float:
    bitrate_factor = min(0.12, max(0.0, size_bytes / max(duration_s, 1) / 1_000_000 / 100))
    base = 0.76 + bitrate_factor - (index % 5) * 0.015
    return round(min(0.96, max(0.52, base)), 2)
