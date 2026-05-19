#!/usr/bin/env python3
"""Transcribe → essay-longread pipeline.

Linear-Refine orchestrator with checkpoint state. Each step writes
state/{video_id}/{NN_step}.json|md and skips already-completed steps
on --resume.

Usage:
    pipeline.py URL [--resume] [--from-step N] [--lang ru] [--prompt "..."]
    pipeline.py /path/to/audio.mp3 [...]

Steps:
    01_acquire    fetch transcript (subs|whisper)
    02_chunk      embedding-cosine TextTiling
    03_extract    parallel per chunk (semaphore=3)
    04_ledger     dedup + score
    05_plan       outline
    06_tensions   subtle tensions + presupposed beliefs
    07_compose    Russian slow-journalism essay
    08_verify     Chain-of-Verification
    09_gap        coverage check
    10_edit       refine loop (≤3 iter)
    11_emit       final outputs

State is JSON per step; resume reads what exists and starts at first missing.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from anthropic import Anthropic, APIError, APIStatusError, APITimeoutError

SKILL_ROOT = Path(__file__).parent
sys.path.insert(0, str(SKILL_ROOT))

from source_router import resolve as resolve_source  # noqa: E402

# ---------- Config ----------
CONFIG = yaml.safe_load((SKILL_ROOT / "config.yaml").read_text())
LOG = logging.getLogger("transcribe")
logging.basicConfig(
    level=os.environ.get("TRANSCRIBE_LOG", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def _data_dir() -> Path:
    """Cross-run user data dir (episodic memory, learned playbook state).
    Default: ~/.local/share/transcribe/ (XDG). Override via TRANSCRIBE_DATA_DIR env.
    Never the skill install dir — the install dir is treated as read-only."""
    env = os.environ.get("TRANSCRIBE_DATA_DIR")
    base = expand(env) if env else expand("~/.local/share/transcribe")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _episodic_memory_path() -> Path:
    return _data_dir() / "episodes.jsonl"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write a file atomically (tmp + rename) so concurrent readers never see torn writes."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def _flock_append(path: Path, line: str) -> None:
    """Append a line to a file under an advisory lock, so parallel runs interleave cleanly.
    Used for `memory/episodes.jsonl` which two concurrent pipelines might both extend."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ---------- State management ----------
@dataclass
class State:
    video_id: str
    state_dir: Path

    @classmethod
    def for_video(cls, video_id: str, base_dir: Path | None = None) -> "State":
        # State and output live in the CALLER's working directory, not in the skill folder.
        # Skill folder = code (pipeline, prompts, playbooks). State = ephemeral run data.
        # Default: <cwd>/transcribe/<video_id>/
        # Override: --out-dir <path>  (passed by main())
        if base_dir is None:
            base_dir = Path.cwd() / "transcribe"
        d = base_dir / video_id
        d.mkdir(parents=True, exist_ok=True)
        return cls(video_id=video_id, state_dir=d)

    def has(self, step_name: str, validate: bool = False) -> bool:
        """Return True if step output exists. With validate=True, also confirm
        the file parses (JSON) or is non-trivial (Markdown). Corrupt or empty
        checkpoints register as missing so resume re-runs them cleanly."""
        files = list(self.state_dir.glob(f"{step_name}.*"))
        if not files:
            return False
        if not validate:
            return True
        for f in files:
            try:
                if f.stat().st_size < 5:
                    return False
                if f.suffix == ".json":
                    json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                return False
        return True

    def path(self, step_name: str, ext: str = "json") -> Path:
        return self.state_dir / f"{step_name}.{ext}"

    def read_json(self, step_name: str) -> dict | list | None:
        p = self.path(step_name, "json")
        if p.exists():
            return json.loads(p.read_text())
        return None

    def read_text(self, step_name: str, ext: str = "md") -> str | None:
        p = self.path(step_name, ext)
        if p.exists():
            return p.read_text()
        return None

    def write_json(self, step_name: str, data) -> Path:
        p = self.path(step_name, "json")
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        LOG.info("wrote %s (%d bytes)", p.name, p.stat().st_size)
        return p

    def write_text(self, step_name: str, content: str, ext: str = "md") -> Path:
        p = self.path(step_name, ext)
        p.write_text(content)
        LOG.info("wrote %s (%d bytes)", p.name, p.stat().st_size)
        return p


# ---------- Claude call wrapper (SDK first, `claude` CLI as fallback) ----------
# Two backends are supported:
#   1. `anthropic` SDK with ANTHROPIC_API_KEY  — preferred, hits prompt cache.
#   2. `claude` CLI in --print mode             — fallback for Claude Code MAX
#      users who have the CLI but no separate API key. Auth is whatever the
#      CLI already uses (OAuth from ~/.claude/.credentials.json).
# Selection is one-time and logged at the first call.

_client: Anthropic | None = None
_backend: str | None = None  # "sdk" | "cli", picked on first call


def _pick_backend() -> str:
    global _backend
    if _backend is not None:
        return _backend
    if os.environ.get("ANTHROPIC_API_KEY"):
        _backend = "sdk"
    elif shutil.which("claude"):
        _backend = "cli"
        LOG.info("ANTHROPIC_API_KEY not set; falling back to `claude` CLI subprocess.")
    else:
        raise RuntimeError(
            "Need either ANTHROPIC_API_KEY env var (https://console.anthropic.com/) "
            "or the `claude` CLI in $PATH (https://claude.com/claude-code)."
        )
    return _backend


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def load_prompt(name: str) -> str:
    """Load a system prompt from prompts/."""
    p = SKILL_ROOT / "prompts" / f"{name}.md"
    return p.read_text()


def _claude_call_sdk(system, user_content, model, max_tokens, cache_system,
                     timeout, max_retries):
    client = _get_client()
    system_blocks = [{"type": "text", "text": system}]
    if cache_system and len(system) > 800:
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}

    LOG.info("claude SDK call (model=%s, sys=%d chars, user=%d chars, max_tokens=%d)",
             model, len(system), len(user_content), max_tokens)
    t0 = time.time()

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": user_content}],
                timeout=timeout,
            )
            elapsed = time.time() - t0
            text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
            out = "".join(text_parts).strip()
            usage = resp.usage
            LOG.info(
                "claude returned %d chars in %.1fs (in=%d cached=%d out=%d)",
                len(out), elapsed,
                usage.input_tokens, getattr(usage, "cache_read_input_tokens", 0),
                usage.output_tokens,
            )
            return out
        except (APIStatusError, APITimeoutError, APIError) as e:
            last_err = e
            status = getattr(e, "status_code", None)
            retriable = isinstance(e, APITimeoutError) or status in (429, 500, 502, 503, 504, 529)
            if not retriable or attempt == max_retries - 1:
                raise
            backoff = (2 ** attempt) + random.uniform(0, 1)
            LOG.warning("claude SDK attempt %d failed (%s); sleep %.1fs", attempt + 1, e, backoff)
            time.sleep(backoff)
    raise RuntimeError(f"claude_call exhausted retries: {last_err}")


def _claude_call_cli(system, user_content, model, timeout):
    """Subprocess `claude --print`. Auth via the CLI's own OAuth/key."""
    cmd = [
        "claude", "--print", "--output-format", "text",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--tools", "",
        "--model", model,
    ]
    # Some models (Haiku) don't accept effort; strip parent env so we don't force it.
    sub_env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE_CODE_EFFORT", "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"))}

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as sf:
        sf.write(system)
        sys_path = sf.name
    try:
        cmd += ["--system-prompt-file", sys_path]
        LOG.info("claude CLI call (model=%s, sys=%d chars, user=%d chars)",
                 model, len(system), len(user_content))
        t0 = time.time()
        proc = subprocess.run(
            cmd, input=user_content, capture_output=True, text=True,
            timeout=timeout, check=False, env=sub_env,
        )
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (exit={proc.returncode}, {elapsed:.1f}s): "
                f"stderr={proc.stderr[-500:]}"
            )
        LOG.info("claude CLI returned %d chars in %.1fs", len(proc.stdout), elapsed)
        return proc.stdout.strip()
    finally:
        try:
            os.unlink(sys_path)
        except OSError:
            pass


def claude_call(
    system: str,
    user_content: str,
    model: str | None = None,
    max_tokens: int = 8000,
    cache_system: bool = True,
    timeout: int = 1500,
    max_retries: int = 4,
) -> str:
    """Call Claude. Uses anthropic SDK if ANTHROPIC_API_KEY is set, else the
    `claude` CLI if available. System prompt is cached on the SDK path."""
    model = model or CONFIG["claude"]["model"]
    backend = _pick_backend()
    if backend == "sdk":
        return _claude_call_sdk(system, user_content, model, max_tokens,
                                cache_system, timeout, max_retries)
    return _claude_call_cli(system, user_content, model, timeout)


def _extract_json_payload(raw: str) -> str:
    """Strip markdown fences / prose around a JSON object or array."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n", "", raw)
        raw = re.sub(r"\n```\s*$", "", raw)
    if not raw.startswith(("{", "[")):
        m = re.search(r"[\{\[]", raw)
        if m:
            raw = raw[m.start():]
            for i in range(len(raw) - 1, -1, -1):
                if raw[i] in "}]":
                    raw = raw[:i+1]
                    break
    return raw


def claude_call_json(*args, json_retries: int = 2, **kwargs) -> dict | list:
    """Like claude_call but parses JSON. Retries on JSONDecodeError up to json_retries
    times — a model occasionally returns prose-wrapped JSON or a truncated payload,
    and a re-roll usually fixes it cheaper than burning the whole pipeline."""
    last_err: json.JSONDecodeError | None = None
    for attempt in range(json_retries + 1):
        raw = claude_call(*args, **kwargs)
        payload = _extract_json_payload(raw)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as e:
            last_err = e
            if attempt < json_retries:
                LOG.warning(
                    "JSON parse failed attempt %d/%d at pos %d: %s — retrying",
                    attempt + 1, json_retries + 1, e.pos, e.msg,
                )
                continue
            LOG.error("JSON parse failed after %d attempts. Payload head: %r",
                      json_retries + 1, payload[:300])
    raise last_err  # type: ignore[misc]


# ---------- Step 01: ACQUIRE ----------
# Source name → acquisition handler. Adding a new source means writing a new
# `_acquire_<name>(state, source, args) -> dict` and adding it here + a regex in
# source_router.PATTERNS. No `elif` chain.
SOURCE_HANDLERS: dict = {}


def register_source(name: str):
    """Decorator: register an `_acquire_*` as the handler for a source name."""
    def deco(fn):
        SOURCE_HANDLERS[name] = fn
        return fn
    return deco


def step_acquire(state: State, source, args) -> dict:
    """Fetch transcript: try subs first (YouTube), fallback to whisper."""
    LOG.info("[01_acquire] source=%s", source.name)
    out = {"source": source.name, "video_id": source.video_id}

    handler = SOURCE_HANDLERS.get(source.name)
    if handler is None:
        raise NotImplementedError(
            f"No handler registered for source '{source.name}'. Define a new "
            f"`@register_source(\"{source.name}\")` function and a matching pattern "
            f"in source_router.PATTERNS."
        )
    out.update(handler(state, source, args))

    # User-supplied --prompt overrides whatever the source handler chose.
    if args.prompt:
        out.setdefault("meta", {})["initial_prompt"] = args.prompt

    # If we have audio but no transcript yet — call whisper.
    if out.get("needs_whisper") and not out.get("transcript_segments"):
        LOG.info("[01_acquire] whisper transcribing %s", out["audio_path"])
        partial = state.state_dir / "01_whisper.partial.json"
        segs = _whisper_transcribe(
            audio_path=out["audio_path"],
            language=out["meta"].get("lang", "auto"),
            initial_prompt=out["meta"].get("initial_prompt", ""),
            partial_path=partial,
        )
        out["transcript_segments"] = segs

    return out


@register_source("local_file")
def _acquire_local(state, source, args) -> dict:
    path = Path(source.metadata_hint["path"])
    wav_path = state.state_dir / "audio.wav"
    if not wav_path.exists():
        _ffmpeg_to_wav(path, wav_path)
    return {
        "audio_path": str(wav_path),
        "needs_whisper": True,
        "meta": {"title": path.stem, "lang": args.lang or "auto"},
    }


def _yt_dlp_auth_args() -> list[str]:
    """Build cookies/proxy args for yt-dlp from config + env."""
    args = []
    cookies = expand(CONFIG["youtube"]["cookies_file"])
    if cookies.exists():
        args += ["--cookies", str(cookies)]
    proxy = os.environ.get("YT_PROXY") or CONFIG["youtube"].get("proxy") or ""
    if proxy:
        args += ["--proxy", proxy]
    return args


def _youtube_preflight() -> None:
    """Bail early when a YouTube URL is paired with no cookies and no proxy.
    YouTube blocks most datacenter and cloud-VPS IPs with anti-bot, so without auth
    yt-dlp dies on the metadata call with a cryptic 'Sign in to confirm you're not a
    bot' error. Better to surface the requirement up front than after 30s of network."""
    cookies = expand(CONFIG["youtube"]["cookies_file"])
    proxy = os.environ.get("YT_PROXY") or CONFIG["youtube"].get("proxy") or ""
    if cookies.exists() or proxy:
        return
    raise RuntimeError(
        "YouTube source detected but no auth configured.\n\n"
        "YouTube blocks most datacenter and cloud-VPS IPs (\"Sign in to confirm "
        "you're not a bot\"). To use YouTube as a source, set up ONE of these:\n\n"
        "  1) Cookies (simplest for personal use):\n"
        "     Export your browser session via the 'Get cookies.txt LOCALLY'\n"
        f"     extension (Chrome/Firefox) and save it to {cookies}\n\n"
        "  2) Residential proxy:\n"
        "     export YT_PROXY=http://user:pass@host:port\n\n"
        "If you only need podcasts, prefer the show's RSS feed (Apple Podcasts →\n"
        "feedUrl resolves to direct mp3, no anti-bot). See SKILL.md 'YouTube\n"
        "anti-bot' section for details."
    )


@register_source("youtube")
def _acquire_youtube(state, source, args) -> dict:
    """YouTube: try subs, fallback to audio+whisper. Cookies required if banned."""
    url = source.metadata_hint["url"]
    auth = _yt_dlp_auth_args()

    # 1. metadata
    meta_cmd = [CONFIG["yt_dlp"], "--skip-download", "--print-json", *auth, url]
    LOG.info("yt-dlp meta: %s", shlex.join(meta_cmd))
    r = subprocess.run(meta_cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        LOG.error("metadata failed: %s", r.stderr[-500:])
        cookies = expand(CONFIG["youtube"]["cookies_file"])
        raise RuntimeError(
            f"yt-dlp metadata failed. If your IP is rate-limited by YouTube, "
            f"set cookies at {cookies} or set YT_PROXY env."
        )
    info = json.loads(r.stdout)
    meta = {
        "title": info.get("title", ""),
        "channel": info.get("channel", ""),
        "upload_date": info.get("upload_date", ""),
        "duration": info.get("duration", 0),
        "description": (info.get("description") or "")[:1000],
        "chapters": info.get("chapters", []) or [],
        "lang": args.lang or info.get("language") or "en",
    }
    persons = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", meta["title"] + " " + meta["description"])[:5]
    meta["initial_prompt"] = (
        f"Podcast interview. Title: {meta['title']}. Channel: {meta['channel']}. "
        f"Persons: {', '.join(set(persons))}."
    )[:1000]

    # 2. try subs
    sub_dir = state.state_dir / "subs"
    sub_dir.mkdir(exist_ok=True)
    lang = "en" if meta["lang"].startswith("en") else "ru,en"
    sub_cmd = [
        CONFIG["yt_dlp"], "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", lang, "--convert-subs", "srt",
        *auth,
        "-o", f"{sub_dir}/%(id)s.%(ext)s",
        url,
    ]
    LOG.info("yt-dlp subs: %s", shlex.join(sub_cmd))
    subprocess.run(sub_cmd, capture_output=True, check=False)

    srt_files = list(sub_dir.glob(f"{source.video_id}*.srt"))
    chosen = next((s for s in srt_files if ".en." in s.name), None) or (srt_files[0] if srt_files else None)

    if chosen:
        lines = chosen.read_text().count("\n")
        if lines > meta["duration"] * 0.05:
            LOG.info("[01_acquire] using subs %s (%d lines)", chosen.name, lines)
            segs = _parse_srt(chosen.read_text())
            return {
                "transcript_segments": segs,
                "transcript_source": "subs",
                "subs_file": str(chosen),
                "meta": meta,
                "needs_whisper": False,
            }
        LOG.warning("[01_acquire] subs too sparse, falling back to whisper")

    # 3. fallback: download audio
    audio_dir = state.state_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    audio_cmd = [
        CONFIG["yt_dlp"], "-f", "bestaudio", "-x",
        "--audio-format", "opus", "--audio-quality", "5",
        *auth,
        "-o", f"{audio_dir}/%(id)s.%(ext)s",
        url,
    ]
    LOG.info("yt-dlp audio: %s", shlex.join(audio_cmd))
    r = subprocess.run(audio_cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        cookies = expand(CONFIG["youtube"]["cookies_file"])
        raise RuntimeError(
            f"YouTube audio download failed: {r.stderr[-300:]}\n"
            f"Set cookies at {cookies} or use YT_PROXY env."
        )
    # yt-dlp succeeded but the expected .opus is missing — happens when the format
    # was unavailable and a different one was substituted. Be explicit instead of
    # raising StopIteration.
    opus = next(audio_dir.glob(f"{source.video_id}*.opus"), None)
    if opus is None:
        downloaded = sorted(f.name for f in audio_dir.iterdir())
        raise RuntimeError(
            f"yt-dlp downloaded {downloaded} but no .opus found. "
            f"Try clearing {audio_dir} and re-running, or check your yt-dlp version."
        )
    wav = state.state_dir / "audio.wav"
    if not wav.exists():
        _ffmpeg_to_wav(opus, wav)

    return {"audio_path": str(wav), "meta": meta, "needs_whisper": True}


def _require_https(url: str, *, label: str) -> None:
    """Refuse non-https for any URL we'll fetch and feed into a parser.
    HTTP would let a network attacker swap the response — and we trust feedUrl, RSS, etc."""
    from urllib.parse import urlparse
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("https", "http"):
        raise RuntimeError(f"{label}: refuse to fetch {url!r}: unexpected scheme {scheme!r}")
    if scheme == "http":
        LOG.warning("%s: %s uses http (no TLS); responses are tamperable in transit", label, url)


def _http_get(url: str, *, label: str = "http", ua: str = "Mozilla/5.0", max_bytes: int = 50_000_000) -> bytes:
    """Fetch a URL via curl (list-form, no shell). Caps response size (DoS defense)."""
    _require_https(url, label=label)
    r = subprocess.run(
        ["curl", "-sSL", "-A", ua,
         "--max-time", "60",
         "--max-filesize", str(max_bytes),
         url],
        capture_output=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl {url!r} failed: {r.stderr[-300:]!r}")
    return r.stdout


def _http_download(url: str, dst: Path, *, label: str = "download",
                   timeout: int = 600, max_bytes: int = 500_000_000) -> None:
    """Download URL to file via curl. Caps file size (no surprise 10GB downloads)."""
    _require_https(url, label=label)
    r = subprocess.run(
        ["curl", "-L", "-sS",
         "--max-time", str(timeout),
         "--max-filesize", str(max_bytes),
         "-o", str(dst), url],
        capture_output=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl download of {url!r} → {dst} failed: {r.stderr[-300:]!r}")


def _safe_xml_fromstring(body: bytes):
    """Parse XML with defusedxml to block billion-laughs / external-entity attacks.
    Falls back to a strict-mode stdlib parser with a warning if defusedxml is missing,
    so installs without it still work but lose this defense layer."""
    try:
        from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore
        return _safe_fromstring(body)
    except ImportError:
        import xml.etree.ElementTree as ET
        LOG.warning("defusedxml not installed — XML parsing falls back to stdlib (vulnerable to billion-laughs DoS). Run `pip install defusedxml`.")
        return ET.fromstring(body)


def _ffmpeg_to_wav(src: Path, dst: Path) -> None:
    """Normalize audio/video file to 16kHz mono WAV for Whisper."""
    norm_args = shlex.split(CONFIG["audio_normalize"])
    cmd = [CONFIG["ffmpeg"], "-y", "-i", str(src), *norm_args, str(dst)]
    LOG.info("ffmpeg: %s", shlex.join(cmd))
    subprocess.run(cmd, check=True, capture_output=True)


@register_source("apple_podcasts")
def _acquire_apple(state, source, args) -> dict:
    """Apple Podcasts: parse iTunes feed, download enclosure."""
    url = source.metadata_hint["url"]
    coll = re.search(r"id(\d+)", url)
    if not coll:
        raise ValueError("Could not extract collectionId from Apple URL")
    coll_id = coll.group(1)
    payload = _http_get(
        f"https://itunes.apple.com/lookup?id={coll_id}&entity=podcast",
        label="itunes-lookup",
    ).decode()
    try:
        feed_url = json.loads(payload)["results"][0]["feedUrl"]
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"iTunes lookup for id={coll_id} returned no feedUrl: {e}")
    # The feedUrl is attacker-controllable via Apple's catalog content — re-validate scheme.
    _require_https(feed_url, label="apple-feed")
    LOG.info("[01_acquire] apple feed: %s", feed_url)
    return _acquire_from_rss(state, feed_url, episode_hint=url, args=args)


@register_source("rss")
def _acquire_rss(state, source, args) -> dict:
    return _acquire_from_rss(state, source.metadata_hint["url"], None, args)


def _acquire_from_rss(state, feed_url: str, episode_hint, args) -> dict:
    body = _http_get(feed_url, label="rss-feed")
    root = _safe_xml_fromstring(body)
    channel = root.find(".//channel")
    items = channel.findall("item")
    target = None
    if episode_hint and "?i=" in episode_hint:
        guid = episode_hint.split("?i=")[1].split("&")[0]
        for it in items:
            g = it.find("guid")
            if g is not None and guid in (g.text or ""):
                target = it
                break
    if not target:
        target = items[0]  # latest

    title = (target.findtext("title") or "").strip()
    enc = target.find("enclosure")
    if enc is None:
        raise RuntimeError("No <enclosure> in episode")
    ep_url = enc.attrib["url"]

    audio_dir = state.state_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    raw = audio_dir / "episode.mp3"
    if not raw.exists():
        _http_download(ep_url, raw)
    wav = state.state_dir / "audio.wav"
    if not wav.exists():
        _ffmpeg_to_wav(raw, wav)

    show = (channel.findtext("title") or "").strip()
    meta = {
        "title": title,
        "channel": show,
        "duration": 0,
        "description": (target.findtext("description") or "")[:500],
        "chapters": [],
        "lang": args.lang or "auto",
        "initial_prompt": f"Podcast: {show}. Episode: {title}",
    }
    return {"audio_path": str(wav), "meta": meta, "needs_whisper": True}


def _whisper_transcribe(audio_path: str, language: str, initial_prompt: str,
                        partial_path: Path | None = None) -> list:
    """Call faster-whisper. Returns segments list.

    If partial_path is given, dumps current segments every 50 segments — enables
    crash recovery without re-doing the 30-min whisper run.
    """
    from faster_whisper import WhisperModel
    cfg = CONFIG["whisper"]

    # Resume from partial if exists
    if partial_path and partial_path.exists():
        try:
            prev = json.loads(partial_path.read_text())
            if prev.get("done"):
                LOG.info("whisper: partial complete (%d segs), reusing", len(prev["segments"]))
                return _post_filter_whisper(prev["segments"])
            LOG.info("whisper: partial has %d segs but not marked done — restarting", len(prev.get("segments", [])))
        except Exception as e:
            LOG.warning("whisper: partial unreadable, restarting: %s", e)

    # Preflight: how long is the audio, do we have RAM, which model can we afford?
    duration_s = _audio_duration_seconds(audio_path)
    chosen_model, reason = _pick_whisper_model(cfg["model"], duration_s)
    free_disk = _free_disk_mb(Path(audio_path).parent)
    LOG.info(
        "whisper preflight: audio=%.1f min, free_ram=%d MB, free_disk=%d MB → model=%s (%s)",
        duration_s / 60.0, _free_ram_mb(), free_disk, chosen_model, reason,
    )
    if free_disk and free_disk < 500:
        LOG.warning(
            "free disk %d MB is low; Whisper writes partial checkpoints and "
            "the pipeline writes intermediate JSON — risk of ENOSPC mid-run.",
            free_disk,
        )

    LOG.info("loading whisper model=%s", chosen_model)
    model = WhisperModel(
        chosen_model, device="cpu", compute_type=cfg["compute_type"],
        cpu_threads=cfg["cpu_threads"], num_workers=cfg["num_workers"],
    )
    lang = language if language and language != "auto" else None
    segments, info = model.transcribe(
        audio_path,
        language=lang,
        beam_size=cfg["beam_size"],
        condition_on_previous_text=cfg["condition_on_previous_text"],
        repetition_penalty=cfg["repetition_penalty"],
        no_repeat_ngram_size=cfg["no_repeat_ngram_size"],
        vad_filter=cfg["vad_filter"],
        vad_parameters=cfg["vad_parameters"],
        word_timestamps=cfg["word_timestamps"],
        hallucination_silence_threshold=cfg["hallucination_silence_threshold"],
        initial_prompt=initial_prompt or None,
        prompt_reset_on_temperature=cfg["prompt_reset_on_temperature"],
    )
    out = []
    for i, s in enumerate(segments):
        out.append({
            "start": s.start, "end": s.end, "text": s.text.strip(),
            "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob,
        })
        if partial_path and (i + 1) % 50 == 0:
            partial_path.write_text(json.dumps({"segments": out, "done": False}, ensure_ascii=False))
            LOG.info("whisper: %d segments → partial checkpoint", i + 1)
    LOG.info("whisper: %d segments, lang=%s prob=%.2f", len(out), info.language, info.language_probability)
    if partial_path:
        partial_path.write_text(json.dumps({
            "segments": out, "done": True,
            "language": info.language, "language_probability": info.language_probability,
        }, ensure_ascii=False))
    # Critical: free memory before subsequent steps
    del model
    gc.collect()
    return _post_filter_whisper(out)


_HALLUCINATION_PATTERNS: list[re.Pattern] | None = None


def _hallucination_patterns() -> list[re.Pattern]:
    """Lazy-load and cache the regex blacklist — avoids re-reading the file on
    every Whisper segment batch."""
    global _HALLUCINATION_PATTERNS
    if _HALLUCINATION_PATTERNS is not None:
        return _HALLUCINATION_PATTERNS
    out: list[re.Pattern] = []
    path = SKILL_ROOT / "filters" / "whisper_hallucinations.txt"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(re.compile(line, re.IGNORECASE))
    _HALLUCINATION_PATTERNS = out
    return out


def _post_filter_whisper(segments: list) -> list:
    """Apply hallucination regex blacklist + low-confidence drops."""
    blacklist = _hallucination_patterns()
    out = []
    for s in segments:
        text = s["text"].strip()
        if any(p.match(text) for p in blacklist):
            LOG.debug("filter regex: %r", text)
            continue
        if s.get("no_speech_prob", 0) > 0.6 and s.get("avg_logprob", 0) < -1.0:
            LOG.debug("filter conf: %r", text)
            continue
        out.append(s)
    LOG.info("post-filter: %d → %d", len(segments), len(out))
    return out


# YouTube auto-subs sometimes leak inline formatting (`<c>`, <c.colorE5E5E5>`, etc.).
# Strip before storing the segment text — these tags survive otherwise.
_SUB_TAG_RE = re.compile(r"</?(?:c|i|b|u|font|s)[^>]*>", re.IGNORECASE)


# ---------- Resource preflight (RAM/disk → Whisper model autoselect) ----------
# Empirical peak RSS during faster-whisper inference (int8, CPU) for typical 1-hour
# audio. Long-form (>60 min) gets a multiplier because activations + audio buffer
# accumulate. Numbers err on the conservative side — better refuse upfront than OOM
# mid-Whisper and lose 30 minutes of CPU work.
_WHISPER_PEAK_MB: dict[str, int] = {
    "tiny": 600, "tiny.en": 600,
    "base": 800, "base.en": 800,
    "small": 1500, "small.en": 1500,
    "medium": 3000, "medium.en": 3000,
    "large": 4500, "large-v2": 4500, "large-v3": 4500,
    "large-v3-turbo": 3500,
    "distil-large-v3": 2500,
}


def _free_ram_mb() -> int:
    """Read /proc/meminfo MemAvailable in MB. Returns 0 on non-Linux platforms,
    which short-circuits the preflight (we don't refuse to run; just lose the check)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def _free_disk_mb(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        return 0


def _required_whisper_ram_mb(model: str, duration_s: float) -> int:
    """Estimate peak RAM needed for a Whisper run.
    Base = model peak from the table; long-form gets x1.15 (60-90 min) or x1.30 (>90 min);
    +500 MB safety so we don't sit on the edge of OOM-killer."""
    base = _WHISPER_PEAK_MB.get(model, 3000)
    duration_min = duration_s / 60.0
    if duration_min > 90:
        base = int(base * 1.30)
    elif duration_min > 60:
        base = int(base * 1.15)
    return base + 500


def _pick_whisper_model(requested: str, duration_s: float) -> tuple[str, str]:
    """Return (model_name, reason). If the requested model doesn't fit free RAM,
    downgrade to the largest model that does. If even 'tiny' doesn't fit, raise."""
    free = _free_ram_mb()
    if not free:
        return requested, "preflight skipped (not Linux or /proc unreadable)"

    # Downgrade ladder. Pin order so we always pick the biggest that fits.
    ladder = [requested]
    for fallback in ("small", "base", "tiny"):
        if fallback not in ladder:
            ladder.append(fallback)

    requested_need = _required_whisper_ram_mb(requested, duration_s)

    for cand in ladder:
        need = _required_whisper_ram_mb(cand, duration_s)
        if need <= free:
            if cand == requested:
                return cand, f"fits (need ~{need} MB, free {free} MB)"
            return cand, (
                f"requested {requested!r} needs ~{requested_need} MB but only "
                f"{free} MB free; downgraded to {cand!r} (~{need} MB)"
            )

    smallest = "tiny"
    raise RuntimeError(
        f"Cannot fit any Whisper model in available RAM. "
        f"free={free} MB, even {smallest!r} would need ~{_required_whisper_ram_mb(smallest, duration_s)} MB "
        f"for {duration_s/60:.0f}-min audio. Options: add swap "
        f"(`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`), "
        f"reduce audio length with --max-duration-min, or use a host with more RAM."
    )


def _audio_duration_seconds(audio_path: str) -> float:
    """Quick ffprobe to learn audio length. Returns 0 if it fails (we then default to
    the most pessimistic long-form estimate to stay safe)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, check=False, timeout=15,
        )
        return float(r.stdout.strip() or 0)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        return 0.0


def _parse_srt(srt_text: str) -> list:
    """Parse SRT into segments. Strips inline `<c>`-style YouTube formatting tags."""
    segs = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for b in blocks:
        lines = b.strip().split("\n")
        if len(lines) < 3:
            continue
        m = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", lines[1])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        text = " ".join(lines[2:]).strip()
        text = _SUB_TAG_RE.sub("", text).strip()
        if text:
            segs.append({"start": start, "end": end, "text": text,
                         "no_speech_prob": 0, "avg_logprob": 0})
    return segs


# ---------- Step 02: CHUNK ----------
def step_chunk(state, acquire_data) -> dict:
    """TextTiling-like chunking using sentence-transformers cosine."""
    LOG.info("[02_chunk] embedding-cosine TextTiling")
    segs = acquire_data["transcript_segments"]
    # Group segments into ~30-sec sentences first
    text_units = []
    cur = {"start": None, "end": None, "text": []}
    for s in segs:
        if cur["start"] is None:
            cur["start"] = s["start"]
        cur["text"].append(s["text"])
        cur["end"] = s["end"]
        if s["end"] - cur["start"] > 30 or len(" ".join(cur["text"])) > 400:
            text_units.append({"start": cur["start"], "end": cur["end"], "text": " ".join(cur["text"])})
            cur = {"start": None, "end": None, "text": []}
    if cur["text"]:
        text_units.append({"start": cur["start"], "end": cur["end"], "text": " ".join(cur["text"])})

    LOG.info("text_units: %d", len(text_units))

    # Compute boundaries via embedding cosine
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        emb_model = SentenceTransformer(CONFIG["chunking"]["embedding_model"])
        embs = emb_model.encode([u["text"] for u in text_units], normalize_embeddings=True)
        cosines = (embs[:-1] * embs[1:]).sum(axis=1)
        del emb_model
        gc.collect()
        thresh = CONFIG["chunking"]["cosine_threshold"]
        boundaries = [i+1 for i, c in enumerate(cosines) if c < thresh]
        # Limit chunks to 4-12 (cap for sanity)
        if len(boundaries) > 12:
            ranked = sorted(enumerate(cosines), key=lambda x: x[1])[:12]
            boundaries = sorted([i+1 for i, _ in ranked])
        elif len(boundaries) < 3 and len(text_units) > 6:
            ranked = sorted(enumerate(cosines), key=lambda x: x[1])[:4]
            boundaries = sorted([i+1 for i, _ in ranked])
    except Exception as e:
        LOG.warning("embedding chunking failed: %s — fallback to time-based", e)
        # Fallback: every ~6 minutes
        boundaries = []
        cur_t = 0
        for i, u in enumerate(text_units):
            if u["start"] - cur_t > 360:
                boundaries.append(i)
                cur_t = u["start"]

    # Build chunks
    chunks = []
    starts = [0] + boundaries + [len(text_units)]
    for i in range(len(starts)-1):
        units = text_units[starts[i]:starts[i+1]]
        if not units:
            continue
        chunks.append({
            "id": f"ch{i+1:03d}",
            "start": units[0]["start"],
            "end": units[-1]["end"],
            "text": "\n\n".join(u["text"] for u in units),
            "n_units": len(units),
        })
    LOG.info("[02_chunk] %d chunks", len(chunks))
    return {"chunks": chunks, "method": "embedding_cosine"}


# ---------- Step 03: EXTRACT (parallel) ----------
# Retriable narrow set — explicitly NOT `except Exception`, which would silently
# swallow real bugs (KeyError on a schema typo, AttributeError, etc.) as if they
# were transient network blips and downgrade the chunk to empty claims.
_EXTRACT_RETRIABLE = (
    json.JSONDecodeError,
    APIError, APITimeoutError, APIStatusError,
    subprocess.SubprocessError, subprocess.TimeoutExpired,
    OSError,
)


async def step_extract(state, chunks_data, meta) -> dict:
    """Parallel claim extraction per chunk with bounded concurrency."""
    LOG.info("[03_extract] %d chunks, semaphore=%d",
             len(chunks_data["chunks"]), CONFIG["extract"]["semaphore"])
    sem = asyncio.Semaphore(CONFIG["extract"]["semaphore"])
    # Cache lives inside the per-video state dir (which is in cwd/transcribe/),
    # NOT in the skill install dir. Resumed extracts hit the cache; cross-video runs
    # don't share — that's correct because extraction is content-specific.
    cache = state.state_dir / "extract_cache"
    cache.mkdir(parents=True, exist_ok=True)
    sys_prompt = load_prompt("01_extract")

    async def extract_one(chunk):
        cache_path = cache / f"{chunk['id']}.json"
        if cache_path.exists():
            LOG.info("[03_extract] cache hit %s", chunk["id"])
            return json.loads(cache_path.read_text())
        user = (
            f"Metadata: {json.dumps(meta, ensure_ascii=False)[:500]}\n\n"
            f"Chunk {chunk['id']} (t={_fmt_ts(chunk['start'])} - {_fmt_ts(chunk['end'])}):\n\n"
            f"{chunk['text']}"
        )
        last_err: Exception | None = None
        for attempt in range(CONFIG["extract"]["retry_max"]):
            # Acquire semaphore for the call; release before sleeping so other chunks
            # don't starve while we back off.
            async with sem:
                try:
                    result = await asyncio.to_thread(
                        claude_call_json,
                        sys_prompt, user,
                        max_tokens=CONFIG["claude"]["max_tokens_extract"],
                    )
                    result["chunk_id"] = chunk["id"]
                    _atomic_write_text(cache_path, json.dumps(result, indent=2, ensure_ascii=False))
                    LOG.info("[03_extract] %s done (claims=%d quotes=%d)",
                             chunk["id"],
                             len(result.get("claims", [])),
                             len(result.get("quotes", [])))
                    return result
                except _EXTRACT_RETRIABLE as e:
                    last_err = e
            backoff = CONFIG["extract"]["retry_backoff_base"] ** attempt
            LOG.warning("[03_extract] %s attempt %d failed: %s; sleep %ds",
                        chunk["id"], attempt + 1, last_err, backoff)
            await asyncio.sleep(backoff)
        LOG.error("[03_extract] %s failed after retries: %s", chunk["id"], last_err)
        return {"chunk_id": chunk["id"], "claims": [], "quotes": [],
                "error": str(last_err) or "failed"}

    extracts = await asyncio.gather(
        *[extract_one(c) for c in chunks_data["chunks"]],
        return_exceptions=False,
    )
    failed = [e for e in extracts if e.get("error")]
    if failed and len(failed) == len(extracts):
        raise RuntimeError(
            f"[03_extract] all {len(extracts)} chunks failed; refusing to write a "
            f"checkpoint that would be picked up by --resume as 'done'. "
            f"Last error: {failed[-1].get('error')}"
        )
    return {"extracts": extracts, "failed_chunks": [e["chunk_id"] for e in failed]}


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------- Step 04: LEDGER ----------
def step_ledger(state, extracts_data, meta) -> dict:
    """Single Claude call to dedup + score + build graph."""
    LOG.info("[04_ledger] dedup + score")
    sys_prompt = load_prompt("02_ledger")
    # Episodic memory: prior episode top claims for novelty scoring
    prior = _load_prior_episodes(meta.get("channel"))
    user = json.dumps({
        "extracts": extracts_data["extracts"],
        "prior_episode_claims": prior[:50],  # cap
    }, ensure_ascii=False)
    result = claude_call_json(sys_prompt, user, max_tokens=CONFIG["claude"]["max_tokens_extract"])
    return result


def _load_prior_episodes(channel: str | None) -> list:
    """Load top-claims from episodes that share this channel name.
    Reads from ~/.local/share/transcribe/episodes.jsonl (or TRANSCRIBE_DATA_DIR).
    Returns empty list when channel is unknown, file is absent, or no match."""
    if not channel:
        return []
    p = _episodic_memory_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            LOG.warning("memory: skipping malformed line in %s", p)
            continue
        if d.get("channel") == channel:
            out.extend(d.get("top_claims", []))
    return out


def _load_related_episodes_by_similarity(meta: dict, top_k: int = 3) -> list:
    """Pick the top_k most similar past episodes to this one, by embedding cosine
    over (channel + title + first 5 claims). Skips when episodes.jsonl is empty
    or sentence-transformers isn't available."""
    p = _episodic_memory_path()
    if not p.exists():
        return []
    try:
        records = []
        for line in p.read_text().splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not records:
            return []
        from sentence_transformers import SentenceTransformer
        emb_model = SentenceTransformer(CONFIG["chunking"]["embedding_model"])

        def signature(r):
            claims = " ".join(c.get("text", "") for c in r.get("top_claims", [])[:5])
            return f"{r.get('channel', '')} | {r.get('title', '')} | {claims}"

        query_claims = " ".join(c.get("text", "") for c in (meta.get("top_claims") or [])[:5])
        query = f"{meta.get('channel', '')} | {meta.get('title', '')} | {query_claims}"
        corpus = [signature(r) for r in records]
        embs = emb_model.encode([query] + corpus, normalize_embeddings=True)
        del emb_model
        gc.collect()
        q, corp = embs[0], embs[1:]
        scored = sorted(
            ((float((q * c).sum()), records[i]) for i, c in enumerate(corp)),
            key=lambda x: x[0], reverse=True,
        )
        return [r for _, r in scored[:top_k]]
    except (ImportError, OSError, KeyError) as e:
        LOG.warning("related-episodes retrieval skipped: %s", e)
        return []


# ---------- Step 05: PLAN ----------
def step_plan(state, ledger, meta) -> dict:
    LOG.info("[05_plan] outliner")
    sys_prompt = load_prompt("03_outline")
    constitution = (SKILL_ROOT / "style" / "constitution.md").read_text()
    # Use embedding similarity over (channel + title + top-claims) — falls back to
    # empty list if sentence-transformers isn't installed or memory is empty.
    related = _load_related_episodes_by_similarity(
        {**meta, "top_claims": ledger.get("top_central", [])[:5]},
        top_k=CONFIG["memory"]["related_top_k"],
    )
    user = json.dumps({
        "ledger": ledger,
        "metadata": meta,
        "related_episodes": related,
        "constitution": constitution,
    }, ensure_ascii=False)
    return claude_call_json(sys_prompt, user, max_tokens=4000)


# ---------- Step 06: TENSIONS ----------
def step_tensions(state, ledger, plan, transcript_segments) -> dict:
    LOG.info("[06_tensions] subtle tensions + presupposed beliefs")
    sys_prompt = load_prompt("04_tensions")
    transcript_text = "\n".join(f"[{_fmt_ts(s['start'])}] {s['text']}" for s in transcript_segments)
    user = json.dumps({
        "ledger": ledger,
        "outline": plan,
        "transcript_with_timestamps": transcript_text[:50000],  # cap
    }, ensure_ascii=False)
    return claude_call_json(sys_prompt, user, max_tokens=4000)


# ---------- Step 07: COMPOSE ----------
def step_compose(state, ledger, plan, tensions, meta) -> str:
    LOG.info("[07_compose] write essay")
    sys_prompt = load_prompt("05_compose")
    constitution = (SKILL_ROOT / "style" / "constitution.md").read_text()
    anti_barnum = (SKILL_ROOT / "style" / "anti_barnum.md").read_text()
    gold = _load_gold_anchors(plan)

    # transcript_with_timestamps lets COMPOSE pick the correct `[t=...]` for each
    # verbatim quote — instead of inheriting drifted ts from the ledger.
    acq = state.read_json("01_acquire") or {}
    transcript_segments = acq.get("transcript_segments", [])
    transcript_text = "\n".join(
        f"[{_fmt_ts(s['start'])}] {s['text']}" for s in transcript_segments
    )
    duration_s = int(transcript_segments[-1]["end"]) if transcript_segments else 0

    source_lang = (meta.get("lang") or "auto").split("-")[0][:2]
    output_lang = CONFIG.get("compose", {}).get("output_lang", "ru")
    user = json.dumps({
        "outline": plan,
        "ledger": ledger,
        "tensions_extra": tensions,
        "metadata": meta,
        "constitution": constitution,
        "anti_barnum": anti_barnum,
        "gold_anchors": gold,
        "transcript_with_timestamps": transcript_text[:80000],
        "source_duration_seconds": duration_s,
        "source_lang": source_lang,
        "output_lang": output_lang,
    }, ensure_ascii=False)
    return claude_call(sys_prompt, user, max_tokens=CONFIG["claude"]["max_tokens_compose"])


def _load_gold_anchors(plan) -> list:
    """Load top-K gold essays. Without retrieval embedding for now — load all up to K."""
    gold_dir = SKILL_ROOT / "style" / "gold"
    if not gold_dir.exists():
        return []
    files = sorted(gold_dir.glob("*.md"))[:CONFIG["compose"]["gold_retrieval_top_k"]]
    return [{"title": f.stem, "content": f.read_text()} for f in files]


# ---------- Step 08: VERIFY ----------
def step_verify(state, essay_md, ledger, transcript_segments, meta=None) -> dict:
    LOG.info("[08_verify] Chain-of-Verification")
    sys_prompt = load_prompt("06_verify")
    constitution = (SKILL_ROOT / "style" / "constitution.md").read_text()
    anti_barnum = (SKILL_ROOT / "style" / "anti_barnum.md").read_text()
    duration_s = int(transcript_segments[-1]["end"]) if transcript_segments else 0
    source_lang = ((meta or {}).get("lang") or "auto").split("-")[0][:2]
    output_lang = CONFIG.get("compose", {}).get("output_lang", "ru")
    user = json.dumps({
        "essay_md": essay_md,
        "ledger": ledger,
        "transcript_segments": transcript_segments[:500],
        "constitution": constitution,
        "anti_barnum": anti_barnum,
        "source_duration_seconds": duration_s,
        "source_lang": source_lang,
        "output_lang": output_lang,
    }, ensure_ascii=False)
    return claude_call_json(sys_prompt, user, max_tokens=CONFIG["claude"]["max_tokens_verify"])


# ---------- Step 09: GAP ----------
def step_gap(state, essay_md, ledger, plan) -> dict:
    LOG.info("[09_gap] coverage analysis")
    sys_prompt = load_prompt("07_gap")
    user = json.dumps({
        "essay_md": essay_md,
        "ledger": ledger,
        "outline_non_inclusion": plan.get("non_inclusion_rationale", ""),
    }, ensure_ascii=False)
    return claude_call_json(sys_prompt, user, max_tokens=3000)


# ---------- Step 10: EDIT loop ----------
def step_edit_loop(state, essay_md, ledger) -> str:
    LOG.info("[10_edit] refine loop")
    sys_prompt = load_prompt("08_edit")
    constitution = (SKILL_ROOT / "style" / "constitution.md").read_text()
    anti_barnum = (SKILL_ROOT / "style" / "anti_barnum.md").read_text()
    current = essay_md
    acq = state.read_json("01_acquire")
    transcript_segments = acq["transcript_segments"]
    meta = acq.get("meta", {})

    for it in range(1, CONFIG["verify"]["edit_loop_max"] + 1):
        verify = step_verify(state, current, ledger, transcript_segments, meta=meta)
        gap = step_gap(state, current, ledger, state.read_json("05_plan"))
        state.write_json(f"08_verify_iter{it}", verify)
        state.write_json(f"09_gap_iter{it}", gap)
        if verify["summary"]["verdict"] == "PASS" and gap["verdict"] == "ACCEPTABLE":
            LOG.info("[10_edit] PASS at iteration %d", it)
            return current
        if it == CONFIG["verify"]["edit_loop_max"]:
            LOG.warning("[10_edit] reached max iter %d, shipping anyway", it)
            return current

        user = json.dumps({
            "essay_md_current": current,
            "verify_result": verify,
            "gap_result": gap,
            "ledger": ledger,
            "constitution": constitution,
            "anti_barnum": anti_barnum,
            "iteration": it,
        }, ensure_ascii=False)
        new = claude_call(sys_prompt, user, max_tokens=CONFIG["claude"]["max_tokens_compose"])
        # Stop early if diff < threshold
        diff_ratio = _word_diff_ratio(current, new)
        LOG.info("[10_edit] iter %d diff_ratio=%.2f", it, diff_ratio)
        if diff_ratio < CONFIG["verify"]["edit_diff_stop"]:
            LOG.info("[10_edit] diff stable, stopping")
            return new
        current = new
        state.write_text(f"10_edit_iter{it}", new)
    return current


def _word_diff_ratio(a: str, b: str) -> float:
    """How different two essay drafts are (0 = identical, 1 = no shared content).
    Uses difflib.quick_ratio: O(n) bag-of-words similarity bound — good enough
    for the edit-loop stop condition without paying O(n²) on 1000-word essays."""
    import difflib
    aw = a.split()
    if not aw:
        return 1.0
    bw = b.split()
    similarity = difflib.SequenceMatcher(None, aw, bw, autojunk=False).quick_ratio()
    return 1.0 - similarity


# ---------- Step 11: EMIT ----------
def _render_takeaways(ledger: dict, top_n: int = 10) -> str:
    """Render top-N claims as a flat takeaways list — pure derivation from the
    canonical ledger. The essay is one derivation; this is another. Demonstrates
    that the ledger is the primary artifact, not the prose."""
    claims = ledger.get("ledger", []) or []
    top = sorted(claims, key=lambda c: c.get("centrality", 0), reverse=True)[:top_n]
    lines = ["# Key takeaways", ""]
    for c in top:
        text = (c.get("text") or "").rstrip(".")
        speaker = c.get("speaker") or "—"
        ts = c.get("ts") or "??:??:??"
        cid = c.get("id") or ""
        lines.append(f"- {text}  ")
        lines.append(f"  *{speaker}, [t={ts}] · `{cid}`*")
    return "\n".join(lines) + "\n"


def _appendix_locale(lang: str) -> str:
    """Localize the quotes-appendix heading. Default is Russian (matches the
    pipeline's default compose.output_lang)."""
    return "## Quotes" if (lang or "").startswith("en") else "## Цитаты"


def step_emit(state, essay_md, ledger, transcript_segments, meta, verify, gap) -> dict:
    LOG.info("[11_emit] writing outputs")
    out_dir = state.state_dir / "output"
    out_dir.mkdir(exist_ok=True)

    # summary.md = essay + quotes appendix (heading localized to compose.output_lang)
    quotes = sorted(ledger.get("quotes", []), key=lambda q: q.get("ts", ""))[:7]
    appendix_heading = _appendix_locale(CONFIG.get("compose", {}).get("output_lang", "en"))
    appendix = f"\n\n{appendix_heading}\n\n" + "\n\n".join(
        f"- «{q['verbatim']}» — {q.get('speaker', '')} [{q.get('ts', '')}]"
        for q in quotes
    )
    full_md = essay_md.rstrip() + appendix

    # Canonical outputs.
    (out_dir / "summary.md").write_text(full_md)
    (out_dir / "claim_ledger.json").write_text(json.dumps(ledger, indent=2, ensure_ascii=False))
    (out_dir / "key_takeaways.md").write_text(_render_takeaways(ledger))
    _write_srt(out_dir / "transcript.srt", transcript_segments)
    (out_dir / "transcript.json").write_text(json.dumps(transcript_segments, indent=2, ensure_ascii=False))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    (out_dir / "verify.json").write_text(json.dumps(verify, indent=2, ensure_ascii=False))
    (out_dir / "gap.json").write_text(json.dumps(gap, indent=2, ensure_ascii=False))

    _append_episodic_memory(meta, ledger)

    LOG.info("[11_emit] outputs in %s", out_dir)
    return {"output_dir": str(out_dir), "files": sorted(f.name for f in out_dir.iterdir())}


def _write_srt(path, segments):
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_ts(s['start'])} --> {_fmt_srt_ts(s['end'])}")
        lines.append(s["text"])
        lines.append("")
    path.write_text("\n".join(lines))


def _fmt_srt_ts(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = int(seconds % 60); ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _append_episodic_memory(meta, ledger):
    """Append this run's top-10 claims to the cross-run episodic memory.
    Lives under TRANSCRIBE_DATA_DIR (default ~/.local/share/transcribe/),
    NOT in the skill install dir. Uses flock so concurrent runs don't tear."""
    mem = _episodic_memory_path()
    top = sorted(
        ledger.get("ledger", []),
        key=lambda c: (c.get("centrality", 0), c.get("novelty", 0)),
        reverse=True,
    )[:10]
    record = {
        "channel": meta.get("channel", ""),
        "title": meta.get("title", ""),
        "date": meta.get("upload_date", ""),
        "top_claims": [
            {"text": c["text"], "speaker": c.get("speaker"), "centrality": c.get("centrality")}
            for c in top
        ],
    }
    _flock_append(mem, json.dumps(record, ensure_ascii=False) + "\n")


# ---------- Orchestrator ----------

# Step name → step number. Numbers are the user-facing --from-step indices and
# match the prompt files / SKILL.md description. The EDIT loop (steps 8/9/10)
# is represented by its terminal artifact, "10_edit_final".
STEP_ORDER: list[tuple[str, int]] = [
    ("01_acquire",     1),
    ("02_chunk",       2),
    ("03_extract",     3),
    ("04_ledger",      4),
    ("05_plan",        5),
    ("06_tensions",    6),
    ("07_compose",     7),
    ("10_edit_final", 10),
]


def _should_run(state: State, step_name: str, step_num: int, from_step: int, no_resume: bool) -> bool:
    """Run a step if --no-resume, OR --from-step targets it, OR its checkpoint is missing/corrupt."""
    if no_resume:
        return True
    if from_step > 0 and from_step <= step_num:
        return True
    return not state.has(step_name, validate=True)


def _validate_from_step(state: State, from_step: int) -> None:
    """If --from-step N is passed, every step before N must already exist on disk,
    and N itself must be a known step number."""
    if from_step <= 0:
        return
    known_nums = {num for _, num in STEP_ORDER}
    if from_step not in known_nums:
        LOG.error(
            "--from-step %d is not a known step. Valid values: %s",
            from_step, sorted(known_nums),
        )
        sys.exit(2)
    for name, num in STEP_ORDER:
        if num >= from_step:
            return
        if not state.has(name, validate=True):
            LOG.error(
                "--from-step %d requires '%s' on disk first. Run without --from-step "
                "to bootstrap, or pick a smaller --from-step value.",
                from_step, name,
            )
            sys.exit(2)


def _invalidate_downstream(state: State, from_step: int) -> None:
    """Delete checkpoints for steps at or after from_step, so a re-run rebuilds
    downstream artifacts consistently. Without this, --from-step 3 leaves a stale
    plan/ledger on disk that the next non-from-step run silently reuses."""
    if from_step <= 0:
        return
    targets = [name for name, num in STEP_ORDER if num >= from_step]
    # Also invalidate verify/gap iterations (they belong to the edit loop, step 10).
    if from_step <= 10:
        targets += ["08_verify_iter*", "09_gap_iter*"]
    for name in targets:
        for f in state.state_dir.glob(f"{name}.*"):
            LOG.info("invalidating downstream: %s", f.name)
            f.unlink()


def _final_iter_json(state: State, prefix: str) -> dict:
    """Return the *highest*-numbered iteration JSON for a prefix like '08_verify' or
    '09_gap'. Without this, step_emit was reading iter1 even after the edit loop
    ran iter3 — and writing it to output/verify.json as if it were the final result."""
    pattern = re.compile(rf"{re.escape(prefix)}_iter(\d+)\.json$")
    candidates = []
    for p in state.state_dir.glob(f"{prefix}_iter*.json"):
        m = pattern.search(p.name)
        if m:
            candidates.append((int(m.group(1)), p))
    if not candidates:
        return {}
    candidates.sort()
    return json.loads(candidates[-1][1].read_text())


def _bootstrap_warnings() -> None:
    """At startup, warn about the things in README's 'Bootstrap requirements'
    section that the user often misses: too few gold essays, no episodic memory yet."""
    gold = list((SKILL_ROOT / "style" / "gold").glob("*.md"))
    if len(gold) < 3:
        LOG.warning(
            "style/gold/ has %d essay(s); expect mode-collapse / derivative output. "
            "Add at least 3 reference essays in your target voice before relying on quality.",
            len(gold),
        )
    if not _episodic_memory_path().exists():
        LOG.info(
            "no episodic memory yet at %s — cross-episode parallels won't be available "
            "on this run; will accumulate as you process more episodes.",
            _episodic_memory_path(),
        )


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe long audio/video into a slow-journalism essay. "
                    "Resume is on by default; corrupt or absent checkpoints re-run automatically.",
    )
    parser.add_argument("url_or_path", help="URL or local file path")
    parser.add_argument("--no-resume", action="store_true",
                        help="Force re-run all steps even if checkpoints exist (default: resume)")
    parser.add_argument("--from-step", type=int, default=0,
                        help="Force start from this step (1-10). Downstream checkpoints are invalidated.")
    parser.add_argument("--lang", default=None, help="Override Whisper language (e.g. ru, en)")
    parser.add_argument("--prompt", default=None, help="Whisper initial prompt override (max ~244 tokens)")
    parser.add_argument("--out-dir", default=None,
                        help="Base dir for state and outputs (default: <cwd>/transcribe/)")
    parser.add_argument("--max-duration-min", type=int,
                        default=CONFIG.get("acquire", {}).get("max_duration_min", 360),
                        help="Reject sources longer than this (default 6 hours)")
    args = parser.parse_args()

    # Backwards-compat: accept the old --resume flag silently — it was always
    # the default behavior anyway, the flag never did anything.
    # We do NOT add it to argparse; if the user typed it, argparse will error.

    # Fail-fast auth check — better than discovering it after 5 min of Whisper.
    _pick_backend()
    _bootstrap_warnings()

    src = resolve_source(args.url_or_path)
    LOG.info("source: %s, video_id: %s", src.name, src.video_id)
    if src.name == "unknown":
        LOG.error(
            "Unknown source for %r. Add a playbook under playbooks/_learned/ "
            "(see playbooks/_template.md) and extend PATTERNS in source_router.py.",
            args.url_or_path,
        )
        sys.exit(2)

    if src.name == "youtube":
        _youtube_preflight()

    base = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    state = State.for_video(src.video_id, base_dir=base)
    LOG.info("state dir: %s", state.state_dir)

    _validate_from_step(state, args.from_step)
    _invalidate_downstream(state, args.from_step)

    no_resume = args.no_resume

    # 01 ACQUIRE
    if _should_run(state, "01_acquire", 1, args.from_step, no_resume):
        acq = step_acquire(state, src, args)
        _check_duration_cap(acq, args.max_duration_min)
        state.write_json("01_acquire", acq)
    acq = state.read_json("01_acquire")
    meta = acq["meta"]

    # 02 CHUNK
    if _should_run(state, "02_chunk", 2, args.from_step, no_resume):
        ck = step_chunk(state, acq)
        state.write_json("02_chunk", ck)
    ck = state.read_json("02_chunk")

    # 03 EXTRACT (async)
    if _should_run(state, "03_extract", 3, args.from_step, no_resume):
        ex = asyncio.run(step_extract(state, ck, meta))
        state.write_json("03_extract", ex)
    ex = state.read_json("03_extract")

    # 04 LEDGER
    if _should_run(state, "04_ledger", 4, args.from_step, no_resume):
        ld = step_ledger(state, ex, meta)
        state.write_json("04_ledger", ld)
    ld = state.read_json("04_ledger")

    # 05 PLAN
    if _should_run(state, "05_plan", 5, args.from_step, no_resume):
        pl = step_plan(state, ld, meta)
        state.write_json("05_plan", pl)
    pl = state.read_json("05_plan")

    # 06 TENSIONS
    if _should_run(state, "06_tensions", 6, args.from_step, no_resume):
        tn = step_tensions(state, ld, pl, acq["transcript_segments"])
        state.write_json("06_tensions", tn)
    tn = state.read_json("06_tensions")

    # 07 COMPOSE
    if _should_run(state, "07_compose", 7, args.from_step, no_resume):
        es = step_compose(state, ld, pl, tn, meta)
        state.write_text("07_compose", es)
    essay = state.read_text("07_compose")

    # 08-10: EDIT loop (handles 08 verify + 09 gap + 10 edit internally)
    if _should_run(state, "10_edit_final", 10, args.from_step, no_resume):
        final_essay = step_edit_loop(state, essay, ld)
        state.write_text("10_edit_final", final_essay)
    final_essay = state.read_text("10_edit_final")

    # 11 EMIT — read the *final* iteration of verify/gap, not iter1.
    verify = _final_iter_json(state, "08_verify")
    gap = _final_iter_json(state, "09_gap")
    out = step_emit(state, final_essay, ld, acq["transcript_segments"], meta, verify, gap)
    print(json.dumps(out, indent=2, ensure_ascii=False))


def _check_duration_cap(acq: dict, max_min: int) -> None:
    """Refuse sources longer than max_min — prevents an accidental 8-hour transcribe."""
    if max_min <= 0:
        return
    secs = (acq.get("meta") or {}).get("duration", 0) or 0
    if secs and secs / 60 > max_min:
        raise RuntimeError(
            f"Source duration {secs/60:.0f} min exceeds --max-duration-min={max_min}. "
            f"Re-run with --max-duration-min larger if intentional."
        )


if __name__ == "__main__":
    main()
