"""xAI API template: text, image, video, and TTS.

Usage:
  python xai_client.py text "Your prompt"
  python xai_client.py image "A collage of London landmarks..."
  python xai_client.py video "A glowing crystal-powered rocket..."
  python xai_client.py tts "Hello!" --voice eve -o hello.mp3

  # shorthand (defaults to text):
  python xai_client.py "Your prompt"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
API_BASE = "https://api.x.ai/v1"

DEFAULT_TEXT_MODEL = "grok-4.5"
DEFAULT_IMAGE_MODEL = "grok-imagine-image-quality"
DEFAULT_VIDEO_MODEL = "grok-imagine-video"
DEFAULT_TTS_VOICE = "eve"
DEFAULT_TTS_LANGUAGE = "en"

DEFAULT_TEXT_PROMPT = (
    "Fix this function and explain the bug: "
    "function median(a){a.sort();return a[a.length/2]}"
)
DEFAULT_IMAGE_PROMPT = "A collage of London landmarks in a stenciled street-art style"
DEFAULT_VIDEO_PROMPT = "A glowing crystal-powered rocket launching from Mars"
DEFAULT_TTS_TEXT = "Hello! Welcome to the xAI Text to Speech API."


def load_api_key() -> str:
    load_dotenv(ROOT / ".env")

    key = os.getenv("XAI_API_KEY", "").strip()
    if key and not key.startswith("xai-your-key"):
        return key

    key_file = ROOT / "api_key.txt"
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        if key and not key.startswith("xai-your-key"):
            return key

    raise SystemExit(
        "Missing XAI API key. Set XAI_API_KEY in .env or put the key in api_key.txt "
        "(see .env.example)."
    )


def get_client() -> OpenAI:
    return OpenAI(api_key=load_api_key(), base_url=API_BASE)


def _request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    expect_json: bool = True,
) -> dict | bytes:
    """Raw REST helper for endpoints the OpenAI SDK does not cover."""
    data = None
    headers = {
        "Authorization": f"Bearer {load_api_key()}",
    }
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
    """POST /v1/responses → output text."""
    response = get_client().responses.create(model=model, input=prompt)
    return response.output_text


def generate_image(
    prompt: str,
    *,
    model: str = DEFAULT_IMAGE_MODEL,
    n: int = 1,
) -> list[str]:
    """POST /v1/images/generations → image URL(s)."""
    response = get_client().images.generate(model=model, prompt=prompt, n=n)
    return [item.url for item in response.data if item.url]


def generate_video(
    prompt: str,
    *,
    model: str = DEFAULT_VIDEO_MODEL,
    poll_seconds: float = 5.0,
) -> str:
    """POST /v1/videos/generations, then poll GET /v1/videos/{id} until done."""
    started = _request(
        "POST",
        "/videos/generations",
        payload={"model": model, "prompt": prompt},
    )
    assert isinstance(started, dict)
    request_id = started.get("request_id")
    if not request_id:
        raise SystemExit(f"No request_id in video start response: {started}")

    print(f"video request_id={request_id}", file=sys.stderr)
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


def text_to_speech(
    text: str,
    *,
    voice_id: str = DEFAULT_TTS_VOICE,
    language: str = DEFAULT_TTS_LANGUAGE,
    output_path: Path,
) -> Path:
    """POST /v1/tts → write MP3 bytes to output_path."""
    audio = _request(
        "POST",
        "/tts",
        payload={"text": text, "voice_id": voice_id, "language": language},
        expect_json=False,
    )
    assert isinstance(audio, bytes)
    output_path.write_bytes(audio)
    return output_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="xAI API template (text / image / video / tts)",
    )
    sub = parser.add_subparsers(dest="command")

    text_p = sub.add_parser("text", help="Responses API (default)")
    text_p.add_argument("prompt", nargs="*", help="Text prompt")
    text_p.add_argument("--model", default=DEFAULT_TEXT_MODEL)

    image_p = sub.add_parser("image", help="Image generation")
    image_p.add_argument("prompt", nargs="*", help="Image prompt")
    image_p.add_argument("--model", default=DEFAULT_IMAGE_MODEL)
    image_p.add_argument("-n", type=int, default=1, help="Number of images")

    video_p = sub.add_parser("video", help="Video generation (polls until done)")
    video_p.add_argument("prompt", nargs="*", help="Video prompt")
    video_p.add_argument("--model", default=DEFAULT_VIDEO_MODEL)
    video_p.add_argument("--poll", type=float, default=5.0, help="Poll interval seconds")

    tts_p = sub.add_parser("tts", help="Text to speech")
    tts_p.add_argument("text", nargs="*", help="Text to speak")
    tts_p.add_argument("--voice", default=DEFAULT_TTS_VOICE, dest="voice_id")
    tts_p.add_argument("--language", default=DEFAULT_TTS_LANGUAGE)
    tts_p.add_argument("-o", "--output", default="hello.mp3", type=Path)

    # Positional-only shorthand for text when no subcommand is given.
    parser.add_argument("prompt", nargs="*", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"text", "image", "video", "tts", "-h", "--help"}
    if not argv or argv[0] not in known:
        argv = ["text", *argv]

    args = _build_parser().parse_args(argv)
    command = args.command or "text"

    if command == "text":
        prompt = " ".join(args.prompt).strip() or DEFAULT_TEXT_PROMPT
        print(respond(prompt, model=args.model))
        return

    if command == "image":
        prompt = " ".join(args.prompt).strip() or DEFAULT_IMAGE_PROMPT
        for url in generate_image(prompt, model=args.model, n=args.n):
            print(url)
        return

    if command == "video":
        prompt = " ".join(args.prompt).strip() or DEFAULT_VIDEO_PROMPT
        print(generate_video(prompt, model=args.model, poll_seconds=args.poll))
        return

    if command == "tts":
        text = " ".join(args.text).strip() or DEFAULT_TTS_TEXT
        path = text_to_speech(
            text,
            voice_id=args.voice_id,
            language=args.language,
            output_path=args.output,
        )
        print(f"Saved to {path}")
        return

    raise SystemExit(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
