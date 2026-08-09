"""ffmpeg helpers for summary videos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg

from summary.cards import H, W

FPS = 30
SNAPSHOT_HOLD = 1.6


def ffmpeg_bin() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], *, label: str = "ffmpeg") -> None:
    cmd = [ffmpeg_bin(), "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"{label} failed ({proc.returncode}):\n{(proc.stderr or '')[-2500:]}")


def media_duration(path: Path) -> float:
    proc = subprocess.run([ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True)
    err = proc.stderr or ""
    import re

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    if not m:
        raise SystemExit(f"Could not read duration for {path}")
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return max(0.05, h * 3600 + mi * 60 + s)


def _has_audio(path: Path) -> bool:
    proc = subprocess.run([ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True)
    return "Audio:" in (proc.stderr or "")


def _scale_pad() -> str:
    return (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FPS}"
    )


def image_to_clip(image: Path, duration: float, out: Path) -> Path:
    duration = max(0.05, duration)
    run_ffmpeg(
        [
            "-loop", "1", "-i", str(image),
            "-t", f"{duration:.3f}",
            "-vf", _scale_pad(),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
        ],
        label=f"image_clip {out.name}",
    )
    return out


def _concat_list_line(path: Path) -> str:
    # ffmpeg concat demuxer: file '/abs/path'
    esc = str(path.resolve()).replace("'", r"'\''")
    return f"file '{esc}'"


def concat_clips(clips: list[Path], out: Path) -> Path:
    list_file = out.with_suffix(".txt")
    list_file.write_text(
        "\n".join(_concat_list_line(c) for c in clips) + "\n",
        encoding="utf-8",
    )
    run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
        ],
        label="concat_video",
    )
    return out


def concat_av_clips(clips: list[Path], out: Path) -> Path:
    list_file = out.with_suffix(".avconcat.txt")
    list_file.write_text(
        "\n".join(_concat_list_line(c) for c in clips) + "\n",
        encoding="utf-8",
    )
    run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(out),
        ],
        label="concat_av",
    )
    return out


def pad_av_to_duration(src: Path, duration: float, out: Path) -> Path:
    duration = max(0.1, float(duration))
    has_a = _has_audio(src)
    if has_a:
        fc = (
            f"[0:v]tpad=stop_mode=clone:stop_duration={duration:.3f},"
            f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[v];"
            f"[0:a]aresample=async=1,apad=whole_dur={duration:.3f},"
            f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    else:
        fc = (
            f"[0:v]tpad=stop_mode=clone:stop_duration={duration:.3f},"
            f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[v]"
        )
        maps = ["-map", "[v]", "-an"]
    run_ffmpeg(
        ["-i", str(src), "-filter_complex", fc, *maps,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}", str(out)],
        label=f"pad_av {out.name}",
    )
    return out


def change_speed(src: Path, speed: float, out: Path) -> Path:
    """Change playback rate; re-encode CFR 30fps + faststart for player reliability."""
    speed = float(speed)
    if speed <= 0:
        raise SystemExit(f"speed must be > 0, got {speed}")

    def atempo_chain(s: float) -> str:
        parts, remaining = [], s
        while remaining < 0.5 - 1e-9:
            parts.append("atempo=0.5")
            remaining /= 0.5
        while remaining > 2.0 + 1e-9:
            parts.append("atempo=2.0")
            remaining /= 2.0
        parts.append(f"atempo={remaining:.6f}")
        return ",".join(parts)

    has_a = _has_audio(src)
    if abs(speed - 1.0) < 1e-6:
        vfilter = f"fps={FPS},format=yuv420p"
        if has_a:
            fc = f"[0:v]{vfilter}[v];[0:a]aresample=48000[a]"
            maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        else:
            fc = f"[0:v]{vfilter}[v]"
            maps = ["-map", "[v]", "-an"]
    else:
        vfilter = f"setpts=PTS/{speed:.6f},fps={FPS},format=yuv420p"
        if has_a:
            fc = f"[0:v]{vfilter}[v];[0:a]{atempo_chain(speed)},aresample=48000[a]"
            maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        else:
            fc = f"[0:v]{vfilter}[v]"
            maps = ["-map", "[v]", "-an"]

    run_ffmpeg(
        [
            "-i", str(src),
            "-filter_complex", fc,
            *maps,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out),
        ],
        label=f"speed_{speed}",
    )
    return out


def overlay_pip_bottom_right(
    base: Path,
    pip: Path,
    out: Path,
    *,
    pip_width: int = 400,
    margin: int = 40,
    chromakey: bool = True,
    key_color: str = "0x00FF00",
    key_similarity: float = 0.30,
    key_blend: float = 0.08,
) -> Path:
    if chromakey:
        pip_chain = (
            f"[1:v]chromakey={key_color}:{key_similarity}:{key_blend},"
            f"scale={pip_width}:-2:force_original_aspect_ratio=decrease[pip]"
        )
    else:
        pip_chain = f"[1:v]scale={pip_width}:-2:force_original_aspect_ratio=decrease[pip]"
    fc = f"{pip_chain};[0:v][pip]overlay=W-w-{margin}:H-h-{margin}:format=auto[vout]"
    run_ffmpeg(
        [
            "-i", str(base), "-i", str(pip),
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "1:a?",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(out),
        ],
        label=f"pip {out.name}",
    )
    return out
