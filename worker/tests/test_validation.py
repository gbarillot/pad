from __future__ import annotations

import pytest

from app import validation


def test_parse_confidence_normalizes_numbers_and_percentages() -> None:
    assert validation.parse_confidence("92%") == 0.92
    assert validation.parse_confidence("0,75") == 0.75
    assert validation.parse_confidence(42) == 0.42
    assert validation.parse_confidence("not a number") is None
    assert validation.parse_confidence(500) == 1.0
    assert validation.parse_confidence(-1) == 0.0


def test_format_output_date_handles_common_formats() -> None:
    assert validation.format_output_date("01/02/24") == "2024-02-01"
    assert validation.format_output_date("2024-2-1") == "2024-02-01"
    assert validation.format_output_date("01021984") == "1984-02-01"
    assert validation.format_output_date(123) == 123
    assert validation.format_output_date("") is None


def test_parse_patient_identity_from_combined_line() -> None:
    text = "Nom / prénom DUCHMOL Camille\nNé le 04/03/1988"

    assert validation.parse_patient_identity(text) == {
        "last_name": "DUCHMOL",
        "first_name": "Camille",
        "birthday": "04/03/1988",
    }


def test_parse_hcg_prefers_result_over_reference_values() -> None:
    text = "Valeurs de reference hcg < 5 UI/L\nRésultat beta HCG = 123,4 UI/L"

    assert validation.parse_hcg(text) == {
        "hcg": "123,4",
        "operator": "=",
        "unit": "UI/L",
        "anteriority": None,
    }


def test_normalize_default_result_builds_rich_payload_from_text() -> None:
    result = {
        "patient": {"first_name": "Camille", "last_name": "DUCHMOL", "birth_date": "04/03/1988"},
        "analysis": {
            "date": "05/03/2024",
            "method": "beta hcg",
            "result": {"value": "123,4", "operator": "=", "unit": "UI/L"},
        },
        "extraction": {"confidence": 0.95},
    }
    text = "Nom / prénom DUCHMOL Camille\nBeta HCG = 123,4 UI/L"

    normalized = validation.normalize_default_result(result, text)

    assert normalized["patient"] == {
        "first_name": "Camille",
        "last_name": "DUCHMOL",
        "birth_date": "1988-03-04",
    }
    assert normalized["analysis"]["date"] == "2024-03-05"  # type: ignore[index]
    assert normalized["analysis"]["result"] == {  # type: ignore[index]
        "target": "beta-HCG",
        "value": "123,4",
        "operator": "=",
        "unit": "UI/L",
    }
    assert normalized["extraction"]["confidence"] >= 0.9  # type: ignore[index]


def test_normalize_default_result_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        validation.normalize_default_result([], "")


def test_warnings_and_confidence_caps_for_incomplete_result() -> None:
    normalized = {
        "first_name": None,
        "last_name": "DUCHMOL",
        "birthday": None,
        "date": None,
        "hcg": "123",
        "operator": "=",
        "unit": None,
    }

    assert validation.build_extraction_warnings(normalized) == [
        "incomplete_patient_identity",
        "missing_analysis_date",
    ]
    assert validation.apply_final_confidence_rules(normalized, 0.99) == 0.7


def test_zero_or_below_threshold_hcg_confidence_is_zero() -> None:
    assert validation.has_zero_hcg_without_unit({"hcg": "0", "operator": "=", "unit": None}) is True
    assert validation.has_hcg_below_confidence_threshold({"hcg": "0,1"}) is True
    assert validation.compute_confidence({}, {"hcg": "0", "operator": "=", "unit": None}, None, {}) == 0.0


def test_choose_better_result_prefers_higher_confidence_and_fields() -> None:
    first = {"extraction": {"confidence": 0.5}, "patient": {"first_name": "A"}}
    retry = {
        "extraction": {"confidence": 0.8},
        "patient": {"first_name": "A", "last_name": "B", "birth_date": "01/01/2000"},
        "analysis": {"date": "02/01/2024", "result": {"value": "1", "operator": "=", "unit": "UI/L"}},
    }

    assert validation.choose_better_result(first, retry) is retry


def test_normalizers_handle_ocr_artifacts() -> None:
    assert validation.normalize_hcg_value("O S I L") == "0511"
    assert validation.normalize_hcg_operator("≤") == "<="
    assert validation.normalize_hcg_unit("µUI / ml") == "UI/L"
    assert validation.prepare_extraction_text("Pr6nom\n8-h.c.g")


def test_clean_anteriority_value_normalizes_complete_values() -> None:
    assert validation.clean_anteriority_value({"date": "01.02.24", "value": "O,5", "operator": "<"}) == {
        "date": "01/02/24",
        "hcg": "0,5",
        "operator": "<",
    }
    assert validation.clean_anteriority_value({"date": "01.02.24"}) is None
    assert validation.clean_anteriority_value("not a dict") is None


def test_build_previous_result_formats_date_and_defaults_operator() -> None:
    assert validation.build_previous_result({"date": "01/02/24", "hcg": "12", "operator": None}) == {
        "date": "2024-02-01",
        "value": "12",
        "operator": "=",
    }


def test_patient_identity_can_fallback_to_patient_line() -> None:
    assert validation.parse_patient_identity("Patient: Camille DUCHMOL né le 01/02/2000") == {
        "birthday": "01/02/2000",
        "first_name": "Camille",
        "last_name": "DUCHMOL",
    }


def test_parse_hcg_returns_none_for_weak_or_invalid_candidates() -> None:
    assert validation.parse_hcg("Valeurs de référence HCG < 5 UI/L") is None
    assert validation.parse_hcg("Beta HCG abc UI/L") is None


def test_method_parsing_handles_short_and_beta_labels() -> None:
    assert validation.parse_method("Beta HCG = 12 UI/L") == "beta-HCG"
    assert validation.clean_method_label("HCG = 12 UI/L") is None
    assert validation.normalize_method_output(None) is None


def test_normalize_default_result_fills_hcg_method_identity_and_default_operator_from_text() -> None:
    normalized = validation.normalize_default_result(
        {
            "patient": {"first_name": None, "last_name": None, "birth_date": None},
            "analysis": {"date": "03/04/2024", "result": {"value": None, "operator": None, "unit": None}},
            "extraction": {"confidence": 0.5},
        },
        "Nom / prénom DUCHMOL Camille\nNé le 01/02/2000\nAnalyse: Beta HCG = 12 UI/L",
    )

    assert normalized["patient"] == {
        "first_name": "Camille",
        "last_name": "DUCHMOL",
        "birth_date": "2000-02-01",
    }
    assert normalized["analysis"]["method"] == "beta-HCG"  # type: ignore[index]
    assert normalized["analysis"]["result"]["operator"] == "="  # type: ignore[index]
    assert normalized["analysis"]["result"]["value"] == "12"  # type: ignore[index]


def test_build_rich_result_handles_hcg_target_and_anteriority() -> None:
    normalized = {
        "first_name": "Camille",
        "last_name": "DUCHMOL",
        "birthday": "01/02/2000",
        "date": "03/04/2024",
        "hcg": "12",
        "operator": ">=",
        "unit": "mUI/mL",
        "lab": "Lab",
        "method": "HCG",
        "anteriority": {"date": "02/04/2024", "hcg": "10", "operator": "<"},
        "confidence": 0.8,
    }

    result = validation.build_rich_result(normalized, {})

    assert result["analysis"]["result"]["target"] == "HCG"  # type: ignore[index]
    assert result["analysis"]["anteriority"] == {  # type: ignore[index]
        "date": "2024-04-02",
        "value": "10",
        "operator": "<",
    }


def test_clean_model_value_handles_blank_non_numeric_hcg_and_compact_date() -> None:
    assert validation.clean_model_value("first_name", "   ") is None
    assert validation.clean_model_value("hcg", "abc") == "abc"
    assert validation.clean_model_value("date", "0102/24") == "01/02/24"


def test_fill_helpers_only_replace_blank_or_authoritative_values() -> None:
    target = {"first_name": None, "last_name": "Existing", "hcg": None, "operator": None, "unit": None, "date": None}

    validation.fill_blank_values(target, {"first_name": "Camille", "last_name": "Ignored"})
    validation.fill_hcg_values(target, {"hcg": "12", "operator": ">", "unit": "UI/L", "date": "03/04/2024"})

    assert target == {
        "first_name": "Camille",
        "last_name": "Existing",
        "hcg": "12",
        "operator": ">",
        "unit": "UI/L",
        "date": "03/04/2024",
    }


def test_field_and_confidence_helpers_cover_missing_and_conflicting_values() -> None:
    assert validation.is_hcg_value(None) is False
    assert validation.is_hcg_value("12,5") is True
    assert validation.parse_hcg_float(None) is None
    assert validation.parse_hcg_float("abc") is None
    assert validation.parse_confidence(object()) is None

    normalized = {
        "first_name": "Wrong",
        "last_name": "Name",
        "birthday": "01/02/2000",
        "date": "03/04/2024",
        "hcg": "abc",
        "operator": None,
        "unit": "UI/L",
        "lab": "Lab",
        "method": "HCG",
        "anteriority": {"hcg": "10"},
    }
    score = validation.compute_confidence(
        {"extraction": {"confidence": 0.9}},
        normalized,
        {"hcg": "12", "operator": "=", "unit": "UI/L"},
        {"first_name": "Camille", "last_name": "Name"},
    )

    assert score <= 0.65


def test_confidence_caps_for_missing_identity_result_date_and_operator() -> None:
    assert validation.apply_final_confidence_rules({"hcg": None}, 0.99) == 0.25
    assert validation.apply_final_confidence_rules({"hcg": "12", "operator": None, "unit": "UI/L", "first_name": "A", "last_name": "B", "date": "2024-01-01", "birthday": "2000-01-01"}, 0.99) == 0.8
    assert validation.apply_final_confidence_rules({"hcg": "12", "first_name": None, "last_name": None}, 0.99) == 0.55
    assert validation.apply_final_confidence_rules({"hcg": "12", "operator": "=", "unit": "UI/L", "first_name": "A", "last_name": "B", "date": None, "birthday": "2000-01-01"}, 0.99) == 0.85
    assert validation.has_hcg_below_confidence_threshold({"hcg": "abc"}) is False


def test_identity_parsers_handle_labeled_and_invalid_values() -> None:
    text = "Nom: DUCHMOL\nPrénom: Camille\nDate de naissance: 01-02-2000"
    assert validation.parse_patient_identity(text) == {
        "last_name": "DUCHMOL",
        "first_name": "Camille",
        "birthday": "01/02/2000",
    }
    assert validation.extract_combined_name("Nom / prénom 1234") is None
    assert validation.extract_patient_line_name("Patient: 12345") is None
    assert validation.split_last_first_name("Single") is None
    assert validation.split_last_first_name("Camille Duchmol") == {"first_name": "Camille", "last_name": "Duchmol"}


def test_hcg_candidate_helpers_cover_anteriority_and_invalid_values() -> None:
    candidates = validation.extract_hcg_candidates("Beta HCG >= 12 mUI/mL antériorité < 10 mUI/mL")

    assert candidates[0]["operator"] == ">="
    assert candidates[0]["unit"] == "mUI/mL"
    assert candidates[0]["anteriority"] == {"date": None, "hcg": "10", "operator": "<"}
    assert validation.parse_right_side_anteriority(" abc") is None
    assert validation.parse_right_side_anteriority(" ??") is None
    assert validation.normalize_hcg_operator("≥") == ">="
    assert validation.normalize_hcg_operator("=>") == ">="
    assert validation.normalize_hcg_operator("<") == "<"
    assert validation.normalize_hcg_operator(">") == ">"
    assert validation.normalize_hcg_operator("?") == "="
    assert validation.normalize_hcg_unit(None) is None
    assert validation.normalize_hcg_unit("mg/L") is None
    assert validation.normalize_hcg_unit("mUI/ml") == "mUI/mL"
