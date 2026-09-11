"""Reading plan windows out of the CLI's own `/usage` output.

The input is human-facing text with no documented format, so the parser is
tolerant by design: these tests pin what it reads, and equally that an
unfamiliar line costs one window rather than the whole reading.
"""

from __future__ import annotations

import plan_usage

USAGE_OUTPUT = """Current session: 24% used · resets Aug 21, 10:39am (America/New_York)
Current week (all models): 6% used · resets Aug 26, 8am (America/New_York)
Current week (Opus): 2% used · resets Aug 26, 8am (America/New_York)

Approximate, based on sessions run on this machine.
"""


def windows_by_key(text):
    usage = plan_usage.parse(text)
    return {w.key: w for w in usage.windows}


def test_every_window_is_read_from_a_full_report():
    parsed = windows_by_key(USAGE_OUTPUT)
    assert set(parsed) == {"session", "week", "week:opus"}
    assert parsed["session"].percent == 24
    assert parsed["week"].percent == 6
    assert parsed["week:opus"].percent == 2


def test_labels_are_ready_for_display():
    parsed = windows_by_key(USAGE_OUTPUT)
    assert parsed["session"].label == "Session"
    assert parsed["week"].label == "Week"
    assert parsed["week:opus"].label == "Week (Opus)"


def test_the_reset_time_keeps_the_cli_wording_and_timezone():
    assert windows_by_key(USAGE_OUTPUT)["session"].resets == "Aug 21, 10:39am (America/New_York)"


def test_the_approximation_note_is_carried_when_the_output_says_so():
    assert plan_usage.parse(USAGE_OUTPUT).note == plan_usage.NOTE
    without = USAGE_OUTPUT.replace("Approximate, based on", "Computed from")
    assert plan_usage.parse(without).note == ""


def test_a_near_empty_window_reads_as_its_floor_rather_than_being_dropped():
    parsed = windows_by_key("Current session: <1% used · resets soon")
    assert parsed["session"].percent == 1


def test_a_window_with_no_reset_text_still_reports_its_percentage():
    parsed = windows_by_key("Current session: 40% used")
    assert parsed["session"].percent == 40
    assert parsed["session"].resets == ""


def test_an_unfamiliar_separator_costs_the_reset_time_and_nothing_else():
    parsed = windows_by_key("Current session: 40% used | resets Aug 21")
    assert parsed["session"].percent == 40
    assert parsed["session"].resets == "Aug 21"


def test_a_renamed_window_costs_that_window_only():
    text = ("Current session: 10% used\n"
            "Rolling fortnight (all models): 20% used\n"
            "Current week (Sonnet): 30% used\n")
    parsed = windows_by_key(text)
    assert set(parsed) == {"session", "week:sonnet"}


def test_text_that_is_not_a_usage_report_returns_none():
    # parse() is run speculatively over passing transcript text, so this is
    # the answer for almost everything.
    for text in ["", "Hello, how can I help?", None,
                 "The build used 40% of the disk", "% used"]:
        assert plan_usage.parse(text) is None


def test_captured_at_is_stamped():
    assert plan_usage.parse(USAGE_OUTPUT).captured_at > 0


def test_to_dict_is_json_ready():
    as_dict = plan_usage.parse(USAGE_OUTPUT).to_dict()
    assert as_dict["windows"][0] == {"key": "session", "label": "Session",
                                     "percent": 24,
                                     "resets": "Aug 21, 10:39am (America/New_York)"}
    assert set(as_dict) == {"windows", "captured_at", "note"}


def test_windows_keep_the_order_the_report_listed_them_in():
    keys = [w.key for w in plan_usage.parse(USAGE_OUTPUT).windows]
    assert keys == ["session", "week", "week:opus"]


def test_a_model_name_is_lowercased_in_the_key_but_not_in_the_label():
    parsed = plan_usage.parse("Current week (Opus 5): 3% used").windows[0]
    assert parsed.key == "week:opus 5"
    assert parsed.label == "Week (Opus 5)"
