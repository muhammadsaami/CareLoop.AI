"""
CareLoop AI — Deterministic Red-Flag Rule Layer (Phase 6)

THE SAFETY CONTRACT OF THIS MODULE
This is the only place a check-in can become an escalation, and it is a pure
function of three things: the patient's coded answers, the warning symptoms
already stored from their discharge document, and a fixed, versioned rule set.
There is no model call anywhere in this file, and there never will be.

That is a deliberate, load-bearing design decision. If an LLM were allowed to
decide "this patient is having a medical emergency", the system would be making
a clinical judgement on the strength of a model's confidence, and the failure
mode is not a wrong answer - it is confidently telling a patient, or a
caregiver, that an emergency exists when none does, or that none exists when
one does. So the system does not do that. It evaluates a published rule set
against stored facts, and it records which rule fired and which version of the
rule set fired it.

Three properties follow, and each is enforced by code below:

1. AUDITABLE.  Every escalation stores `(rule_code, rule_version)`.  Given
   those two, the exact decision logic is recoverable from this module, so
   "why did this fire?" has a checkable answer that involves no patient text.

2. NO INVENTED SEVERITY.  `severity` is copied from the `WarningSymptom`
   row that matched - a label the document-extraction step already wrote.  The
   rule layer never derives a new severity, never ranks symptoms clinically,
   and never escalates to a number that a human did not put in the data.

3. UNCERTAINTY IS ITS OWN OUTCOME.  A response the rules cannot evaluate
   reliably produces `needs_review` and a review code.  It is never rounded
   down to "fine" and never rounded up to "emergency".  `REVIEW_CODES` is the
   closed set of those reasons.

MATCHING IS BY ID, NEVER BY TEXT
A patient reports a symptom by selecting one of their OWN stored
`WarningSymptom` rows, by id.  Nothing here parses free text, because free-text
symptom matching is where a system quietly starts guessing.  A symptom id the
patient does not own, or that no longer exists, is not "probably the same
symptom" - it is `unrecognised_warning_symptom`, and a human looks at it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from app.core.escalation_codes import (
    RULE_CODES,
    RULE_SET_VERSION,
    RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR,
    RULE_WARNING_SYMPTOM_WORSENED,
)
from app.models.checkin import ConditionChange, SymptomChange, WellbeingAnswer
from app.models.escalation import EscalationCategory, EscalationWorkflow
from app.models.warning_symptom import SymptomSeverity, WarningSymptom

# `RULE_SET_VERSION` and `RULE_CODES` are imported rather than redefined: they
# are declared in `app.core.escalation_codes` so that settings validation can
# check an operator's `CHECKIN_ENABLED_RULES` against the same vocabulary
# without importing this module.  The assertion at the bottom of this file
# keeps the two halves from drifting apart.

__all__ = [
    "RULE_CODES",
    "RULE_SET_VERSION",
    "REVIEW_CODES",
    "SEVERITY_ORDER",
    "RULES",
    "Evaluation",
    "MatchedRule",
    "RedFlagRule",
    "ReviewCode",
    "RuleSet",
    "SymptomReport",
    "build_reports",
]


#: Ordered low -> high.  Used only to test a stored label against a configured
#: floor.  This ordering is a property of the EXISTING `symptom_severity` enum
#: defined in Phase 2, not a clinical ranking invented here - which is why the
#: system may compare against it but may not extend it.
SEVERITY_ORDER: tuple[SymptomSeverity, ...] = (
    SymptomSeverity.low,
    SymptomSeverity.medium,
    SymptomSeverity.high,
    SymptomSeverity.critical,
)


class ReviewCode:
    """
    The closed set of "a human must look at this" reasons.

    Strings, not an enum, because they are persisted on `CheckIn.review_reason`
    and logged; a stable literal that never needs a migration is worth more
    here than type safety on a value nothing branches on.
    """

    #: The patient reported feeling unwell or very unwell, or said they are
    #: worse, but did not select any of their documented warning symptoms.  The
    #: rules therefore have nothing to match against.  This is NOT "safe" and
    #: NOT an escalation: it is unevaluable, and the system says so.
    UNMAPPED_DISTRESS = "unmapped_distress"

    #: A symptom id was submitted that is not one of this patient's documented
    #: warning symptoms, or does not exist at all.  Guessing which symptom the
    #: patient meant is exactly the inference this phase refuses to make.
    UNRECOGNISED_WARNING_SYMPTOM = "unrecognised_warning_symptom"

    #: A reported symptom row carries a severity label outside the known
    #: ordering, so the configured floor cannot be applied to it.
    UNKNOWN_SYMPTOM_SEVERITY = "unknown_symptom_severity"

    #: The answer set was empty or structurally incomplete, so no rule could be
    #: evaluated.  Recorded rather than treated as a normal check-in.
    INCOMPLETE_RESPONSES = "incomplete_responses"


#: Every review code this module can emit.  Asserted by tests, so adding a rule
#: that produces a new review reason fails the suite until it is registered.
REVIEW_CODES: frozenset[str] = frozenset(
    {
        ReviewCode.UNMAPPED_DISTRESS,
        ReviewCode.UNRECOGNISED_WARNING_SYMPTOM,
        ReviewCode.UNKNOWN_SYMPTOM_SEVERITY,
        ReviewCode.INCOMPLETE_RESPONSES,
    }
)


@dataclass(frozen=True)
class RedFlagRule:
    """
    One published, auditable rule.

    Immutable (`frozen=True`) because a rule that could be mutated at runtime
    would not be reproducible from `(rule_code, rule_version)`, which would
    defeat the audit trail.

    `triggered_by` is a set of *patient-reported* states.  The rule fires when
    the patient reported one of these states for a symptom whose STORED
    severity is at or above `min_severity`.  Nothing about the symptom is
    inferred: both halves are read, not reasoned about.
    """

    code: str
    category: EscalationCategory
    workflow: EscalationWorkflow
    #: Which reported changes count as "this symptom is an issue right now".
    triggered_by: frozenset[SymptomChange]
    #: The configured severity floor, compared against the STORED label.
    min_severity: SymptomSeverity
    #: Operator-facing summary.  No clinical advice, no diagnosis.
    summary: str
    #: Whether `CHECKIN_SEVERITY_FLOOR` may raise this rule's threshold.
    #:
    #: True for a rule about a symptom's ABSOLUTE severity: "is this already
    #: serious enough to matter" is a legitimate question for a deployment to
    #: tune, and raising the floor is how an operator says "only tell me about
    #: the serious ones".
    #:
    #: False for a rule about a CHANGE over time. A worsening documented symptom
    #: is a change, and the floor exists to tune an absolute threshold - it has
    #: nothing to say about a symptom that has become worse than it was. If the
    #: floor applied here, `CHECKIN_SEVERITY_FLOOR=critical` would silence
    #: worsening alerts for every documented symptom below critical, including a
    #: high-severity symptom that got worse: a clinical signal disappearing
    #: silently, in the direction nobody is watching, because a configuration
    #: knob was turned. The exemption is declared per rule rather than special-
    #: cased in the evaluator so that the published set stays readable in one
    #: place and a new rule has to make this choice explicitly.
    honours_deployment_floor: bool = True


#: The published rule set for `RULE_SET_VERSION`.
#:
#: Two rules, both anchored on warning symptoms the patient already has on
#: record from their discharge instructions.  Neither fires on a symptom the
#: patient does not have, and neither fires on free text.
RULES: tuple[RedFlagRule, ...] = (
    RedFlagRule(
        code=RULE_WARNING_SYMPTOM_WORSENED,
        category=EscalationCategory.warning_criteria_changed,
        workflow=EscalationWorkflow.contact_care_team,
        # "It is present AND it has got worse" is the change the discharge
        # instructions care about, at any documented severity.
        triggered_by=frozenset({SymptomChange.worse}),
        min_severity=SymptomSeverity.low,
        summary=(
            "A documented warning symptom was reported as present and worse "
            "than before."
        ),
        # A worsening is a change, not a severity, so the deployment floor
        # cannot silence it - see `RedFlagRule.honours_deployment_floor`.
        honours_deployment_floor=False,
    ),
    RedFlagRule(
        code=RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR,
        category=EscalationCategory.warning_criteria_present,
        workflow=EscalationWorkflow.review_by_care_team,
        # Direction-agnostic: a high-severity documented symptom being present
        # at all is what the care team configured this floor to catch.
        triggered_by=frozenset(
            {SymptomChange.worse, SymptomChange.same, SymptomChange.better}
        ),
        min_severity=SymptomSeverity.high,
        summary=(
            "A documented warning symptom at or above the configured severity "
            "floor was reported as present."
        ),
    ),
)

#: Codes of the published rules.  Derived from `RULES` so a rule cannot be
#: added without its code becoming configurable, and asserted equal to the
#: shared vocabulary so the two cannot drift.
RULE_CODES_FROM_RULES: frozenset[str] = frozenset(rule.code for rule in RULES)
assert RULE_CODES_FROM_RULES == RULE_CODES, (
    "app.services.red_flag_rules defines a rule whose code is not registered "
    "in app.core.escalation_codes.RULE_CODES. Settings validation accepts only "
    "the registered codes, so an unregistered rule could not be configured."
)


@dataclass(frozen=True)
class SymptomReport:
    """
    One patient statement about one documented warning symptom.

    Built from a validated request plus a `WarningSymptom` row that was
    positively confirmed to belong to the patient.  Constructing one is the
    ONLY way to get a report into the evaluator, so an unowned or unknown
    symptom id cannot reach rule evaluation at all.
    """

    symptom_id: uuid.UUID
    change: SymptomChange
    severity: SymptomSeverity


@dataclass(frozen=True)
class MatchedRule:
    """A rule that fired, with everything needed to write the audit row."""

    rule: RedFlagRule
    report: Optional[SymptomReport]


@dataclass
class Evaluation:
    """
    The full, auditable result of evaluating one check-in.

    `matched` and `review_codes` are independent: a check-in can escalate AND
    need review, and the two facts are recorded separately rather than being
    collapsed into one verdict.
    """

    matched: list[MatchedRule] = field(default_factory=list)
    review_codes: list[str] = field(default_factory=list)
    #: The rule set version that produced this result.
    rule_version: str = RULE_SET_VERSION

    @property
    def needs_review(self) -> bool:
        return bool(self.review_codes)

    @property
    def escalated(self) -> bool:
        return bool(self.matched)

    def reason_code(self, match: MatchedRule) -> str:
        """
        The safe, non-diagnostic string persisted on the escalation row.

        Contains the rule code, the rule version, and the matched symptom's
        severity LABEL.  It deliberately does not contain the symptom
        description, the patient's answer, or any clinical wording, because
        this value is what operators read and what lands in logs.
        """
        parts = [f"rule={match.rule.code}", f"v={self.rule_version}"]
        if match.report is not None:
            parts.append(f"severity={match.report.severity.value}")
        return " ".join(parts)


class RuleSet:
    """
    The rule set as configured for this deployment.

    Wrapping the published `RULES` with the configuration knobs means the
    effective rule set is still a pure function of (stored facts, config) -
    it just no longer has to be a constant.
    """

    def __init__(
        self,
        *,
        rules: Optional[Sequence[RedFlagRule]] = None,
        enabled_codes: Optional[Iterable[str]] = None,
        severity_floor: Optional[SymptomSeverity] = None,
    ) -> None:
        source = tuple(rules) if rules is not None else RULES
        if enabled_codes is not None:
            allowed = set(enabled_codes)
            unknown = allowed - RULE_CODES
            if unknown:
                # A typo in configuration must not silently disable every rule.
                raise ValueError(
                    f"Unknown red-flag rule code(s) configured: "
                    f"{sorted(unknown)}. Known codes: {sorted(RULE_CODES)}."
                )
            source = tuple(r for r in source if r.code in allowed)
        self._rules = source
        # A deployment may raise the floor above the highest published
        # threshold, which disables the direction-agnostic rule without
        # editing code.  It cannot be used to LOWER a threshold below what a
        # rule declares, because that would let configuration invent a more
        # sensitive clinical rule than the one that was reviewed.
        self._severity_floor = severity_floor

    @property
    def rules(self) -> tuple[RedFlagRule, ...]:
        return self._rules

    def _rule_effective_floor(self, rule: RedFlagRule) -> SymptomSeverity:
        """
        The severity this rule actually requires, for this deployment.

        A rule that `honours_deployment_floor` uses the stricter of its own
        declared minimum and the configured floor.  A rule that does not - a
        rule about a symptom that has CHANGED - keeps its own minimum, so
        tuning the floor cannot silence it.  See `RedFlagRule` for why that
        asymmetry is deliberate.
        """
        if self._severity_floor is None or not rule.honours_deployment_floor:
            return rule.min_severity
        return _max_severity(rule.min_severity, self._severity_floor)

    def evaluate(
        self,
        *,
        wellbeing: Optional[WellbeingAnswer],
        condition_change: Optional[ConditionChange],
        reports: Sequence[SymptomReport],
    ) -> Evaluation:
        """
        Evaluate one check-in. Pure function - no I/O, no clock, no model.

        Order matters and is deliberate: unrecoverable input problems are
        recorded as review codes FIRST, and only then are rules evaluated
        against whatever was reliably understood.  A check-in with one bad
        symptom id still gets its other symptoms evaluated - losing the whole
        evaluation because of one typo would be a worse failure than flagging
        it.
        """
        evaluation = Evaluation()

        if wellbeing is None and condition_change is None and not reports:
            evaluation.review_codes.append(ReviewCode.INCOMPLETE_RESPONSES)
            return evaluation

        # ── Uncertainty: record, do not resolve ──────────────────────────────
        # Distress with nothing to match.  `unwell`/`very_unwell`/`worse` with
        # no reported symptom is the single most important case: the patient
        # may be telling us something is wrong, and we have no documented
        # criterion to compare it against.  Round it neither way.
        reports_distress = (
            wellbeing in {WellbeingAnswer.unwell, WellbeingAnswer.very_unwell}
            or condition_change == ConditionChange.worse
        )
        if reports_distress and not reports:
            evaluation.review_codes.append(ReviewCode.UNMAPPED_DISTRESS)

        # ── Red-flag rules ───────────────────────────────────────────────────
        for report in reports:
            try:
                rank = _severity_rank(report.severity)
            except KeyError:
                evaluation.review_codes.append(
                    ReviewCode.UNKNOWN_SYMPTOM_SEVERITY
                )
                # A severity we cannot rank is not a severity we can threshold.
                # Skip the rule rather than guessing which side of the floor
                # it falls on.
                continue

            for rule in self._rules:
                if report.change not in rule.triggered_by:
                    continue
                floor = self._rule_effective_floor(rule)
                if rank < _severity_rank(floor):
                    continue
                evaluation.matched.append(
                    MatchedRule(rule=rule, report=report)
                )

        # Deterministic ordering so two evaluations of the same inputs produce
        # the same escalation rows in the same order - which is what makes the
        # `(checkin_id, rule_code)` idempotency test meaningful.
        evaluation.matched.sort(
            key=lambda m: (m.rule.code, str(m.report.symptom_id if m.report else ""))
        )
        # Deduplicate review codes while preserving discovery order.
        seen: set[str] = set()
        evaluation.review_codes = [
            code
            for code in evaluation.review_codes
            if not (code in seen or seen.add(code))
        ]
        return evaluation


def _severity_rank(severity: SymptomSeverity) -> int:
    """
    Position of a severity label in the stored ordering.

    Raises `KeyError` for an unknown label, which the caller turns into
    `UNKNOWN_SYMPTOM_SEVERITY` rather than treating as the lowest value -
    defaulting an unknown label to "low" would let an unreadable symptom pass
    every threshold.
    """
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError as exc:
        raise KeyError(severity) from exc


def _max_severity(
    left: SymptomSeverity, right: SymptomSeverity
) -> SymptomSeverity:
    """The more severe of two stored labels."""
    return left if _severity_rank(left) >= _severity_rank(right) else right


def build_reports(
    *,
    requested: Mapping[uuid.UUID, SymptomChange],
    owned_symptoms: Mapping[uuid.UUID, WarningSymptom],
) -> tuple[list[SymptomReport], list[str]]:
    """
    Turn the patient's reported symptom changes into evaluable reports.

    `owned_symptoms` must contain ONLY this patient's `WarningSymptom` rows -
    the caller loads them with a patient filter, which is what makes this
    function's membership test a genuine ownership check rather than an
    existence check.  A caller who supplies an id belonging to someone else
    gets `UNRECOGNISED_WARNING_SYMPTOM`, never that patient's symptom.

    Returns `(reports, review_codes)`.  Unknown ids are dropped from `reports`
    so they cannot reach rule evaluation, and reported as review codes so the
    gap is visible to a human.
    """
    reports: list[SymptomReport] = []
    review_codes: list[str] = []

    for symptom_id, change in requested.items():
        symptom = owned_symptoms.get(symptom_id)
        if symptom is None:
            review_codes.append(ReviewCode.UNRECOGNISED_WARNING_SYMPTOM)
            continue
        reports.append(
            SymptomReport(
                symptom_id=symptom.id,
                change=change,
                severity=symptom.severity,
            )
        )

    return reports, review_codes
