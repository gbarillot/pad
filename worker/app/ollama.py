from __future__ import annotations

import json
import os

import httpx


class OllamaError(RuntimeError):
    """Raised when Ollama cannot produce a usable JSON response."""


def format_as_json(
    *,
    text: str,
    instructions: str,
    schema: str | None,
    model: str,
    ollama_base_url: str,
    images: list[str] | None = None,
) -> object:
    prompt = build_prompt(text=text, instructions=instructions, schema=schema, has_images=bool(images))
    url = f"{ollama_base_url.rstrip('/')}/api/generate"
    options: dict[str, object] = {"temperature": 0}
    if os.getenv("OLLAMA_NUM_CTX"):
        options["num_ctx"] = int(os.environ["OLLAMA_NUM_CTX"])

    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "think": False,
        "options": options,
    }
    if images:
        payload["images"] = images

    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=300,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or exc.response.reason_phrase
        raise OllamaError(
            f"Ollama request failed for model {model!r}: "
            f"HTTP {exc.response.status_code} from {url}: {detail}"
        ) from exc
    except httpx.RequestError as exc:
        raise OllamaError(f"Could not connect to Ollama at {url}: {exc}") from exc

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama returned non-JSON response: {response.text}") from exc

    raw = payload.get("response", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama did not return valid JSON: {raw}") from exc


def build_prompt(*, text: str, instructions: str, schema: str | None, has_images: bool = False) -> str:
    schema_block = (
        f"\nExpected JSON schema or example:\n{schema}\n"
        if schema
        else """
No schema was provided. Infer concise snake_case top-level keys from the document.
Never return an empty object unless the PDF text contains no readable data.
"""
    )
    image_instruction = (
        "Use the attached PDF page images as the primary source. Use the OCR text as a secondary hint for search and disambiguation.\n"
        if has_images
        else ""
    )
    return f"""You extract structured data from OCR and PDF text.

Return only valid JSON. Do not include Markdown, explanations, or comments.
Do not include reasoning, analysis, or <think> tags.
Return a single JSON object, not an array or string.
If a value is missing or unreadable, use null. Preserve dates, identifiers, totals,
names, addresses, line items, and document metadata when present.
{image_instruction}

Instructions:
{instructions}
{schema_block}
PDF text:
{text}
"""
