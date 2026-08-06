from __future__ import annotations

import json
import re
from typing import TypedDict

DEFAULT_SCHEMA = json.dumps(
    {
        "patient": {
            "first_name": None,
            "last_name": None,
            "birth_date": None,
        },
        "laboratory": {
            "name": None,
        },
        "analysis": {
            "date": None,
            "name": None,
            "method": None,
            "result": {
                "target": None,
                "value": None,
                "operator": None,
                "unit": None,
            },
            "anteriority": {
                "date": None,
                "value": None,
                "operator": None,
            },
        },
        "extraction": {
            "confidence": None,
            "warnings": [],
        },
    }
)
DEFAULT_KEYS = (
    "first_name",
    "last_name",
    "birthday",
    "date",
    "hcg",
    "operator",
    "unit",
    "lab",
    "method",
    "anteriority",
    "confidence",
)
REQUIRED_KEYS = tuple(
    key for key in DEFAULT_KEYS if key not in {"confidence", "lab", "method", "anteriority"}
)
HCG_LABEL_PATTERN = (
    r"(?i)\b(?:beta|b[éeè]ta|β)?\s*[-']?\s*h\s*[. -]?\s*c\s*[. -]?\s*g\b"
    r"|\bb\s*[-.]?\s*hcg\b"
    r"|\bbhcg\b"
    r"|\bhormone\s+chorionique\s+gonadotrope\b"
    r"|\bgonadotrophine\s+chorionique\b"
    r"|\bhormone\s+de\s+grossesse\b"
)
HCG_NUMBER_PATTERN = r"[0-9OoSsIiLl]+(?:[\s.,][0-9OoSsIiLl]+)*"
HCG_OPERATOR_PATTERN = r"<=|>=|≤|≥|<|>|="
HCG_UNIT_PATTERN = r"m?\s*[uµμ]\s*[i1l]\s*(?:/\s*)?(?:m\s*)?[l1]"


class HcgCandidate(TypedDict):
    hcg: str
    operator: str
    unit: str
    score: int
    anteriority: dict[str, object] | None


def normalize_default_result(result: object, text: str) -> dict[str, object]:
    if not isinstance(result, dict):
        raise ValueError(f"Expected Ollama to return a JSON object, got {type(result).__name__}")
    model_values = extract_default_fields(result)
    normalized = {key: clean_model_value(key, model_values.get(key)) for key in DEFAULT_KEYS}
    hcg = parse_hcg(text)
    if hcg:
        fill_hcg_values(normalized, hcg)
        if is_blank(normalized.get("anteriority")) and hcg.get("anteriority"):
            normalized["anteriority"] = hcg["anteriority"]
    method = parse_method(text)
    if method and is_blank(normalized.get("method")):
        normalized["method"] = method
    identity = parse_patient_identity(text)
    fill_blank_values(normalized, identity)
    if normalized.get("operator") in {None, ""} and normalized.get("hcg") is not None:
        normalized["operator"] = "="
    normalized["confidence"] = compute_confidence(result, normalized, hcg, identity)
    return build_rich_result(normalized, result)


def extract_default_fields(result: dict[str, object]) -> dict[str, object]:
    if any(key in result for key in DEFAULT_KEYS):
        return result

    document = nested_dict(result.get("document"))
    patient = nested_dict(result.get("patient"))
    laboratory = nested_dict(result.get("laboratory"))
    analysis = nested_dict(result.get("analysis"))
    analysis_result = nested_dict(analysis.get("result"))
    previous_result = analysis.get("anteriority") or analysis.get("previous_result")
    extraction = nested_dict(result.get("extraction"))

    return {
        "first_name": patient.get("first_name"),
        "last_name": patient.get("last_name"),
        "birthday": patient.get("birth_date") or patient.get("birthday"),
        "date": document.get("date") or analysis.get("date"),
        "hcg": analysis_result.get("value") or analysis_result.get("hcg"),
        "operator": analysis_result.get("operator"),
        "unit": analysis_result.get("unit"),
        "lab": laboratory.get("name") or result.get("lab"),
        "method": analysis.get("method") or analysis.get("name"),
        "anteriority": previous_result,
        "confidence": extraction.get("confidence") or result.get("confidence"),
    }


def nested_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def build_rich_result(normalized: dict[str, object], model_result: dict[str, object]) -> dict[str, object]:
    method = normalize_method_output(normalized.get("method"))
    analysis_name = method or normalize_method_output(nested_dict(model_result.get("analysis")).get("name"))
    return {
        "patient": {
            "first_name": normalized.get("first_name"),
            "last_name": normalized.get("last_name"),
            "birth_date": format_output_date(normalized.get("birthday")),
        },
        "laboratory": {
            "name": normalized.get("lab"),
        },
        "analysis": {
            "date": format_output_date(normalized.get("date")),
            "name": analysis_name,
            "method": method,
            "result": {
                "target": infer_hcg_target(normalized.get("method")),
                "value": normalized.get("hcg"),
                "operator": normalized.get("operator"),
                "unit": normalized.get("unit"),
            },
            "anteriority": build_previous_result(normalized.get("anteriority")),
        },
        "extraction": {
            "confidence": normalized.get("confidence"),
            "warnings": build_extraction_warnings(normalized),
        },
    }


def infer_hcg_target(method: object) -> str:
    if isinstance(method, str) and re.search(r"(?i)b[éeè]ta|beta|β|\bb\s*[-.]?\s*hcg\b|\bbhcg\b", method):
        return "beta-HCG"
    return "HCG"


def build_previous_result(value: object) -> dict[str, object] | None:
    anteriority = clean_anteriority_value(value)
    if anteriority is None:
        return None
    return {
        "date": format_output_date(anteriority.get("date")),
        "value": anteriority.get("hcg"),
        "operator": anteriority.get("operator"),
    }


def build_extraction_warnings(normalized: dict[str, object]) -> list[str]:
    warnings: list[str] = []
    if is_blank(normalized.get("hcg")):
        warnings.append("missing_hcg_result")
    if is_blank(normalized.get("first_name")) or is_blank(normalized.get("last_name")):
        warnings.append("incomplete_patient_identity")
    if is_blank(normalized.get("date")):
        warnings.append("missing_analysis_date")
    if has_zero_hcg_without_unit(normalized):
        warnings.append("zero_hcg_without_unit")
    return warnings


def clean_model_value(key: str, value: object) -> object:
    if key == "anteriority":
        return clean_anteriority_value(value)
    if isinstance(value, str):
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return None
        if key == "hcg":
            return clean_hcg_model_value(value)
        if key == "operator":
            return normalize_hcg_operator(value)
        if key == "unit":
            return normalize_hcg_unit(value) or value
        if key in {"birthday", "date"}:
            return clean_date_model_value(value)
        return value
    return value


def clean_anteriority_value(value: object) -> dict[str, object] | None:
    if value is None or value == "":
        return None
    if not isinstance(value, dict):
        return None

    date = clean_model_value("date", value.get("date"))
    hcg = clean_model_value("hcg", value.get("hcg") or value.get("value"))
    operator = clean_model_value("operator", value.get("operator"))
    anteriority = {
        "date": date,
        "hcg": hcg,
        "operator": operator,
    }
    if is_blank(anteriority["hcg"]):
        return None
    if is_blank(anteriority["operator"]):
        anteriority["operator"] = "="
    return anteriority


def clean_hcg_model_value(value: str) -> str:
    match = re.match(r"\s*([0-9OoSsIiLl]+(?:[\s.,][0-9OoSsIiLl]+)*)", value)
    if not match:
        return value
    normalized = normalize_hcg_value(match.group(1))
    return normalized if re.fullmatch(r"\d+(?:[,.]\d+)?", normalized) else value


def clean_date_model_value(value: str) -> str:
    value = normalize_date(value)
    compact_match = re.fullmatch(r"(\d{2})(\d{2})([./-]\d{2,4})", value)
    if compact_match:
        return f"{compact_match.group(1)}/{compact_match.group(2)}{compact_match.group(3)}"
    return value


def format_output_date(value: object) -> object:
    if is_blank(value):
        return None
    if not isinstance(value, str):
        return value

    normalized = normalize_date(value.strip())
    year_first = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", normalized)
    if year_first:
        return format_date_parts(
            year_first.group(1),
            year_first.group(2),
            year_first.group(3),
        )

    day_first = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", normalized)
    if day_first:
        return format_date_parts(
            expand_year(day_first.group(3)),
            day_first.group(2),
            day_first.group(1),
        )

    compact_day_first = re.fullmatch(r"(\d{2})(\d{2})(\d{2,4})", normalized)
    if compact_day_first:
        return format_date_parts(
            expand_year(compact_day_first.group(3)),
            compact_day_first.group(2),
            compact_day_first.group(1),
        )

    return normalized


def format_date_parts(year: str, month: str, day: str) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def expand_year(year: str) -> str:
    if len(year) != 2:
        return year
    value = int(year)
    return str(1900 + value if value >= 30 else 2000 + value)


def fill_blank_values(target: dict[str, object], values: dict[str, str | None]) -> None:
    for key, value in values.items():
        if value is not None and is_blank(target.get(key)):
            target[key] = value


def fill_hcg_values(target: dict[str, object], hcg: dict[str, object]) -> None:
    for key, value in hcg.items():
        if value is None:
            continue
        if key in {"hcg", "operator", "unit"}:
            target[key] = value
        elif is_blank(target.get(key)):
            target[key] = value


def is_hcg_value(value: object) -> bool:
    if is_blank(value):
        return False
    return bool(re.fullmatch(r"\d+(?:[,.]\d+)?", normalize_hcg_value(str(value))))


def choose_better_result(
    first_result: dict[str, object], retry_result: dict[str, object]
) -> dict[str, object]:
    first_score = (result_confidence(first_result), weighted_field_score(first_result))
    retry_score = (result_confidence(retry_result), weighted_field_score(retry_result))
    return retry_result if retry_score > first_score else first_result


def result_confidence(result: dict[str, object]) -> float:
    extraction = nested_dict(result.get("extraction"))
    return parse_confidence(extraction.get("confidence") or result.get("confidence")) or 0.0


def compute_confidence(
    model_result: dict[str, object],
    normalized: dict[str, object],
    hcg: dict[str, str] | None,
    identity: dict[str, str],
) -> float:
    if has_zero_hcg_without_unit(normalized) or has_hcg_below_confidence_threshold(normalized):
        return 0.0

    model_extraction = nested_dict(model_result.get("extraction"))
    model_confidence = parse_confidence(model_extraction.get("confidence") or model_result.get("confidence"))
    field_score = weighted_field_score(normalized)
    score = field_score if model_confidence is None else (0.55 * model_confidence) + (0.45 * field_score)
    deterministic_hcg = {key: (hcg or {}).get(key) for key in ("hcg", "operator", "unit")}
    deterministic = {**identity, **deterministic_hcg}
    support = 0
    conflicts = 0

    for key, deterministic_value in deterministic.items():
        if is_blank(deterministic_value):
            continue
        model_value = normalized.get(key)
        if is_blank(model_value):
            continue
        if equivalent_value(key, model_value, deterministic_value):
            support += 1
        else:
            conflicts += 1

    score += min(0.08, support * 0.02)
    score -= min(0.12, conflicts * 0.04)

    if is_blank(normalized.get("hcg")):
        score -= 0.18
    if is_blank(normalized.get("first_name")) and is_blank(normalized.get("last_name")):
        score -= 0.12
    elif is_blank(normalized.get("first_name")) or is_blank(normalized.get("last_name")):
        score -= 0.04
    if is_blank(normalized.get("date")) and is_blank(normalized.get("birthday")):
        score -= 0.08
    if is_blank(normalized.get("unit")) and not is_blank(normalized.get("hcg")):
        score -= 0.03

    if field_score >= 1.0 and conflicts == 0:
        score = max(score, 0.92)
    elif field_score >= 0.85 and conflicts == 0:
        score = max(score, 0.78)

    score = apply_final_confidence_rules(normalized, score)
    return round(max(0.0, min(1.0, score)), 2)


def apply_final_confidence_rules(result: dict[str, object], score: float) -> float:
    if has_zero_hcg_without_unit(result) or has_hcg_below_confidence_threshold(result):
        return 0.0

    caps: list[float] = []
    if is_blank(result.get("hcg")):
        caps.append(0.25)
    elif not is_hcg_value(result.get("hcg")):
        caps.append(0.65)

    if is_blank(result.get("unit")) and not is_blank(result.get("hcg")):
        caps.append(0.70)
    if is_blank(result.get("operator")) and not is_blank(result.get("hcg")):
        caps.append(0.80)

    first_name_missing = is_blank(result.get("first_name"))
    last_name_missing = is_blank(result.get("last_name"))
    if first_name_missing and last_name_missing:
        caps.append(0.55)
    elif first_name_missing or last_name_missing:
        caps.append(0.85)

    if is_blank(result.get("date")):
        caps.append(0.85)
    if is_blank(result.get("birthday")):
        caps.append(0.90)

    return min([score, *caps]) if caps else score


def has_zero_hcg_without_unit(result: dict[str, object]) -> bool:
    return (
        normalize_number(result.get("hcg")) in {"0", "0.0", "0.00"}
        and normalize_hcg_operator(str(result.get("operator") or "=")) == "="
        and normalize_hcg_unit(str(result.get("unit") or "")) is None
    )


def has_hcg_below_confidence_threshold(result: dict[str, object]) -> bool:
    hcg_value = parse_hcg_float(result.get("hcg"))
    if hcg_value is None:
        return False
    return hcg_value < 0.2


def parse_hcg_float(value: object) -> float | None:
    if is_blank(value):
        return None
    try:
        return float(normalize_number(value))
    except ValueError:
        return None


def parse_confidence(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if value.endswith("%"):
            value = value[:-1]
            percent = True
        else:
            percent = False
        try:
            parsed = float(value)
        except ValueError:
            return None
        if percent:
            parsed /= 100
    else:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
    if parsed > 1 and parsed <= 100:
        parsed /= 100
    return max(0.0, min(1.0, parsed))


def weighted_field_score(result: dict[str, object]) -> float:
    result = extract_default_fields(result)
    weights = {
        "first_name": 1.0,
        "last_name": 1.0,
        "birthday": 1.0,
        "date": 1.0,
        "hcg": 1.5,
        "operator": 0.5,
        "unit": 0.75,
        "lab": 0.25,
        "method": 0.25,
        "anteriority": 0.25,
    }
    total = sum(weights.values())
    present = sum(weight for key, weight in weights.items() if not is_blank(result.get(key)))
    return present / total


def equivalent_value(key: str, left: object, right: object) -> bool:
    if key == "hcg":
        return normalize_number(left) == normalize_number(right)
    if key == "unit":
        return normalize_hcg_unit(str(left)) == normalize_hcg_unit(str(right))
    return normalize_text(left) == normalize_text(right)


def normalize_number(value: object) -> str:
    return normalize_hcg_value(str(value)).replace(",", ".")


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def is_blank(value: object) -> bool:
    return value is None or value == ""


def prepare_extraction_text(text: str) -> str:
    return normalize_ocr_artifacts(text)


def normalize_ocr_artifacts(text: str) -> str:
    normalized = text
    normalized = normalized.replace("μ", "µ")
    normalized = re.sub(r"(?i)\bpr[eéè]?n[o0]m\b", "prénom", normalized)
    normalized = re.sub(r"(?i)\bn[o0]m\b", "nom", normalized)
    normalized = re.sub(
        r"(?i)\bn[eéè]\s*[\(\[]?\s*e?\s*[\)\]]?\s*le\b",
        "né le",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\bdate\s+(?:de\s+)?na[il1ï]ssance\b",
        "date de naissance",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\b(?:β|8|b)\s*[-.]?\s*h\s*[. -]?\s*c\s*[. -]?\s*g\b",
        "beta hcg",
        normalized,
    )
    normalized = re.sub(r"(?i)\bh\s*[. -]?\s*c\s*[. -]?\s*g\b", "hcg", normalized)
    return normalized


def parse_patient_identity(text: str) -> dict[str, str]:
    normalized_text = re.sub(r"[ \t]+", " ", normalize_ocr_artifacts(text))
    identity: dict[str, str] = {}

    combined_name = extract_combined_name(normalized_text)
    last_name = extract_labeled_value(
        normalized_text,
        r"nom(?:\s+de\s+naissance)?|nom\s+patronymique|patronyme",
    )
    first_name = extract_labeled_value(normalized_text, r"pr[eé]nom(?:s)?")
    birthday = extract_birth_date(normalized_text)

    if combined_name:
        identity.update(combined_name)
    if last_name and "last_name" not in identity:
        identity["last_name"] = last_name
    if first_name and "first_name" not in identity:
        identity["first_name"] = first_name
    if birthday:
        identity["birthday"] = birthday

    if "first_name" not in identity or "last_name" not in identity:
        patient_name = extract_patient_line_name(normalized_text)
        if patient_name:
            identity.setdefault("last_name", patient_name["last_name"])
            identity.setdefault("first_name", patient_name["first_name"])

    return identity


def extract_combined_name(text: str) -> dict[str, str] | None:
    for line in text.splitlines():
        match = re.search(
            r"(?i)\bnom\b\s*(?:/|\\|et|-)\s*pr[eé]nom(?:s)?\b\s*[:\-]?\s*(.+)",
            line,
        )
        if not match:
            continue
        value = strip_after_identity_value(match.group(1))
        value = clean_name(value)
        if not value:
            continue
        name = split_last_first_name(value)
        if name:
            return name
    return None


def extract_labeled_value(text: str, label_pattern: str) -> str | None:
    labels = (
        r"pr[eé]nom(?:s)?|nom(?:\s+de\s+naissance)?|nom\s+patronymique|"
        r"patronyme|n[eé]\(?e?\)?\s+le|date\s+de\s+naissance|"
        r"naissance|sexe|dossier|patient(?:e)?"
    )
    for line in text.splitlines():
        match = re.search(rf"(?i)\b(?:{label_pattern})\b\s*[:\-]?\s*(.+)", line)
        if not match:
            continue
        value = re.split(rf"(?i)\b(?:{labels})\b\s*[:\-]?", match.group(1), maxsplit=1)[0]
        value = clean_name(value)
        if value:
            return value
    return None


def extract_birth_date(text: str) -> str | None:
    date = r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}"
    match = re.search(
        rf"(?i)\b(?:n[eé]\(?e?\)?\s+le|date\s+de\s+naissance|naissance)\b"
        rf"\s*[:\-]?\s*({date})",
        text,
    )
    return normalize_date(match.group(1)) if match else None


def normalize_date(value: str) -> str:
    return re.sub(r"\s*[./-]\s*", "/", value)


def extract_patient_line_name(text: str) -> dict[str, str] | None:
    for line in text.splitlines():
        match = re.search(r"(?i)\bpatient(?:e)?\b\s*[:\-]?\s*(.+)", line)
        if not match:
            continue
        value = re.split(r"(?i)\b(?:n[eé]\(?e?\)?\s+le|date|naissance|dossier)\b", match.group(1), maxsplit=1)[0]
        value = clean_name(value)
        if not value:
            continue
        name = split_last_first_name(value)
        if name:
            return name
    return None


def split_last_first_name(value: str) -> dict[str, str] | None:
    parts = value.replace(",", " ").split()
    if len(parts) < 2:
        return None
    uppercase_prefix = []
    for part in parts:
        if is_upper_name_part(part):
            uppercase_prefix.append(part)
            continue
        break
    if uppercase_prefix and len(uppercase_prefix) < len(parts):
        return {
            "last_name": " ".join(uppercase_prefix),
            "first_name": " ".join(parts[len(uppercase_prefix) :]),
        }
    return {"first_name": parts[0], "last_name": " ".join(parts[1:])}


def strip_after_identity_value(value: str) -> str:
    return re.split(
        r"(?i)\b(?:n[eé]\(?e?\)?\s+le|date\s+de\s+naissance|naissance|"
        r"sexe|dossier|ipp|nir|patient(?:e)?|prescripteur|docteur|dr\b)\b",
        value,
        maxsplit=1,
    )[0]


def is_upper_name_part(value: str) -> bool:
    letters = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", value)
    return bool(letters) and letters.upper() == letters


def clean_name(value: str) -> str | None:
    value = re.sub(r"\b(?:m\.?|mme|madame|monsieur|mlle)\b", "", value, flags=re.I)
    value = re.split(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}", value, maxsplit=1)[0]
    value = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' -]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -'")
    return value or None


def parse_hcg(text: str) -> dict[str, object] | None:
    normalized_text = normalize_ocr_artifacts(text)
    candidates = extract_hcg_candidates(normalized_text)
    if not candidates:
        return None

    best = max(candidates, key=lambda candidate: candidate["score"])
    if best["score"] < 4:
        return None
    return {key: best[key] for key in ("hcg", "operator", "unit", "anteriority")}


def extract_hcg_candidates(text: str) -> list[HcgCandidate]:
    candidates: list[HcgCandidate] = []
    lines = text.splitlines()
    for index, raw_line in enumerate(lines):
        line = strip_same_line_parenthesized_values(raw_line)
        for unit_match in re.finditer(HCG_UNIT_PATTERN, line, flags=re.I):
            unit = normalize_hcg_unit(unit_match.group(0))
            if unit is None:
                continue

            left = line[: unit_match.start()]
            value_match = re.search(
                rf"(?P<prefix>.*?)(?P<operator>{HCG_OPERATOR_PATTERN})?\s*"
                rf"(?P<value>{HCG_NUMBER_PATTERN})\s*$",
                left,
            )
            if not value_match:
                continue

            value = normalize_hcg_value(value_match.group("value"))
            if not re.fullmatch(r"\d+(?:[,.]\d+)?", value):
                continue

            candidates.append(
                {
                    "hcg": value,
                    "operator": normalize_hcg_operator(value_match.group("operator")),
                    "unit": unit,
                    "score": score_hcg_candidate(
                        line=line,
                        prefix=value_match.group("prefix"),
                        line_index=index,
                    ),
                    "anteriority": parse_right_side_anteriority(line[unit_match.end() :]),
                }
            )
    return candidates


def score_hcg_candidate(*, line: str, prefix: str, line_index: int) -> int:
    score = 3
    if re.search(HCG_LABEL_PATTERN, line):
        score += 5
    if re.search(r"(?i)\b(?:taux|r[eé]sultat|dosage)\b", line):
        score += 3
    if line_index <= 2:
        score -= 1
    if re.search(r"(?i)\b(?:valeurs?\s+de\s+r[eé]f[eé]rence|r[eé]f[eé]rence|norme|seuil|vr)\b", line):
        score -= 5
    if re.search(r"(?i)\b(?:ant[eé]riorit[eé]|ant[eé]rieur|pr[eé]c[eé]dent)\b", prefix):
        score -= 4
    return score


def parse_right_side_anteriority(text: str) -> dict[str, object] | None:
    match = re.search(
        rf"(?:\b(?:ant[eé]riorit[eé]|ant[eé]rieur|pr[eé]c[eé]dent)\b\s*)?"
        rf"(?P<operator>{HCG_OPERATOR_PATTERN})?\s*"
        rf"(?P<value>{HCG_NUMBER_PATTERN})"
        rf"(?:\s*{HCG_UNIT_PATTERN})?",
        text,
        flags=re.I,
    )
    if not match:
        return None
    value = normalize_hcg_value(match.group("value"))
    if not re.fullmatch(r"\d+(?:[,.]\d+)?", value):
        return None
    return {
        "date": None,
        "hcg": value,
        "operator": normalize_hcg_operator(match.group("operator")),
    }


def strip_same_line_parenthesized_values(text: str) -> str:
    return "\n".join(re.sub(r"\([^\n)]*\)", " ", line) for line in text.splitlines())


def parse_method(text: str) -> str | None:
    normalized_text = normalize_ocr_artifacts(text)
    for line in normalized_text.splitlines():
        if not re.search(HCG_LABEL_PATTERN, line):
            continue
        method = clean_method_label(line)
        if method:
            return method
        if re.search(r"(?i)\b(?:beta|b[éeè]ta|β)\b|\bb\s*[-.]?\s*hcg\b|\bbhcg\b", line):
            return "beta-HCG"
        return "HCG"
    return None


def clean_method_label(line: str) -> str | None:
    label = re.split(
        rf"(?i)\s+(?:{HCG_OPERATOR_PATTERN})?\s*{HCG_NUMBER_PATTERN}\s*{HCG_UNIT_PATTERN}.*$",
        line.strip(),
        maxsplit=1,
    )[0]
    label = re.sub(r"(?i)^[-–—:\s]*(?:analyse|examen|param[eè]tre)\s*[:\-]\s*", "", label)
    label = re.sub(r"\s+", " ", label).strip(" -–—:")
    label = normalize_method_output(label)
    if not label or not re.search(HCG_LABEL_PATTERN, label):
        return None
    if len(label) <= 4:
        return None
    return label


def normalize_method_output(value: object) -> object:
    if not isinstance(value, str):
        return value
    value = re.sub(
        r"(?i)\b(?:beta|b[éeè]ta|β)\s*[-']?\s*h\s*[. -]?\s*c\s*[. -]?\s*g\b|\bb\s*[-.]?\s*hcg\b|\bbhcg\b",
        "beta-HCG",
        value,
    )
    value = re.sub(r"(?i)\bh\s*[. -]?\s*c\s*[. -]?\s*g\b", "HCG", value)
    return value


def normalize_hcg_value(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).strip(".,")
    return normalized.translate(
        str.maketrans({"O": "0", "o": "0", "S": "5", "s": "5", "I": "1", "i": "1", "L": "1", "l": "1"})
    )


def normalize_hcg_operator(operator: str | None) -> str:
    if not operator:
        return "="
    operator = operator.strip()
    if operator in {"≤", "=<", "<="}:
        return "<="
    if operator in {"≥", "=>", ">="}:
        return ">="
    if operator.startswith("<"):
        return "<"
    if operator.startswith(">"):
        return ">"
    return "="


def normalize_hcg_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    compact = unit.casefold().replace("µ", "u").replace("μ", "u")
    compact = re.sub(r"[^a-z0-9/]+", "", compact)
    compact = compact.replace("1", "i")
    compact = compact.replace("ul", "ui")
    compact = compact.replace("u/", "ui/")

    if not compact or "u" not in compact:
        return None
    if compact.startswith("mui") or compact.startswith("mui/") or compact.startswith("mu"):
        return "mUI/mL" if "ml" in compact or compact.endswith("m") else "mUI/mL"
    if compact.startswith("ui") or compact.startswith("u"):
        return "UI/L" if "l" in compact else "UI/L"
    return None
