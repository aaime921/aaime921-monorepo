"""Tests for trainiq.export.fmt (issue #71) — pure string formatting."""

from __future__ import annotations

from trainiq.export.fmt import fmt, fmt_pace, md_table


def test_fmt_none_is_dash():
    assert fmt(None) == "-"


def test_fmt_float_uses_fixed_precision():
    assert fmt(3.14159, 2) == "3.14"
    assert fmt(3.0, 1) == "3.0"


def test_fmt_int_has_no_decimal_point():
    assert fmt(145) == "145"


def test_fmt_str_passes_through():
    assert fmt("male") == "male"


def test_fmt_pace_none_is_dash():
    assert fmt_pace(None) == "-"


def test_fmt_pace_nonpositive_is_dash():
    assert fmt_pace(0) == "-"
    assert fmt_pace(-5) == "-"


def test_fmt_pace_formats_minutes_and_seconds():
    assert fmt_pace(330) == "5:30 /km"


def test_fmt_pace_pads_seconds_under_ten():
    assert fmt_pace(305) == "5:05 /km"


def test_md_table_layout():
    table = md_table(["A", "B"], [["1", "2"], ["3", "4"]])
    lines = table.split("\n")
    assert lines[0] == "| A | B |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| 1 | 2 |"
    assert lines[3] == "| 3 | 4 |"


def test_md_table_empty_rows():
    table = md_table(["A", "B"], [])
    assert table == "| A | B |\n| --- | --- |"
