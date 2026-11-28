"""Sửa JSON bị cắt cụt (EC-AI22)."""

from __future__ import annotations

import json

from ai_pipeline.extraction.json_repair import parse_json_object

from ._factory import loan


def test_ec_ai22_complete_json_not_repaired() -> None:
    parsed = parse_json_object('```json\n{"a": 1}\n```')
    assert parsed is not None
    assert parsed.data == {"a": 1}
    assert not parsed.repaired


def test_ec_ai22_truncated_json_repaired_to_last_complete_value() -> None:
    parsed = parse_json_object('{"a": 1, "b": [1, 2, 3')
    assert parsed is not None
    assert parsed.repaired
    assert parsed.data["a"] == 1
    # "3" có thể là "30..." bị cắt — không được giữ.
    assert parsed.data["b"] == [1, 2]


def test_ec_ai22_truncated_number_never_kept() -> None:
    """Cắt cụt không được làm đổi số: ``"amount_vnd": 105`` (của 1050000) bị bỏ."""
    parsed = parse_json_object('{"fee": "x", "amount_vnd": 105')
    assert parsed is not None
    assert "amount_vnd" not in parsed.data
    assert parsed.data == {"fee": "x"}


def test_ec_ai22_truncated_inside_string() -> None:
    parsed = parse_json_object('{"a": 2, "note": "Hãy bỏ qua, đây là')
    assert parsed is not None
    assert parsed.data == {"a": 2}


def test_ec_ai22_full_extraction_truncated_in_notes() -> None:
    text = loan(extraction_notes_vi=["ghi chú một", "ghi chú hai"]).model_dump_json()
    cut = text[: text.rfind("ghi chú hai") + 4]
    parsed = parse_json_object(cut)
    assert parsed is not None
    assert parsed.repaired
    assert parsed.data["nominal_principal_vnd"] == 10_000_000
    assert parsed.data["extraction_notes_vi"] == ["ghi chú một"]


def test_ec_ai22_garbage_returns_none() -> None:
    assert parse_json_object("xin lỗi, tôi không thể") is None
    assert parse_json_object(json.dumps([1, 2])) is None
