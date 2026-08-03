from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import requests
import urllib3
from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError
from requests.exceptions import SSLError as RequestsSSLError
from redcap import Project, RedcapError
from urllib3.exceptions import InsecureRequestWarning

from app.config import load_env_file


load_env_file()


DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETURN_CONTENT = "count"
DEFAULT_OVERWRITE = "normal"
DEFAULT_DATE_FORMAT = "YMD"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "test" / "output"
DEFAULT_RECORD_ID_FIELD = "pat"
DEFAULT_FIRST_NAME_FIELD = "prenom"
DEFAULT_LAST_NAME_FIELD = "nom"
DEFAULT_DUPLICATE_CHECK_FIELD = "concat_nom_prenom_ddn"
DEFAULT_HCG_INSTRUMENT = "table_hcg"
DEFAULT_HCG_DATE_FIELD = "date_hcg"
DEFAULT_HCG_VALUE_FIELD = "hcg"
DEFAULT_HCG_LESS_THAN_FIELD = "signe_inferieur___inferieur"
DEFAULT_HCG_GREATER_THAN_FIELD = "signe_superieur___superieur"
DEFAULT_HCG_LABORATORY_FIELD = "nom_labo_hcg"
DEFAULT_HCG_METHOD_FIELD = "kit_labo"
DEFAULT_HCG_DATE_FORMAT = "DMY"
DEFAULT_HCG_REPEATING_MODE = "instrument"


class TransferNotConfiguredError(RuntimeError):
    """Raised when external API transfer is requested before configuration exists."""


class TransferError(RuntimeError):
    """Raised when REDCap rejects or fails a transfer."""


class PatientMatchError(TransferError):
    """Raised when extracted patient identity needs manual review."""


class PatientNotFoundError(TransferError):
    """Raised when no REDCap patient record matches the extracted identity."""


def main() -> None:
    print("[transfer] starting fixed transfer debug workflow")
    print("[transfer] step 1: export REDCap events")
    try:
        debug_events()
    except Exception as exc:  # noqa: BLE001 - debug workflow should continue when possible.
        print(f"[transfer] event export failed: {exc}")
        print("[transfer] continuing to patient lookup")
    print("[transfer] step 2: export REDCap instrument mappings")
    try:
        debug_instrument_mappings()
    except Exception as exc:  # noqa: BLE001 - debug workflow should continue when possible.
        print(f"[transfer] instrument mapping export failed: {exc}")
        print("[transfer] continuing to patient lookup")
    print("[transfer] step 3: lookup extracted JSON patients")
    transfer_output_dir(DEFAULT_OUTPUT_DIR, should_import=True)


def debug_events(
    *,
    api_url: str | None = None,
    token: str | None = None,
    timeout: float | None = None,
    verify_ssl: bool | str | None = None,
    event_id: int | None = None,
) -> None:
    project = redcap_project(
        api_url=api_url,
        token=token,
        timeout=timeout,
        verify_ssl=verify_ssl,
    )
    print("[transfer] exporting REDCap events")
    events = project.export_events()
    if not isinstance(events, list):
        print(f"[transfer] unexpected REDCap events response type: {type(events).__name__}")
        print(f"[transfer] REDCap events response: {events!r}")
        return

    filtered_events = [event for event in events if event_matches_id(event, event_id)]
    print(f"[transfer] REDCap events count: {len(events)}")
    if event_id is not None:
        print(f"[transfer] REDCap events matching event_id={event_id}: {len(filtered_events)}")

    for event in filtered_events:
        if not isinstance(event, dict):
            print(f"[transfer] unexpected REDCap event item: {event!r}")
            continue
        print(
            "[transfer] event "
            f"event_id={event.get('event_id')} "
            f"unique_event_name={event.get('unique_event_name')!r} "
            f"event_name={event.get('event_name')!r} "
            f"arm_num={event.get('arm_num')!r} "
            f"custom_event_label={event.get('custom_event_label')!r}"
        )


def debug_instrument_mappings() -> None:
    project = redcap_project()
    hcg_config = hcg_import_config()
    instrument = hcg_config["instrument"]

    print("[transfer] exporting REDCap instrument-event mappings")
    mappings = export_instrument_event_mappings(project)
    matching_mappings = [mapping for mapping in mappings if mapping.get("form") == instrument]
    print(f"[transfer] REDCap instrument-event mappings count: {len(mappings)}")
    print(f"[transfer] mappings for {instrument}: {json.dumps(matching_mappings, ensure_ascii=False, default=str)}")

    print("[transfer] exporting REDCap repeating instruments/events settings")
    try:
        repeating_settings = export_repeating_settings(project)
    except RedcapError as exc:
        print(f"[transfer] repeating settings unavailable: {exc}")
        return
    matching_repeating = [
        setting
        for setting in repeating_settings
        if setting.get("form_name") in {instrument, ""}
    ]
    print(f"[transfer] REDCap repeating settings count: {len(repeating_settings)}")
    print(
        f"[transfer] repeating settings relevant to {instrument}: "
        f"{json.dumps(matching_repeating, ensure_ascii=False, default=str)}"
    )


def event_matches_id(event: object, event_id: int | None) -> bool:
    if event_id is None:
        return True
    if not isinstance(event, dict):
        return False
    return str(event.get("event_id")) == str(event_id)


def redcap_project(
    *,
    api_url: str | None = None,
    token: str | None = None,
    timeout: float | None = None,
    verify_ssl: bool | str | None = None,
) -> Project:
    resolved_api_url = redcap_api_url(api_url or os.getenv("REDCAP_API_URL"))
    if not resolved_api_url:
        raise TransferNotConfiguredError("Set REDCAP_API_URL or pass --api-url")

    resolved_token = token or os.getenv("REDCAP_TOKEN")
    if not resolved_token:
        raise TransferNotConfiguredError("Set REDCAP_TOKEN or pass --token")

    resolved_timeout = timeout if timeout is not None else transfer_timeout()
    resolved_verify_ssl = verify_ssl if verify_ssl is not None else redcap_verify_ssl()
    print(f"[transfer] REDCap API URL: {resolved_api_url}")
    print(f"[transfer] REDCap token configured: yes ({len(resolved_token)} chars)")
    print(f"[transfer] REDCap timeout: {resolved_timeout}")
    print(f"[transfer] REDCap verify_ssl: {resolved_verify_ssl}")
    if resolved_verify_ssl is False:
        urllib3.disable_warnings(InsecureRequestWarning)
        print("[transfer] REDCap SSL warnings disabled for this debug run")

    print("[transfer] connecting to REDCap with PyCap Project")
    project = Project(
        resolved_api_url,
        resolved_token,
        verify_ssl=resolved_verify_ssl,
        timeout=resolved_timeout,
    )
    print("[transfer] connected to REDCap")
    return project


def transfer_output_dir(
    output_dir: Path,
    *,
    api_url: str | None = None,
    token: str | None = None,
    timeout: float | None = None,
    return_content: str | None = None,
    overwrite: str | None = None,
    date_format: str | None = None,
    force_auto_number: bool | None = None,
    verify_ssl: bool | str | None = None,
    first_name_field: str | None = None,
    last_name_field: str | None = None,
    record_id_field: str | None = None,
    should_import: bool = False,
) -> None:
    resolved_output_dir = output_dir.resolve()
    files = [path for path in sorted(resolved_output_dir.glob("*.json")) if path.is_file()]
    if not files:
        print(f"No JSON files found in {resolved_output_dir}")
        return

    for output_path in files:
        print(f"transfer {output_path.name}")
        payload = load_payload(output_path)
        response_payload = transfer_payload(
            payload,
            api_url=api_url,
            token=token,
            timeout=timeout,
            return_content=return_content,
            overwrite=overwrite,
            date_format=date_format,
            force_auto_number=force_auto_number,
            verify_ssl=verify_ssl,
            first_name_field=first_name_field,
            last_name_field=last_name_field,
            record_id_field=record_id_field,
            should_import=should_import,
        )
        print(f"ok {output_path.name}: {response_payload}")


def load_payload(output_path: Path) -> dict[str, object]:
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{output_path.name} must contain a JSON object")
    return payload


def transfer_payload(
    payload: dict[str, object],
    *,
    api_url: str | None = None,
    token: str | None = None,
    timeout: float | None = None,
    return_content: str | None = None,
    overwrite: str | None = None,
    date_format: str | None = None,
    force_auto_number: bool | None = None,
    verify_ssl: bool | str | None = None,
    first_name_field: str | None = None,
    last_name_field: str | None = None,
    record_id_field: str | None = None,
    should_import: bool = False,
) -> Any:
    print("[transfer] start payload transfer")
    print(f"[transfer] payload keys: {sorted(payload.keys())}")
    resolved_api_url = redcap_api_url(api_url or os.getenv("REDCAP_API_URL"))
    if not resolved_api_url:
        raise TransferNotConfiguredError("Set REDCAP_API_URL or pass --api-url")

    resolved_token = token or os.getenv("REDCAP_TOKEN")
    if not resolved_token:
        raise TransferNotConfiguredError("Set REDCAP_TOKEN or pass --token")

    resolved_timeout = timeout if timeout is not None else transfer_timeout()
    resolved_verify_ssl = verify_ssl if verify_ssl is not None else redcap_verify_ssl()
    resolved_first_name_field = first_name_field or os.getenv(
        "REDCAP_FIRST_NAME_FIELD", DEFAULT_FIRST_NAME_FIELD
    )
    resolved_last_name_field = last_name_field or os.getenv(
        "REDCAP_LAST_NAME_FIELD", DEFAULT_LAST_NAME_FIELD
    )
    resolved_record_id_field = record_id_field or os.getenv(
        "REDCAP_RECORD_ID_FIELD", DEFAULT_RECORD_ID_FIELD
    )
    resolved_duplicate_check_field = os.getenv(
        "REDCAP_DUPLICATE_CHECK_FIELD", DEFAULT_DUPLICATE_CHECK_FIELD
    )
    hcg_config = hcg_import_config()
    records = redcap_records(payload)
    print(f"[transfer] REDCap API URL: {resolved_api_url}")
    print(f"[transfer] REDCap token configured: yes ({len(resolved_token)} chars)")
    print(f"[transfer] REDCap timeout: {resolved_timeout}")
    print(f"[transfer] REDCap verify_ssl: {resolved_verify_ssl}")
    if resolved_verify_ssl is False:
        urllib3.disable_warnings(InsecureRequestWarning)
        print("[transfer] REDCap SSL warnings disabled for this debug run")
    print(f"[transfer] REDCap record id field: {resolved_record_id_field}")
    print(f"[transfer] REDCap duplicate check field: {resolved_duplicate_check_field}")
    print(f"[transfer] REDCap first name field: {resolved_first_name_field}")
    print(f"[transfer] REDCap last name field: {resolved_last_name_field}")
    print(f"[transfer] REDCap HCG event override: {hcg_config['event']!r}")
    print(f"[transfer] REDCap HCG instrument: {hcg_config['instrument']}")
    print(f"[transfer] REDCap HCG date field: {hcg_config['date_field']}")
    print(f"[transfer] REDCap HCG value field: {hcg_config['value_field']}")
    print(f"[transfer] REDCap HCG less-than field: {hcg_config['less_than_field']}")
    print(f"[transfer] REDCap HCG greater-than field: {hcg_config['greater_than_field']}")
    print(f"[transfer] REDCap HCG laboratory field: {hcg_config['laboratory_field']}")
    print(f"[transfer] REDCap HCG method field: {hcg_config['method_field']}")
    print(f"[transfer] REDCap HCG repeating mode: {hcg_config['repeating_mode']}")
    print(f"[transfer] import enabled: {should_import}")
    if should_import:
        print(f"[transfer] prepared import records count: {len(records)}")
        print(
            "[transfer] prepared import records preview: "
            f"{json.dumps(records[:3], ensure_ascii=False, default=str)}"
        )

    try:
        print("[transfer] connecting to REDCap with PyCap Project")
        project = Project(
            resolved_api_url,
            resolved_token,
            verify_ssl=resolved_verify_ssl,
            timeout=resolved_timeout,
        )
        print("[transfer] connected to REDCap")
        search_result = search_patient_records(
            project,
            payload,
            record_id_field=resolved_record_id_field,
            duplicate_check_field=resolved_duplicate_check_field,
            first_name_field=resolved_first_name_field,
            last_name_field=resolved_last_name_field,
        )
        print(f"[transfer] patient search returned {len(search_result)} record(s)")
        print_patient_matches(search_result, record_id_field=resolved_record_id_field)
        if not should_import:
            print("[transfer] lookup-only mode: import skipped")
            return {
                "matched_count": len(search_result),
                "record_ids": patient_record_ids(
                    search_result,
                    record_id_field=resolved_record_id_field,
                ),
            }

        print("[transfer] preparing REDCap import record")
        hcg_record = build_hcg_import_record(
            project,
            payload,
            search_result,
            record_id_field=resolved_record_id_field,
            hcg_config=hcg_config,
        )
        return project.import_records(
            [hcg_record],
            return_content=return_content or os.getenv("REDCAP_RETURN_CONTENT", DEFAULT_RETURN_CONTENT),
            overwrite=overwrite or os.getenv("REDCAP_OVERWRITE", DEFAULT_OVERWRITE),
            date_format=date_format or os.getenv("REDCAP_HCG_DATE_FORMAT", DEFAULT_HCG_DATE_FORMAT),
            force_auto_number=force_auto_number
            if force_auto_number is not None
            else env_bool("REDCAP_FORCE_AUTO_NUMBER", False),
        )
    except RequestsSSLError as exc:
        print("[transfer] REDCap SSL verification failed")
        print("[transfer] For local debugging, set REDCAP_VERIFY_SSL=false in .env or pass --no-verify-ssl")
        print("[transfer] For verified SSL, set REDCAP_VERIFY_SSL to the CA bundle path trusted by the container")
        raise TransferError(f"REDCap SSL verification failed: {exc}") from exc
    except RequestsJSONDecodeError as exc:
        print("[transfer] REDCap returned a non-JSON response while JSON was expected")
        print("[transfer] This often means the API URL, token, project rights, or SSO/network layer returned HTML/text")
        raise TransferError(f"REDCap returned non-JSON response: {exc}") from exc
    except RedcapError as exc:
        raise TransferError(f"REDCap transfer failed: {exc}") from exc


def redcap_api_url(api_url: str | None) -> str | None:
    if not api_url:
        return None
    normalized = api_url.strip()
    if normalized.endswith("/api"):
        return f"{normalized}/"
    return normalized


def search_patient_records(
    project: Project,
    payload: dict[str, object],
    *,
    record_id_field: str,
    duplicate_check_field: str,
    first_name_field: str,
    last_name_field: str,
) -> list[dict[str, Any]]:
    first_name, last_name, birth_date = patient_identity(payload)
    print(f"[transfer] extracted patient.first_name: {first_name!r}")
    print(f"[transfer] extracted patient.last_name: {last_name!r}")
    print(f"[transfer] extracted patient.birth_date: {birth_date!r}")
    duplicate_check_value = patient_duplicate_check_value(
        first_name=first_name,
        last_name=last_name,
        birth_date=birth_date,
    )
    if not duplicate_check_value:
        print("[transfer] patient search skipped: missing first name, last name, or birth date")
        return []

    filter_logic = duplicate_check_filter_logic(
        duplicate_check_field=duplicate_check_field,
        duplicate_check_value=duplicate_check_value,
    )
    fields = redcap_search_fields(record_id_field, duplicate_check_field)
    print(f"[transfer] REDCap intended search fields: {fields}")
    print(f"[transfer] REDCap duplicate check value: {duplicate_check_value}")
    print("[transfer] REDCap export fields: explicit fields via direct PyCap API call")
    print(f"[transfer] REDCap filter_logic: {filter_logic}")
    print("[transfer] running REDCap direct record export search")

    payload = redcap_record_export_payload(fields=fields, filter_logic=filter_logic)
    payload["token"] = project.token
    print(f"[transfer] REDCap search payload: {redacted_payload(payload)}")
    try:
        records = project._call_api(payload, "json")
    except RequestsJSONDecodeError:
        debug_raw_redcap_response(project, payload)
        raise

    if not isinstance(records, list):
        print(f"[transfer] unexpected REDCap search response type: {type(records).__name__}")
        return []

    print(
        "[transfer] REDCap search response preview: "
        f"{json.dumps(records[:10], ensure_ascii=False, default=str)}"
    )
    return records


def print_patient_matches(
    records: list[dict[str, Any]],
    *,
    record_id_field: str,
) -> None:
    if not records:
        print("[transfer] no REDCap patient match found")
        return

    record_ids = patient_record_ids(records, record_id_field=record_id_field)
    print(f"[transfer] matched REDCap {record_id_field} value(s): {record_ids}")
    if len(records) == 1:
        print(
            "[transfer] matched REDCap record: "
            f"{json.dumps(records[0], ensure_ascii=False, default=str)}"
        )
        return

    print("[transfer] multiple REDCap patient matches found")
    print(
        "[transfer] matched REDCap records preview: "
        f"{json.dumps(records[:10], ensure_ascii=False, default=str)}"
    )


def patient_record_ids(
    records: list[dict[str, Any]],
    *,
    record_id_field: str,
) -> list[Any]:
    return [record.get(record_id_field) for record in records if record.get(record_id_field)]


def attach_record_id(
    import_records: list[dict[str, Any]],
    search_records: list[dict[str, Any]],
    *,
    record_id_field: str,
) -> list[dict[str, Any]]:
    record_ids = patient_record_ids(search_records, record_id_field=record_id_field)
    if len(record_ids) == 0:
        raise PatientNotFoundError("patient_not_found")
    if len(record_ids) > 1:
        raise PatientMatchError(
            f"Import requires exactly one matched {record_id_field}, got {len(record_ids)}"
        )

    record_id = record_ids[0]
    print(f"[transfer] attaching matched {record_id_field}: {record_id!r}")
    return [{**record, record_id_field: record_id} for record in import_records]


def build_hcg_import_record(
    project: Project,
    extraction_payload: dict[str, object],
    search_records: list[dict[str, Any]],
    *,
    record_id_field: str,
    hcg_config: dict[str, str],
) -> dict[str, Any]:
    record_ids = patient_record_ids(search_records, record_id_field=record_id_field)
    if len(record_ids) == 0:
        raise PatientNotFoundError("patient_not_found")
    if len(record_ids) > 1:
        raise PatientMatchError(
            f"HCG import requires exactly one matched {record_id_field}, got {len(record_ids)}"
        )

    record_id = record_ids[0]
    event_name = hcg_config["event"] or discover_hcg_event(
        project,
        instrument=hcg_config["instrument"],
        record_id=record_id,
        record_id_field=record_id_field,
        search_records=search_records,
    )
    if not event_name:
        raise TransferError("HCG import needs a REDCap event name")
    repeating_mode = resolve_hcg_repeating_mode(
        project,
        event_name=event_name,
        hcg_config=hcg_config,
    )

    analysis_date = hcg_analysis_date(extraction_payload)
    hcg_value = hcg_result_value(extraction_payload)
    if not analysis_date:
        raise TransferError("HCG import needs analysis.date in the extraction payload")
    if hcg_value is None:
        raise TransferError("HCG import needs analysis.result.value in the extraction payload")

    print(f"[transfer] matched {record_id_field}: {record_id!r}")
    print(f"[transfer] HCG target event: {event_name}")
    print(f"[transfer] HCG repeating mode: {repeating_mode}")
    print("[transfer] checking existing REDCap HCG dates across all patient episodes")
    all_patient_hcg_rows = export_all_patient_hcg_rows(
        project,
        record_id=record_id,
        hcg_config=hcg_config,
        repeating_mode=repeating_mode,
    )
    if hcg_date_exists(all_patient_hcg_rows, hcg_config["date_field"], analysis_date):
        raise TransferError("same_date_analysis_found")

    print("[transfer] exporting existing REDCap HCG rows")
    existing_rows = export_existing_hcg_rows(
        project,
        record_id=record_id,
        record_id_field=record_id_field,
        event_name=event_name,
        hcg_config=hcg_config,
        repeating_mode=repeating_mode,
    )

    next_instance = next_hcg_repeat_instance(
        existing_rows,
        hcg_config["instrument"],
        repeating_mode=repeating_mode,
    )
    print(f"[transfer] next HCG repeat instance: {next_instance}")

    hcg_record = {
        record_id_field: record_id,
        "redcap_event_name": event_name,
        hcg_config["date_field"]: format_redcap_dmy_date(analysis_date),
        hcg_config["value_field"]: hcg_value,
    }
    hcg_record.update(hcg_operator_fields(extraction_payload, hcg_config))
    hcg_record.update(hcg_optional_text_fields(extraction_payload, hcg_config))
    if repeating_mode == "instrument":
        hcg_record["redcap_repeat_instrument"] = hcg_config["instrument"]
        hcg_record["redcap_repeat_instance"] = next_instance
    elif repeating_mode == "event":
        hcg_record["redcap_repeat_instrument"] = ""
        hcg_record["redcap_repeat_instance"] = next_instance

    print(
        "[transfer] HCG import record: "
        f"{json.dumps(hcg_record, ensure_ascii=False, default=str)}"
    )
    return hcg_record


def hcg_import_config() -> dict[str, str]:
    return {
        "event": os.getenv("REDCAP_HCG_EVENT", "").strip(),
        "instrument": os.getenv("REDCAP_HCG_INSTRUMENT", DEFAULT_HCG_INSTRUMENT).strip()
        or DEFAULT_HCG_INSTRUMENT,
        "date_field": os.getenv("REDCAP_HCG_DATE_FIELD", DEFAULT_HCG_DATE_FIELD).strip()
        or DEFAULT_HCG_DATE_FIELD,
        "value_field": os.getenv("REDCAP_HCG_VALUE_FIELD", DEFAULT_HCG_VALUE_FIELD).strip()
        or DEFAULT_HCG_VALUE_FIELD,
        "less_than_field": os.getenv(
            "REDCAP_HCG_LESS_THAN_FIELD", DEFAULT_HCG_LESS_THAN_FIELD
        ).strip()
        or DEFAULT_HCG_LESS_THAN_FIELD,
        "greater_than_field": os.getenv(
            "REDCAP_HCG_GREATER_THAN_FIELD", DEFAULT_HCG_GREATER_THAN_FIELD
        ).strip()
        or DEFAULT_HCG_GREATER_THAN_FIELD,
        "laboratory_field": os.getenv(
            "REDCAP_HCG_LABORATORY_FIELD", DEFAULT_HCG_LABORATORY_FIELD
        ).strip()
        or DEFAULT_HCG_LABORATORY_FIELD,
        "method_field": os.getenv(
            "REDCAP_HCG_METHOD_FIELD", DEFAULT_HCG_METHOD_FIELD
        ).strip()
        or DEFAULT_HCG_METHOD_FIELD,
        "repeating_mode": os.getenv(
            "REDCAP_HCG_REPEATING_MODE", DEFAULT_HCG_REPEATING_MODE
        ).strip()
        or DEFAULT_HCG_REPEATING_MODE,
    }


def matched_event_name(search_records: list[dict[str, Any]]) -> str | None:
    for record in search_records:
        value = string_value(record.get("redcap_event_name"))
        if value:
            return value
    return None


def discover_hcg_event(
    project: Project,
    *,
    instrument: str,
    record_id: Any,
    record_id_field: str,
    search_records: list[dict[str, Any]],
) -> str | None:
    mappings = export_instrument_event_mappings(project)
    instrument_events = [
        string_value(mapping.get("unique_event_name"))
        for mapping in mappings
        if mapping.get("form") == instrument
    ]
    instrument_events = [event for event in instrument_events if event]
    print(f"[transfer] REDCap events mapped to {instrument}: {instrument_events}")
    if not instrument_events:
        return matched_event_name(search_records)

    latest_arm = latest_patient_arm(project, record_id=record_id, record_id_field=record_id_field)
    if latest_arm is not None:
        latest_event = event_for_arm(instrument_events, latest_arm)
        if latest_event:
            print(f"[transfer] selected HCG event from latest patient episode arm {latest_arm}: {latest_event}")
            return latest_event
        raise TransferError(f"No {instrument} event is mapped to latest patient episode arm {latest_arm}")

    matched_event = matched_event_name(search_records)
    if matched_event:
        matched_arm = event_arm_number(matched_event)
        if matched_arm is not None:
            matched_arm_event = event_for_arm(instrument_events, matched_arm)
            if matched_arm_event:
                print(f"[transfer] selected HCG event by matched patient arm {matched_arm}: {matched_arm_event}")
                return matched_arm_event

    print(f"[transfer] selected first HCG mapped event: {instrument_events[0]}")
    return instrument_events[0]


def latest_patient_arm(project: Project, *, record_id: Any, record_id_field: str) -> int | None:
    rows = export_patient_rows(project, record_id=record_id)
    data_rows = [row for row in rows if has_redcap_data(row, record_id_field=record_id_field)]
    arms = [arm for row in data_rows if (arm := event_arm_number(row.get("redcap_event_name"))) is not None]
    print(f"[transfer] patient rows count: {len(rows)}")
    print(f"[transfer] patient rows with data count: {len(data_rows)}")
    print(f"[transfer] patient episode arms with data: {sorted(set(arms))}")
    if not arms:
        return None
    return max(arms)


def export_patient_rows(project: Project, *, record_id: Any) -> list[dict[str, Any]]:
    payload = redcap_record_export_payload(fields=None, records=[str(record_id)])
    payload["token"] = project.token
    print(f"[transfer] REDCap patient episode export payload: {redacted_payload(payload)}")
    try:
        rows = project._call_api(payload, "json")
    except RequestsJSONDecodeError:
        debug_raw_redcap_response(project, payload)
        raise

    if not isinstance(rows, list):
        print(f"[transfer] unexpected patient episode export response type: {type(rows).__name__}")
        return []
    return [row for row in rows if isinstance(row, dict)]


def has_redcap_data(row: dict[str, Any], *, record_id_field: str) -> bool:
    metadata_fields = {
        record_id_field,
        "redcap_event_name",
        "redcap_repeat_instrument",
        "redcap_repeat_instance",
    }
    for field_name, value in row.items():
        if field_name in metadata_fields or field_name.endswith("_complete"):
            continue
        if string_value(value):
            return True
    return False


def event_for_arm(events: list[str], arm: int) -> str | None:
    for event in events:
        if event_arm_number(event) == arm:
            return event
    return None


def event_arm_suffix(event_name: str) -> str | None:
    marker = "_arm_"
    if marker not in event_name:
        return None
    return event_name.rsplit(marker, 1)[1]


def event_arm_number(event_name: object) -> int | None:
    value = string_value(event_name)
    if not value:
        return None
    suffix = event_arm_suffix(value)
    if suffix is None:
        return None
    return parse_int(suffix)


def resolve_hcg_repeating_mode(
    project: Project,
    *,
    event_name: str,
    hcg_config: dict[str, str],
) -> str:
    configured_mode = hcg_config["repeating_mode"].lower()
    if configured_mode in {"instrument", "event", "none"}:
        print(f"[transfer] using configured HCG repeating mode: {configured_mode}")
        return configured_mode
    if configured_mode != "auto":
        raise TransferError(
            "REDCAP_HCG_REPEATING_MODE must be instrument, event, none, or auto"
        )

    print("[transfer] auto-detecting HCG repeating mode")
    return hcg_repeating_mode(
        project,
        event_name=event_name,
        instrument=hcg_config["instrument"],
    )


def hcg_repeating_mode(project: Project, *, event_name: str, instrument: str) -> str:
    repeating_settings = export_repeating_settings(project)
    print(
        "[transfer] repeating settings for target event: "
        f"{json.dumps([row for row in repeating_settings if row.get('event_name') == event_name], ensure_ascii=False, default=str)}"
    )
    for setting in repeating_settings:
        if setting.get("event_name") != event_name:
            continue
        form_name = setting.get("form_name")
        if form_name == instrument:
            return "instrument"
        if form_name == "":
            return "event"
    return "none"


def export_instrument_event_mappings(project: Project) -> list[dict[str, Any]]:
    mappings = project.export_instrument_event_mappings()
    if not isinstance(mappings, list):
        print(f"[transfer] unexpected mapping response type: {type(mappings).__name__}")
        return []
    return [mapping for mapping in mappings if isinstance(mapping, dict)]


def export_repeating_settings(project: Project) -> list[dict[str, Any]]:
    settings = project.export_repeating_instruments_events()
    if not isinstance(settings, list):
        print(f"[transfer] unexpected repeating settings response type: {type(settings).__name__}")
        return []
    return [setting for setting in settings if isinstance(setting, dict)]


def hcg_analysis_date(payload: dict[str, object]) -> str | None:
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        return None
    return string_value(analysis.get("date"))


def hcg_result_value(payload: dict[str, object]) -> object:
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        return None
    result = analysis.get("result")
    if not isinstance(result, dict):
        return None
    return result.get("value")


def hcg_result_operator(payload: dict[str, object]) -> str:
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        return "="
    result = analysis.get("result")
    if not isinstance(result, dict):
        return "="
    return string_value(result.get("operator")) or "="


def hcg_operator_fields(payload: dict[str, object], hcg_config: dict[str, str]) -> dict[str, str]:
    operator = hcg_result_operator(payload).strip()
    if operator.startswith("<"):
        return {hcg_config["less_than_field"]: "1"}
    if operator.startswith(">"):
        return {hcg_config["greater_than_field"]: "1"}
    return {}


def hcg_optional_text_fields(payload: dict[str, object], hcg_config: dict[str, str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    laboratory_name = hcg_laboratory_name(payload)
    if laboratory_name:
        fields[hcg_config["laboratory_field"]] = laboratory_name

    method = hcg_analysis_method(payload)
    if method:
        fields[hcg_config["method_field"]] = method

    return fields


def hcg_laboratory_name(payload: dict[str, object]) -> str | None:
    laboratory = payload.get("laboratory")
    if not isinstance(laboratory, dict):
        return None
    return string_value(laboratory.get("name"))


def hcg_analysis_method(payload: dict[str, object]) -> str | None:
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        return None
    return string_value(analysis.get("method"))


def format_redcap_dmy_date(value: str) -> str:
    normalized = value.strip().replace("/", "-")
    parts = normalized.split("-")
    if len(parts) != 3:
        raise TransferError(f"Unsupported analysis date format: {value!r}")

    if len(parts[0]) == 4:
        year, month, day = parts
    else:
        day, month, year = parts
    return f"{int(day):02d}/{int(month):02d}/{int(year):04d}"


def hcg_date_exists(rows: list[dict[str, Any]], date_field: str, analysis_date: str) -> bool:
    target_date = normalized_redcap_date(analysis_date)
    if target_date is None:
        return False

    for row in rows:
        row_date = string_value(row.get(date_field))
        if row_date and normalized_redcap_date(row_date) == target_date:
            return True
    return False


def hcg_dates(rows: list[dict[str, Any]], date_field: str) -> list[str]:
    dates: list[str] = []
    for row in rows:
        row_date = string_value(row.get(date_field))
        if row_date:
            dates.append(row_date)
    return dates


def normalized_redcap_date(value: str) -> str | None:
    try:
        return format_redcap_dmy_date(value)
    except (TransferError, ValueError):
        return None


def export_existing_hcg_rows(
    project: Project,
    *,
    record_id: Any,
    record_id_field: str,
    event_name: str,
    hcg_config: dict[str, str],
    repeating_mode: str,
) -> list[dict[str, Any]]:
    payload = redcap_record_export_payload(
        fields=None,
        records=[str(record_id)],
        events=[event_name],
    )
    payload["token"] = project.token
    print(f"[transfer] REDCap HCG export payload: {redacted_payload(payload)}")
    try:
        rows = project._call_api(payload, "json")
    except RequestsJSONDecodeError:
        debug_raw_redcap_response(project, payload)
        raise

    if not isinstance(rows, list):
        print(f"[transfer] unexpected HCG export response type: {type(rows).__name__}")
        return []

    rows = [row for row in rows if isinstance(row, dict)]
    print(f"[transfer] REDCap raw rows for patient/event count: {len(rows)}")
    target_rows = [
        row
        for row in rows
        if is_target_hcg_repeat_row(
            row,
            instrument=hcg_config["instrument"],
            repeating_mode=repeating_mode,
        )
    ]
    print(f"[transfer] existing HCG rows after local filter count: {len(target_rows)}")
    print(f"[transfer] existing HCG repeat instances: {hcg_repeat_instances(target_rows)}")
    print(
        "[transfer] existing HCG rows preview: "
        f"{json.dumps(target_rows[:10], ensure_ascii=False, default=str)}"
    )
    return target_rows


def export_all_patient_hcg_rows(
    project: Project,
    *,
    record_id: Any,
    hcg_config: dict[str, str],
    repeating_mode: str,
) -> list[dict[str, Any]]:
    rows = export_patient_rows(project, record_id=record_id)
    target_rows = [
        row
        for row in rows
        if is_target_hcg_repeat_row(
            row,
            instrument=hcg_config["instrument"],
            repeating_mode=repeating_mode,
        )
    ]
    print(f"[transfer] existing HCG rows across all patient episodes count: {len(target_rows)}")
    print(f"[transfer] existing HCG dates across all patient episodes: {hcg_dates(target_rows, hcg_config['date_field'])}")
    return target_rows


def is_target_hcg_repeat_row(
    row: dict[str, Any],
    *,
    instrument: str,
    repeating_mode: str,
) -> bool:
    repeat_instrument = row.get("redcap_repeat_instrument")
    if repeating_mode == "none":
        return True
    if repeating_mode == "event":
        return repeat_instrument in {"", None}
    return repeat_instrument == instrument


def next_hcg_repeat_instance(
    rows: list[dict[str, Any]],
    instrument: str,
    *,
    repeating_mode: str,
) -> int | None:
    if repeating_mode == "none":
        return None

    max_instance = 0
    for row in rows:
        if not is_target_hcg_repeat_row(
            row,
            instrument=instrument,
            repeating_mode=repeating_mode,
        ):
            continue
        instance = parse_int(row.get("redcap_repeat_instance"))
        if instance is not None:
            max_instance = max(max_instance, instance)
    return max_instance + 1


def hcg_repeat_instances(rows: list[dict[str, Any]]) -> list[int]:
    instances: list[int] = []
    for row in rows:
        instance = parse_int(row.get("redcap_repeat_instance"))
        if instance is not None:
            instances.append(instance)
    return sorted(instances)


def parse_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def redcap_record_export_payload(
    *,
    fields: list[str] | None,
    filter_logic: str | None = None,
    events: list[str] | None = None,
    forms: list[str] | None = None,
    records: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content": "record",
        "format": "json",
        "type": "flat",
        "rawOrLabel": "raw",
        "rawOrLabelHeaders": "raw",
        "exportCheckboxLabel": "false",
    }
    if filter_logic:
        payload["filterLogic"] = filter_logic
    for index, field in enumerate(fields or []):
        payload[f"fields[{index}]"] = field
    for index, record in enumerate(records or []):
        payload[f"records[{index}]"] = record
    for index, event in enumerate(events or []):
        payload[f"events[{index}]"] = event
    for index, form in enumerate(forms or []):
        payload[f"forms[{index}]"] = form
    return payload


def debug_raw_redcap_response(project: Project, payload: dict[str, Any]) -> None:
    print("[transfer] repeating REDCap search once for raw response debug")
    try:
        response = requests.post(
            project.url,
            data=payload,
            verify=project.verify_ssl,
            timeout=project._request_kwargs.get("timeout"),
        )
    except requests.RequestException as exc:
        print(f"[transfer] raw debug request failed: {type(exc).__name__}: {exc}")
        return

    print(f"[transfer] raw REDCap status: {response.status_code}")
    print(f"[transfer] raw REDCap content-type: {response.headers.get('content-type')}")
    print(f"[transfer] raw REDCap response preview: {response.text[:1000]!r}")


def redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: ("***" if key == "token" else value) for key, value in payload.items()}


def patient_identity(payload: dict[str, object]) -> tuple[str | None, str | None, str | None]:
    patient = payload.get("patient")
    if not isinstance(patient, dict):
        return None, None, None
    return (
        string_value(patient.get("first_name")),
        string_value(patient.get("last_name")),
        string_value(patient.get("birth_date")),
    )


def string_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def patient_filter_logic(
    *,
    first_name: str,
    last_name: str,
    first_name_field: str,
    last_name_field: str,
) -> str:
    return (
        f"[{first_name_field}] = '{redcap_string_literal(first_name)}' "
        f"and [{last_name_field}] = '{redcap_string_literal(last_name)}'"
    )


def duplicate_check_filter_logic(*, duplicate_check_field: str, duplicate_check_value: str) -> str:
    return f"[{duplicate_check_field}] = '{redcap_string_literal(duplicate_check_value)}'"


def patient_duplicate_check_value(
    *,
    first_name: str | None,
    last_name: str | None,
    birth_date: str | None,
) -> str | None:
    if not first_name or not last_name or not birth_date:
        return None

    birth_date_value = birth_date_ddmmyyyy(birth_date)
    if not birth_date_value:
        return None

    return f"{duplicate_check_name_part(last_name)}{duplicate_check_name_part(first_name)}{birth_date_value}"


def duplicate_check_name_part(value: str) -> str:
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^A-Za-z0-9]", "", without_accents).upper()


def birth_date_ddmmyyyy(value: str) -> str | None:
    normalized = value.strip().replace("/", "-")
    parts = normalized.split("-")
    if len(parts) != 3:
        return None

    if len(parts[0]) == 4:
        year, month, day = parts
    else:
        day, month, year = parts
    try:
        return f"{int(day):02d}{int(month):02d}{int(year):04d}"
    except ValueError:
        return None


def redcap_string_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def redcap_search_fields(*fields: str) -> list[str]:
    return unique_values(list(fields))


def unique_values(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique


def redcap_records(payload: dict[str, object]) -> list[dict[str, Any]]:
    if isinstance(payload.get("redcap"), dict):
        redcap_payload = payload["redcap"]
        records = redcap_payload.get("records")
        if isinstance(records, list):
            return validate_records(records)

    records = payload.get("records")
    if isinstance(records, list):
        return validate_records(records)

    record = payload.get("record")
    if isinstance(record, dict):
        return [dict(record)]

    return [flatten_record(payload)]


def validate_records(records: list[object]) -> list[dict[str, Any]]:
    if not records:
        raise TransferError("REDCap transfer needs at least one record")

    invalid_records = [index for index, record in enumerate(records) if not isinstance(record, dict)]
    if invalid_records:
        raise TransferError(f"REDCap records must be JSON objects: {invalid_records}")

    return [dict(record) for record in records]


def flatten_record(payload: dict[str, object]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    flatten_value(record, "", payload)
    return record


def flatten_value(record: dict[str, Any], prefix: str, value: object) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            next_prefix = f"{prefix}_{key}" if prefix else str(key)
            flatten_value(record, next_prefix, nested_value)
        return

    if isinstance(value, list):
        record[prefix] = json.dumps(value, ensure_ascii=False)
        return

    record[prefix] = value


def transfer_timeout() -> float:
    try:
        return float(os.getenv("REDCAP_TIMEOUT") or str(DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def redcap_verify_ssl() -> bool | str:
    value = os.getenv("REDCAP_VERIFY_SSL")
    if value is None or value == "":
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def transfer_record(record_id: str) -> None:
    del record_id
    raise TransferNotConfiguredError("DB-backed REDCap transfer is not implemented yet")


if __name__ == "__main__":
    main()
