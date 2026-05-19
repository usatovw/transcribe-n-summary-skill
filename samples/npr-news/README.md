# Sample run — NPR News Now (5-min wire bulletin)

This is a real `transcribe` run, kept in the repo so you can see what the pipeline produces *before* you spend 30-90 min on your own first run.

## What was the input

| | |
|---|---|
| Source | [NPR News Now](https://podcasts.apple.com/us/podcast/npr-news-now/id121493675) — top-of-hour 5-min news bulletin |
| Episode | NPR News: 05-19-2026 2AM EDT (4:40 audio) |
| URL given to pipeline | `https://podcasts.apple.com/us/podcast/npr-news-now/id121493675` |
| Language detected | English (prob 1.00) |
| Output language | Russian (default) |

## How long did it take

~13 minutes end-to-end on CPU:

| step | time |
|---|---|
| ACQUIRE (curl + ffmpeg) | 16 s |
| Whisper `medium` int8, 4:40 audio | 5:15 |
| CHUNK (sentence-transformers MiniLM) | 12 s |
| EXTRACT (4 chunks, Opus 4.7, semaphore=3) | ~2 min |
| LEDGER / PLAN / TENSIONS / COMPOSE / VERIFY / GAP | ~5 min |
| EDIT loop (iter1 → PASS) | 0 retries needed |

## What the pipeline reported

`verify.json` summary:
- `verdict: PASS`
- `supported_pct: 1.0`
- `contradicted_count: 0`
- `quote_ratio: 0.126` (target 0.12-0.15)
- `coverage: 1.0`
- `barnum_flagged: 0`

`gap.json`: `verdict: ACCEPTABLE`, `compression: good`, 0 missed claims.

## What's good

- 41 atomic claims extracted from 4:40 audio; quotes byte-faithful with one micro-edit flagged
- Real thesis (not "NPR covers four stories") — argues that 3 of 4 stories close accountability questions structurally, contrast with Ebola's named-everyone story
- Inline `[c14]` citations resolve to real ledger entries
- No bullets in body, voice is observer not Wikipedia

## What's flawed (honest)

The output reviewer found two real problems the pipeline's own self-check missed:

1. **Timestamp drift**: several `[t=HH:MM:SS]` in the essay drift 5–25 seconds from the actual transcript.srt. The ledger inherited drifted timestamps from the per-chunk EXTRACT step. This is now caught by `prompts/06_verify.md` (timestamp_drift check) — on a fresh run you'd see those flagged FAIL, the EDIT loop would correct them.
2. **Ambition vs. source**: a 4:40 wire-news bulletin doesn't support claims about NPR's *editorial design* or *systematic patterns*. This is now caught by the same prompt (`ambition_vs_source: OVERREACH` for sources <10 min that make systemic claims).

This is what "samples worth shipping" looks like — a real output, honestly annotated with what works and what doesn't.

## Reproduce

```bash
python pipeline.py "https://podcasts.apple.com/us/podcast/npr-news-now/id121493675"
```

(NPR News Now is a rolling feed — you'll get a different bulletin than this one.)
