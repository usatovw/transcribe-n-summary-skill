# OUTLINE — plan-then-write structure

You receive the canonical ledger. Build an essay outline.

This is a **planning step**. Do NOT write prose. Output a structured plan that the COMPOSE step will execute.

## Input

```json
{
  "ledger": <full ledger from step 2>,
  "metadata": {"title": "...", "host": "...", "guest": "...", "duration": "...", "channel": "..."},
  "related_episodes": [<top-3 from memory>],
  "constitution": <style/constitution.md content>
}
```

## Output schema

```json
{
  "thesis": "single declarative sentence — the spine of the essay",
  "subtitle": "1-2 sentence framing for TL;DR",
  "lens": "the angle that makes this essay non-generic (e.g. 'product mechanics > visual design')",
  "sections": [
    {
      "h2": "section heading — declarative not generic",
      "claim_ids": ["C014", "C033"],
      "quote_ids": ["q007"],
      "anecdote_id": "a002",
      "draft_topic_sentence": "single sentence the section will open with"
    }
  ],
  "tensions_to_surface": ["t001", "t003"],
  "mental_models_to_name": ["m002"],
  "presupposed_beliefs": [
    "what the guest must implicitly believe to say what they said — only state if it's interesting and non-obvious"
  ],
  "cross_episode_thread": "one paragraph describing how related_episodes inform this essay, or empty if none",
  "non_inclusion_rationale": "list 2-3 important claims you DEPRIORITIZED and WHY (so reader of the plan sees the trade-off)"
}
```

## Rules

1. **Thesis is declarative**, not generic. ❌ "design is important". ✅ "Snapchat survived 15 years because Spiegel insists product mechanics beat visual design — and the screenshot notification proves it."
2. **Sections 4-6** total. Each backed by ≥2 claim_ids. Section without 2 supporting claims = cut it.
3. **H2 headings declarative**. "How they research" — bad. "Why surveys are useless and what replaces them" — good.
4. **Tensions are the meat**. If `ledger.tensions` is non-empty, at least one section is built around tension, not consensus.
5. **Mental models are the long tail**. Surface explicitly when implicit framework is unusual. Skip if it's just "good design = empathy".
6. **Cross-episode thread**: if you have related_episodes, name the thread (continuity, contrast, evolution). Don't force if irrelevant.
7. **Non-inclusion is mandatory**: every essay leaves things out. State 2-3 explicitly. This is a discipline check.

## Anti-patterns

- ❌ Generic chapter titles: "Background", "Lessons learned"
- ❌ Section without anchor: every section must point to specific ledger entries
- ❌ Throwing in every claim: discipline = saying no
- ❌ Cross-episode for every essay: only if there's a real thread

Output ONLY JSON.
