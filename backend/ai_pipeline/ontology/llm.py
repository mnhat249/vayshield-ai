"""Fake LLM cho test/sandbox (không gọi mạng)."""

from __future__ import annotations

from app.core.schemas.common import ONTOLOGY_AXIS_NAMES, OntologyAxis
from app.core.schemas.shield import DetectedAxis


class FakeLlmIntentClassifier:
    """Trả kết quả cố định theo chuỗi con; ghi lại số lần gọi."""

    def __init__(self, mapping: dict[str, OntologyAxis] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def classify(self, text: str) -> list[DetectedAxis]:
        self.calls.append(text)
        return [
            DetectedAxis(axis=axis, name=ONTOLOGY_AXIS_NAMES[axis], confidence=0.8)
            for needle, axis in self.mapping.items()
            if needle in text
        ]
