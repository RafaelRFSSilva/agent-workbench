"""Tests for shared deterministic changed-line accounting."""

import pytest

from agent_workbench.line_changes import count_changed_lines


@pytest.mark.parametrize(
    ("old_content", "new_content", "expected"),
    [
        ("value\r\n", "value\n", 2),
        ("value\n", "value\r\n", 2),
        ("one\r\ntwo\r\nthree\r\n", "one\ntwo\nthree\n", 6),
        ("one\ntwo\nthree\n", "one\r\ntwo\r\nthree\r\n", 6),
        ("value", "value\n", 2),
        ("value\n", "value", 2),
        ("value\r\n", "value\r\n", 0),
        ("value\n", "value\n", 0),
        ("same\r\nold\nkeep\n", "same\nnew\r\nkeep\n", 4),
        ("one\nthree\n", "one\ntwo\nthree\n", 1),
        ("one\ntwo\nthree\n", "one\nthree\n", 1),
    ],
)
def test_count_changed_lines_preserves_terminators_and_edit_semantics(
    old_content: str,
    new_content: str,
    expected: int,
) -> None:
    """Count terminator changes while preserving replacement and edit semantics."""

    assert count_changed_lines(old_content, new_content) == expected


@pytest.mark.parametrize(
    ("old_ending", "new_ending"),
    [("\r\n", "\n"), ("\n", "\r\n")],
)
def test_count_changed_lines_counts_501_line_ending_replacements(
    old_ending: str,
    new_ending: str,
) -> None:
    """Count every removed and added line in a broad terminator-only change."""

    old_content = "".join(f"line {index}{old_ending}" for index in range(501))
    new_content = "".join(f"line {index}{new_ending}" for index in range(501))

    assert count_changed_lines(old_content, new_content) == 1_002
