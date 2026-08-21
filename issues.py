"""Issue file parsing, serialization and CRUD for the project workspace track
(ISSUE-019, M4).

An issue is a Markdown file with a YAML front matter block, documented in
`issues/README.md` and made machine-writable by
`docs/project-workspace-protocol.md`. This module owns that file format only:
it imports nothing from the rest of the app (`project.py`, `files.py`,
`vcs.py`, `session.py`, ...) so `remote/server.py` can call it without
dragging in git, the database or the GTK window, and so it can be tested on
its own against copies of real issue files.

Front matter is parsed with `yaml.safe_load` but never written with
`yaml.dump`: PyYAML's dumper reorders keys and re-quotes scalars by its own
rules, which would rewrite every field of every file the first time this
module touched it instead of diffing only the fields that changed. Writing
is therefore a small hand-rolled serializer that keeps the field order the
format documents (`id, title, status, type, area, created, updated,
related`, then whatever else the file had) and only quotes a scalar when
leaving it bare would change its meaning. `render_issue_text` undoing
`parse_issue_text` byte-for-byte on every file already in this format is the
acceptance test for the module (see docs/project-workspace-protocol.md).

One consequence of using `yaml.safe_load` as-is: an unquoted `created:
2026-08-16` line parses to a `datetime.date`, not a string, and this module
leaves it that way rather than stringifying it, because that is what lets
the writer tell "a real date, write it bare" apart from "a string that
happens to look like a date, quote it so it survives the round trip as a
string". `create_issue` and `update_issue` stamp `created`/`updated` with
`datetime.date.today()` for the same reason. A caller that hands these
dicts to `json.dumps` needs to call `.isoformat()` on them first.

Numbering: `next_id` takes the highest number found in either a filename or
an `id:` field, scanning every `.md` file's front matter (not just ones
already following the `NNN-slug.md` filename convention) so a stray or
mismatched file can never cause a number to be reused. `list_issues` and
`read_issue` are narrower on purpose and only look at `NNN-slug.md` files,
so `README.md` and `_TEMPLATE.md` never show up as issues.

Writes go through a temp file in the same directory, then `Path.replace`,
so a crash mid-write cannot leave a half-written issue file behind.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

import yaml

# Front matter key order the format documents. Any other key the file (or a
# caller) carries is written after these, in the order it was first seen.
CANONICAL_KEYS = ["id", "title", "status", "type", "area", "created", "updated", "related"]

# The issue tracker's own file (NNN-slug.md); README.md and _TEMPLATE.md live
# alongside issues but are not issues, and this is what tells them apart.
ISSUE_FILENAME_RE = re.compile(r"^(\d+)-.*\.md$")

# A body heading, per the format: "the body splits on lines matching ^## ".
_HEADING_RE = re.compile(r"^## (.*)$", re.MULTILINE)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_issue_text(text: str) -> dict:
    """Split an issue file into its front matter, preamble and sections.

    Raises ValueError if the file has no well-formed front matter block, so
    callers that read a whole directory (list_issues, next_id) can catch one
    bad file without losing the rest.
    """
    if not text.startswith("---\n"):
        raise ValueError("issue file must open with a '---' front matter block")

    close = text.find("\n---\n", 4)
    if close != -1:
        fm_text = text[4 : close + 1]
        body = text[close + 5 :]
    elif text.endswith("\n---"):
        # Front matter with nothing after it: no trailing body, no closing
        # blank line. Not seen in this repo's files, handled anyway since a
        # front-matter-only issue is a legal (if empty) file.
        fm_text = text[4:-4]
        body = ""
    else:
        raise ValueError("issue file has no closing '---' front matter delimiter")

    frontmatter = yaml.safe_load(fm_text)
    if not isinstance(frontmatter, dict):
        raise ValueError("front matter is not a YAML mapping")

    headings = list(_HEADING_RE.finditer(body))
    preamble = _strip_blank_lines(body[: headings[0].start()] if headings else body)

    sections = []
    for i, m in enumerate(headings):
        start = m.end() + 1  # past the heading line's own newline
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        sections.append({"heading": m.group(1), "body": _strip_blank_lines(body[start:end])})

    return {"frontmatter": frontmatter, "preamble": preamble, "sections": sections}


def _strip_blank_lines(s: str) -> str:
    """Drop leading and trailing whitespace-only lines, keep everything
    else untouched. This is "leading and trailing blank lines stripped" from
    the format doc, applied line-wise rather than as a plain strip() so
    internal indentation in a section body survives."""
    lines = s.split("\n")
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_issue_text(frontmatter: dict, preamble: str, sections: list[dict]) -> str:
    """Serialize front matter, preamble and sections back to the on-disk
    format. Inverse of parse_issue_text; round-tripping a file already in
    this format must produce identical bytes."""
    lines = ["---"]
    for key, value in _ordered_items(frontmatter):
        lines.append(f"{key}: {_format_value(value)}")
    lines.append("---")
    out = "\n".join(lines) + "\n\n"

    preamble = _strip_blank_lines(preamble or "")
    if preamble:
        out += preamble + "\n\n"

    out += "\n".join(f"## {s['heading']}\n\n{s['body']}\n" for s in sections)
    return out


def _ordered_items(fm: dict):
    """Canonical keys first in the documented order, then whatever else the
    dict carries, in the order it was first seen."""
    for key in CANONICAL_KEYS:
        if key in fm:
            yield key, fm[key]
    for key, value in fm.items():
        if key not in CANONICAL_KEYS:
            yield key, value


def _format_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_scalar_repr(v) for v in value) + "]"
    return _scalar_repr(value)


def _scalar_repr(value) -> str:
    # A real date/datetime is written bare as YYYY-MM-DD: PyYAML reads that
    # straight back into the same type, so nothing is lost by leaving it
    # unquoted, and quoting it would just make it a string on the next read.
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    return json.dumps(s, ensure_ascii=False) if _needs_quoting(s) else s


def _needs_quoting(s: str) -> bool:
    """Would writing s bare change what it means when read back? Quote in
    that case and only that case, so a file already in canonical form comes
    back byte-identical.

    A first pass tried to flag this from a fixed set of "special" YAML
    characters, but real plain scalars tolerate most of them mid-string
    (`One-line title [TEMPLATE FILE]` is bare in _TEMPLATE.md; only a
    leading `[` would be a problem). Asking the same parser that will read
    the file back is both simpler and correct: quote exactly when
    `yaml.safe_load` would not hand back this same string bare.
    """
    if s == "" or s[0].isspace() or s[-1].isspace() or "\n" in s:
        return True
    try:
        parsed = yaml.safe_load(s)
    except yaml.YAMLError:
        return True
    return not (isinstance(parsed, str) and parsed == s)


# --------------------------------------------------------------------------
# Directory-level operations
# --------------------------------------------------------------------------


def list_issues(root: str | Path, issues_dir: str) -> list[dict]:
    """IssueSummary dicts for every NNN-slug.md file in issues_dir, newest
    id first. A file that fails to parse is noted on stderr and skipped
    rather than raised, so one bad file cannot blank the whole list."""
    dir_path = Path(root) / issues_dir
    if not dir_path.is_dir():
        return []

    summaries = []
    for path in sorted(dir_path.glob("*.md")):
        if not ISSUE_FILENAME_RE.match(path.name):
            continue
        try:
            parsed = parse_issue_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
            print(f"issues: skipping {path}: {exc}", file=sys.stderr)
            continue
        issue = _issue_dict(parsed, issues_dir, path.name)
        summaries.append(_summarize(issue))

    summaries.sort(key=lambda s: s["num"] if s["num"] is not None else -1, reverse=True)
    return summaries


def _summarize(issue: dict) -> dict:
    body = ""
    for s in issue["sections"]:
        if s["heading"] == "Summary":
            body = s["body"]
            break
    excerpt = re.sub(r"\s+", " ", body).strip()[:200]
    return {
        "id": issue["id"],
        "num": issue["num"],
        "file": issue["file"],
        "frontmatter": issue["frontmatter"],
        "excerpt": excerpt,
    }


def read_issue(root: str | Path, issues_dir: str, issue_ref: str) -> dict | None:
    """Look up one issue by ISSUE-019, 019 or 19 (all resolve to the same
    file). Prefers a filename match; falls back to scanning id: fields so a
    file whose number and id disagree is still reachable by either."""
    dir_path = Path(root) / issues_dir
    m = re.search(r"(\d+)\s*$", str(issue_ref))
    if not m or not dir_path.is_dir():
        return None
    target = int(m.group(1))

    candidates = sorted(dir_path.glob("*.md"))
    for path in candidates:
        fm = ISSUE_FILENAME_RE.match(path.name)
        if fm and int(fm.group(1)) == target:
            try:
                parsed = parse_issue_text(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
                return None
            return _issue_dict(parsed, issues_dir, path.name)

    for path in candidates:
        if not ISSUE_FILENAME_RE.match(path.name):
            continue
        try:
            parsed = parse_issue_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
            continue
        if _id_num(parsed["frontmatter"].get("id")) == target:
            return _issue_dict(parsed, issues_dir, path.name)

    return None


def next_id(root: str | Path, issues_dir: str, prefix: str = "ISSUE") -> tuple[int, str]:
    """One more than the highest number found in either a filename or an
    id: field, across every .md file in issues_dir (not just ones already
    matching NNN-slug.md), so a gap or a stray file can never cause reuse."""
    dir_path = Path(root) / issues_dir
    highest = 0
    if dir_path.is_dir():
        for path in dir_path.glob("*.md"):
            fm = ISSUE_FILENAME_RE.match(path.name)
            if fm:
                highest = max(highest, int(fm.group(1)))
            try:
                parsed = parse_issue_text(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
                continue
            num = _id_num(parsed["frontmatter"].get("id"))
            if num is not None:
                highest = max(highest, num)
    n = highest + 1
    return n, f"{prefix}-{n:03d}"


def _id_num(id_value) -> int | None:
    if id_value is None:
        return None
    m = re.search(r"(\d+)\s*$", str(id_value))
    return int(m.group(1)) if m else None


def _issue_dict(parsed: dict, issues_dir: str, filename: str) -> dict:
    fm = parsed["frontmatter"]
    fm_match = ISSUE_FILENAME_RE.match(filename)
    num = _id_num(fm.get("id"))
    if num is None and fm_match:
        num = int(fm_match.group(1))
    return {
        "id": fm.get("id"),
        "num": num,
        "file": f"{str(issues_dir).rstrip('/')}/{filename}",
        "frontmatter": fm,
        "preamble": parsed["preamble"],
        "sections": parsed["sections"],
    }


# --------------------------------------------------------------------------
# Create / update
# --------------------------------------------------------------------------


def slugify(title: str) -> str:
    """Lowercased, non-alphanumerics collapsed to single hyphens, trimmed to
    60 characters. Purely cosmetic: the frontmatter id, not the filename, is
    the source of truth (issues/README.md), so this never needs to be
    reversible."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60].strip("-")


def create_issue(
    root: str | Path,
    issues_dir: str,
    frontmatter: dict,
    sections: list[dict],
    prefix: str = "ISSUE",
) -> dict:
    """Assign id/created/updated, derive the filename, and write the file.
    A client-supplied id is ignored, not honoured, per the contract: id
    assignment has to stay a single-writer operation or two concurrent
    creates could hand out the same number."""
    root = Path(root)
    dir_path = root / issues_dir
    dir_path.mkdir(parents=True, exist_ok=True)

    num, issue_id = next_id(root, issues_dir, prefix)
    today = datetime.date.today()

    fm = dict(frontmatter)
    fm.pop("id", None)
    fm["id"] = issue_id
    fm["created"] = today
    fm["updated"] = today

    filename = f"{num:03d}-{slugify(str(fm.get('title', '')))}.md"
    _write_atomic(dir_path / filename, render_issue_text(fm, "", sections))

    return _issue_dict({"frontmatter": fm, "preamble": "", "sections": sections}, issues_dir, filename)


def update_issue(
    root: str | Path, issues_dir: str, issue_ref: str, frontmatter: dict, sections: list[dict]
) -> dict | None:
    """Overwrite an issue's front matter, preamble-preserving, and sections.
    id and created survive from the file regardless of what the caller
    sends; updated becomes today. The filename is not recomputed from a new
    title: renaming is not this function's job, and a stale slug is
    harmless (the frontmatter id is the source of truth). Returns None if
    issue_ref does not resolve to a file."""
    root = Path(root)
    existing = read_issue(root, issues_dir, issue_ref)
    if existing is None:
        return None

    fm = dict(frontmatter)
    fm["id"] = existing["frontmatter"]["id"]
    fm["created"] = existing["frontmatter"]["created"]
    fm["updated"] = datetime.date.today()

    preamble = existing.get("preamble", "")
    filename = Path(existing["file"]).name
    _write_atomic(root / existing["file"], render_issue_text(fm, preamble, sections))

    return _issue_dict({"frontmatter": fm, "preamble": preamble, "sections": sections}, issues_dir, filename)


def _write_atomic(path: Path, text: str) -> None:
    # Same trick as config.py: write through a temp file in the same
    # directory and rename over the target, so a crash mid-write cannot
    # leave a truncated issue file. The temp name carries the real
    # filename, not a single shared name, since several issue files can be
    # written in close succession.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
