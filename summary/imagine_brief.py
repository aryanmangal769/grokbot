"""Camp-JSON → Imagine brief video (no TTS).

  python -m summary.imagine_brief --input path/to/camps.json
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.env import REPO_ROOT
from summary.cards import (
    render_camp_divider,
    render_leaning_card,
    render_summary_card,
    render_title_card,
    render_tweet_card,
)
from summary.media_dl import download_url
from summary.video import (
    change_speed,
    concat_av_clips,
    concat_clips,
    image_to_clip,
    media_duration,
    overlay_pip_bottom_right,
    pad_av_to_duration,
    run_ffmpeg,
)
from summary.xai import (
    DEFAULT_TEXT_MODEL,
    DEFAULT_VIDEO_MODEL,
    DEFAULT_VIDEO_MODEL_V15,
    generate_video,
    respond,
)

DEFAULT_HOST_IMAGE = Path(__file__).resolve().parent / "assets" / "host_placeholder.jpg"

TARGET_DURATION_SEC = 30.0
PART_DURATION_SEC = 15
DEFAULT_PLAYBACK_SPEED = 0.8  # 20% slower after assemble

SCRIPT_PROMPT = """You are a social-media news host. Turn this X camps analysis into a 30s on-camera monologue.

Return ONLY valid JSON (no markdown):
{{
  "spoken_part_a": "First ~15s (~35-45 English words MAX). Topic + overall summary gist, then camp1 thesis and 1-2 real post hooks (use real handles). Sparse.",
  "spoken_part_b": "Second ~15s (~35-45 English words MAX). Camp2 thesis and 1-2 real post hooks, then X_leaning. Clear ending.",
  "spoken_script": "part_a + space + part_b"
}}

Style: "Here's the summary on [topic]… Camp one argues… Camp two argues… Overall X leaning is…"
Rules:
- Use ONLY facts in the JSON (including real tweet text when present).
- Prefer real handles and real claims from tweet text over analyst paraphrase.
- No hashtags/URLs/emoji. Prefer less content over rushing.
- Camp "name" fields are the camp thesis labels.

INPUT JSON:
{payload}
"""

AVATAR_PROMPT = """Animate this person as a vertical social-media news host speaking to camera.

They deliver ONLY this English monologue with natural lip-sync:

"{script}"

CRITICAL:
- Bright solid pure green chroma-key background (#00FF00), flat, no set
- Medium close-up head and shoulders, same person as the still
- Native dialogue audio matching the monologue
- No subtitles, captions, logos, or music
"""


def clean_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def load_camp_input(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Input must be a JSON object: {path}")
    for key in ("topic", "tweet_summary", "top_tweets", "X_leaning"):
        if key not in data:
            raise SystemExit(f"Missing required field '{key}' in {path}")
    camps = data.get("top_tweets") or {}
    if not isinstance(camps, dict) or "camp1" not in camps or "camp2" not in camps:
        raise SystemExit("top_tweets must include camp1 and camp2 objects")
    # Keep provided corpus size; also count featured tweets
    featured = 0
    for camp in ("camp1", "camp2"):
        block = camps.get(camp) or {}
        if isinstance(block, dict):
            featured += sum(1 for k, v in block.items() if str(k).startswith("tweet") and v)
    if data.get("num_tweets") in (None, "", 0):
        data["num_tweets"] = featured
    data["_featured_tweets"] = featured
    data["_source_file"] = str(path)
    return data


def camp_display_name(block: dict[str, Any], camp_key: str) -> str:
    name = (block or {}).get("name")
    if name:
        return str(name)
    return "Camp 1" if camp_key == "camp1" else "Camp 2"


def iter_camp_tweets(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten tweet objects: camp1 then camp2 (tweet1..tweetN)."""
    out: list[dict[str, Any]] = []
    camps = data.get("top_tweets") or {}
    for camp_name in ("camp1", "camp2"):
        block = camps.get(camp_name) or {}
        if not isinstance(block, dict):
            continue
        camp_name_label = camp_display_name(block, camp_name)
        keys = sorted(k for k in block.keys() if str(k).startswith("tweet"))
        for k in keys:
            v = block.get(k)
            if not isinstance(v, dict):
                continue
            text = (v.get("text") or "").strip()
            if not text:
                h = (v.get("handle") or "").lstrip("@")
                s = (v.get("summary") or "").strip()
                text = f"@{h}: {s}" if h and s else s or h
            if not text:
                continue
            out.append(
                {
                    "camp": camp_name,
                    "camp_name": camp_name_label,
                    "key": k,
                    "text": text,
                    "handle": (v.get("handle") or "").lstrip("@") or None,
                    "name": v.get("name"),
                    "metrics": v.get("metrics"),
                    "url": v.get("url"),
                    "created_at": v.get("created_at"),
                    "verified": bool(v.get("verified")),
                    "verified_type": v.get("verified_type"),
                    "avatar_path": v.get("avatar_path"),
                    "profile_image_url": v.get("profile_image_url"),
                    "summary": v.get("summary"),
                    "id": v.get("id"),
                }
            )
    return out


def write_spoken_script(data: dict[str, Any], *, model: str) -> dict[str, str]:
    # Compact payload with real text when hydrated
    camps_out: dict[str, Any] = {}
    for camp_key in ("camp1", "camp2"):
        block = (data.get("top_tweets") or {}).get(camp_key) or {}
        entry: dict[str, Any] = {"name": block.get("name")}
        for k, v in block.items():
            if not str(k).startswith("tweet"):
                continue
            if isinstance(v, dict):
                entry[k] = {
                    "handle": v.get("handle"),
                    "text": v.get("text") or v.get("summary"),
                    "likes": (v.get("metrics") or {}).get("like_count"),
                    "url": v.get("url"),
                }
        camps_out[camp_key] = entry
    payload = {
        "topic": data.get("topic"),
        "tweet_summary": data.get("tweet_summary"),
        "top_tweets": camps_out,
        "X_leaning": data.get("X_leaning"),
        "num_tweets": data.get("num_tweets"),
    }
    raw = clean_json(
        respond(SCRIPT_PROMPT.format(payload=json.dumps(payload, indent=2, ensure_ascii=False)), model=model)
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Grok script JSON invalid:\n{raw}") from exc
    a = (parsed.get("spoken_part_a") or "").strip()
    b = (parsed.get("spoken_part_b") or "").strip()
    full = (parsed.get("spoken_script") or f"{a} {b}").strip()
    if not a or not b:
        raise SystemExit(f"Missing spoken_part_a/b:\n{raw}")
    return {"spoken_part_a": a, "spoken_part_b": b, "spoken_script": full}


def host_image_ref(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"Host image not found: {path}")
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    print(f"Static host still (no Imagine image) → {path}", file=sys.stderr)
    return f"data:{mime};base64,{b64}"


def generate_avatar_part(
    *,
    script: str,
    image_url: str,
    out_path: Path,
    video_model: str,
    duration: int,
    label: str,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file() and out_path.stat().st_size > 50_000:
        print(f"Reusing {label} → {out_path}", file=sys.stderr)
        return out_path
    prompt = AVATAR_PROMPT.format(script=script.replace('"', "'"))
    print(f"Imagine VIDEO {label} ~{duration}s ({video_model}) …", file=sys.stderr)
    url = None
    errors = []
    for kwargs in (
        {"model": video_model, "duration": duration, "image_url": image_url},
        {"model": video_model, "duration": duration},
        {"model": DEFAULT_VIDEO_MODEL, "image_url": image_url},
        {"model": DEFAULT_VIDEO_MODEL},
    ):
        try:
            url = generate_video(prompt, **kwargs)
            break
        except SystemExit as exc:
            errors.append(str(exc))
            print(f"  warn: {exc}", file=sys.stderr)
    if not url:
        raise SystemExit(f"{label} failed:\n" + "\n".join(errors))
    download_url(url, out_path)
    print(f"  saved {label} ({media_duration(out_path):.2f}s)", file=sys.stderr)
    return out_path


def build_main_from_camps(
    data: dict[str, Any],
    *,
    total_duration: float,
    outdir: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    """Local main video: title, summary, each tweet by camp, leaning."""
    work = outdir / "main_work"
    cards = work / "cards"
    clips_dir = work / "clips"
    for d in (work, cards, clips_dir):
        d.mkdir(parents=True, exist_ok=True)

    topic = str(data.get("topic") or "Topic")
    summary = str(data.get("tweet_summary") or "")
    leaning = str(data.get("X_leaning") or "")
    tweets = iter_camp_tweets(data)
    camps = data.get("top_tweets") or {}
    camp1_name = str((camps.get("camp1") or {}).get("name") or "Camp 1")
    camp2_name = str((camps.get("camp2") or {}).get("name") or "Camp 2")

    # Beats: title, summary, camp divider (not on tweet), real tweet UIs, leaning.
    # Camp labels only appear on short divider slides — never on the tweet card.
    beats: list[dict[str, Any]] = [
        {"kind": "title", "weight": 0.9},
        {"kind": "summary", "weight": 1.2},
    ]
    last_camp = None
    for t in tweets:
        if t["camp"] != last_camp:
            beats.append(
                {
                    "kind": "camp_divider",
                    "weight": 0.35,
                    "camp": t["camp"],
                    "camp_name": t.get("camp_name") or t["camp"],
                }
            )
            last_camp = t["camp"]
        beats.append({"kind": "tweet", "weight": 1.15, **t})
    beats.append({"kind": "leaning", "weight": 1.1})

    wsum = sum(b["weight"] for b in beats) or 1.0
    durs = [total_duration * (b["weight"] / wsum) for b in beats]

    print(f"Local main cards → {total_duration:.1f}s ({len(beats)} beats) …", file=sys.stderr)
    clip_paths: list[Path] = []
    timeline: list[dict[str, Any]] = []
    t = 0.0

    for i, (beat, dur) in enumerate(zip(beats, durs)):
        kind = beat["kind"]
        if kind == "title":
            img = render_title_card(
                topic,
                out_path=cards / "00_title.png",
                n_tweets=int(data.get("num_tweets") or len(tweets)),
            )
            label = "title"
        elif kind == "summary":
            img = render_summary_card(
                summary,
                out_path=cards / "01_summary.png",
                topic=topic,
            )
            label = "summary"
        elif kind == "camp_divider":
            camp = beat["camp"]
            img = render_camp_divider(
                camp,
                out_path=cards / f"{i:02d}_{camp}_divider.png",
                subtitle=str(beat.get("camp_name") or ""),
            )
            label = f"divider/{camp}"
        elif kind == "leaning":
            img = render_leaning_card(
                leaning,
                out_path=cards / "99_leaning.png",
                topic=topic,
                camp1_name=camp1_name,
                camp2_name=camp2_name,
            )
            label = "leaning"
        else:
            # Real X-style tweet — avatar, premium badge, real text + metrics
            img = render_tweet_card(
                beat.get("text") or "",
                out_path=cards / f"{i:02d}_tweet_{beat['camp']}_{beat['key']}.png",
                handle=beat.get("handle"),
                name=beat.get("name") or beat.get("handle"),
                body=beat.get("text"),
                created_at=beat.get("created_at"),
                metrics=beat.get("metrics"),
                verified=bool(beat.get("verified")),
                verified_type=beat.get("verified_type"),
                avatar_path=beat.get("avatar_path"),
            )
            label = f"tweet/{beat.get('handle') or beat['key']}"

        clip = clips_dir / f"{i:02d}.mp4"
        image_to_clip(img, dur, clip)
        clip_paths.append(clip)
        timeline.append(
            {
                "label": label,
                "start": round(t, 3),
                "end": round(t + dur, 3),
                "duration": round(dur, 3),
            }
        )
        t += dur

    silent = work / "main_silent.mp4"
    concat_clips(clip_paths, silent)
    main = outdir / "main_camps_30s.mp4"
    run_ffmpeg(
        [
            "-i", str(silent), "-t", f"{total_duration:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(main),
        ],
        label="main_trim",
    )
    if media_duration(main) + 0.05 < total_duration:
        padded = work / "main_padded.mp4"
        run_ffmpeg(
            [
                "-i", str(main),
                "-vf",
                f"tpad=stop_mode=clone:stop_duration={total_duration:.3f},trim=duration={total_duration:.3f}",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(padded),
            ],
            label="main_pad",
        )
        main.write_bytes(padded.read_bytes())
    print(f"  main → {main} ({media_duration(main):.2f}s)", file=sys.stderr)
    return main, timeline


def build_30s_avatar(
    *,
    part_a: str,
    part_b: str,
    host_ref: str,
    outdir: Path,
    video_model: str,
    target_sec: float,
) -> tuple[Path, Path]:
    parts = outdir / "avatar_parts"
    parts.mkdir(parents=True, exist_ok=True)
    raw_a = generate_avatar_part(
        script=part_a, image_url=host_ref, out_path=parts / "part_a_raw.mp4",
        video_model=video_model, duration=PART_DURATION_SEC, label="part_a",
    )
    raw_b = generate_avatar_part(
        script=part_b, image_url=host_ref, out_path=parts / "part_b_raw.mp4",
        video_model=video_model, duration=PART_DURATION_SEC, label="part_b",
    )
    fit_a, fit_b = parts / "part_a_15.mp4", parts / "part_b_15.mp4"
    pad_av_to_duration(raw_a, float(PART_DURATION_SEC), fit_a)
    pad_av_to_duration(raw_b, float(PART_DURATION_SEC), fit_b)
    joined = parts / "avatar_joined.mp4"
    print("Concat part_a + part_b …", file=sys.stderr)
    concat_av_clips([fit_a, fit_b], joined)
    avatar = outdir / "avatar_30s.mp4"
    pad_av_to_duration(joined, target_sec, avatar)
    print(f"  avatar → {avatar} ({media_duration(avatar):.3f}s)", file=sys.stderr)
    audio = outdir / "avatar_audio_30s.m4a"
    run_ffmpeg(
        ["-i", str(avatar), "-vn", "-c:a", "aac", "-b:a", "192k",
         "-t", f"{target_sec:.3f}", str(audio)],
        label="extract_audio",
    )
    return avatar, audio


def run_pipeline(
    data: dict[str, Any],
    *,
    outdir: Path,
    text_model: str,
    video_model: str,
    host_image: Path,
    target_sec: float = TARGET_DURATION_SEC,
    playback_speed: float = DEFAULT_PLAYBACK_SPEED,
    chromakey: bool = True,
    hydrate: bool = True,
) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)

    if hydrate:
        from summary.hydrate import hydrate_camp_data

        data = hydrate_camp_data(
            data,
            avatar_cache=outdir / "avatar_cache",
        )
        (outdir / "input_hydrated.json").write_text(
            json.dumps(
                {k: v for k, v in data.items() if not str(k).startswith("_")},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    print("Grok text: camp monologue (2×15s parts) …", file=sys.stderr)
    script = write_spoken_script(data, model=text_model)
    (outdir / "summary_script.txt").write_text(
        "=== PART A ===\n" + script["spoken_part_a"]
        + "\n\n=== PART B ===\n" + script["spoken_part_b"]
        + "\n\n=== FULL ===\n" + script["spoken_script"] + "\n",
        encoding="utf-8",
    )
    print(f"  A: {len(script['spoken_part_a'].split())} words", file=sys.stderr)
    print(f"  B: {len(script['spoken_part_b'].split())} words", file=sys.stderr)

    host_ref = host_image_ref(host_image)
    try:
        (outdir / "host_still.jpg").write_bytes(host_image.read_bytes())
    except OSError:
        pass

    avatar, audio = build_30s_avatar(
        part_a=script["spoken_part_a"],
        part_b=script["spoken_part_b"],
        host_ref=host_ref,
        outdir=outdir,
        video_model=video_model,
        target_sec=target_sec,
    )

    main, timeline = build_main_from_camps(data, total_duration=target_sec, outdir=outdir)

    print(f"Merge PiP chromakey={'on' if chromakey else 'off'} …", file=sys.stderr)
    raw = outdir / "summary_imagine_raw.mp4"
    overlay_pip_bottom_right(main, avatar, raw, pip_width=420, margin=40, chromakey=chromakey)
    base = outdir / "summary_imagine_base30.mp4"
    if abs(media_duration(raw) - target_sec) > 0.2:
        pad_av_to_duration(raw, target_sec, base)
    else:
        base.write_bytes(raw.read_bytes())

    final = outdir / "summary_imagine.mp4"
    if abs(playback_speed - 1.0) > 1e-6:
        print(f"Playback speed {playback_speed} (20% slower if 0.8) …", file=sys.stderr)
        change_speed(base, playback_speed, final)
    else:
        final.write_bytes(base.read_bytes())

    final_len = media_duration(final)
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "camp_json_imagine_2x15_pip",
        "branch_note": "main",
        "no_tts": True,
        "input_schema": "topic/tweet_summary/top_tweets.camp1|camp2/X_leaning/num_tweets",
        "source_file": data.get("_source_file"),
        "topic": data.get("topic"),
        "X_leaning": data.get("X_leaning"),
        "num_tweets": data.get("num_tweets"),
        "target_duration_sec_before_speed": target_sec,
        "playback_speed": playback_speed,
        "actual_duration_sec": round(final_len, 3),
        "imagine_videos": 2,
        "chromakey_person_only": chromakey,
        "text_model": text_model,
        "video_model": video_model,
        "host_image": str(host_image),
        "script": script,
        "timeline": timeline,
        "paths": {
            "final_video": str(final),
            "main": str(main),
            "avatar_30s": str(avatar),
            "audio_30s": str(audio),
            "base_30s": str(base),
        },
    }
    (outdir / "summary_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (outdir / "video_timeline.json").write_text(
        json.dumps({"duration_sec": round(final_len, 3), "timeline": timeline}, indent=2) + "\n",
        encoding="utf-8",
    )
    # echo input beside outputs
    (outdir / "input_camps.json").write_text(
        json.dumps({k: v for k, v in data.items() if not str(k).startswith("_")}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"Done → {final} ({final_len:.2f}s)", file=sys.stderr)
    return meta


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Camp JSON → Imagine brief video")
    p.add_argument("--input", type=Path, required=True, help="Camp analysis JSON")
    p.add_argument("--model", default=DEFAULT_TEXT_MODEL)
    p.add_argument("--video-model", default=DEFAULT_VIDEO_MODEL_V15)
    p.add_argument("--host-image", type=Path, default=DEFAULT_HOST_IMAGE)
    p.add_argument("--duration", type=float, default=TARGET_DURATION_SEC)
    p.add_argument("--speed", type=float, default=DEFAULT_PLAYBACK_SPEED)
    p.add_argument("--no-chromakey", action="store_true")
    p.add_argument("--no-hydrate", action="store_true")
    p.add_argument("-o", "--outdir", type=Path, default=Path("outputs/summary/run"))
    args = p.parse_args(argv)

    input_path = args.input if args.input.is_absolute() else REPO_ROOT / args.input
    outdir = args.outdir if args.outdir.is_absolute() else REPO_ROOT / args.outdir
    host = args.host_image
    if not host.is_file():
        alt = REPO_ROOT / host
        host = alt if alt.is_file() else DEFAULT_HOST_IMAGE
    if not input_path.is_file():
        raise SystemExit(f"Input not found: {input_path}")

    data = load_camp_input(input_path)
    print(f"input: {input_path}", file=sys.stderr)
    print(f"topic: {data.get('topic')}", file=sys.stderr)

    meta = run_pipeline(
        data,
        outdir=outdir,
        text_model=args.model,
        video_model=args.video_model,
        host_image=host,
        target_sec=float(args.duration),
        playback_speed=float(args.speed),
        chromakey=not args.no_chromakey,
        hydrate=not args.no_hydrate,
    )
    print(f"Final      → {meta['paths']['final_video']} ({meta['actual_duration_sec']}s @ {args.speed}x)")
    print(f"Main       → {meta['paths']['main']}")
    print(f"Avatar     → {meta['paths']['avatar_30s']} (2× Imagine)")
    print(f"Script     → {outdir / 'summary_script.txt'}")
    print(f"Input copy → {outdir / 'input_camps.json'}")


if __name__ == "__main__":
    main()
