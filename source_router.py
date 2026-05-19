"""URL → source type → playbook router.

For URLs that don't match any built-in pattern, returns Source(name='unknown');
the caller is expected to fail with a message asking the user to add a playbook
under playbooks/_learned/ (see playbooks/_template.md) and extend PATTERNS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILL_ROOT = Path(__file__).parent
PLAYBOOKS = SKILL_ROOT / "playbooks"
LEARNED = PLAYBOOKS / "_learned"


@dataclass
class Source:
    name: str
    playbook_path: Path
    video_id: str  # canonical id used for state/{video_id}/
    metadata_hint: dict


# Pattern → playbook name. Order matters (most specific first).
PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"), "youtube"),
    (re.compile(r"https?://(?:www\.)?youtu\.be/([A-Za-z0-9_-]{11})"), "youtube"),
    (re.compile(r"https?://(?:www\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})"), "youtube"),
    (re.compile(r"https?://podcasts\.apple\.com/[a-z]{2}/podcast/[^/]+/id\d+"), "apple_podcasts"),
    # No capturing group — use _hash_id for video_id (URL → stable short id).
    (re.compile(r"https?://.+\.(?:rss|xml)$"), "rss"),
    (re.compile(r"https?://.+/feed/?$"), "rss"),
    (re.compile(r"https?://.+/feed\.xml$"), "rss"),
]

LOCAL_AUDIO_EXTS = {".mp3", ".opus", ".m4a", ".wav", ".ogg", ".flac"}
LOCAL_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov"}


_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_video_id(raw: str) -> str:
    """Strip anything that could escape a state directory: path separators, leading
    dots (`.`, `..`, `.ssh`), and any non-[A-Za-z0-9._-] character. Falls back to a
    URL-hash if the result becomes empty."""
    cleaned = _SAFE_ID.sub("_", raw).lstrip(".")
    return cleaned[:80] or _hash_id(raw)


def resolve(url_or_path: str) -> Source:
    """Resolve a URL or local path to a Source with playbook reference."""
    # Local file?
    if not url_or_path.startswith(("http://", "https://")):
        p = Path(url_or_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Path does not exist: {p}")
        ext = p.suffix.lower()
        allowed = LOCAL_AUDIO_EXTS | LOCAL_VIDEO_EXTS
        if ext not in allowed:
            raise ValueError(
                f"Unsupported local file extension {ext!r}. "
                f"Supported: {sorted(allowed)}"
            )
        return Source(
            name="local_file",
            playbook_path=PLAYBOOKS / "local_file.md",
            video_id=_sanitize_video_id(p.stem),
            metadata_hint={"path": str(p), "ext": ext, "is_video": ext in LOCAL_VIDEO_EXTS},
        )

    # Known URL?
    for pattern, playbook in PATTERNS:
        m = pattern.match(url_or_path)
        if m:
            raw_id = m.group(1) if m.groups() else _hash_id(url_or_path)
            return Source(
                name=playbook,
                playbook_path=PLAYBOOKS / f"{playbook}.md",
                video_id=_sanitize_video_id(raw_id),
                metadata_hint={"url": url_or_path},
            )

    return Source(
        name="unknown",
        playbook_path=PLAYBOOKS / "_template.md",
        video_id=_hash_id(url_or_path),
        metadata_hint={"url": url_or_path},
    )


def _hash_id(s: str) -> str:
    """Stable short id for unknown URLs."""
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:12]


if __name__ == "__main__":
    import sys
    src = resolve(sys.argv[1])
    print(f"name={src.name}")
    print(f"playbook={src.playbook_path}")
    print(f"video_id={src.video_id}")
    print(f"hint={src.metadata_hint}")
