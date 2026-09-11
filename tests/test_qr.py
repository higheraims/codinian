"""QR matrices for the Remote Access dialog."""

from __future__ import annotations

import builtins

import pytest

import qr

URL = "http://127.0.0.1:8787/?token=abcdefghijklmnopqrstuvwxyz"


def test_the_encoder_is_present_on_this_machine():
    # Treated as optional at runtime: a machine without python3-qrcode gets a
    # dialog that says so rather than an import error at startup.
    assert qr.available() is True


def test_a_matrix_is_square_rows_of_booleans():
    matrix = qr.matrix_for(URL)
    assert len(matrix) == len(matrix[0])
    assert all(isinstance(cell, bool) for row in matrix for cell in row)


def test_the_quiet_zone_is_left_clear_on_every_side():
    # Scanners need the margin to find the finder patterns.
    matrix = qr.matrix_for(URL)
    border = qr.QUIET_ZONE
    for row in matrix[:border] + matrix[-border:]:
        assert not any(row)
    for row in matrix:
        assert not any(row[:border]) and not any(row[-border:])


def test_the_finder_pattern_is_dark_where_it_should_be():
    matrix = qr.matrix_for(URL)
    at = qr.QUIET_ZONE
    assert matrix[at][at] is True
    assert matrix[at + 1][at + 1] is False  # the white ring inside the finder


def test_longer_data_needs_a_bigger_matrix():
    small = qr.matrix_for("http://127.0.0.1:8787/")
    large = qr.matrix_for(URL + "&extra=" + "x" * 300)
    assert len(large) > len(small)


def test_empty_text_has_nothing_to_encode():
    assert qr.matrix_for("") is None


def test_data_too_long_to_encode_returns_none_rather_than_raising():
    assert qr.matrix_for("x" * 10_000) is None


def test_a_machine_without_the_encoder_gets_none(monkeypatch):
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "qrcode":
            raise ImportError("no qrcode here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert qr.available() is False
    assert qr.matrix_for(URL) is None
