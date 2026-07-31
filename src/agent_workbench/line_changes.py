"""Shared deterministic changed-line accounting."""

import difflib


def count_changed_lines(old_content: str, new_content: str) -> int:
    """Count removed and added lines without normalizing line terminators."""

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    changed_lines = 0
    for tag, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    ).get_opcodes():
        if tag != "equal":
            changed_lines += old_end - old_start
            changed_lines += new_end - new_start
    return changed_lines
