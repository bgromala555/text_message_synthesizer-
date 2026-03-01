"""Quality-report models for generation and AI-assist outputs.

Defines the structured, machine-readable schema used by report-only quality
gates. These models are intentionally generic so they can annotate both
generation runs and AI helper endpoint outputs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class QualitySeverity(StrEnum):
    """Severity bucket derived from numeric score."""

    CRITICAL = "critical"
    WARNING = "warning"
    OK = "ok"


class QualityCheckId(StrEnum):
    """Canonical IDs for quality checks across the pipeline."""

    PERSONALITY_COHERENCE = "personality_coherence"
    ARC_EVENT_CONSISTENCY = "arc_event_consistency"
    RELATIONSHIP_BEHAVIOR = "relationship_behavior_rules"
    SHARED_IDENTITY_LOCK = "shared_identity_cross_device_lock"
    CONVERSATION_MEMORY = "conversation_memory_quality"
    TEMPORAL_REALISM = "temporal_realism"
    LANGUAGE_CONSISTENCY = "language_dialect_consistency"
    GROUP_EVENT_COHERENCE = "group_event_coherence"
    PAIRWISE_COVERAGE = "pairwise_pair_coverage"


class QualityFinding(BaseModel):
    """Single actionable quality finding."""

    check_id: QualityCheckId
    severity: QualitySeverity
    score: float
    scope: str = "run"
    entity_id: str = ""
    message: str
    suggestion: str = ""


class QualityCheckResult(BaseModel):
    """Result bundle for one quality check."""

    check_id: QualityCheckId
    score: float
    severity: QualitySeverity
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: list[QualityFinding] = Field(default_factory=list)


class QualitySummary(BaseModel):
    """Top-level quality summary for quick UI/manifest rendering."""

    overall_score: float
    overall_severity: QualitySeverity
    check_scores: dict[str, float] = Field(default_factory=dict)
    findings_total: int = 0
    critical_count: int = 0
    warning_count: int = 0
    ok_count: int = 0


class QualityReport(BaseModel):
    """Full quality report generated at the end of a run."""

    scenario_id: str
    mode: str = "report_only"
    generated_at: str = Field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    summary: QualitySummary
    checks: list[QualityCheckResult] = Field(default_factory=list)
    top_findings: list[QualityFinding] = Field(default_factory=list)
