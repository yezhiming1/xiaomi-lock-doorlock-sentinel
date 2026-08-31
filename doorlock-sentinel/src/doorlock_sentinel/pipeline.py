from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
from sqlalchemy import select

from .artifacts import (
    export_manifest,
    file_sha256,
    promote_track_artifacts,
    register_artifact,
)
from .config import Settings
from .db import Database
from .face_backend import (
    FaceBackend,
    ModelUnavailableError,
    create_face_backend,
)
from .media_names import derived_image_name, is_current_video_filename
from .metadata import read_sidecar
from .models import (
    AnalysisSkip,
    CannotLink,
    Event,
    FaceTrack,
    IngestState,
    ModelRegistry,
    RuntimeSetting,
    VideoIngest,
    utcnow,
)
from .outbox import enqueue
from .recognition import (
    IdentityMatcher,
    UnknownClusterer,
    admit_prototype,
)
from .risk import RiskScorer, TrackRiskInput
from .tracking import (
    FaceSample,
    FaceTrackResult,
    build_tracks,
    cooccurring_track_pairs,
)
from .vector import pack_vector
from .video import VideoInfo, iter_sampled_frames, probe_video, write_jpeg

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AnalysisResult:
    info: VideoInfo
    tracks: list[FaceTrackResult]
    skips: list[dict[str, object]]
    sampled_frames: int


class ProcessingPipeline:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.matcher = IdentityMatcher(settings)
        self.clusterer = UnknownClusterer(settings)
        self.risk = RiskScorer(settings)
        self.backend: FaceBackend | None = None
        self.readiness_error: str | None = None
        try:
            self.backend = create_face_backend(settings)
            self._register_model()
        except (ModelUnavailableError, OSError, ValueError) as exc:
            self.readiness_error = f"{type(exc).__name__}: {exc}"
            logger.error("face backend unavailable code=MODEL_UNAVAILABLE")

    @property
    def ready(self) -> bool:
        return self.backend is not None and self.readiness_error is None

    @staticmethod
    def _runtime_flag(session, key: str, fallback: bool) -> bool:
        row = session.get(RuntimeSetting, key)
        return bool(row.value_json) if row is not None else fallback

    def _register_model(self) -> None:
        assert self.backend is not None
        with self.database.session() as session:
            existing = session.get(ModelRegistry, self.backend.model_id)
            if existing:
                if (
                    existing.detector_sha256 != self.backend.detector_sha256
                    or existing.recognizer_sha256 != self.backend.recognizer_sha256
                    or existing.embedding_dimension != self.backend.embedding_dimension
                ):
                    raise ModelUnavailableError(
                        "model id already belongs to a different model contract"
                    )
                return
            session.add(
                ModelRegistry(
                    model_id=self.backend.model_id,
                    detector_sha256=self.backend.detector_sha256,
                    recognizer_sha256=self.backend.recognizer_sha256,
                    embedding_dimension=self.backend.embedding_dimension,
                    metadata_json={
                        "detector": "SCRFD 2.5G",
                        "recognizer": "ArcFace R50",
                        "runtime": "ONNX Runtime CPU",
                    },
                )
            )

    def analyze(self, path: Path) -> AnalysisResult:
        if not self.backend:
            raise ModelUnavailableError(self.readiness_error or "face backend unavailable")
        info = probe_video(path)
        samples: list[FaceSample] = []
        skips: list[dict[str, object]] = []
        sampled_frames = 0
        for frame_index, timestamp, frame in iter_sampled_frames(
            path,
            self.settings.sample_fps,
            self.settings.max_sampled_frames,
        ):
            sampled_frames += 1
            faces = self.backend.detect_and_embed(frame)
            for face in faces:
                if face.rejection_reason or face.embedding is None:
                    skips.append(
                        {
                            "reason": face.rejection_reason or "embedding_unavailable",
                            "frame_index": frame_index,
                            "detector_score": face.detector_score,
                            "quality_score": face.quality_score,
                            "details": {
                                "face_pixels": min(face.bbox[2], face.bbox[3]),
                                "blur_score": round(face.blur_score, 2),
                                "brightness": round(face.brightness, 2),
                            },
                        }
                    )
                    continue
                samples.append(
                    FaceSample(
                        frame_index=frame_index,
                        timestamp=timestamp,
                        frame=frame,
                        face=face,
                    )
                )
        tracks = build_tracks(
            samples,
            min_similarity=self.settings.track_min_similarity,
            strong_similarity=self.settings.track_strong_similarity,
            min_iou=self.settings.track_min_iou,
            max_center_distance=self.settings.track_max_center_distance,
            max_gap_seconds=self.settings.max_track_gap_seconds,
            max_samples=self.settings.max_track_samples,
        )
        if not faces_seen(samples, skips):
            skips.append(
                {
                    "reason": "no_face_detected",
                    "frame_index": None,
                    "detector_score": None,
                    "quality_score": None,
                    "details": {"sampled_frames": sampled_frames},
                }
            )
        return AnalysisResult(info, tracks, skips, sampled_frames)

    def _write_track_media(
        self,
        video_name: str,
        event_id: str,
        occurred_at,
        track: FaceTrackResult,
    ) -> tuple[Path, Path]:
        day = occurred_at.strftime("%Y/%m/%d")
        target_dir = self.settings.derived_dir / day
        target_dir.mkdir(parents=True, exist_ok=True)
        best = track.best_sample
        scene = best.frame.copy()
        x, y, width, height = best.face.bbox
        cv2.rectangle(scene, (x, y), (x + width, y + height), (155, 91, 36), 2)
        if is_current_video_filename(video_name):
            frame_name = derived_image_name(video_name, track.index, "scene")
            face_name = derived_image_name(video_name, track.index, "face")
        else:
            frame_name = f"{event_id}_track{track.index:02d}_scene.jpg"
            face_name = f"{event_id}_track{track.index:02d}_face.jpg"
        frame_path = target_dir / frame_name
        face_path = target_dir / face_name
        write_jpeg(frame_path, scene, quality=86)
        write_jpeg(face_path, best.face.aligned_face, quality=92)
        return frame_path, face_path

    @staticmethod
    def _logical_source(metadata, source: Path) -> str:
        return f"events/{metadata.occurred_at:%Y/%m/%d}/{source.name}"

    @staticmethod
    def _logical_derived(metadata, path: Path) -> str:
        return f"derived/{metadata.occurred_at:%Y/%m/%d}/{path.name}"

    def process(self, ingest_id: str) -> str:
        if not self.ready:
            raise ModelUnavailableError(self.readiness_error or "model is not ready")
        started = time.perf_counter()
        with self.database.session() as session:
            ingest = session.get(VideoIngest, ingest_id)
            if not ingest:
                raise ValueError("ingest record does not exist")
            if ingest.state != IngestState.PROCESSING.value:
                raise ValueError("ingest record is not leased for processing")
            source = Path(ingest.source_path)
            known_hash = ingest.sha256
        if not source.is_file():
            raise FileNotFoundError(source)
        sha256 = known_hash or file_sha256(source)
        metadata = read_sidecar(source)
        analysis = self.analyze(source)
        cooccurring_pairs = cooccurring_track_pairs(analysis.tracks)
        written: list[Path] = []
        track_media: dict[int, tuple[Path, Path]] = {}
        committed = False
        try:
            event_id = f"evt_{sha256[:32]}"
            for track in analysis.tracks:
                media = self._write_track_media(
                    source.name,
                    event_id,
                    metadata.occurred_at,
                    track,
                )
                track_media[track.index] = media
                written.extend(media)
            with self.database.session() as session:
                ingest = session.get(VideoIngest, ingest_id)
                if not ingest:
                    raise ValueError("ingest record disappeared")
                duplicate = session.scalar(
                    select(VideoIngest).where(
                        VideoIngest.id != ingest.id,
                        VideoIngest.sha256 == sha256,
                        VideoIngest.state.in_(
                            [IngestState.PROCESSED.value, IngestState.DUPLICATE.value]
                        ),
                    )
                )
                if duplicate:
                    for path in written:
                        path.unlink(missing_ok=True)
                    written.clear()
                    ingest.sha256 = sha256
                    ingest.state = IngestState.DUPLICATE.value
                    ingest.duplicate_of_id = duplicate.duplicate_of_id or duplicate.id
                    ingest.event_id = duplicate.event_id
                    ingest.completed_at = utcnow()
                    ingest.lease_owner = None
                    ingest.lease_until = None
                    return duplicate.event_id or ""

                source_artifact = register_artifact(
                    session,
                    self.settings,
                    path=source,
                    artifact_type="source_video",
                    logical_path=self._logical_source(metadata, source),
                    retention_class="ordinary_35d",
                    sha256=sha256,
                    created_at=metadata.occurred_at,
                )
                event = Event(
                    id=event_id,
                    video_ingest_id=ingest.id,
                    source_artifact_id=source_artifact.id,
                    external_event_id=metadata.event_id,
                    occurred_at=metadata.occurred_at,
                    downloaded_at=metadata.downloaded_at,
                    source=metadata.source,
                    event_type=metadata.event_type,
                    unlock_method=metadata.unlock_method,
                    operation_user=metadata.operation_user,
                    duration_seconds=analysis.info.duration_seconds,
                    metadata_json={
                        **metadata.as_dict(),
                        "sampled_frames": analysis.sampled_frames,
                        "video": {
                            "width": analysis.info.width,
                            "height": analysis.info.height,
                            "fps": analysis.info.fps,
                        },
                    },
                    analysis_state="complete" if analysis.tracks else "skipped",
                    track_count=len(analysis.tracks),
                    skipped_face_count=len(analysis.skips),
                )
                session.add(event)
                session.flush()
                for item in analysis.skips:
                    session.add(
                        AnalysisSkip(
                            event_id=event.id,
                            reason=str(item["reason"]),
                            frame_index=item["frame_index"],
                            detector_score=item["detector_score"],
                            quality_score=item["quality_score"],
                            details_json=item["details"],
                        )
                    )

                track_rows: list[FaceTrack] = []
                for result in analysis.tracks:
                    frame_path, face_path = track_media[result.index]
                    frame_artifact = register_artifact(
                        session,
                        self.settings,
                        path=frame_path,
                        artifact_type="scene_preview",
                        logical_path=self._logical_derived(metadata, frame_path),
                        retention_class="ordinary_35d",
                        created_at=metadata.occurred_at,
                    )
                    face_artifact = register_artifact(
                        session,
                        self.settings,
                        path=face_path,
                        artifact_type="face_sample",
                        logical_path=self._logical_derived(metadata, face_path),
                        retention_class="ordinary_35d",
                        created_at=metadata.occurred_at,
                    )
                    best = result.best_sample
                    row = FaceTrack(
                        event_id=event.id,
                        track_index=result.index,
                        model_id=self.backend.model_id,
                        embedding=pack_vector(result.embedding),
                        embedding_dimension=result.embedding.size,
                        quality_score=result.quality_score,
                        sample_count=len(result.samples),
                        first_timestamp=result.first_timestamp,
                        last_timestamp=result.last_timestamp,
                        best_bbox_json=list(best.face.bbox),
                        best_face_artifact_id=face_artifact.id,
                        best_frame_artifact_id=frame_artifact.id,
                    )
                    session.add(row)
                    session.flush()
                    track_rows.append(row)

                rows_by_index = {row.track_index: row for row in track_rows}
                for left_index, right_index in cooccurring_pairs:
                    first, second = sorted(
                        (rows_by_index[left_index].id, rows_by_index[right_index].id)
                    )
                    session.add(
                        CannotLink(
                            left_track_id=first,
                            right_track_id=second,
                            reason="same_frame",
                        )
                    )

                accepted_people: set[str] = set()
                assigned_people_by_track: dict[int, str] = {}
                risk_inputs: list[TrackRiskInput] = []
                for row in track_rows:
                    vector = next(
                        result.embedding
                        for result in analysis.tracks
                        if result.index == row.track_index
                    )
                    excluded_people = {
                        person_id
                        for other_index, person_id in assigned_people_by_track.items()
                        if tuple(sorted((row.track_index, other_index)))
                        in cooccurring_pairs
                    }
                    decision = self.matcher.match(
                        session,
                        vector,
                        row.model_id,
                        excluded_person_ids=excluded_people,
                    )
                    row.decision = decision.decision
                    row.decision_reason = decision.reason
                    row.top_similarity = decision.top_similarity
                    row.second_similarity = decision.second_similarity
                    if decision.decision == "known" and decision.person:
                        row.person_id = decision.person.id
                        accepted_people.add(decision.person.id)
                        assigned_people_by_track[row.track_index] = decision.person.id
                        self.matcher.observe_person(
                            session,
                            decision.person,
                            event,
                            row,
                            decision.top_similarity,
                        )
                        prototype = admit_prototype(
                            session,
                            self.settings,
                            decision.person,
                            row,
                            reason="continuous_learning",
                        )
                        row.representative = prototype is not None
                        risk_inputs.append(
                            TrackRiskInput(
                                decision="known",
                                relationship=decision.person.relationship,
                                quality_score=row.quality_score,
                            )
                        )
                    else:
                        assignment = self.clusterer.assign(
                            session,
                            event,
                            row,
                            vector,
                            row.quality_score,
                        )
                        if (
                            row.quality_score >= self.settings.prototype_quality_score
                            and (
                                assignment.created
                                or assignment.similarity
                                < self.settings.prototype_diversity_similarity
                            )
                        ):
                            row.representative = True
                            promote_track_artifacts(session, row)
                        risk_inputs.append(
                            TrackRiskInput(
                                decision=row.decision,
                                cluster_event_count=assignment.cluster.event_count,
                                quality_score=row.quality_score,
                            )
                        )

                risk = self.risk.score(
                    metadata,
                    metadata.occurred_at,
                    analysis.info.duration_seconds,
                    risk_inputs,
                )
                event.risk_score = risk.score
                event.risk_level = risk.level
                event.risk_reasons = risk.reasons
                if self._runtime_flag(
                    session,
                    "identity_notifications_enabled",
                    self.settings.identity_notifications_enabled,
                ) and accepted_people:
                    enqueue(
                        session,
                        topic="identity.recognized",
                        dedupe_key=f"identity:{event.id}",
                        priority=40,
                        payload={"event_id": event.id, "person_count": len(accepted_people)},
                    )
                if (
                    self._runtime_flag(
                        session,
                        "risk_notifications_enabled",
                        self.settings.risk_notifications_enabled,
                    )
                    and risk.level in {"alert", "urgent"}
                ):
                    enqueue(
                        session,
                        topic="risk.alert",
                        dedupe_key=f"risk:{event.id}:{risk.level}",
                        priority=80 if risk.level == "alert" else 95,
                        payload={
                            "event_id": event.id,
                            "risk_level": risk.level,
                            "risk_score": risk.score,
                            "reasons": risk.reasons,
                        },
                    )

                ingest.sha256 = sha256
                ingest.event_id = event.id
                ingest.state = IngestState.PROCESSED.value
                ingest.completed_at = utcnow()
                ingest.processing_ms = round((time.perf_counter() - started) * 1000)
                ingest.lease_owner = None
                ingest.lease_until = None
                ingest.last_error = None
                ingest.last_error_code = None
                ingest.failure_notified = False
                ingest.retry_requested = False
            committed = True
            try:
                export_manifest(self.database, self.settings)
            except Exception:
                logger.error(
                    "artifact manifest export deferred code=MANIFEST_EXPORT_FAILED"
                )
            logger.info("video processed code=VIDEO_PROCESSED")
            return event_id
        except Exception:
            if not committed:
                for path in written:
                    path.unlink(missing_ok=True)
            raise


def faces_seen(samples: list[FaceSample], skips: list[dict[str, object]]) -> bool:
    return bool(samples or any(item["reason"] != "no_face_detected" for item in skips))


def fingerprint(path: Path) -> str:
    stat = path.stat()
    payload = json.dumps(
        {
            "path": str(path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()
