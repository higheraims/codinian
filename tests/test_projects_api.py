"""The project workspace API's JSON encoding.

Issue front matter carries real `date` objects, deliberately, and JSON has no
date type. Every issue response goes out through this encoder, so a date that
reaches it unhandled is a 500 on the Issues tab.
"""

from __future__ import annotations

import datetime
import json

import pytest

from codinian.remote import projects_api


def test_a_date_is_encoded_as_iso_8601():
    encoded = projects_api._issue_json({"created": datetime.date(2026, 8, 16)})
    assert json.loads(encoded) == {"created": "2026-08-16"}


def test_a_datetime_keeps_its_time():
    encoded = projects_api._issue_json({"at": datetime.datetime(2026, 8, 16, 9, 30)})
    assert json.loads(encoded)["at"] == "2026-08-16T09:30:00"


def test_a_whole_issue_dict_survives_encoding():
    issue = {
        "id": "ISSUE-044", "num": 44,
        "frontmatter": {"id": "ISSUE-044", "status": "open",
                        "created": datetime.date(2026, 8, 24),
                        "updated": datetime.date(2026, 9, 11),
                        "related": []},
        "sections": [{"heading": "Summary", "body": "Create automated test suite"}],
    }
    assert json.loads(projects_api._issue_json(issue))["frontmatter"]["updated"] == "2026-09-11"


def test_something_genuinely_unencodable_still_raises():
    with pytest.raises(TypeError):
        projects_api._issue_json({"x": object()})


def test_an_error_response_carries_a_code_and_a_detail():
    response = projects_api._err("bad_path", 400, "path escapes project root")
    assert response.status == 400
    assert json.loads(response.text) == {"error": "bad_path",
                                         "detail": "path escapes project root"}
