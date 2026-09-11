"""Image tool results: decoding, the bounded live store, and the reference that
replaces base64 on the wire.

The measured problem behind this module was one session replaying 2.6 MB, of
which 2.3 MB was base64 sent four times over. So the test that matters most is
that `dereference_content` leaves no base64 in what it returns.
"""

from __future__ import annotations

import base64

import pytest

from codinian import images

PNG = b"\x89PNG\r\n\x1a\n" + b"fake pixel data" * 4
PNG_B64 = base64.b64encode(PNG).decode()


def image_block(data=PNG_B64, media_type="image/png", source_type="base64"):
    return {"type": "image", "source": {"type": source_type, "media_type": media_type,
                                        "data": data}}


# ------------------------------------------------------------- decoding

def test_a_base64_source_decodes_to_its_bytes():
    assert images.decode(image_block()["source"]) == ("image/png", PNG)


@pytest.mark.parametrize("source", [
    {"type": "url", "url": "https://example.invalid/x.png"},
    {"type": "base64", "media_type": "image/png", "data": "not valid base64!!"},
    {"type": "base64", "media_type": "image/png"},
    {"type": "base64", "data": PNG_B64},
    {"type": "base64", "media_type": 42, "data": PNG_B64},
    {},
])
def test_anything_that_is_not_a_decodable_base64_source_returns_none(source):
    assert images.decode(source) is None


def test_is_image_block_only_matches_an_image_block():
    assert images.is_image_block(image_block()) is True
    assert images.is_image_block({"type": "text", "text": "hi"}) is False
    assert images.is_image_block("not a block") is False


# ---------------------------------------------------------- the store

def test_an_image_comes_back_under_its_key():
    store = images.ImageStore()
    store.put("s1", "toolu_01", 0, "image/png", PNG)
    assert store.get("s1", "toolu_01", 0) == ("image/png", PNG)


def test_a_key_that_was_never_stored_returns_none():
    store = images.ImageStore()
    store.put("s1", "toolu_01", 0, "image/png", PNG)
    assert store.get("s1", "toolu_01", 1) is None
    assert store.get("s2", "toolu_01", 0) is None


def test_total_bytes_tracks_what_is_held():
    store = images.ImageStore()
    store.put("s1", "a", 0, "image/png", b"x" * 100)
    store.put("s1", "b", 0, "image/png", b"x" * 50)
    assert store.total_bytes() == 150


def test_replacing_a_key_does_not_double_count():
    store = images.ImageStore()
    store.put("s1", "a", 0, "image/png", b"x" * 100)
    store.put("s1", "a", 0, "image/png", b"x" * 10)
    assert store.total_bytes() == 10


def test_the_oldest_image_is_evicted_first():
    store = images.ImageStore(max_bytes=250)
    for i, name in enumerate("abc"):
        store.put("s1", name, i, "image/png", b"x" * 100)

    assert store.get("s1", "a", 0) is None
    assert store.get("s1", "b", 1) is not None
    assert store.get("s1", "c", 2) is not None
    assert store.total_bytes() == 200


def test_reading_an_image_does_not_make_it_newer():
    store = images.ImageStore(max_bytes=250)
    store.put("s1", "a", 0, "image/png", b"x" * 100)
    store.put("s1", "b", 0, "image/png", b"x" * 100)
    store.get("s1", "a", 0)
    store.put("s1", "c", 0, "image/png", b"x" * 100)
    # Insertion order is eviction order; a read does not reorder anything.
    assert store.get("s1", "a", 0) is None


def test_an_image_larger_than_the_whole_cap_is_still_held():
    # It would otherwise evict itself on the way in, and holding the picture
    # that just arrived is the one thing the store exists to do.
    store = images.ImageStore(max_bytes=100)
    store.put("s1", "huge", 0, "image/png", b"x" * 5000)
    assert store.get("s1", "huge", 0) is not None


def test_forgetting_a_session_frees_only_its_own_images():
    store = images.ImageStore()
    store.put("s1", "a", 0, "image/png", b"x" * 100)
    store.put("s2", "b", 0, "image/png", b"x" * 40)

    store.forget_session("s1")
    assert store.get("s1", "a", 0) is None
    assert store.get("s2", "b", 0) is not None
    assert store.total_bytes() == 40


# ------------------------------------------------------------ references

def test_a_reference_points_at_the_route_that_serves_the_bytes():
    ref = images.reference("/api/sessions/s1", "toolu_01", 2, "image/png", 1234)
    assert ref == {"type": "codinian_ref", "media_type": "image/png",
                   "bytes": 1234, "path": "/api/sessions/s1/image/toolu_01/2"}


def test_a_reference_carries_a_query_when_the_caller_supplies_one():
    ref = images.reference("/api/history/x", "toolu_01", 0, "image/png", 1,
                           query="?agent=abc")
    assert ref["path"] == "/api/history/x/image/toolu_01/0?agent=abc"


def test_base64_is_replaced_and_the_bytes_are_not_in_the_result():
    content = [{"type": "text", "text": "here it is"}, image_block()]
    out = images.dereference_content(content, "/api/sessions/s1", "toolu_01")

    assert out[0] == {"type": "text", "text": "here it is"}
    assert out[1]["source"]["type"] == "codinian_ref"
    assert out[1]["source"]["bytes"] == len(PNG)
    assert PNG_B64 not in repr(out)


def test_keep_puts_the_decoded_bytes_in_the_live_store():
    store_before = images.store.total_bytes()
    images.dereference_content([image_block()], "/api/sessions/s1", "toolu_keep",
                               keep=True, session_id="s1")
    assert images.store.get("s1", "toolu_keep", 0) == ("image/png", PNG)
    images.store.forget_session("s1")
    assert images.store.total_bytes() == store_before


def test_without_keep_nothing_is_stored():
    images.dereference_content([image_block()], "/api/history/x", "toolu_nokeep",
                               session_id="s1")
    assert images.store.get("s1", "toolu_nokeep", 0) is None


def test_content_with_no_image_is_returned_unchanged_and_uncopied():
    content = [{"type": "text", "text": "no pictures here"}]
    assert images.dereference_content(content, "/api", "toolu_01") is content


def test_a_non_list_content_is_returned_unchanged():
    assert images.dereference_content("plain text", "/api", "toolu_01") == "plain text"


@pytest.mark.parametrize("tool_use_id", ["", "has/slash", "has space", "x" * 129, None])
def test_an_unreferenceable_tool_use_id_leaves_the_image_inline(tool_use_id):
    # A picture that cannot be addressed in a URL is better left inline than
    # turned into a link to nothing.
    content = [image_block()]
    assert images.dereference_content(content, "/api", tool_use_id) is content


def test_a_non_base64_image_source_is_left_alone():
    content = [image_block(source_type="url")]
    assert images.dereference_content(content, "/api", "toolu_01") is content
