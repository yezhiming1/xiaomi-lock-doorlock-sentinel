from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets may be supplied directly only in tests."""

    model_config = SettingsConfigDict(
        env_prefix="DOORLOCK_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "production"
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"

    data_dir: Path = Path("/data")
    inbox_dir: Path = Path("/inbox")
    derived_dir: Path = Path("/data/derived")
    export_dir: Path = Path("/data/exports")
    models_dir: Path = Path("/models")
    runtime_dir: Path = Path("/run/doorlock")
    database_url: str = "sqlite:////data/doorlock.sqlite3"

    api_host: str = "127.0.0.1"
    api_port: int = 8787
    public_base_url: str = "https://doorlock.example.invalid"
    trusted_hosts: str = "localhost,127.0.0.1"

    internal_api_secret: str | None = None
    internal_api_secret_file: Path | None = Path("/run/doorlock/secrets/internal_api_secret")
    web_password_hash: str | None = None
    web_password_hash_file: Path | None = Path("/run/doorlock/secrets/web_password_hash")
    security_pepper: str | None = None
    security_pepper_file: Path | None = Path("/run/doorlock/secrets/security_pepper")
    allowed_operator_userids: str = ""

    cookie_name: str = "doorlock_session"
    session_hours: int = 12
    login_window_seconds: int = 900
    login_max_failures: int = 5
    login_lock_seconds: int = 1800

    face_backend: Literal["onnx", "mock", "disabled"] = "onnx"
    detector_model: Path = Path("/models/det_2.5g.onnx")
    recognizer_model: Path = Path("/models/w600k_r50.onnx")
    detector_sha256: str = ""
    recognizer_sha256: str = ""
    model_id: str = "insightface-buffalo-m-det2.5g-r50-v1"
    embedding_dimension: int = 512
    detector_input_size: int = 640
    ort_intra_threads: int = 2
    ort_inter_threads: int = 1
    detector_score_threshold: float = 0.62
    detector_nms_threshold: float = 0.40

    sample_fps: float = 2.0
    max_sampled_frames: int = 120
    minimum_face_pixels: int = 56
    minimum_blur_score: float = 28.0
    minimum_brightness: float = 20.0
    maximum_brightness: float = 238.0
    minimum_quality_score: float = 0.48
    prototype_quality_score: float = 0.68

    max_track_samples: int = 8
    max_track_gap_seconds: float = 2.5
    track_min_similarity: float = 0.34
    track_strong_similarity: float = 0.62
    track_min_iou: float = 0.08
    track_max_center_distance: float = 1.35

    identity_accept_similarity: float = 0.52
    identity_min_margin: float = 0.08
    identity_coarse_similarity: float = 0.38
    prototype_diversity_similarity: float = 0.96
    prototype_search_limit_per_person: int = 32
    cluster_similarity: float = 0.58
    cluster_review_events: int = 3
    cluster_review_days: int = 2
    cluster_review_tracks: int = 3
    trusted_person_events: int = 6
    trusted_person_days: int = 3
    nighttime_start_hour: int = 22
    nighttime_end_hour: int = 6
    risk_alert_threshold: int = 60
    risk_urgent_threshold: int = 85

    stable_seconds: int = 8
    scan_interval_seconds: float = 4.0
    processing_lease_seconds: int = 900
    outbox_lease_seconds: int = 90
    ingest_retry_seconds: str = "300,1200,3600"
    supported_extensions: str = ".mp4,.mov,.mkv,.avi,.m4v"
    normal_retention_days: int = 35
    manifest_export_seconds: int = 60

    identity_notifications_enabled: bool = False
    risk_notifications_enabled: bool = False
    failure_notifications_enabled: bool = True

    @field_validator(
        "data_dir",
        "inbox_dir",
        "derived_dir",
        "export_dir",
        "models_dir",
        "runtime_dir",
        "detector_model",
        "recognizer_model",
        "internal_api_secret_file",
        "web_password_hash_file",
        "security_pepper_file",
        mode="before",
    )
    @classmethod
    def expand_path(cls, value: object) -> object:
        return Path(str(value)).expanduser() if value is not None else value

    @property
    def extensions(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.supported_extensions.split(",")
            if item.strip()
        }

    @property
    def retry_schedule(self) -> tuple[int, ...]:
        values = tuple(
            int(item.strip())
            for item in self.ingest_retry_seconds.split(",")
            if item.strip()
        )
        if not values or any(value < 1 for value in values):
            raise ValueError("ingest retry schedule must contain positive seconds")
        return values

    @property
    def operators(self) -> set[str]:
        return {
            item.strip()
            for item in self.allowed_operator_userids.split(",")
            if item.strip()
        }

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]
        return hosts or ["localhost", "127.0.0.1"]

    def read_secret(self, direct: str | None, file_path: Path | None, name: str) -> str:
        if direct and direct.strip():
            return direct.strip()
        if file_path and file_path.is_file():
            value = file_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        if self.environment == "production":
            raise RuntimeError(f"missing required secret: {name}")
        return f"development-{name}"

    @property
    def api_secret_value(self) -> str:
        return self.read_secret(
            self.internal_api_secret,
            self.internal_api_secret_file,
            "internal_api_secret",
        )

    @property
    def password_hash_value(self) -> str:
        return self.read_secret(
            self.web_password_hash,
            self.web_password_hash_file,
            "web_password_hash",
        )

    @property
    def security_pepper_value(self) -> str:
        return self.read_secret(
            self.security_pepper,
            self.security_pepper_file,
            "security_pepper",
        )

    def ensure_writable_directories(self) -> None:
        for path in (self.data_dir, self.derived_dir, self.export_dir, self.runtime_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_writable_directories()
    return settings
