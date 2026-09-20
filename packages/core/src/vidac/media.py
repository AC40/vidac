from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mts", ".m2ts", ".mxf"}
Check = Callable[[], None]


def noop():
    pass


def executable(name: str) -> str:
    result = shutil.which(os.environ.get(f"VIDAC_{name.upper()}", name))
    if not result:
        raise RuntimeError(f"{name} is missing. Install FFmpeg and add it to PATH.")
    return result


def run_media(
    args: list[str], check: Check = noop, timeout: float = 7200, cwd: Path | None = None
) -> str:
    """Drain output to disk so large FFmpeg jobs cannot deadlock or exhaust memory."""
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=cwd)
        started = time.monotonic()
        try:
            while proc.poll() is None:
                check()
                if time.monotonic() - started > timeout:
                    raise RuntimeError(f"{Path(args[0]).name} exceeded its time limit.")
                time.sleep(0.1)
            check()
        except BaseException:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
        log.seek(0)
        output = log.read().decode("utf-8", errors="replace")
        if proc.returncode:
            raise RuntimeError(f"{Path(args[0]).name} failed:\n{output[-2500:]}")
        return output


def ffmpeg(args: list[str], check: Check = noop, cwd: Path | None = None) -> str:
    return run_media(
        [executable("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-threads", "2", *args],
        check,
        cwd=cwd,
    )


def probe(path: Path, check: Check = noop) -> dict:
    raw = json.loads(
        run_media(
            [
                executable("ffprobe"),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            check,
            timeout=120,
        )
    )
    video = next(
        (
            s
            for s in raw.get("streams", [])
            if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise ValueError("This file has no readable video stream.")
    fps_value = video.get("avg_frame_rate", "0/0")
    if fps_value in ("0/0", "0", "N/A"):
        fps_value = video.get("r_frame_rate", "25/1")
    fps = Fraction(fps_value)
    if not 1 <= fps <= 240:
        raise ValueError(f"Unsupported frame rate: {fps}.")
    duration = float(video.get("duration") or raw["format"].get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Cannot determine a positive media duration.")
    nominal_value = video.get("r_frame_rate", fps_value)
    nominal = Fraction(nominal_value if nominal_value not in {"0/0", "0", "N/A"} else fps_value)
    possible_vfr = bool(nominal and abs(float(nominal - fps)) / float(fps) > 0.001)
    rotation = next(
        (s.get("rotation", 0) for s in video.get("side_data_list", []) if "rotation" in s),
        int(video.get("tags", {}).get("rotate", 0)),
    )
    width, height = int(video["width"]), int(video["height"])
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return {
        "duration": duration,
        "fps": f"{fps.numerator}/{fps.denominator}",
        "frames": int(video["nb_frames"])
        if str(video.get("nb_frames", "")).isdigit()
        else max(1, round(duration * fps)),
        "width": width,
        "height": height,
        "has_audio": any(s["codec_type"] == "audio" for s in raw.get("streams", [])),
        "metadata": {
            "probe": raw,
            "possible_vfr": possible_vfr,
            "rotation": rotation,
            "color_primaries": video.get("color_primaries", "unknown"),
            "color_transfer": video.get("color_transfer", "unknown"),
            "color_space": video.get("color_space", "unknown"),
            "time_base": video.get("time_base"),
            "start_pts": video.get("start_pts", 0),
            "timecode": video.get("tags", {}).get(
                "timecode", raw["format"].get("tags", {}).get("timecode")
            ),
            "codec": video.get("codec_name"),
        },
    }


def digest(path: Path, check: Check = noop) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(8 * 1024 * 1024):
            check()
            h.update(chunk)
    return h.hexdigest()


def files_at(path: Path) -> list[Path]:
    path = path.expanduser().resolve()
    if path.is_file():
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError("Unsupported video extension.")
        return [path]
    if not path.is_dir():
        raise ValueError("File or folder does not exist on this computer.")
    files = sorted(
        p.resolve() for p in path.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    if len(files) > 500:
        raise ValueError("Import at most 500 files at a time; choose a smaller folder.")
    if not files:
        raise ValueError("No supported video files found in this folder.")
    return files


def create_proxy(source: Path, target: Path, info: dict, height: int, check: Check = noop):
    filters = [f"fps={info['fps']}", f"scale=-2:'min({height},ih)'", "setsar=1"]
    if info["metadata"].get("color_transfer") in {"smpte2084", "arib-std-b67"}:
        filters = [
            "zscale=t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=hable:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
            *filters,
        ]
    temporary = target.with_name("proxy.partial.mp4")
    ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            ",".join(filters),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "25",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        check,
    )
    temporary.replace(target)


def conform_vfr(source: Path, target: Path, info: dict, check: Check = noop) -> dict:
    # VFR needs an explicit CFR editing source. Keep camera originals untouched.
    candidates = [
        Fraction(24),
        Fraction(24000, 1001),
        Fraction(25),
        Fraction(30),
        Fraction(30000, 1001),
        Fraction(50),
        Fraction(60),
        Fraction(60000, 1001),
    ]
    fps = min(candidates, key=lambda x: abs(x - Fraction(info["fps"])))
    temporary = target.with_name("conformed.partial.mov")
    ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"fps={fps.numerator}/{fps.denominator}",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "1",
            "-pix_fmt",
            "yuv422p10le",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        check,
    )
    temporary.replace(target)
    conformed = probe(target, check)
    conformed["metadata"] = {
        **info["metadata"],
        "conformed": True,
        "edit_path": str(target),
        "conform_fps": conformed["fps"],
        "original_fps": info["fps"],
    }
    return conformed


def detect_shots(
    proxy: Path, fps: str, frames: int, threshold: float, minimum: float, check: Check = noop
) -> list[tuple[int, int]]:
    output = ffmpeg(
        [
            "-i",
            str(proxy),
            "-an",
            "-vf",
            f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ],
        check,
    )
    times = re.findall(r"pts_time:([0-9.]+)", output)
    rate = Fraction(fps)
    min_frames = max(1, round(minimum * rate))
    boundaries = [0]
    for timestamp in times:
        point = round(Fraction(timestamp) * rate)
        if point - boundaries[-1] >= min_frames and frames - point >= min_frames:
            boundaries.append(point)
    boundaries.append(frames)
    return list(zip(boundaries[:-1], boundaries[1:], strict=True))


def thumbnail(source: Path, target: Path, seconds: float, check: Check = noop):
    ffmpeg(
        [
            "-v",
            "error",
            "-ss",
            f"{seconds:.6f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2",
            "-q:v",
            "3",
            str(target),
        ],
        check,
    )
    if not target.is_file():
        raise RuntimeError("Could not decode a thumbnail at the requested frame.")


def waveform(source: Path, target: Path, check: Check = noop):
    ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            "[0:a:0]aformat=channel_layouts=mono,showwavespic=s=1000x120:colors=0xb8dc85",
            "-frames:v",
            "1",
            str(target),
        ],
        check,
    )


LOOKS = {
    "neutral": {"slope": [1, 1, 1], "offset": [0, 0, 0], "power": [1, 1, 1], "saturation": 1},
    "warm": {
        "slope": [1.035, 1.005, 0.96],
        "offset": [0.005, 0.002, 0],
        "power": [1.01, 1.01, 1.01],
        "saturation": 0.95,
    },
    "cool": {
        "slope": [0.965, 1.005, 1.035],
        "offset": [0, 0.002, 0.005],
        "power": [1.015, 1.015, 1.015],
        "saturation": 0.92,
    },
    "monochrome": {
        "slope": [1.04, 1.04, 1.04],
        "offset": [-0.01, -0.01, -0.01],
        "power": [1, 1, 1],
        "saturation": 0,
    },
}


def cube_lut(look: str, path: Path, size: int = 17):
    cdl = LOOKS[look]
    lines = [
        f'TITLE "Vidac {look} - Rec.709 preview"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0 0 0",
        "DOMAIN_MAX 1 1 1",
    ]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rgb = [
                    max(0, (v / (size - 1)) * s + o) ** p
                    for v, s, o, p in zip(
                        (r, g, b), cdl["slope"], cdl["offset"], cdl["power"], strict=True
                    )
                ]
                luma = sum(v * w for v, w in zip(rgb, (0.2126, 0.7152, 0.0722), strict=True))
                rgb = [max(0, min(1, luma + cdl["saturation"] * (v - luma))) for v in rgb]
                lines.append(" ".join(f"{v:.8f}" for v in rgb))
    path.write_text("\n".join(lines) + "\n")
