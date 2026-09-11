"""The issue file format: parsing, serialization, numbering and CRUD.

The headline test is `test_every_repo_issue_round_trips_byte_for_byte`. The
module docstring in issues.py names it as the acceptance test for the format,
and it is the one that catches a hand edit or an agent edit that leaves a file
Codinian would rewrite the next time anyone touches it.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
import yaml

from codinian import issues
from conftest import REPO_ROOT


def round_trip(text: str) -> str:
    parsed = issues.parse_issue_text(text)
    return issues.render_issue_text(parsed["frontmatter"], parsed["preamble"],
                                    parsed["sections"])


# --------------------------------------------------------------- parsing

def test_parses_frontmatter_preamble_and_sections():
    text = (
        "---\n"
        "id: ISSUE-001\n"
        "title: A title\n"
        "related: []\n"
        "---\n"
        "\n"
        "Some preamble.\n"
        "\n"
        "## Summary\n"
        "\n"
        "First line.\n"
        "Second line.\n"
        "\n"
        "## Resolution\n"
        "\n"
        "Done.\n"
    )
    parsed = issues.parse_issue_text(text)

    assert parsed["frontmatter"] == {"id": "ISSUE-001", "title": "A title", "related": []}
    assert parsed["preamble"] == "Some preamble."
    assert [s["heading"] for s in parsed["sections"]] == ["Summary", "Resolution"]
    assert parsed["sections"][0]["body"] == "First line.\nSecond line."


def test_unquoted_date_parses_to_a_date_object():
    # Deliberate: it is what lets the writer tell a real date from a string
    # that looks like one. json.dumps on this dict needs .isoformat() first.
    parsed = issues.parse_issue_text("---\ncreated: 2026-08-16\n---\n\n")
    assert parsed["frontmatter"]["created"] == datetime.date(2026, 8, 16)


def test_section_body_keeps_internal_blank_lines_and_indentation():
    text = "---\nid: X\n---\n\n## Notes\n\nfirst\n\n  indented\n\nlast\n"
    body = issues.parse_issue_text(text)["sections"][0]["body"]
    assert body == "first\n\n  indented\n\nlast"


@pytest.mark.parametrize("text, reason", [
    ("no front matter at all\n", "missing opening delimiter"),
    ("---\nid: X\nstill going\n", "missing closing delimiter"),
    ("---\njust a string\n---\n\n", "front matter is not a mapping"),
])
def test_malformed_front_matter_raises_value_error(text, reason):
    with pytest.raises(ValueError):
        issues.parse_issue_text(text)


def test_front_matter_only_file_parses_with_an_empty_body():
    parsed = issues.parse_issue_text("---\nid: ISSUE-001\n---")
    assert parsed["frontmatter"] == {"id": "ISSUE-001"}
    assert parsed["sections"] == []


def test_unparseable_yaml_raises_rather_than_returning_a_partial_dict():
    # The title contains ": ", which is not a valid bare scalar. This is the
    # real failure that makes an issue vanish from the GUI list.
    with pytest.raises(yaml.YAMLError):
        issues.parse_issue_text("---\ntitle: Analysis layer: scope fixed\n---\n\n")


# ------------------------------------------------------------- rendering

def test_canonical_key_order_is_restored_from_a_shuffled_dict():
    fm = {"related": [], "title": "T", "id": "ISSUE-007", "status": "open"}
    rendered = issues.render_issue_text(fm, "", [])
    keys = [line.split(":")[0] for line in rendered.splitlines()[1:-2]]
    assert keys == ["id", "title", "status", "related"]


def test_unknown_keys_are_written_after_the_canonical_ones_in_first_seen_order():
    fm = {"zebra": 1, "id": "ISSUE-001", "apple": 2}
    rendered = issues.render_issue_text(fm, "", [])
    assert rendered.startswith("---\nid: ISSUE-001\nzebra: 1\napple: 2\n---\n")


@pytest.mark.parametrize("value, expected", [
    ("plain title", "plain title"),
    ("One-line title [TEMPLATE FILE]", "One-line title [TEMPLATE FILE]"),
    ("Analysis layer: scope fixed", '"Analysis layer: scope fixed"'),
    ("[bracketed]", '"[bracketed]"'),
    ("  leading space", '"  leading space"'),
    ("trailing space ", '"trailing space "'),
    ("", '""'),
    ("yes", '"yes"'),          # bare, this reads back as a bool
    ("2026-08-16", '"2026-08-16"'),  # bare, this reads back as a date
    ("123", '"123"'),          # bare, this reads back as an int
])
def test_a_scalar_is_quoted_exactly_when_leaving_it_bare_would_change_it(value, expected):
    assert issues._scalar_repr(value) == expected
    # And the stated rule holds: what comes back out is the same string.
    assert yaml.safe_load(f"k: {expected}")["k"] == value


def test_dates_bools_and_none_are_written_bare():
    assert issues._scalar_repr(datetime.date(2026, 8, 16)) == "2026-08-16"
    assert issues._scalar_repr(True) == "true"
    assert issues._scalar_repr(None) == "null"
    assert issues._scalar_repr(7) == "7"


def test_a_list_is_written_inline():
    assert issues._format_value(["ISSUE-001", "ISSUE-002"]) == "[ISSUE-001, ISSUE-002]"
    assert issues._format_value([]) == "[]"


# ------------------------------------------------------------ round trip

def test_round_trip_is_byte_identical_for_a_canonical_file():
    text = (
        "---\n"
        "id: ISSUE-044\n"
        "title: Automated test suite\n"
        "status: open\n"
        "created: 2026-08-24\n"
        "related: []\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "One paragraph.\n"
        "\n"
        "And another.\n"
        "\n"
        "## Resolution\n"
        "\n"
        "Text.\n"
    )
    assert round_trip(text) == text


def test_round_trip_normalizes_a_trailing_blank_line_away():
    # A file with a stray newline at the end is legal input but is not
    # canonical: the next write drops it. Worth pinning, because it is the
    # difference between a one-byte edit that survives and one that does not.
    canonical = "---\nid: ISSUE-001\n---\n\n## Summary\n\nText.\n"
    assert round_trip(canonical + "\n") == canonical


def test_normalization_is_idempotent_for_non_canonical_input():
    messy = "---\ntitle: T\nid: ISSUE-001\n---\n\n\n## Summary\n\n\nText.\n\n\n"
    once = round_trip(messy)
    assert round_trip(once) == once


def test_every_repo_issue_round_trips_byte_for_byte():
    """The acceptance test issues.py names in its own docstring.

    A failure here means some file in issues/ is not in the format Codinian
    writes, so the next edit through the GUI will rewrite lines nobody touched.
    """
    offenders = []
    for path in sorted((REPO_ROOT / "issues").glob("*.md")):
        if not issues.ISSUE_FILENAME_RE.match(path.name):
            continue  # README.md and _TEMPLATE.md are not issues
        text = path.read_text(encoding="utf-8")
        if round_trip(text) != text:
            offenders.append(path.name)
    assert offenders == []


def test_every_repo_issue_has_parseable_front_matter():
    """A file that fails to parse is skipped by list_issues with one line on
    stderr, so in the GUI the issue is simply absent."""
    for path in sorted((REPO_ROOT / "issues").glob("[0-9]*.md")):
        issues.parse_issue_text(path.read_text(encoding="utf-8"))


# -------------------------------------------------------- directory level

def test_list_issues_is_newest_first_and_ignores_non_issue_files(project_dir, write_issue):
    write_issue("001-first.md", "id: ISSUE-001\nstatus: done")
    write_issue("010-tenth.md", "id: ISSUE-010\nstatus: open")
    write_issue("002-second.md", "id: ISSUE-002\nstatus: open")
    (project_dir / "issues" / "README.md").write_text("# Issues\n")
    (project_dir / "issues" / "_TEMPLATE.md").write_text("---\nid: X\n---\n\n")

    listed = issues.list_issues(project_dir, "issues")
    assert [i["num"] for i in listed] == [10, 2, 1]
    assert all(i["file"].startswith("issues/") for i in listed)


def test_list_issues_skips_a_bad_file_and_keeps_the_rest(project_dir, write_issue, capsys):
    write_issue("001-good.md", "id: ISSUE-001")
    write_issue("002-bad.md", "title: Layer: broken")

    listed = issues.list_issues(project_dir, "issues")
    assert [i["num"] for i in listed] == [1]
    assert "002-bad.md" in capsys.readouterr().err


def test_list_issues_returns_empty_for_a_missing_directory(project_dir):
    assert issues.list_issues(project_dir, "nowhere") == []


def test_summary_excerpt_is_flattened_and_capped(project_dir, write_issue):
    body = "\n## Summary\n\n" + ("word " * 100) + "\n"
    write_issue("001-long.md", "id: ISSUE-001", body)
    excerpt = issues.list_issues(project_dir, "issues")[0]["excerpt"]
    assert len(excerpt) == 200
    assert "\n" not in excerpt


@pytest.mark.parametrize("ref", ["ISSUE-019", "019", "19", 19])
def test_read_issue_accepts_every_spelling_of_a_reference(project_dir, write_issue, ref):
    write_issue("019-thing.md", "id: ISSUE-019")
    assert issues.read_issue(project_dir, "issues", ref)["num"] == 19


def test_read_issue_falls_back_to_the_id_field_when_the_filename_disagrees(
        project_dir, write_issue):
    write_issue("777-misnamed.md", "id: ISSUE-019")
    found = issues.read_issue(project_dir, "issues", "19")
    assert found is not None
    assert found["file"] == "issues/777-misnamed.md"


def test_read_issue_returns_none_for_an_unknown_reference(project_dir, write_issue):
    write_issue("001-thing.md", "id: ISSUE-001")
    assert issues.read_issue(project_dir, "issues", "404") is None
    assert issues.read_issue(project_dir, "issues", "no digits") is None


def test_next_id_is_one_past_the_highest_number_anywhere(project_dir, write_issue):
    write_issue("001-first.md", "id: ISSUE-001")
    write_issue("002-second.md", "id: ISSUE-002")
    assert issues.next_id(project_dir, "issues") == (3, "ISSUE-003")


def test_next_id_counts_a_stray_file_so_a_number_is_never_reused(project_dir, write_issue):
    write_issue("001-first.md", "id: ISSUE-001")
    # Not NNN-slug.md, so list_issues never shows it. It still holds ISSUE-050,
    # and handing 050 out again would break the promise that an id resolves
    # forever.
    write_issue("draft.md", "id: ISSUE-050")
    assert issues.next_id(project_dir, "issues") == (51, "ISSUE-051")


def test_next_id_starts_at_one_in_an_empty_or_missing_directory(project_dir):
    assert issues.next_id(project_dir, "issues") == (1, "ISSUE-001")
    assert issues.next_id(project_dir, "docs/issues", prefix="BUG") == (1, "BUG-001")


def test_next_id_ignores_a_file_it_cannot_parse(project_dir, write_issue):
    write_issue("001-first.md", "id: ISSUE-001")
    (project_dir / "issues" / "broken.md").write_text("no front matter\n")
    assert issues.next_id(project_dir, "issues")[0] == 2


# ------------------------------------------------------------ create/update

def test_slugify_lowercases_collapses_and_trims():
    assert issues.slugify("Put the project under version control") == \
        "put-the-project-under-version-control"
    assert issues.slugify("  Spaces & 'punctuation'!  ") == "spaces-punctuation"
    assert issues.slugify("x" * 100) == "x" * 60
    assert issues.slugify("!!!") == ""


def test_create_issue_assigns_the_id_and_both_dates(project_dir, write_issue):
    write_issue("001-first.md", "id: ISSUE-001")
    created = issues.create_issue(
        project_dir, "issues",
        {"title": "A new thing", "status": "open"},
        [{"heading": "Summary", "body": "Why."}],
    )

    today = datetime.date.today()
    assert created["id"] == "ISSUE-002"
    assert created["file"] == "issues/002-a-new-thing.md"
    assert created["frontmatter"]["created"] == today
    assert created["frontmatter"]["updated"] == today
    on_disk = (project_dir / "issues" / "002-a-new-thing.md").read_text()
    assert f"created: {today.isoformat()}\n" in on_disk
    assert round_trip(on_disk) == on_disk


def test_create_issue_ignores_a_client_supplied_id(project_dir, write_issue):
    write_issue("001-first.md", "id: ISSUE-001")
    created = issues.create_issue(project_dir, "issues",
                                  {"id": "ISSUE-999", "title": "Mine"}, [])
    # Id assignment is single-writer, or two concurrent creates hand out the
    # same number.
    assert created["id"] == "ISSUE-002"


def test_create_issue_makes_the_directory_when_it_is_missing(project_dir):
    issues.create_issue(project_dir, "docs/issues", {"title": "First"}, [])
    assert (project_dir / "docs" / "issues" / "001-first.md").is_file()


def test_update_issue_keeps_id_and_created_but_moves_updated(project_dir, write_issue):
    write_issue("005-thing.md",
                "id: ISSUE-005\ntitle: Thing\nstatus: open\ncreated: 2026-01-02\n"
                "updated: 2026-01-02")

    updated = issues.update_issue(
        project_dir, "issues", "5",
        {"id": "ISSUE-999", "title": "Thing", "status": "done",
         "created": datetime.date(2030, 1, 1)},
        [{"heading": "Resolution", "body": "Fixed."}],
    )

    assert updated["frontmatter"]["id"] == "ISSUE-005"
    assert updated["frontmatter"]["created"] == datetime.date(2026, 1, 2)
    assert updated["frontmatter"]["updated"] == datetime.date.today()
    assert updated["frontmatter"]["status"] == "done"


def test_update_issue_preserves_the_preamble_and_the_filename(project_dir, write_issue):
    write_issue("005-old-slug.md", "id: ISSUE-005\ntitle: Old title",
                "\nKeep this preamble.\n\n## Summary\n\nText.\n")

    updated = issues.update_issue(project_dir, "issues", "5",
                                  {"title": "A completely different title"},
                                  [{"heading": "Summary", "body": "Text."}])

    assert updated["file"] == "issues/005-old-slug.md"
    assert updated["preamble"] == "Keep this preamble."
    assert "Keep this preamble." in (project_dir / "issues" / "005-old-slug.md").read_text()


def test_update_issue_returns_none_for_an_unknown_reference(project_dir):
    assert issues.update_issue(project_dir, "issues", "404", {}, []) is None


def test_a_written_file_leaves_no_temp_file_behind(project_dir):
    issues.create_issue(project_dir, "issues", {"title": "Thing"}, [])
    assert [p.name for p in (project_dir / "issues").iterdir()] == ["001-thing.md"]
