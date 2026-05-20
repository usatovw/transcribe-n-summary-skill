---
name: transcribe
description: "Transcribe and summarize long audio/video sources (YouTube, Apple Podcasts, RSS, local files) into Russian slow-journalism essay-longreads. Linear-Refine pipeline with claim ledger, plan-then-write, Chain-of-Verification, gap analysis. Use for /transcribe URL or /transcribe path."
user_invocable: true
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
---

# /transcribe — pipeline

Processes long audio/video into essay-longreads in slow-journalism style. The pipeline is linear, 9 steps, with a checkpoint after each — any step can be restarted without losing prior work.

**Default output language is Russian.** See [README.md → Configuration](./README.md#configuration) for switching to English (requires English gold essays in `style/gold/`).

## Trigger

- `/transcribe URL` — YouTube, Apple Podcasts, RSS feed
- `/transcribe path/to/file` — local audio or video

## Architecture

**Linear-Refine** ([Anthropic 2024](https://www.anthropic.com/research/building-effective-agents) + [Cognition 2025](https://cognition.ai/blog/dont-build-multi-agents)): single agent with long memory + sequential refinement. Hierarchical orchestrator-workers cause context fragmentation on a linear task; we don't use them.

**Primary artifact — the claim ledger** (`output/claim_ledger.json`), not the prose. `summary.md` (essay) and `key_takeaways.md` (top-10 list) are both *derivations* from the same ledger. You can write your own derivation (tweet thread, abstract) over the same JSON.

## Pipeline (9 steps with checkpoints)

```
0. RESOLVE     URL → source type → handler (SOURCE_HANDLERS registry)
1. ACQUIRE     fetch audio + metadata; whisper-transcribe if no subs
               YouTube preflight: bails early without cookies/proxy
2. CHUNK       embedding-cosine TextTiling (sentence-transformers MiniLM)
3. EXTRACT     parallel per chunk (semaphore=3, asyncio.gather):
               element-aware checklist (Wang et al. 2305.13412):
               speakers, atomic_claims, verbatim_quotes, ts,
               tensions, anecdotes, open_questions, mental_models
4. LEDGER      one LLM call: dedup + score novelty/centrality (LLM-estimated)
               + build claim-supports-claim graph + identify top_central claims
5. PLAN        outliner: thesis + 4-6 sections, evidence_ids ≥2 per section
               + tension extraction
               + presupposed-beliefs pass
               + cross-episode retrieval (embedding cosine over channel+title+top-claims)
6. COMPOSE     slow-journalism essay, gold-anchored,
               inline [c14] citations, 12-15% verbatim quote ratio,
               anti-Barnum filter, ts annotations from transcript (not ledger)
7. VERIFY      Chain-of-Verification (Dhuliawala 2309.11495):
               each sentence → SUPPORTED / UNSUPPORTED / CONTRADICTED
               Pass: ≥95% supported, 0 contradicted, ts drift ≤10s,
               ambition_vs_source EARNED, coverage ≥70% top-quartile claims
8. EDIT loop   ≤3 iterations; stop when verify+gap pass OR diff <5%
9. EMIT        output/{summary.md, key_takeaways.md, claim_ledger.json,
               transcript.{srt,json}, meta.json, verify.json, gap.json}
```

## State checkpoints

Each step writes `state/{video_id}/{step_NN}.json|md`. Resume is on by default — corrupt or absent checkpoints re-run automatically. `--from-step N` forces re-run from step N and **invalidates downstream checkpoints** so a re-extract doesn't leave a stale ledger/plan on disk.

State and output live in the **caller's working directory** at `<cwd>/transcribe/{video_id}/`, not in the skill folder. The skill folder is code-only. Cross-run episodic memory lives in `~/.local/share/transcribe/episodes.jsonl` (override via `TRANSCRIBE_DATA_DIR`).

## Skill files

| File | Purpose |
|------|---------|
| `pipeline.py` | orchestrator for steps 1-9 with SOURCE_HANDLERS registry |
| `source_router.py` | URL/path → source type |
| `prompts/01_extract.md` … `08_edit.md` | system prompts per step |
| `style/gold/` | reference essays (≥3 in your voice before production) |
| `style/constitution.md` | 12 style rules |
| `style/anti_barnum.md` | Campbell-Barnum filter test |
| `playbooks/{youtube,apple_podcasts,rss,local_file}.md` | per-source documentation |
| `playbooks/_learned/` | playbooks for sources you add (DIVE Club example included) |
| `filters/whisper_hallucinations.txt` | regex-blacklist (post-Whisper) |
| `config.yaml` | knobs (Whisper, chunking, claude model, verify thresholds) |

## How to run

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
# OR have `claude` CLI in $PATH (pipeline auto-detects, prefers SDK)

python pipeline.py "https://podcasts.apple.com/us/podcast/.../id..."
python pipeline.py "https://youtu.be/VIDEO_ID"        # needs cookies on VPS IPs
python pipeline.py /path/to/local.mp3 --lang en
python pipeline.py URL --from-step 4                  # re-run from LEDGER onward
python pipeline.py URL --no-resume                    # full re-run
python pipeline.py URL --out-dir /custom/path
python pipeline.py URL --max-duration-min 60          # refuse sources >60 min
```

## Using as a Claude Code skill

Drop this repo under `~/.claude/skills/`:

```bash
git clone https://github.com/usatovw/transcribe-n-summary-skill.git ~/.claude/skills/transcribe
# Then in Claude Code: /transcribe <url-or-path>
```

When invoked as a skill, the agent reads this file and runs `pipeline.py` for you via Bash.

## Bootstrap requirements

Output quality is gated on three artifacts the repo ships sparsely:

- `style/gold/` should contain **≥3 reference essays** in your target voice. Mode-collapse risk with N=1. The pipeline logs a warning at startup if it sees fewer than 3.
- `playbooks/youtube.md` should be verified against one live URL on your network (YouTube anti-bot behavior is IP-dependent).
- `prompts/06_verify.md` thresholds (timestamp drift tolerance, ambition_vs_source rule) should be calibrated against ≥1 successful run with manual review.

## When the source is unknown

For URLs that don't match any built-in pattern, the pipeline exits with a clear message asking you to add a playbook under `playbooks/_learned/` and a regex in `source_router.PATTERNS` + a handler with `@register_source("<name>")` in `pipeline.py`. Automatic playbook drafting from a sample fetch is **on the roadmap, not implemented**.

## Honest about what's implemented vs. promised

- ✅ **Claim ledger is the primary artifact** — `summary.md` and `key_takeaways.md` are both derivations; you can write more.
- ✅ **Chain-of-Verification with timestamp drift check** — `verify.json` reports per-sentence labels, quote ratio, coverage, AND per-quote `[t=...]` drift vs SRT, AND `ambition_vs_source` (overreach detection for short sources).
- ✅ **Episodic memory with similarity retrieval** — `~/.local/share/transcribe/episodes.jsonl` accumulates, `step_plan` retrieves top-K related episodes by embedding cosine over (channel + title + top-claims).
- ✅ **Whisper partial checkpoint** — `01_whisper.partial.json` lets a crashed Whisper run resume without redoing 30 min of inference.
- ✅ **SDK + CLI dual backend** — picks Anthropic SDK if `ANTHROPIC_API_KEY` set, else `claude` CLI subprocess. Logged at startup.
- ⚠️ **Centrality / novelty scoring** — described as "PageRank / TF-IDF rarity" conceptually. The actual implementation has the LEDGER LLM call estimate both via term-overlap and graph-edge heuristics in a single pass, not separate PageRank/TF-IDF runs. The numbers in `ledger.json` are useful for ranking but don't have the formal-algorithm precision the prompt suggests.
- ⚠️ **Playbooks** — `playbooks/*.md` are documentation (bash snippets, known issues). Runtime dispatch is via `SOURCE_HANDLERS` registry in `pipeline.py`; playbooks don't drive behavior — they describe it for humans adding new sources.
- ❌ **Self-evolving playbook drafting** — not implemented.

## Pitfalls / Known issues

### Long runs need a detached terminal

A 60-min podcast end-to-end takes 30-90 min. Launch in `tmux`:

```bash
tmux new-session -d -s transcribe \
  "python pipeline.py /path/to/audio.mp3 2>&1 | tee pipeline.log"
tmux attach -t transcribe  # Ctrl+B, D to detach
```

### Whisper checkpointing

Whisper takes ~30 min for ~60-min audio on CPU. `_whisper_transcribe` writes `state/<id>/01_whisper.partial.json` every 50 segments with a `done` flag. On rerun: if `done: true`, segments are reused.

### Whisper hallucination control

- `condition_on_previous_text: false` — critical against repetition hallucinations
- Don't pre-chunk audio — `faster-whisper` handles long-form natively via VAD
- Russian auto-subs from YouTube are unreliable — always Whisper Russian
- Post-Whisper filter (`filters/whisper_hallucinations.txt`) removes common artifacts ("Subscribe", "[Music]", attribution lines from subtitle communities)

### Whisper model selection by free RAM

| Free RAM | Model | Peak RSS |
|---|---|---|
| 2-4 GB | `small` | ~700 MB |
| 4-6 GB | `medium` (default) | ~1.5 GB |
| ≥6 GB or GPU | `large-v3-turbo` | ~3 GB |

Add ≥2 GB swap if tight — Whisper peaks past resident size during inference.

### YouTube anti-bot

Datacenter / cloud-VPS IPs are flagged. Pipeline preflight detects "no cookies + no proxy" before Whisper and bails. Setup one of:

1. Cookies — `~/.config/yt-dlp/cookies.txt` (export via "Get cookies.txt LOCALLY")
2. Proxy — `export YT_PROXY=http://user:pass@host:port`

For podcast content prefer the RSS feed when available.

### Source-specific notes

- **Apple Podcasts**: metadata via `itunes.apple.com/lookup?id=<collectionId>&entity=podcast` → `feedUrl`; episode mp3 from `<enclosure url>` direct download.
- **rss.com hosting**: enclosure URLs work via direct download.

### Network safety

- All RSS XML parsed via `defusedxml` (billion-laughs / external-entity protection).
- `curl` calls cap response size (`--max-filesize`) and refuse non-https.
- `feedUrl` from iTunes is re-validated for `https://` scheme before fetch.
