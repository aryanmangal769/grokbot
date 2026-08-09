"""Minimal xAI helpers for summary (text + Imagine video only)."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from openai import OpenAI

from common.env import require_env

API_BASE = "https://api.x.ai/v1"
DEFAULT_TEXT_MODEL = "grok-4.5"
DEFAULT_VIDEO_MODEL = "grok-imagine-video"
DEFAULT_VIDEO_MODEL_V15 = "grok-imagine-video-1.5"


def load_api_key() -> str:
    return require_env("XAI_API_KEY", placeholder_prefix="xai-your-key")


def get_client() -> OpenAI:
    return OpenAI(api_key=load_api_key(), base_url=API_BASE)


def _request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    expect_json: bool = True,
) -> dict | bytes:
    data = None
    headers = {"Authorization": f"Bearer {load_api_key()}"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = resp.read()
            if expect_json:
                return json.loads(body.decode("utf-8"))
            return body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {path}: {detail}") from exc


def respond(prompt: str, *, model: str = DEFAULT_TEXT_MODEL) -> str:
    return get_client().responses.create(model=model, input=prompt).output_text


def generate_video(
    prompt: str,
    *,
    model: str = DEFAULT_VIDEO_MODEL_V15,
    poll_seconds: float = 5.0,
    duration: int | None = None,
    image_url: str | None = None,
) -> str:
    payload: dict[str, Any] = {"model": model, "prompt": prompt}
    if duration is not None:
        payload["duration"] = int(duration)
    if image_url:
        payload["image"] = {"url": image_url}

    started = _request("POST", "/videos/generations", payload=payload)
    assert isinstance(started, dict)
    request_id = started.get("request_id")
    if not request_id:
        raise SystemExit(f"No request_id in video start response: {started}")

    print(f"video request_id={request_id} model={model}", file=sys.stderr)
    while True:
        result = _request("GET", f"/videos/{request_id}")
        assert isinstance(result, dict)
        status = result.get("status")
        if status == "done":
            url = (result.get("video") or {}).get("url")
            if not url:
                raise SystemExit(f"Done but missing video.url: {result}")
            return url
        if status in {"failed", "expired"}:
            raise SystemExit(f"Video generation {status}: {json.dumps(result, indent=2)}")
        print(f"status={status} …", file=sys.stderr)
        time.sleep(poll_seconds)
