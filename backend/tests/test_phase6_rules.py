"""
CareLoop AI — Phase 6 deterministic red-flag rules

These are the tests that would matter most in this codebase, and they are
written as pure functions with no database and no HTTP: the rule layer must be
auditable in isolation, because it is the only part of the system that decides
anything clinical.

A recurring theme below is testing a rule with the symptom DESCRIPTION varied
but the stored SEVERITY held constant. That is the whole design claim: a
worsened breathlessness alert is raised because the patient said it got worse
and the record says the symptom was serious, not because the system understood
the words.
"""
import uuid

import pytest

from app.core.escalation_codes import (
    RULE_CODES,
    RULE_SET_VERSION,
    RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR,
    RULE_WARNING_SYMPTOM_WORSENED,
)
from app.models.checkin import ConditionChange, SymptomChange, WellbeingAnswer
from app.models.warning_symptom import SymptomSeverity, WarningSymptom
from app.services.red_flag_rules import (
    REVIEW_CODES,
    RULE_CODES_FROM_RULES,
    RULES,
    RuleSet,
    build_reports,
)


def _symptom(
    *, severity=SymptomSeverity.high, description="Breathlessness on exertion"
) -> WarningSymptom:
    """A stored warning symptom. Only `severity` is ever read by a rule."""
    return WarningSymptom(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        description=description,
        severity=severity,
    )


def _evaluate(
    *,
    symptom=None,
    change=SymptomChange.worse,
    wellbeing=WellbeingAnswer.very_unwell,
    condition_change=ConditionChange.worse,
    **rule_kwargs,
):
    """Evaluate one submission, wiring the ownership check the way the service does."""
    reports = []
    if symptom is not None:
        reports, _ = build_reports(
            requested={symptom.id: change},
            owned_symptoms={symptom.id: symptom},
        )
    return RuleSet(**rule_kwargs).evaluate(
        wellbeing=wellbeing,
        condition_change=condition_change,
        reports=reports,
    )


class TestRuleVocabulary:
    def test_every_rule_code_is_registered_in_the_shared_vocabulary(self):
        # If a rule is added without being registered, an operator could not
        # enable it by name and the config validator would not know the string.
        assert RULE_CODES_FROM_RULES == RULE_CODES

    def test_rule_set_version_is_pinned(self):
        # Stored escalations keep this string; changing it reinterprets history.
        assert RULE_SET_VERSION == "checkin-red-flags-v1"

    def test_review_codes_are_a_closed_set(self):
        # A closed set so a caller can exhaustively map every code to a UI
        # state. An open set would mean a new code could appear in production
        # with no client handling for it.
        assert set(REVIEW_CODES) == {
            "incomplete_responses",
            "unrecognised_warning_symptom",
            "unmapped_distress",
            "unknown_symptom_severity",
        }

    def test_rules_declare_non_diagnostic_categories_and_workflows(self):
        # `contact_care_team` and `review_by_care_team` are CONFIGURED
        # workflows, not severities the system derived. Nothing in the
        # vocabulary may name a condition or imply a treatment.
        for rule in RULES:
            assert rule.category.value in {
                "warning_criteria_changed",
                "warning_criteria_present",
            }
            assert rule.workflow.value in {
                "contact_care_team",
                "review_by_care_team",
            }


class TestOwnershipBoundary:
    """A symptom id is only ever meaningful inside the owner's own set."""

    def test_unowned_symptom_id_is_a_review_code_and_never_a_match(self):
        mine = _symptom(severity=SymptomSeverity.critical)
        theirs = _symptom(severity=SymptomSeverity.critical)

        # The patient reports THEIR OWN id, but only THEIRS is in the owned set.
        reports, ownership_codes = build_reports(
            requested={theirs.id: SymptomChange.worse},
            owned_symptoms={mine.id: mine},
        )
        assert reports == []
        assert "unrecognised_warning_symptom" in ownership_codes

        evaluation = RuleSet().evaluate(
            wellbeing=WellbeingAnswer.very_unwell,
            condition_change=ConditionChange.worse,
            reports=reports,
        )
        assert evaluation.matched == []

    def test_a_patient_with_no_symptoms_gets_an_empty_report_list(self):
        reports, codes = build_reports(requested={}, owned_symptoms={})
        assert reports == []
        assert codes == []

    def test_report_carries_the_stored_severity_not_a_derived_one(self):
        symptom = _symptom(severity=SymptomSeverity.critical)
        reports, _ = build_reports(
            requested={symptom.id: SymptomChange.worse},
            owned_symptoms={symptom.id: symptom},
        )
        assert reports[0].severity is SymptomSeverity.critical


class TestPublishedTruthTable:
    """
    The complete severity x change table, asserted in one place.

    Every other rule test explores one axis at a time; this pins the whole
    surface so a future edit to a rule cannot quietly widen it. A clinical rule
    that starts matching a case it previously ignored is exactly the kind of
    change that should break a test loudly.
    """

    @pytest.mark.parametrize(
        "severity",
        [
            SymptomSeverity.low,
            SymptomSeverity.medium,
            SymptomSeverity.high,
            SymptomSeverity.critical,
        ],
    )
    def test_table(self, severity):
        expected = {
            # Resolved, or getting better: nothing to raise on its own.
            SymptomChange.absent: set(),
            SymptomChange.better: (
                {RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR}
                if severity
                in {SymptomSeverity.high, SymptomSeverity.critical}
                else set()
            ),
            SymptomChange.same: (
                {RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR}
                if severity
                in {SymptomSeverity.high, SymptomSeverity.critical}
                else set()
            ),
            # Worsening always escalates, whatever the stored severity.
            SymptomChange.worse: (
                {RULE_WARNING_SYMPTOM_WORSENED}
                | (
                    {RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR}
                    if severity
                    in {SymptomSeverity.high, SymptomSeverity.critical}
                    else set()
                )
            ),
        }
        for change, expected_codes in expected.items():
            symptom = _symptom(severity=severity)
            reports, _ = build_reports(
                requested={symptom.id: change},
                owned_symptoms={symptom.id: symptom},
            )
            evaluation = RuleSet().evaluate(
                wellbeing=WellbeingAnswer.very_unwell,
                condition_change=ConditionChange.worse,
                reports=reports,
            )
            assert {m.rule.code for m in evaluation.matched} == expected_codes, (
                f"severity={severity.value} change={change.value}"
            )
            assert evaluation.review_codes == []


class TestWorsenedWarningSymptom:
    def test_worsened_high_severity_symptom_matches(self):
        evaluation = _evaluate(
            symptom=_symptom(severity=SymptomSeverity.high),
            change=SymptomChange.worse,
        )
        # Both rules are correct here: the symptom worsened AND it is already
        # at the severity floor. They are separate facts and the escalation
        # table records one row for each.
        assert {m.rule.code for m in evaluation.matched} == {
            RULE_WARNING_SYMPTOM_WORSENED,
            RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR,
        }
        worsening = next(
            m
            for m in evaluation.matched
            if m.rule.code == RULE_WARNING_SYMPTOM_WORSENED
        )
        assert worsening.rule.workflow.value == "contact_care_team"

    def test_the_description_is_never_consulted(self):
        """
        Same stored severity, opposite answers, wildly different wording.

        The descriptions here read like real discharge-document prose. If the
        rule engine ever grew keyword matching, the first case would still pass
        and this test would be the only thing that noticed.
        """
        clinical = _symptom(
            description=(
                "Patient reports increasing breathlessness on minimal exertion "
                "since discharge, worse on stairs."
            ),
            severity=SymptomSeverity.high,
        )
        vague = _symptom(
            description="noted in review", severity=SymptomSeverity.high
        )

        worse = _evaluate(symptom=clinical, change=SymptomChange.worse)
        unchanged = _evaluate(symptom=vague, change=SymptomChange.same)

        assert RULE_WARNING_SYMPTOM_WORSENED in [
            m.rule.code for m in worse.matched
        ]
        assert RULE_WARNING_SYMPTOM_WORSENED not in [
            m.rule.code for m in unchanged.matched
        ]

    @pytest.mark.parametrize(
        "change",
        [SymptomChange.same, SymptomChange.better, SymptomChange.absent],
    )
    def test_only_worse_matches(self, change):
        evaluation = _evaluate(
            symptom=_symptom(severity=SymptomSeverity.high), change=change
        )
        assert RULE_WARNING_SYMPTOM_WORSENED not in [
            m.rule.code for m in evaluation.matched
        ]


class TestSeverityFloorRule:
    @pytest.mark.parametrize(
        "severity",
        [
            SymptomSeverity.high,
            SymptomSeverity.critical,
        ],
    )
    def test_severities_at_or_above_the_rules_own_floor_match(self, severity):
        evaluation = _evaluate(
            symptom=_symptom(severity=severity),
            change=SymptomChange.same,
        )
        assert RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR in [
            m.rule.code for m in evaluation.matched
        ]

    @pytest.mark.parametrize(
        "severity", [SymptomSeverity.low, SymptomSeverity.medium]
    )
    def test_the_floor_rule_declares_its_own_minimum(self, severity):
        # The rule is published as "at or above HIGH". A deployment floor can
        # only make it STRICTER, never more sensitive, so a low-severity
        # symptom never reaches it - otherwise configuration could invent a
        # more sensitive clinical rule than the one that was reviewed.
        evaluation = _evaluate(
            symptom=_symptom(severity=severity),
            change=SymptomChange.same,
            severity_floor=SymptomSeverity.low,
        )
        assert RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR not in [
            m.rule.code for m in evaluation.matched
        ]

    def test_raising_the_floor_hides_symptoms_below_it(self):
        # The same submission under two deployments. Raising the floor is how an
        # operator says "only the serious ones", and it must actually work.
        symptom = _symptom(severity=SymptomSeverity.high)

        default = _evaluate(symptom=symptom, change=SymptomChange.same)
        strict = _evaluate(
            symptom=symptom,
            change=SymptomChange.same,
            severity_floor=SymptomSeverity.critical,
        )

        assert RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR in [
            m.rule.code for m in default.matched
        ]
        assert strict.matched == []

    def test_raising_the_floor_never_silences_a_worsening(self):
        """
        The safety asymmetry, pinned.

        The floor tunes an ABSOLUTE severity threshold. A symptom that has got
        worse is a change over time, and the floor has nothing to say about it.
        If the floor applied to the worsening rule, then
        `CHECKIN_SEVERITY_FLOOR=critical` would drop a worsening HIGH-severity
        symptom entirely - a clinical signal disappearing silently, in the
        direction nobody is watching, because a config knob was turned.
        """
        symptom = _symptom(severity=SymptomSeverity.high)

        default = _evaluate(symptom=symptom, change=SymptomChange.worse)
        strict = _evaluate(
            symptom=symptom,
            change=SymptomChange.worse,
            severity_floor=SymptomSeverity.critical,
        )

        assert [m.rule.code for m in default.matched] == [
            RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR,
            RULE_WARNING_SYMPTOM_WORSENED,
        ]
        # Strictest conceivable configuration, and the change still escalates.
        assert [m.rule.code for m in strict.matched] == [
            RULE_WARNING_SYMPTOM_WORSENED
        ]

    def test_a_worsened_low_severity_symptom_still_escalates(self):
        # The mirror image: the floor does not disarm the worsening rule for a
        # symptom that was only ever documented as mild. A patient whose
        # documented symptom is low severity is still owed the change alert.
        evaluation = _evaluate(
            symptom=_symptom(severity=SymptomSeverity.low),
            change=SymptomChange.worse,
            severity_floor=SymptomSeverity.critical,
        )
        assert [m.rule.code for m in evaluation.matched] == [
            RULE_WARNING_SYMPTOM_WORSENED
        ]


class TestUncertaintyIsNeverRoundedIntoSafety:
    def test_distress_with_no_reported_symptom_needs_review(self):
        """
        The case this design exists for.

        The patient says they feel very unwell and their condition is worse,
        but they have no documented warning symptom to report. There is no
        criterion to evaluate against, so the system must NOT invent one and
        must NOT record a clean result either.
        """
        evaluation = RuleSet().evaluate(
            wellbeing=WellbeingAnswer.very_unwell,
            condition_change=ConditionChange.worse,
            reports=[],
        )
        assert evaluation.matched == []
        assert "unmapped_distress" in evaluation.review_codes

    def test_unmapped_distress_does_not_escalate(self):
        # An unevaluable answer is a review queue item, not an escalation. The
        # two lead to different human actions and must not share a status.
        evaluation = _evaluate(symptom=None, wellbeing=WellbeingAnswer.unwell)
        assert evaluation.matched == []
        assert evaluation.review_codes

    def test_a_settled_patient_with_no_symptoms_is_unremarkable(self):
        evaluation = RuleSet().evaluate(
            wellbeing=WellbeingAnswer.good,
            condition_change=ConditionChange.better,
            reports=[],
        )
        assert evaluation.matched == []
        assert evaluation.review_codes == []

    def test_incomplete_submission_is_flagged(self):
        evaluation = RuleSet().evaluate(
            wellbeing=None, condition_change=None, reports=[]
        )
        assert evaluation.review_codes == ["incomplete_responses"]
        assert evaluation.matched == []

    def test_one_unevaluable_symptom_does_not_discard_the_others(self):
        """
        A typo in one symptom id must not throw away a worsening report.

        Losing the whole evaluation would be a worse failure than flagging the
        bad row, so the rule layer records the problem and carries on.
        """
        real = _symptom(severity=SymptomSeverity.high)
        reports, ownership_codes = build_reports(
            requested={
                real.id: SymptomChange.worse,
                uuid.uuid4(): SymptomChange.worse,  # belongs to nobody
            },
            owned_symptoms={real.id: real},
        )
        evaluation = RuleSet().evaluate(
            wellbeing=WellbeingAnswer.very_unwell,
            condition_change=ConditionChange.worse,
            reports=reports,
        )
        assert RULE_WARNING_SYMPTOM_WORSENED in [
            m.rule.code for m in evaluation.matched
        ]
        assert ownership_codes == ["unrecognised_warning_symptom"]

    def test_a_known_symptom_reported_unchanged_is_not_distress(self):
        # Guards against `unmapped_distress` firing whenever a symptom is
        # merely present, which would flood the review queue on day two.
        symptom = _symptom(severity=SymptomSeverity.high)
        reports, _ = build_reports(
            requested={symptom.id: SymptomChange.same},
            owned_symptoms={symptom.id: symptom},
        )
        evaluation = RuleSet().evaluate(
            wellbeing=WellbeingAnswer.very_unwell,
            condition_change=ConditionChange.worse,
            reports=reports,
        )
        assert "unmapped_distress" not in evaluation.review_codes


class TestDisabledRulesAndEvaluationStamping:
    def test_disabling_a_rule_by_code_stops_it_matching(self):
        symptom = _symptom(severity=SymptomSeverity.high)
        evaluation = _evaluate(
            symptom=symptom,
            change=SymptomChange.worse,
            enabled_codes={RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR},
        )
        assert [m.rule.code for m in evaluation.matched] == [
            RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR
        ]
        assert RULE_WARNING_SYMPTOM_WORSENED not in [
            m.rule.code for m in evaluation.matched
        ]

    def test_disabling_everything_matches_nothing(self):
        symptom = _symptom(severity=SymptomSeverity.critical)
        evaluation = _evaluate(
            symptom=symptom, change=SymptomChange.worse, enabled_codes=set()
        )
        assert evaluation.matched == []

    def test_a_misspelled_code_is_refused_loudly(self):
        """
        A config typo must fail immediately, not degrade quietly.

        Silently ignoring an unknown code would mean a deployment that believes
        a rule is enabled while it is not - and the failure would only be
        visible in the absence of an alert, which is the one thing nobody is
        watching. Note that the error is a `ValueError`, raised while the rule
        set is being built, i.e. before any patient data is touched.
        """
        symptom = _symptom(severity=SymptomSeverity.high)
        with pytest.raises(ValueError, match="NOT_A_REAL_RULE"):
            _evaluate(
                symptom=symptom,
                change=SymptomChange.worse,
                enabled_codes={"NOT_A_REAL_RULE"},
            )

    def test_every_evaluation_is_stamped_with_the_rule_version(self):
        # Even a clean submission, because the stamp is what lets an auditor
        # later ask "which rules were live when this row was written?".
        evaluation = _evaluate(symptom=None, wellbeing=WellbeingAnswer.good)
        assert evaluation.rule_version == RULE_SET_VERSION


class TestPurity:
    def test_evaluate_is_deterministic_for_the_same_input(self):
        symptom = _symptom(severity=SymptomSeverity.high)
        first = _evaluate(symptom=symptom, change=SymptomChange.worse)
        second = _evaluate(symptom=symptom, change=SymptomChange.worse)
        assert [m.rule.code for m in first.matched] == [
            m.rule.code for m in second.matched
        ]
        assert first.rule_version == second.rule_version

    def test_evaluating_does_not_mutate_the_input_symptom(self):
        """
        The rule layer must not write to the patient's record.

        An evaluation that quietly downgraded a stored severity would be a
        self-fulfilling feedback loop: the next check-in would be judged
        against a severity the system had invented on the previous one.
        """
        symptom = _symptom(severity=SymptomSeverity.critical)
        _evaluate(symptom=symptom, change=SymptomChange.worse)
        assert symptom.severity is SymptomSeverity.critical
