"""Quality-check implementations for report-only generation gates.

The checks in this module are intentionally heuristic and non-blocking.
They produce structured findings and scores that help users identify drift
or inconsistencies without interrupting dataset generation.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from messageviewer.models import Message, SmsDataset
from source.models import ContactSlot, ScenarioConfig
from source.quality_models import (
    QualityCheckId,
    QualityCheckResult,
    QualityFinding,
    QualityReport,
    QualitySeverity,
    QualitySummary,
)

# Threshold constants for quality scoring (PLR2004)
_CRITICAL_SCORE_THRESHOLD = 0.40
_WARNING_SCORE_THRESHOLD = 0.70
_REPETITION_RATIO_THRESHOLD = 0.15
_OVERNIGHT_HOUR_CUTOFF = 5
_OVERNIGHT_RATIO_THRESHOLD = 0.35
_REPEAT_RATIO_THRESHOLD = 0.20
_EXCLAIM_RATIO_THRESHOLD = 0.25
_LANG_SCRIPT_RATIO_THRESHOLD = 0.65


def _severity_for_score(score: float) -> QualitySeverity:
    """Map a normalized score to severity.

    Returns:
        QualitySeverity.CRITICAL if score < critical threshold, WARNING if below
        warning threshold, otherwise OK.

    """
    if score < _CRITICAL_SCORE_THRESHOLD:
        return QualitySeverity.CRITICAL
    if score < _WARNING_SCORE_THRESHOLD:
        return QualitySeverity.WARNING
    return QualitySeverity.OK


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _contains_emoji(text: str) -> bool:
    return bool(re.search(r"[\U0001F300-\U0001FAFF]", text))


def _tokenize(text: str) -> set[str]:
    """Extract meaningful tokens from text for overlap comparison.

    Supports Latin-script languages as well as Arabic script.  Arabic
    tokens are extracted as contiguous runs of Arabic characters (min 2)
    while Latin tokens require at least 3 characters.  Common stop words
    in both English and Arabic are removed.

    Returns:
        A set of meaningful tokens (Latin and Arabic) with stop words removed.

    """
    latin_tokens = re.findall(r"[a-zA-Z]{3,}", text.lower())
    arabic_tokens = re.findall(r"[\u0600-\u06ff]{2,}", text)
    en_stop = {"the", "and", "that", "with", "from", "this", "have", "will", "their", "about", "into", "they", "them", "your"}
    ar_stop = {"في", "من", "على", "إلى", "عن", "هذا", "هذه", "التي", "الذي", "أن", "كان", "هو", "هي", "ما", "لا"}
    return {t for t in latin_tokens if t not in en_stop} | {t for t in arabic_tokens if t not in ar_stop}


def _lang_script_ratio(text: str, language: str) -> float:
    if not text.strip():
        return 1.0
    if language == "ar":
        chars = [c for c in text if c.isalpha()]
        if not chars:
            return 1.0
        arabic = [c for c in chars if "\u0600" <= c <= "\u06ff"]
        return len(arabic) / len(chars)
    if language == "en":
        chars = [c for c in text if c.isalpha()]
        if not chars:
            return 1.0
        latin = [c for c in chars if "a" <= c.lower() <= "z"]
        return len(latin) / len(chars)
    return 1.0


def _all_conversation_messages(dataset: SmsDataset) -> list[Message]:
    messages: list[Message] = []
    for node in dataset.nodes:
        messages.extend(node.message_content)
    return messages


def _build_shared_contact_groups(scenario: ScenarioConfig) -> list[list[ContactSlot]]:
    by_id: dict[str, ContactSlot] = {}
    for dev in scenario.devices:
        for c in dev.contacts:
            by_id[c.id] = c

    groups: list[list[ContactSlot]] = []
    seen: set[str] = set()
    for dev in scenario.devices:
        for c in dev.contacts:
            if c.id in seen or not c.shared_with:
                continue
            bucket = [c]
            seen.add(c.id)
            for link in c.shared_with:
                other = by_id.get(link.contact_id)
                if other and other.id not in seen:
                    bucket.append(other)
                    seen.add(other.id)
            if len(bucket) > 1:
                groups.append(bucket)
    return groups


def _check_personality_coherence(scenario: ScenarioConfig) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    total_profiles = 0
    contradiction_hits = 0

    contradictory_pairs = [("introvert", "extrovert"), ("calm", "chaotic"), ("formal", "slang-heavy")]
    for dev in scenario.devices:
        profiles = [dev.owner_personality] + [c.personality for c in dev.contacts]
        for profile in profiles:
            if not profile:
                continue
            total_profiles += 1
            summary = (profile.personality_summary or "").lower()
            emoji_use = (profile.texting_style.emoji_use if profile.texting_style else "").lower()
            sample_phrases = " ".join(profile.sample_phrases or [])

            for a, b in contradictory_pairs:
                if a in summary and b in summary:
                    contradiction_hits += 1
                    findings.append(
                        QualityFinding(
                            check_id=QualityCheckId.PERSONALITY_COHERENCE,
                            severity=QualitySeverity.WARNING,
                            score=0.55,
                            scope="profile",
                            entity_id=profile.name or profile.actor_id,
                            message=f"Personality summary contains potentially conflicting traits: '{a}' and '{b}'.",
                            suggestion="Clarify one dominant trait and move nuance into emotional range.",
                        )
                    )
            if ("never" in emoji_use or "none" in emoji_use) and _contains_emoji(sample_phrases):
                contradiction_hits += 1
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.PERSONALITY_COHERENCE,
                        severity=QualitySeverity.WARNING,
                        score=0.58,
                        scope="profile",
                        entity_id=profile.name or profile.actor_id,
                        message="Emoji usage policy conflicts with sample phrases that include emojis.",
                        suggestion="Align sample phrases with stated texting style.",
                    )
                )

    hit_ratio = (contradiction_hits / max(total_profiles, 1)) if total_profiles else 0.0
    score = max(0.0, min(1.0, 1.0 - hit_ratio))
    return QualityCheckResult(
        check_id=QualityCheckId.PERSONALITY_COHERENCE,
        score=score,
        severity=_severity_for_score(score),
        metrics={"profiles_checked": float(total_profiles), "contradictions": float(contradiction_hits)},
        findings=findings,
    )


def _check_arc_event_consistency(scenario: ScenarioConfig, datasets: dict[str, SmsDataset]) -> QualityCheckResult:
    """Check narrative consistency between story arcs, events, and messages.

    Uses lexical token overlap to detect drift.  When arcs and events are
    written in a different language from the generated messages (common when
    arcs are English but messages are Arabic), the check scores against
    event descriptions separately and applies a cross-language floor so
    that an inherent script mismatch does not produce a misleadingly low
    score.

    Returns:
        QualityCheckResult with score, severity, metrics, and findings.

    """
    findings: list[QualityFinding] = []
    arc_text = " ".join(
        [scenario.story_arc]
        + [d.owner_story_arc for d in scenario.devices if d.owner_story_arc]
        + [c.story_arc for d in scenario.devices for c in d.contacts if c.story_arc]
    )
    event_text = " ".join(ev.description for ev in scenario.timeline_events if ev.description)
    message_text = " ".join(msg.Content for ds in datasets.values() for msg in _all_conversation_messages(ds))

    arc_tokens = _tokenize(arc_text)

    has_messages = bool(message_text.strip())
    reference_tokens = _tokenize(event_text + " " + message_text)
    overlap = len(arc_tokens & reference_tokens)
    ratio = overlap / max(len(arc_tokens), 1) if arc_tokens else 1.0

    # Detect cross-language: arcs are mostly Latin but messages are mostly
    # non-Latin (or vice-versa).  When true, lexical overlap is expected to
    # be low so we score primarily against event descriptions (which share
    # the arc language) and apply a floor.
    target_lang = (scenario.generation_settings.language or "en").lower()
    arc_has_latin = bool(re.search(r"[a-zA-Z]{3,}", arc_text))
    is_cross_language = arc_has_latin and target_lang not in {"en", "fr", "es", "de", "pt", "it"}

    event_only_tokens = _tokenize(event_text)
    event_overlap = len(arc_tokens & event_only_tokens)
    event_ratio = event_overlap / max(len(arc_tokens), 1) if arc_tokens else 1.0

    if not has_messages:
        score = max(0.0, min(1.0, max(ratio, event_ratio, _WARNING_SCORE_THRESHOLD)))
    elif is_cross_language:
        # Arc language differs from message language -- use event overlap
        # as the primary signal and apply a reasonable floor.
        score = max(0.0, min(1.0, max(ratio, event_ratio, _LANG_SCRIPT_RATIO_THRESHOLD)))
    else:
        score = max(0.0, min(1.0, ratio))

    if arc_tokens and score < _CRITICAL_SCORE_THRESHOLD:
        detail = " (cross-language scenario detected — scoring primarily against event descriptions)" if is_cross_language else ""
        findings.append(
            QualityFinding(
                check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
                severity=QualitySeverity.WARNING,
                score=score,
                scope="run",
                message=f"Low lexical overlap between arcs and generated events/messages indicates narrative drift.{detail}",
                suggestion="Regenerate messages so they reflect the updated arcs, or add arc-aligned events.",
            )
        )
    return QualityCheckResult(
        check_id=QualityCheckId.ARC_EVENT_CONSISTENCY,
        score=score,
        severity=_severity_for_score(score),
        metrics={
            "arc_token_count": float(len(arc_tokens)),
            "overlap_token_count": float(overlap),
            "event_overlap_token_count": float(event_overlap),
            "has_messages": float(has_messages),
            "cross_language": float(is_cross_language),
        },
        findings=findings,
    )


def _check_relationship_behavior(scenario: ScenarioConfig, datasets: dict[str, SmsDataset]) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    checked = 0
    violations = 0
    role_expectations = {"boss": {"max_exclaim_ratio": 0.20}, "manager": {"max_exclaim_ratio": 0.20}, "doctor": {"max_exclaim_ratio": 0.15}}

    actor_to_role: dict[str, str] = {}
    for dev in scenario.devices:
        for c in dev.contacts:
            actor_to_role[c.actor_id] = (c.role or "").lower()

    for ds in datasets.values():
        for node in ds.nodes:
            if len(node.target) != 1:
                continue
            target = node.target[0]
            role = actor_to_role.get(target, "")
            if not role:
                continue
            for keyword, expectation in role_expectations.items():
                if keyword in role:
                    checked += 1
                    msgs = [m.Content for m in node.message_content if m.Content]
                    if not msgs:
                        continue
                    exclaim_ratio = sum(1 for t in msgs if "!" in t) / len(msgs)
                    if exclaim_ratio > expectation["max_exclaim_ratio"]:
                        violations += 1
                        findings.append(
                            QualityFinding(
                                check_id=QualityCheckId.RELATIONSHIP_BEHAVIOR,
                                severity=QualitySeverity.WARNING,
                                score=0.60,
                                scope="thread",
                                entity_id=f"{node.source}->{target}",
                                message=f"Role '{role}' thread is more expressive than expected (high '!').",
                                suggestion="Tune tone for formal relationships or adjust role label.",
                            )
                        )

    score = 1.0 - (violations / max(checked, 1)) if checked else 1.0
    return QualityCheckResult(
        check_id=QualityCheckId.RELATIONSHIP_BEHAVIOR,
        score=max(0.0, min(1.0, score)),
        severity=_severity_for_score(score),
        metrics={"threads_checked": float(checked), "violations": float(violations)},
        findings=findings,
    )


def _check_shared_identity_lock(scenario: ScenarioConfig) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    groups = _build_shared_contact_groups(scenario)
    inconsistencies = 0

    for group in groups:
        # Identity lock rule: actor_id (phone number) is the canonical identity.
        # Names can differ across devices (nicknames/aliases) and should not be
        # treated as an identity conflict by themselves.
        actor_ids = {(c.actor_id or "").strip() for c in group if (c.actor_id or "").strip()}
        if len(actor_ids) > 1:
            inconsistencies += 1
            findings.append(
                QualityFinding(
                    check_id=QualityCheckId.SHARED_IDENTITY_LOCK,
                    severity=QualitySeverity.WARNING,
                    score=0.45,
                    scope="shared_identity",
                    entity_id="|".join(c.id for c in group),
                    message="Shared contact group has different actor IDs (phone identities) across devices.",
                    suggestion="Use the same actor_id for the same real person; keep local display names as aliases.",
                )
            )

        summaries = [((c.personality.personality_summary or "").strip().lower()) for c in group if c.personality]
        # Only require strong personality-core alignment when actor_id indicates
        # these are the same real person.
        if len(actor_ids) == 1 and len(set(summaries)) > 1 and len(summaries) == len(group):
            inconsistencies += 1
            findings.append(
                QualityFinding(
                    check_id=QualityCheckId.SHARED_IDENTITY_LOCK,
                    severity=QualitySeverity.WARNING,
                    score=0.58,
                    scope="shared_identity",
                    entity_id="|".join(c.id for c in group),
                    message="Shared contact personality core differs strongly across linked devices.",
                    suggestion="Keep core biography stable; vary only relationship-specific texting behavior.",
                )
            )

    score = 1.0 - (inconsistencies / max(len(groups), 1)) if groups else 1.0
    return QualityCheckResult(
        check_id=QualityCheckId.SHARED_IDENTITY_LOCK,
        score=max(0.0, min(1.0, score)),
        severity=_severity_for_score(score),
        metrics={"shared_groups": float(len(groups)), "inconsistencies": float(inconsistencies)},
        findings=findings,
    )


def _check_conversation_memory_quality(datasets: dict[str, SmsDataset]) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    threads = 0
    repetition_flags = 0

    for ds in datasets.values():
        for node in ds.nodes:
            threads += 1
            normalized = [re.sub(r"\s+", " ", (m.Content or "").strip().lower()) for m in node.message_content if m.Content]
            if not normalized:
                continue
            counts = Counter(normalized)
            repeated = sum(v - 1 for v in counts.values() if v > 1)
            ratio = repeated / max(len(normalized), 1)
            if ratio > _REPETITION_RATIO_THRESHOLD:
                repetition_flags += 1
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.CONVERSATION_MEMORY,
                        severity=QualitySeverity.WARNING,
                        score=0.62,
                        scope="thread",
                        entity_id=f"{node.source}->{','.join(node.target)}",
                        message="High repeated-message ratio suggests looping/redundant conversation memory.",
                        suggestion="Increase topic progression and unresolved-thread follow-through in prompts.",
                    )
                )

    score = 1.0 - (repetition_flags / max(threads, 1)) if threads else 1.0
    return QualityCheckResult(
        check_id=QualityCheckId.CONVERSATION_MEMORY,
        score=max(0.0, min(1.0, score)),
        severity=_severity_for_score(score),
        metrics={"threads": float(threads), "repetition_flags": float(repetition_flags)},
        findings=findings,
    )


def _check_group_event_coherence(scenario: ScenarioConfig) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    groups = scenario.group_chats or []
    issues = 0

    events_by_id = {ev.id: ev for ev in scenario.timeline_events}
    for gc in groups:
        if gc.origin_event_id:
            ev = events_by_id.get(gc.origin_event_id)
            if not ev:
                issues += 1
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.GROUP_EVENT_COHERENCE,
                        severity=QualitySeverity.WARNING,
                        score=0.55,
                        scope="group",
                        entity_id=gc.id,
                        message=f"Group '{gc.name}' references missing origin event.",
                        suggestion="Link the group to an existing event or clear origin_event_id.",
                    )
                )
            elif not gc.start_date and ev.date:
                issues += 1
                findings.append(
                    QualityFinding(
                        check_id=QualityCheckId.GROUP_EVENT_COHERENCE,
                        severity=QualitySeverity.WARNING,
                        score=0.65,
                        scope="group",
                        entity_id=gc.id,
                        message=f"Group '{gc.name}' has event link but no explicit start date.",
                        suggestion="Set start_date to event date for clearer runtime activation.",
                    )
                )
        else:
            issues += 1
            findings.append(
                QualityFinding(
                    check_id=QualityCheckId.GROUP_EVENT_COHERENCE,
                    severity=QualitySeverity.WARNING,
                    score=0.60,
                    scope="group",
                    entity_id=gc.id,
                    message=f"Group '{gc.name}' has no origin event link.",
                    suggestion="Attach origin_event_id so group existence is narratively grounded.",
                )
            )

    score = 1.0 - (issues / max(len(groups), 1)) if groups else 1.0
    return QualityCheckResult(
        check_id=QualityCheckId.GROUP_EVENT_COHERENCE,
        score=max(0.0, min(1.0, score)),
        severity=_severity_for_score(score),
        metrics={"groups_checked": float(len(groups)), "coherence_issues": float(issues)},
        findings=findings,
    )


def _check_pairwise_coverage(scenario: ScenarioConfig, datasets: dict[str, SmsDataset]) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    expected = 0
    missing = 0

    # If no devices have generated data yet, pair-thread coverage is not
    # meaningful — it can only be resolved by running generation.  Return
    # a clean score so the quality report doesn't flag unactionable items.
    has_any_data = any(ds.nodes for ds in datasets.values())
    if not has_any_data:
        return QualityCheckResult(
            check_id=QualityCheckId.PAIRWISE_COVERAGE,
            score=1.0,
            severity=QualitySeverity.OK,
            metrics={"expected_pairs": 0.0, "missing_pairs": 0.0, "has_generated_data": 0.0},
            findings=[],
        )

    for dev in scenario.devices:
        ds = datasets.get(dev.id)
        nodes = ds.nodes if ds else []
        present_pairs = {n.target[0] for n in nodes if n.source == dev.owner_actor_id and len(n.target) == 1 and n.message_content}
        for gc in scenario.group_chats or []:
            if not any(m.device_id == dev.id and m.contact_id == "__owner__" for m in gc.members):
                continue
            if not gc.auto_pair_threads:
                continue
            for member in gc.members:
                if member.device_id != dev.id:
                    continue
                c_id = member.contact_id
                if not c_id or c_id == "__owner__":
                    continue
                contact = next((c for c in dev.contacts if c.id == c_id), None)
                if not contact:
                    continue
                expected += 1
                if contact.actor_id not in present_pairs:
                    missing += 1
                    findings.append(
                        QualityFinding(
                            check_id=QualityCheckId.PAIRWISE_COVERAGE,
                            severity=QualitySeverity.WARNING,
                            score=0.60,
                            scope="device",
                            entity_id=dev.id,
                            message=f"Missing owner<->{contact.name or contact.actor_id} pair thread for group '{gc.name}'.",
                            suggestion="Run generation/resume to create the missing pair threads.",
                        )
                    )

    score = 1.0 - (missing / max(expected, 1)) if expected else 1.0
    return QualityCheckResult(
        check_id=QualityCheckId.PAIRWISE_COVERAGE,
        score=max(0.0, min(1.0, score)),
        severity=_severity_for_score(score),
        metrics={"expected_pairs": float(expected), "missing_pairs": float(missing), "has_generated_data": 1.0},
        findings=findings,
    )


def _check_temporal_realism(datasets: dict[str, SmsDataset]) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    total_messages = 0
    overnight_messages = 0
    non_monotonic_threads = 0
    total_threads = 0

    for ds in datasets.values():
        for node in ds.nodes:
            total_threads += 1
            prev_dt: datetime | None = None
            local_non_monotonic = False
            for msg in node.message_content:
                total_messages += 1
                try:
                    dt = datetime.fromisoformat(msg.TransferTime)
                    if dt.hour < _OVERNIGHT_HOUR_CUTOFF:
                        overnight_messages += 1
                    if prev_dt and dt < prev_dt:
                        local_non_monotonic = True
                    prev_dt = dt
                except ValueError:
                    continue
            if local_non_monotonic:
                non_monotonic_threads += 1

    overnight_ratio = overnight_messages / max(total_messages, 1) if total_messages else 0.0
    score = 1.0
    if overnight_ratio > _OVERNIGHT_RATIO_THRESHOLD:
        score -= min(0.4, overnight_ratio - _OVERNIGHT_RATIO_THRESHOLD)
        findings.append(
            QualityFinding(
                check_id=QualityCheckId.TEMPORAL_REALISM,
                severity=QualitySeverity.WARNING,
                score=max(0.0, score),
                scope="run",
                message="Unusually high overnight messaging volume may reduce realism.",
                suggestion="Adjust message density/skip by daypart or role-specific routines.",
            )
        )
    if non_monotonic_threads > 0:
        score -= min(0.5, non_monotonic_threads / max(total_threads, 1))
        findings.append(
            QualityFinding(
                check_id=QualityCheckId.TEMPORAL_REALISM,
                severity=QualitySeverity.WARNING,
                score=max(0.0, score),
                scope="run",
                message=f"{non_monotonic_threads} thread(s) include non-monotonic timestamps.",
                suggestion="Ensure per-thread message sorting before save.",
            )
        )

    score = max(0.0, min(1.0, score))
    return QualityCheckResult(
        check_id=QualityCheckId.TEMPORAL_REALISM,
        score=score,
        severity=_severity_for_score(score),
        metrics={
            "total_messages": float(total_messages),
            "overnight_ratio": overnight_ratio,
            "non_monotonic_threads": float(non_monotonic_threads),
        },
        findings=findings,
    )


def _check_language_consistency(scenario: ScenarioConfig, datasets: dict[str, SmsDataset]) -> QualityCheckResult:
    findings: list[QualityFinding] = []
    lang = (scenario.generation_settings.language or "en").lower()
    messages = [msg.Content for ds in datasets.values() for msg in _all_conversation_messages(ds) if msg.Content]
    if not messages:
        return QualityCheckResult(
            check_id=QualityCheckId.LANGUAGE_CONSISTENCY,
            score=1.0,
            severity=QualitySeverity.OK,
            metrics={"messages_checked": 0.0},
            findings=[],
        )

    ratios = [_lang_script_ratio(text, lang) for text in messages]
    avg_ratio = sum(ratios) / len(ratios)
    score = max(0.0, min(1.0, avg_ratio))
    if avg_ratio < _WARNING_SCORE_THRESHOLD and lang in {"en", "ar"}:
        findings.append(
            QualityFinding(
                check_id=QualityCheckId.LANGUAGE_CONSISTENCY,
                severity=QualitySeverity.WARNING,
                score=score,
                scope="run",
                message=f"Language/script consistency is low for target language '{lang}'.",
                suggestion="Regenerate with stricter language directive or reduce mixed-language prompts.",
            )
        )

    return QualityCheckResult(
        check_id=QualityCheckId.LANGUAGE_CONSISTENCY,
        score=score,
        severity=_severity_for_score(score),
        metrics={"messages_checked": float(len(messages)), "avg_script_ratio": avg_ratio},
        findings=findings,
    )


def evaluate_generation_quality(scenario: ScenarioConfig, datasets_by_device: dict[str, SmsDataset]) -> QualityReport:
    """Evaluate generation quality across all requested check areas.

    Returns:
        QualityReport with scenario_id, summary, checks, and top findings.

    """
    checks = [
        _check_personality_coherence(scenario),
        _check_arc_event_consistency(scenario, datasets_by_device),
        _check_relationship_behavior(scenario, datasets_by_device),
        _check_shared_identity_lock(scenario),
        _check_group_event_coherence(scenario),
        _check_pairwise_coverage(scenario, datasets_by_device),
        _check_conversation_memory_quality(datasets_by_device),
        _check_temporal_realism(datasets_by_device),
        _check_language_consistency(scenario, datasets_by_device),
    ]

    weights = {
        QualityCheckId.PERSONALITY_COHERENCE: 1.2,
        QualityCheckId.ARC_EVENT_CONSISTENCY: 1.3,
        QualityCheckId.RELATIONSHIP_BEHAVIOR: 1.0,
        QualityCheckId.SHARED_IDENTITY_LOCK: 1.0,
        QualityCheckId.GROUP_EVENT_COHERENCE: 1.1,
        QualityCheckId.PAIRWISE_COVERAGE: 1.1,
        QualityCheckId.CONVERSATION_MEMORY: 1.1,
        QualityCheckId.TEMPORAL_REALISM: 1.0,
        QualityCheckId.LANGUAGE_CONSISTENCY: 1.2,
    }

    weighted_total = 0.0
    weight_sum = 0.0
    for check in checks:
        w = weights.get(check.check_id, 1.0)
        weighted_total += check.score * w
        weight_sum += w
    overall = weighted_total / max(weight_sum, 1.0)

    findings = [f for check in checks for f in check.findings]
    findings_sorted = sorted(findings, key=lambda f: (f.severity != QualitySeverity.CRITICAL, f.score))
    top_findings = findings_sorted[:15]

    summary = QualitySummary(
        overall_score=overall,
        overall_severity=_severity_for_score(overall),
        check_scores={check.check_id.value: check.score for check in checks},
        findings_total=len(findings),
        critical_count=sum(1 for f in findings if f.severity == QualitySeverity.CRITICAL),
        warning_count=sum(1 for f in findings if f.severity == QualitySeverity.WARNING),
        ok_count=sum(1 for f in findings if f.severity == QualitySeverity.OK),
    )

    return QualityReport(
        scenario_id=scenario.id,
        summary=summary,
        checks=checks,
        top_findings=top_findings,
    )


def quick_thread_findings(messages: list[Message], role: str, language: str, entity_id: str) -> list[QualityFinding]:
    """Fast thread-level warnings for SSE progress updates.

    Returns:
        List of QualityFinding for high repeat ratio, formal-role expressiveness,
        or language drift.

    """
    findings: list[QualityFinding] = []
    texts = [m.Content for m in messages if m.Content]
    if not texts:
        return findings

    norm = [re.sub(r"\s+", " ", t.strip().lower()) for t in texts]
    repeat_ratio = (len(norm) - len(set(norm))) / max(len(norm), 1)
    if repeat_ratio > _REPEAT_RATIO_THRESHOLD:
        findings.append(
            QualityFinding(
                check_id=QualityCheckId.CONVERSATION_MEMORY,
                severity=QualitySeverity.WARNING,
                score=max(0.0, 1.0 - repeat_ratio),
                scope="thread",
                entity_id=entity_id,
                message="Conversation has high near-duplicate message ratio.",
                suggestion="Increase topic progression and avoid repeated phrasing.",
            )
        )

    if role and any(k in role.lower() for k in ("boss", "manager", "doctor")):
        exclaim = sum(1 for t in texts if "!" in t) / max(len(texts), 1)
        if exclaim > _EXCLAIM_RATIO_THRESHOLD:
            findings.append(
                QualityFinding(
                    check_id=QualityCheckId.RELATIONSHIP_BEHAVIOR,
                    severity=QualitySeverity.WARNING,
                    score=0.60,
                    scope="thread",
                    entity_id=entity_id,
                    message="Formal relationship thread looks highly expressive/casual.",
                    suggestion="Tone down expressiveness for this relationship role.",
                )
            )

    ratio = _lang_script_ratio(" ".join(texts), language)
    if language in {"en", "ar"} and ratio < _LANG_SCRIPT_RATIO_THRESHOLD:
        findings.append(
            QualityFinding(
                check_id=QualityCheckId.LANGUAGE_CONSISTENCY,
                severity=QualitySeverity.WARNING,
                score=ratio,
                scope="thread",
                entity_id=entity_id,
                message=f"Thread may be drifting from target language '{language}'.",
                suggestion="Regenerate this thread with stronger language constraints.",
            )
        )
    return findings
