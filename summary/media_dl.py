"""Tiny URL downloader for Imagine outputs."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "grokbot-summary/1.0"


def download_url(url: str, dest: Path, *, timeout: float = 180.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dest.write_bytes(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} downloading {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc
    return dest
