"""
CareLoop AI — Check-In Question Set (Phase 6)

The daily check-in is "configurable" in the sense that matters: the question
set is DATA, declared here as a versioned constant and served to clients by
`GET /checkins/questions`, rather than strings hard-coded into a service or a
client.  Changing the set is a reviewed edit to this file, versioned in
`QUESTION_SET_VERSION`.

WHAT IS DELIBERATELY NOT HERE
No question asks the patient to describe a symptom, name a condition, rate a
pain score, or estimate a quantity.  Every question is answerable by choosing
one of the enum members that already exist in `app.models.checkin`, and the
symptom question is answered against the patient's OWN stored warning symptoms
rather than against anything the patient types.

That is the whole safety argument: the answer space is a closed set of codes, so
`app.services.red_flag_rules` can evaluate it as arithmetic on stored facts. No
prompt is constructed, so no prompt can be interpreted, and there is nothing for
a model to be asked about.
"""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from app.models.checkin import ConditionChange, SymptomChange, WellbeingAnswer
from app.models.warning_symptom import SymptomSeverity, WarningSymptom
from app.schemas.checkin import (
    CheckInQuestion,
    CheckInQuestionSet,
    WarningSymptomOption,
)

#: Bump when the question set changes.  Recorded on nothing today, but the
#: version is returned to clients so an app can tell whether its cached copy of
#: the questions is stale, and so a future change to the answer codes is
#: visible in an API log.
QUESTION_SET_VERSION = "checkin-questions-v1"


def _enum_values(enum_cls: type) -> list[str]:
    """
    Enum values in declaration order.

    Order is the display order, so the sequence is part of the contract and
    must not be sorted - `good, okay, unwell, very_unwell` reads as a scale,
    while alphabetical order would not.
    """
    return [member.value for member in enum_cls]


#: The questions.  `key` matches the field on `DailyCheckInCreate`, so a client
#: can bind a response to a question without a translation table.
#:
#: The prompts are worded as self-report and avoid implying that any answer is
#: reassuring: "tell us how today has been" does not assert that a good answer
#: means the patient is well, which the system does not know.
QUESTIONS: tuple[CheckInQuestion, ...] = (
    CheckInQuestion(
        key="general_wellbeing",
        prompt="How are you feeling in general today?",
        answer_type="single_select",
        options=_enum_values(WellbeingAnswer),
    ),
    CheckInQuestion(
        key="condition_change",
        prompt=(
            "Compared with yesterday, how has your recovery been?"
        ),
        answer_type="single_select",
        options=_enum_values(ConditionChange),
    ),
    CheckInQuestion(
        key="warning_symptoms",
        prompt=(
            "For each warning symptom on your discharge instructions, tell "
            "us whether it is happening now, and if so whether it has got "
            "better, stayed the same, or got worse."
        ),
        answer_type="multi_select",
        options=_enum_values(SymptomChange),
        # The options for this question are the patient's own rows, not the
        # list above; the flags tell the client to offer them.
        sourced_from_warning_symptoms=True,
    ),
)


def build_question_set(
    warning_symptoms: Sequence[WarningSymptom],
) -> CheckInQuestionSet:
    """
    Assemble the question set for one patient.

    `warning_symptoms` must already be filtered to THIS patient by the caller -
    the repository query that loads them is the ownership check.  Passing
    another patient's rows here would offer one patient the other's symptoms,
    which is why the service layer does the loading rather than this function.

    The severity in each option is the STORED label from the discharge
    document.  It is included so a client can order or emphasise what the care
    team configured as important; it is not a score this system has computed,
    and no rule in `red_flag_rules` uses a client-visible number.
    """
    options = [
        WarningSymptomOption(id=symptom.id, severity=symptom.severity)
        for symptom in warning_symptoms
    ]
    return CheckInQuestionSet(
        version=QUESTION_SET_VERSION,
        questions=list(QUESTIONS),
        available_warning_symptoms=options,
    )


def ordered_symptom_options(
    warning_symptoms: Sequence[WarningSymptom],
) -> list[WarningSymptomOption]:
    """
    The patient's warning symptoms, most severe first.

    Ordering is by the STORED severity, using the Phase 2 ordering.  It is
    presentation only - the rule layer compares severity against a floor
    independently, and nothing about the order a patient sees changes whether a
    rule fires.  Ties keep a stable order by id so the rendered list does not
    shuffle between two requests.
    """
    from app.services.red_flag_rules import SEVERITY_ORDER

    def sort_key(symptom: WarningSymptom) -> tuple[int, str]:
        try:
            rank = SEVERITY_ORDER.index(symptom.severity)
        except ValueError:
            # An unrankable label sorts last rather than being treated as the
            # most severe - defaulting an unknown label to `low` would bury it.
            rank = len(SEVERITY_ORDER)
        return (rank, str(symptom.id))

    return [
        WarningSymptomOption(id=symptom.id, severity=symptom.severity)
        for symptom in sorted(warning_symptoms, key=sort_key)
    ]


def unanswered_questions(
    *,
    wellbeing: Optional[WellbeingAnswer],
    condition_change: Optional[ConditionChange],
    reported_symptom_ids: Sequence[uuid.UUID],
    available_symptom_ids: Sequence[uuid.UUID],
) -> list[str]:
    """
    The keys of the questions this submission did not answer.

    Informational only - an unanswered question is never a review code and
    never an error.  A patient may reasonably skip the recovery question, and
    treating silence about one question as "cannot evaluate" would drown the
    `needs_review` signal that exists for genuine ambiguity.  Exposed for the
    API so a client can show what was left out.
    """
    missing: list[str] = []
    if wellbeing is None:
        missing.append("general_wellbeing")
    if condition_change is None:
        missing.append("condition_change")
    reported = set(reported_symptom_ids)
    if any(symptom_id not in reported for symptom_id in available_symptom_ids):
        missing.append("warning_symptoms")
    return missing
