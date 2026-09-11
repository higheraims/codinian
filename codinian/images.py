"""Image tool results, kept out of the transcript event stream (ISSUE-035).

A tool that returns a picture sends `{type: "image", source: {type: "base64",
media_type, data}}`, where `data` is the whole image. Passing that block through
verbatim put the bytes in four places at once: the event object, the in-memory
event list for the life of the session, every WebSocket frame, and the whole
backlog again on every reconnect. One measured session -- fifteen screenshots
read during a UI review -- replayed 2.6 MB, of which 2.3 MB was base64.

So the event carries a reference instead, and the bytes are fetched once over
HTTP. There are two places to fetch them from, and both are needed:

* This store, filled as a live turn produces them. It is bounded, and it is not
  the durable copy -- it exists to cover the window before the CLI has flushed
  its own transcript to disk.
* The CLI's JSONL under ~/.claude/projects, which is where replay reads from
  anyway and which holds the same base64 keyed the same way.

The route tries the store and falls back to the file, so an image survives
eviction, and a conversation with no live session behind it still renders.
"""

from __future__ import annotations

import base64
import re
import threading

# How much decoded image data the live store holds before the oldest is
# dropped. A screenshot runs about 150 KB, so this is roughly two hundred of
# them: far more than the window before the CLI flushes, and small enough that
# a session which spends an afternoon reading pictures does not grow without
# limit. Anything evicted is still on disk.
MAX_BYTES = 32 * 1024 * 1024

# What a tool_use_id has to look like to go in a URL. The ids come from the
# model's own tool calls (`toolu_01...`), so this is a shape check on data from
# outside rather than a guess at what is safe.
SAFE_TOOL_USE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def is_image_block(block) -> bool:
    return isinstance(block, dict) and block.get("type") == "image"


def _source(block) -> dict:
    source = block.get("source")
    return source if isinstance(source, dict) else {}


def decode(source: dict) -> tuple[str, bytes] | None:
    """`(media_type, bytes)` for a base64 image source, or None if it is not
    one or the base64 does not decode."""
    if source.get("type") != "base64":
        return None
    data, media_type = source.get("data"), source.get("media_type")
    if not isinstance(data, str) or not isinstance(media_type, str):
        return None
    try:
        return media_type, base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        return None


class ImageStore:
    """Decoded image bytes keyed by (session_id, tool_use_id, block index).

    Bounded by total size, oldest first. Insertion order is eviction order,
    which for a transcript is also age order, so a plain dict is the whole data
    structure. Reads do not reorder anything: an old image being looked at is
    not a reason to evict a newer one, and the durable copy is on disk either
    way.
    """

    def __init__(self, max_bytes: int = MAX_BYTES):
        self._max_bytes = max_bytes
        self._entries: dict[tuple[str, str, int], tuple[str, bytes]] = {}
        self._size = 0
        self._lock = threading.Lock()

    def put(self, session_id: str, tool_use_id: str, index: int,
            media_type: str, data: bytes) -> None:
        key = (session_id, tool_use_id, index)
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._size -= len(existing[1])
            self._entries[key] = (media_type, data)
            self._size += len(data)
            # Never down to nothing: an image larger than the whole cap would
            # otherwise evict itself on the way in, and the one thing the store
            # exists to do is hold the picture that just arrived.
            while self._size > self._max_bytes and len(self._entries) > 1:
                oldest = next(iter(self._entries))
                self._size -= len(self._entries.pop(oldest)[1])

    def get(self, session_id: str, tool_use_id: str,
            index: int) -> tuple[str, bytes] | None:
        with self._lock:
            return self._entries.get((session_id, tool_use_id, index))

    def forget_session(self, session_id: str) -> None:
        """Drop everything a closed session put here. The transcript is still
        on disk, so this costs nothing but the memory it frees."""
        with self._lock:
            for key in [k for k in self._entries if k[0] == session_id]:
                self._size -= len(self._entries.pop(key)[1])

    def total_bytes(self) -> int:
        with self._lock:
            return self._size


# One store for the process. The live path writes to it and the HTTP route
# reads from it, and they are in different threads, which is what the lock is
# for.
store = ImageStore()


def reference(base_path: str, tool_use_id: str, index: int,
              media_type: str, byte_count: int, query: str = "") -> dict:
    """The `source` an image block carries on the wire in place of its base64.

    `path` is same-origin and relative, so the client appends its own token and
    the backend never has to know how it is reached. `query` is how a subagent
    transcript says which file to look in; it is already URL-safe by the time
    it gets here.
    """
    return {
        "type": "codinian_ref",
        "media_type": media_type,
        "bytes": byte_count,
        "path": f"{base_path}/image/{tool_use_id}/{index}{query}",
    }


def dereference_content(content, base_path: str, tool_use_id: str,
                        keep: bool = False, session_id: str | None = None,
                        query: str = ""):
    """Replace every base64 image in a tool result's `content` with a
    reference, and return the rewritten content.

    `keep` puts the decoded bytes in the live store on the way past, which the
    live path wants and replay does not: replay is reading the same file the
    route falls back to.

    Content that holds no image is returned unchanged -- the same object, not a
    copy -- so the overwhelmingly common text result costs one `isinstance` and
    a loop that finds nothing. So is an image whose `tool_use_id` is not a
    shape that can go in a URL: an unreferenceable picture is better left
    inline than turned into a link to nothing.
    """
    if not isinstance(content, list):
        return content
    if not SAFE_TOOL_USE_ID.fullmatch(tool_use_id or ""):
        return content
    if not any(is_image_block(b) and _source(b).get("type") == "base64"
               for b in content):
        return content

    out = []
    for index, block in enumerate(content):
        decoded = decode(_source(block)) if is_image_block(block) else None
        if decoded is None:
            out.append(block)
            continue
        media_type, data = decoded
        if keep and session_id is not None:
            store.put(session_id, tool_use_id, index, media_type, data)
        out.append({
            "type": "image",
            "source": reference(base_path, tool_use_id, index,
                                media_type, len(data), query),
        })
    return out
