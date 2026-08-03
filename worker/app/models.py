from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RawExtraction:
    text: str
    model_result: object
