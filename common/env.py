"""Shared helpers for loading secrets from the repo-root `.env`."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> Path:
    """Load `.env` from the repository root (once per process is fine)."""
    env_path = REPO_ROOT / ".env"
    load_dotenv(env_path)
    return env_path


def _clean(value: str | None) -> str:
    return (value or "").strip()


def require_env(name: str, *, placeholder_prefix: str | None = None) -> str:
    """Return a required env var or exit with a clear error."""
    load_env()
    value = _clean(os.getenv(name))
    if not value:
        raise SystemExit(
            f"Missing {name}. Add it to {REPO_ROOT / '.env'} (see .env.example)."
        )
    if placeholder_prefix and value.startswith(placeholder_prefix):
        raise SystemExit(
            f"{name} still looks like a placeholder. Replace it in "
            f"{REPO_ROOT / '.env'} with your real key."
        )
    return value


def optional_env(name: str) -> str | None:
    load_env()
    value = _clean(os.getenv(name))
    return value or None
