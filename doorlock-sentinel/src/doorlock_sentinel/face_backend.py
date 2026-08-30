from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from .artifacts import file_sha256
from .config import Settings
from .vector import normalize


class ModelUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class DetectedFace:
    bbox: tuple[int, int, int, int]
    landmarks: np.ndarray
    detector_score: float
    aligned_face: np.ndarray
    embedding: np.ndarray | None
    quality_score: float
    blur_score: float
    brightness: float
    rejection_reason: str | None = None


class FaceBackend(ABC):
    model_id: str
    embedding_dimension: int
    detector_sha256: str
    recognizer_sha256: str

    @abstractmethod
    def detect_and_embed(self, frame: np.ndarray) -> list[DetectedFace]:
        raise NotImplementedError

    @property
    def ready(self) -> bool:
        return True


class DisabledFaceBackend(FaceBackend):
    def __init__(self, settings: Settings):
        self.model_id = settings.model_id
        self.embedding_dimension = settings.embedding_dimension
        self.detector_sha256 = "disabled"
        self.recognizer_sha256 = "disabled"

    def detect_and_embed(self, frame: np.ndarray) -> list[DetectedFace]:
        del frame
        return []


class MockFaceBackend(FaceBackend):
    """Deterministic test backend. Production startup rejects it."""

    def __init__(self, settings: Settings):
        if settings.environment == "production":
            raise ModelUnavailableError("mock face backend is forbidden in production")
        self.model_id = settings.model_id
        self.embedding_dimension = settings.embedding_dimension
        self.detector_sha256 = "mock"
        self.recognizer_sha256 = "mock"
        self._counter = 0

    def detect_and_embed(self, frame: np.ndarray) -> list[DetectedFace]:
        self._counter += 1
        if self._counter % 2 == 0:
            return []
        height, width = frame.shape[:2]
        size = max(24, min(height, width) // 4)
        x = max(0, (width - size) // 2)
        y = max(0, (height - size) // 2)
        crop = frame[y : y + size, x : x + size].copy()
        seed = float(np.mean(frame)) + 1.0
        vector = np.sin(np.arange(self.embedding_dimension, dtype=np.float32) + seed)
        return [
            DetectedFace(
                bbox=(x, y, size, size),
                landmarks=np.zeros((5, 2), dtype=np.float32),
                detector_score=0.99,
                aligned_face=crop,
                embedding=normalize(vector),
                quality_score=0.9,
                blur_score=100.0,
                brightness=float(np.mean(frame)),
            )
        ]


def _distance_to_bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    return np.column_stack(
        (
            points[:, 0] - distance[:, 0],
            points[:, 1] - distance[:, 1],
            points[:, 0] + distance[:, 2],
            points[:, 1] + distance[:, 3],
        )
    )


def _distance_to_landmarks(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    output = []
    for index in range(0, distance.shape[1], 2):
        output.append(points + distance[:, index : index + 2])
    return np.stack(output, axis=1)


def _nms(boxes: np.ndarray, threshold: float) -> list[int]:
    x1, y1, x2, y2, scores = (
        boxes[:, 0],
        boxes[:, 1],
        boxes[:, 2],
        boxes[:, 3],
        boxes[:, 4],
    )
    areas = np.maximum(0, x2 - x1 + 1) * np.maximum(0, y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        current = int(order[0])
        keep.append(current)
        xx1 = np.maximum(x1[current], x1[order[1:]])
        yy1 = np.maximum(y1[current], y1[order[1:]])
        xx2 = np.minimum(x2[current], x2[order[1:]])
        yy2 = np.minimum(y2[current], y2[order[1:]])
        width = np.maximum(0.0, xx2 - xx1 + 1)
        height = np.maximum(0.0, yy2 - yy1 + 1)
        overlap = width * height
        union = areas[current] + areas[order[1:]] - overlap
        iou = np.divide(overlap, union, out=np.zeros_like(overlap), where=union > 0)
        remaining = np.where(iou <= threshold)[0]
        order = order[remaining + 1]
    return keep


class SCRFDArcFaceBackend(FaceBackend):
    _ARCFACE_TEMPLATE = np.array(
        [
            [38.2946, 51.6963],
            [73.5318, 51.5014],
            [56.0252, 71.7366],
            [41.5493, 92.3655],
            [70.7299, 92.2041],
        ],
        dtype=np.float32,
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model_id = settings.model_id
        detector = Path(settings.detector_model)
        recognizer = Path(settings.recognizer_model)
        missing = [path.name for path in (detector, recognizer) if not path.is_file()]
        if missing:
            raise ModelUnavailableError("missing model files: " + ", ".join(missing))
        self.detector_sha256 = file_sha256(detector)
        self.recognizer_sha256 = file_sha256(recognizer)
        self._require_hash(self.detector_sha256, settings.detector_sha256, "detector")
        self._require_hash(self.recognizer_sha256, settings.recognizer_sha256, "recognizer")

        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.ort_intra_threads
        options.inter_op_num_threads = settings.ort_inter_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        providers = ["CPUExecutionProvider"]
        self.detector = ort.InferenceSession(
            str(detector),
            sess_options=options,
            providers=providers,
        )
        self.recognizer = ort.InferenceSession(
            str(recognizer),
            sess_options=options,
            providers=providers,
        )
        self.detector_input = self.detector.get_inputs()[0].name
        self.recognizer_input = self.recognizer.get_inputs()[0].name
        self.detector_outputs = [item.name for item in self.detector.get_outputs()]
        self.embedding_dimension = self._infer_embedding_dimension()
        if (
            settings.embedding_dimension > 0
            and self.embedding_dimension != settings.embedding_dimension
        ):
            raise ModelUnavailableError(
                "recognizer embedding dimension does not match configured contract"
            )

    @staticmethod
    def _require_hash(actual: str, expected: str, label: str) -> None:
        if len(expected) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in expected
        ):
            raise ModelUnavailableError(f"{label} model checksum is not pinned")
        if expected and actual.lower() != expected.lower():
            raise ModelUnavailableError(f"{label} model checksum mismatch")

    def _infer_embedding_dimension(self) -> int:
        shape = self.recognizer.get_outputs()[0].shape
        if shape and isinstance(shape[-1], int) and shape[-1] > 0:
            return int(shape[-1])
        sample = np.zeros((1, 3, 112, 112), dtype=np.float32)
        result = self.recognizer.run(None, {self.recognizer_input: sample})[0]
        return int(np.asarray(result).reshape(-1).size)

    def _prepare_detector(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        size = self.settings.detector_input_size
        height, width = frame.shape[:2]
        scale = min(size / width, size / height)
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        resized = cv2.resize(frame, (resized_width, resized_height))
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        canvas[:resized_height, :resized_width] = resized
        blob = cv2.dnn.blobFromImage(
            canvas,
            scalefactor=1 / 128.0,
            size=(size, size),
            mean=(127.5, 127.5, 127.5),
            swapRB=True,
        )
        return blob, scale

    def _decode_detector(
        self,
        outputs: list[np.ndarray],
        scale: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        count = len(outputs)
        if count in {6, 9}:
            strides = (8, 16, 32)
            anchors = 2
        elif count in {10, 15}:
            strides = (8, 16, 32, 64, 128)
            anchors = 1
        else:
            raise ModelUnavailableError(f"unsupported SCRFD output count: {count}")
        stages = len(strides)
        has_landmarks = count == stages * 3
        score_parts: list[np.ndarray] = []
        box_parts: list[np.ndarray] = []
        landmark_parts: list[np.ndarray] = []
        input_size = self.settings.detector_input_size
        for index, stride in enumerate(strides):
            scores = np.asarray(outputs[index]).reshape(-1)
            box_distance = np.asarray(outputs[index + stages]).reshape(-1, 4) * stride
            height = input_size // stride
            width = input_size // stride
            centers = np.stack(np.mgrid[:height, :width][::-1], axis=-1).astype(
                np.float32
            )
            centers = (centers * stride).reshape(-1, 2)
            if anchors > 1:
                centers = np.repeat(centers, anchors, axis=0)
            usable = min(len(scores), len(box_distance), len(centers))
            scores = scores[:usable]
            box_distance = box_distance[:usable]
            centers = centers[:usable]
            positive = np.where(scores >= self.settings.detector_score_threshold)[0]
            if not positive.size:
                continue
            boxes = _distance_to_bbox(centers, box_distance)[positive]
            score_parts.append(scores[positive])
            box_parts.append(boxes)
            if has_landmarks:
                distances = (
                    np.asarray(outputs[index + stages * 2]).reshape(-1, 10)[:usable]
                    * stride
                )
                landmark_parts.append(
                    _distance_to_landmarks(centers, distances)[positive]
                )
        if not score_parts:
            return np.empty((0, 5), dtype=np.float32), np.empty(
                (0, 5, 2), dtype=np.float32
            )
        scores = np.concatenate(score_parts)
        boxes = np.concatenate(box_parts) / scale
        if landmark_parts:
            landmarks = np.concatenate(landmark_parts) / scale
        else:
            landmarks = np.zeros((len(boxes), 5, 2), dtype=np.float32)
        proposals = np.column_stack((boxes, scores))
        order = proposals[:, 4].argsort()[::-1]
        proposals = proposals[order]
        landmarks = landmarks[order]
        keep = _nms(proposals, self.settings.detector_nms_threshold)
        return proposals[keep], landmarks[keep]

    def _align(self, frame: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        matrix, _ = cv2.estimateAffinePartial2D(
            landmarks.astype(np.float32),
            self._ARCFACE_TEMPLATE,
            method=cv2.LMEDS,
        )
        if matrix is None:
            raise ValueError("face landmarks cannot be aligned")
        return cv2.warpAffine(
            frame,
            matrix,
            (112, 112),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )

    def _quality(
        self,
        aligned: np.ndarray,
        detector_score: float,
        face_pixels: int,
        frame_area: int,
        bbox_area: int,
    ) -> tuple[float, float, float, str | None]:
        gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        size_score = min(1.0, face_pixels / max(self.settings.minimum_face_pixels * 2, 1))
        sharpness = min(1.0, blur / max(self.settings.minimum_blur_score * 3, 1.0))
        if brightness < self.settings.minimum_brightness:
            exposure = brightness / max(self.settings.minimum_brightness, 1.0)
            rejection = "too_dark"
        elif brightness > self.settings.maximum_brightness:
            exposure = (255.0 - brightness) / max(
                255.0 - self.settings.maximum_brightness,
                1.0,
            )
            rejection = "too_bright"
        else:
            exposure = 1.0
            rejection = None
        area_score = min(1.0, (bbox_area / max(frame_area, 1)) / 0.08)
        quality = (
            0.30 * detector_score
            + 0.24 * size_score
            + 0.28 * sharpness
            + 0.10 * max(0.0, exposure)
            + 0.08 * area_score
        )
        if face_pixels < self.settings.minimum_face_pixels:
            rejection = "face_too_small"
        elif blur < self.settings.minimum_blur_score:
            rejection = "too_blurry"
        elif quality < self.settings.minimum_quality_score:
            rejection = rejection or "low_quality"
        return float(np.clip(quality, 0.0, 1.0)), blur, brightness, rejection

    def _embed(self, aligned: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(
            aligned,
            scalefactor=1 / 127.5,
            size=(112, 112),
            mean=(127.5, 127.5, 127.5),
            swapRB=True,
        )
        output = self.recognizer.run(None, {self.recognizer_input: blob})[0]
        vector = np.asarray(output, dtype=np.float32).reshape(-1)
        if vector.size != self.embedding_dimension:
            raise ModelUnavailableError("recognizer returned an unexpected embedding size")
        return normalize(vector)

    def detect_and_embed(self, frame: np.ndarray) -> list[DetectedFace]:
        blob, scale = self._prepare_detector(frame)
        raw = self.detector.run(self.detector_outputs, {self.detector_input: blob})
        boxes, landmarks = self._decode_detector(raw, scale)
        height, width = frame.shape[:2]
        result: list[DetectedFace] = []
        for proposal, keypoints in zip(boxes, landmarks, strict=True):
            x1, y1, x2, y2, score = proposal.tolist()
            x1 = int(max(0, min(width - 1, round(x1))))
            y1 = int(max(0, min(height - 1, round(y1))))
            x2 = int(max(x1 + 1, min(width, round(x2))))
            y2 = int(max(y1 + 1, min(height, round(y2))))
            bbox = (x1, y1, x2 - x1, y2 - y1)
            try:
                aligned = self._align(frame, keypoints)
            except (ValueError, cv2.error):
                continue
            quality, blur, brightness, rejection = self._quality(
                aligned,
                float(score),
                min(bbox[2], bbox[3]),
                width * height,
                bbox[2] * bbox[3],
            )
            embedding = None if rejection else self._embed(aligned)
            result.append(
                DetectedFace(
                    bbox=bbox,
                    landmarks=keypoints.astype(np.float32),
                    detector_score=float(score),
                    aligned_face=aligned,
                    embedding=embedding,
                    quality_score=quality,
                    blur_score=blur,
                    brightness=brightness,
                    rejection_reason=rejection,
                )
            )
        return result


def create_face_backend(settings: Settings) -> FaceBackend:
    if settings.face_backend == "disabled":
        return DisabledFaceBackend(settings)
    if settings.face_backend == "mock":
        return MockFaceBackend(settings)
    return SCRFDArcFaceBackend(settings)
