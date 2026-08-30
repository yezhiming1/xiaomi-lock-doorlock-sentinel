from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class VideoError(RuntimeError):
    pass


@dataclass(slots=True)
class VideoInfo:
    duration_seconds: float
    width: int
    height: int
    fps: float
    frame_count: int


def probe_video(path: Path) -> VideoInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,nb_frames:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        payload = json.loads(completed.stdout)
        stream = payload.get("streams", [{}])[0]
        duration = float(payload.get("format", {}).get("duration") or 0.0)
        rate = str(stream.get("r_frame_rate") or "0/1")
        num, den = (rate.split("/", 1) + ["1"])[:2]
        fps = float(num) / max(float(den), 1.0)
        frame_count = int(stream.get("nb_frames") or round(duration * fps))
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (
        subprocess.SubprocessError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        IndexError,
    ) as exc:
        raise VideoError(f"ffprobe failed for {path}: {exc}") from exc
    if duration <= 0 or width <= 0 or height <= 0:
        raise VideoError(f"invalid video metadata for {path}")
    return VideoInfo(duration, width, height, fps, frame_count)


def iter_sampled_frames(
    path: Path, sample_fps: float, max_frames: int
) -> Iterator[tuple[int, float, np.ndarray]]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise VideoError(f"OpenCV cannot open {path}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if source_fps <= 0:
        source_fps = 25.0
    interval = max(1, round(source_fps / max(sample_fps, 0.1)))
    frame_index = 0
    emitted = 0
    try:
        while emitted < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % interval == 0:
                yield frame_index, frame_index / source_fps, frame
                emitted += 1
            frame_index += 1
    finally:
        capture.release()


def write_jpeg(path: Path, image: np.ndarray, quality: int = 88) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp.jpg")
    if not cv2.imwrite(str(temp), image, [cv2.IMWRITE_JPEG_QUALITY, quality]):
        raise VideoError(f"cannot write image: {path}")
    temp.replace(path)
