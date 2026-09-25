"""
CareLoop AI — Phase 2: Extraction Safety Tests

These encode the healthcare safety boundary: the pipeline transcribes, it
never infers, never invents, and never presents model output as clinical
judgement.  No network access; the provider is a test double.
"""
import pytest

from app.schemas.extraction import (
    ExtractedAppointment,
    ExtractedMedication,
    ExtractedWarningSymptom,
    StructuredDischargeExtraction,
)
from app.services.safety import (
    SafetyValidator,
    has_overreach,
    is_placeholder,
)


# ── Placeholder detection ───────────────────────────────────────────────────

class TestPlaceholders:
    @pytest.mark.parametrize(
        "value",
        ["", "  ", "-", "--", "n/a", "N/A", "none", "null", "NIL", "unknown",
         "not specified", "not provided", "not mentioned", "TBD"],
    )
    def test_placeholders_detected(self, value):
        assert is_placeholder(value) is True

    def test_none_is_placeholder(self):
        assert is_placeholder(None) is True

    @pytest.mark.parametrize(
        "value", ["Metformin", "500 mg", "twice daily", "0", "Cardiology"]
    )
    def test_real_values_not_placeholders(self, value):
        assert is_placeholder(value) is False


# ── Over-reach detection ────────────────────────────────────────────────────

class TestOverreach:
    @pytest.mark.parametrize(
        "text",
        [
            "I recommend continuing this medication.",
            "I suggest you take the higher dose.",
            "You should increase the dose.",
            "Patient should stop taking this.",
            "The diagnosis is Type 2 diabetes.",
            "This is an emergency.",
            "Call 911 immediately.",
            "Seek emergency care now.",
            "Increase the dose if symptoms worsen.",
        ],
    )
    def test_detects_authored_clinical_guidance(self, text):
        assert has_overreach(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Metformin 500 mg twice daily with meals",
            "Take one tablet by mouth each morning",
            "Follow up with Cardiology on 2026-10-15",
            "",
            None,
        ],
    )
    def test_plain_transcription_is_not_overreach(self, text):
        assert has_overreach(text) is False


# ── Dropping unusable items ─────────────────────────────────────────────────

class TestDroppingUnusableItems:
    def test_medication_without_name_is_dropped(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(medication_name="Metformin", dosage="500 mg"),
                ExtractedMedication(dosage="500 mg", frequency="twice daily"),
            ]
        )
        result, notes = SafetyValidator().validate(extraction)

        assert len(result.medications) == 1
        assert result.medications[0].medication_name == "Metformin"
        assert any("dropped" in n for n in notes)

    def test_placeholder_name_treated_as_missing(self):
        extraction = StructuredDischargeExtraction(
            medications=[ExtractedMedication(medication_name="N/A", dosage="500 mg")]
        )
        result, notes = SafetyValidator().validate(extraction)
        assert result.medications == []

    def test_appointment_with_no_identifying_info_dropped(self):
        extraction = StructuredDischargeExtraction(
            appointments=[ExtractedAppointment(instructions="Bring your log")]
        )
        result, notes = SafetyValidator().validate(extraction)
        assert result.appointments == []

    def test_appointment_with_date_is_kept(self):
        extraction = StructuredDischargeExtraction(
            appointments=[ExtractedAppointment(appointment_date="2026-10-15")]
        )
        result, _ = SafetyValidator().validate(extraction)
        assert len(result.appointments) == 1

    def test_warning_symptom_with_nothing_is_dropped(self):
        extraction = StructuredDischargeExtraction(
            warning_symptoms=[ExtractedWarningSymptom(symptom="N/A")]
        )
        result, notes = SafetyValidator().validate(extraction)
        assert result.warning_symptoms == []


# ── Never inventing values ──────────────────────────────────────────────────

class TestNoSynthesis:
    def test_placeholders_become_null_not_text(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(
                    medication_name="Metformin",
                    dosage="not specified",
                    frequency="N/A",
                )
            ]
        )
        result, notes = SafetyValidator().validate(extraction)

        med = result.medications[0]
        assert med.dosage is None
        assert med.frequency is None
        assert any("treated as missing" in n for n in notes)

    def test_missing_dosage_flags_review_but_is_never_filled(self):
        extraction = StructuredDischargeExtraction(
            medications=[ExtractedMedication(medication_name="Metformin")]
        )
        result, _ = SafetyValidator().validate(extraction)

        med = result.medications[0]
        assert med.dosage is None
        assert med.needs_review is True

    def test_complete_medication_does_not_require_review(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(
                    medication_name="Metformin",
                    dosage="500 mg",
                    frequency="twice daily",
                )
            ]
        )
        result, _ = SafetyValidator().validate(extraction)
        assert result.medications[0].needs_review is False

    def test_real_values_are_never_rewritten(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(
                    medication_name="Metformin",
                    dosage="500 mg",
                    frequency="twice daily with meals",
                    instructions="Take with food. Do not crush.",
                )
            ]
        )
        result, _ = SafetyValidator().validate(extraction)
        med = result.medications[0]
        assert med.dosage == "500 mg"
        assert med.instructions == "Take with food. Do not crush."


# ── Source page citations ───────────────────────────────────────────────────

class TestSourcePage:
    def test_page_beyond_document_is_cleared(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(
                    medication_name="Metformin",
                    dosage="500 mg",
                    frequency="twice daily",
                    source_page=9,
                )
            ]
        )
        result, notes = SafetyValidator(page_count=3).validate(extraction)

        med = result.medications[0]
        assert med.source_page is None
        assert med.needs_review is True
        assert any("exceeds document" in n for n in notes)

    def test_valid_page_is_preserved(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(
                    medication_name="Metformin",
                    dosage="500 mg",
                    frequency="twice daily",
                    source_page=2,
                )
            ]
        )
        result, _ = SafetyValidator(page_count=5).validate(extraction)
        assert result.medications[0].source_page == 2

    def test_unknown_page_count_keeps_page(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(
                    medication_name="Metformin", dosage="500 mg",
                    frequency="twice daily", source_page=7,
                )
            ]
        )
        result, _ = SafetyValidator(page_count=None).validate(extraction)
        assert result.medications[0].source_page == 7

    def test_page_zero_rejected_by_schema(self):
        """The schema itself forbids a non-positive page citation."""
        with pytest.raises(Exception):
            ExtractedMedication(medication_name="Metformin", source_page=0)


# ── Over-reach is flagged, never rewritten ──────────────────────────────────

class TestOverreachIsFlaggedNotRewritten:
    def test_guidance_text_is_preserved_but_flagged(self):
        raw = "I recommend increasing the dose to 1000 mg."
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(
                    medication_name="Metformin",
                    dosage="500 mg",
                    frequency="twice daily",
                    instructions=raw,
                )
            ]
        )
        result, notes = SafetyValidator().validate(extraction)

        med = result.medications[0]
        # Text is NOT edited — altering clinical text is out of bounds.
        assert med.instructions == raw
        assert med.needs_review is True
        assert any("model-authored" in n for n in notes)

    def test_emergency_language_is_flagged(self):
        extraction = StructuredDischargeExtraction(
            warning_symptoms=[
                ExtractedWarningSymptom(
                    symptom="Chest pain", instruction="Call 911 immediately."
                )
            ]
        )
        result, notes = SafetyValidator().validate(extraction)
        assert result.warning_symptoms[0].needs_review is True


# ── Warning symptoms always require review ──────────────────────────────────

class TestWarningSymptomsAlwaysNeedReview:
    def test_flag_is_forced_even_if_model_says_otherwise(self):
        extraction = StructuredDischargeExtraction(
            warning_symptoms=[
                ExtractedWarningSymptom(symptom="Fever", needs_review=False)
            ]
        )
        result, _ = SafetyValidator().validate(extraction)
        assert result.warning_symptoms[0].needs_review is True

    def test_schema_forces_flag_on_construction(self):
        symptom = ExtractedWarningSymptom(symptom="Fever", needs_review=False)
        assert symptom.needs_review is True

    def test_count_includes_every_warning_symptom(self):
        extraction = StructuredDischargeExtraction(
            warning_symptoms=[
                ExtractedWarningSymptom(symptom="Fever"),
                ExtractedWarningSymptom(symptom="Chest pain"),
            ]
        )
        validator = SafetyValidator()
        result, _ = validator.validate(extraction)
        assert validator.count_needing_review(result) == 2


# ── Top-level scalars ───────────────────────────────────────────────────────

class TestTopLevelScalars:
    def test_placeholder_scalars_become_null(self):
        extraction = StructuredDischargeExtraction(
            patient_name="N/A", hospital_name="not stated", discharge_date="-"
        )
        result, _ = SafetyValidator().validate(extraction)
        assert result.patient_name is None
        assert result.hospital_name is None
        assert result.discharge_date is None

    def test_real_scalars_are_stripped_only(self):
        extraction = StructuredDischargeExtraction(
            patient_name="  Jane Doe  ", hospital_name="General Hospital"
        )
        result, _ = SafetyValidator().validate(extraction)
        assert result.patient_name == "Jane Doe"
        assert result.hospital_name == "General Hospital"


# ── Empty and malformed input ───────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_extraction_is_valid(self):
        result, notes = SafetyValidator().validate(StructuredDischargeExtraction())
        assert result.medications == []
        assert result.appointments == []
        assert result.warning_symptoms == []
        assert notes == []

    def test_extra_keys_from_provider_are_ignored(self):
        """A chatty model adding fields must not break validation."""
        extraction = StructuredDischargeExtraction.model_validate(
            {
                "medications": [
                    {
                        "medication_name": "Metformin",
                        "dosage": "500 mg",
                        "frequency": "twice daily",
                        "confidence": 0.99,
                        "reasoning": "because the patient is diabetic",
                    }
                ],
                "summary": "Patient is fine overall",
            }
        )
        result, _ = SafetyValidator().validate(extraction)
        assert result.medications[0].medication_name == "Metformin"
        assert not hasattr(result.medications[0], "confidence")

    def test_all_categories_counted(self):
        extraction = StructuredDischargeExtraction(
            medications=[
                ExtractedMedication(medication_name="A", dosage="1 mg", frequency="daily"),
                ExtractedMedication(medication_name="B", dosage="N/A", frequency="daily"),
            ],
            appointments=[ExtractedAppointment(appointment_date="2026-10-15")],
            warning_symptoms=[ExtractedWarningSymptom(symptom="Fever")],
        )
        validator = SafetyValidator()
        result, _ = validator.validate(extraction)
        # "B" needs review (no dosage); the appointment and symptom do not.
        assert validator.count_needing_review(result) == 2
