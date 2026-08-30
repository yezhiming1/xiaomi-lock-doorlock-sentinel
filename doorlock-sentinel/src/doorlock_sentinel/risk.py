from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import Settings
from .metadata import EventMetadata


@dataclass(slots=True)
class TrackRiskInput:
    decision: str
    relationship: str | None = None
    cluster_event_count: int = 0
    quality_score: float = 0.0


@dataclass(slots=True)
class RiskResult:
    score: int
    level: str
    reasons: list[str]


class RiskScorer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def score(
        self,
        metadata: EventMetadata,
        occurred_at: datetime,
        duration_seconds: float,
        tracks: list[TrackRiskInput],
    ) -> RiskResult:
        score = 0
        reasons: list[str] = []
        unknown = [item for item in tracks if item.decision in {"unknown", "uncertain"}]
        known = [item for item in tracks if item.decision == "known"]
        if unknown:
            score += 25
            reasons.append("存在高质量未识别人员")
            repeated = max((item.cluster_event_count for item in unknown), default=0)
            if repeated >= 2:
                score += 25
                reasons.append(f"同一未知人员已出现 {repeated} 次")
        elif not tracks:
            score += 15
            reasons.append("检测到事件但没有可确认的人脸")
        if any(item.relationship == "stranger" for item in known):
            score += 30
            reasons.append("已人工确认的陌生人员再次出现")
        dwell = metadata.dwell_seconds if metadata.dwell_seconds is not None else duration_seconds
        if dwell >= 20:
            score += 15
            reasons.append(f"门前停留约 {round(dwell)} 秒")
        if dwell >= 60:
            score += 15
            reasons.append("停留时间明显偏长")
        if metadata.approach_door:
            score += 10
            reasons.append("进入门前重点区域")
        if metadata.touched_handle:
            score += 30
            reasons.append("靠近或触碰门锁区域")
        if metadata.failed_unlock:
            score += 40
            reasons.append("发生失败开锁")
        if metadata.tamper_alarm:
            score += 60
            reasons.append("门锁报告防撬或异常告警")
        if metadata.repeated_return:
            score += 25
            reasons.append("短时间内反复返回")
        hour = occurred_at.astimezone().hour
        if hour >= self.settings.nighttime_start_hour or hour < self.settings.nighttime_end_hour:
            score += 15
            reasons.append("在设定的夜间时段出现")
        if metadata.passerby_only and not metadata.approach_door:
            score -= 30
            reasons.append("轨迹仅为走廊路过")
        if metadata.package_delivery and not metadata.failed_unlock and not metadata.touched_handle:
            score -= 20
            reasons.append("行为符合正常配送")
        relationships = {item.relationship for item in known if item.relationship}
        if not unknown and relationships and relationships <= {"family"}:
            score -= 40
            reasons.append("仅识别到家人")
        if (
            not unknown
            and relationships
            and relationships <= {"neighbor"}
            and metadata.passerby_only
        ):
            score -= 30
            reasons.append("已知邻居正常路过")
        if (
            not unknown
            and relationships
            and relationships <= {"cleaner"}
            and not metadata.failed_unlock
        ):
            score -= 20
            reasons.append("已知保洁人员，无异常开锁行为")
        score = max(0, min(100, score))
        if score >= self.settings.risk_urgent_threshold:
            level = "urgent"
        elif score >= self.settings.risk_alert_threshold:
            level = "alert"
        elif score >= 30:
            level = "summary"
        else:
            level = "record"
        return RiskResult(score, level, reasons)
