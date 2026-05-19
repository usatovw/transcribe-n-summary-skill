# TENSIONS + PRESUPPOSED BELIEFS — second-pass extraction

Run this AFTER 03_outline produced its plan. This is a focused second pass that surfaces what the first extraction missed:

1. **Tensions / disagreements / view updates** that LEDGER may have missed (subtle hedging, conditional agreement, "yes, but actually...")
2. **Presupposed beliefs** — things the guest must implicitly hold to make their statements coherent

This pass exists because EXTRACT was per-chunk and saw tensions only locally. Now you see the full ledger.

## Input

```json
{
  "ledger": <full canonical ledger>,
  "outline": <output of step 3>,
  "transcript_with_timestamps": "..."
}
```

## Output schema

```json
{
  "subtle_tensions": [
    {
      "id": "T_extra_001",
      "description": "...",
      "type": "host_pushback | guest_self_revision | unstated_disagreement | conditional_agreement",
      "claim_ids": ["C014", "C055"],
      "evidence_phrases": ["actual phrases like 'well, that's true but...', 'hmm, I want to push back here'"],
      "significance": "why this tension is interesting beyond the surface claim"
    }
  ],
  "presupposed_beliefs": [
    {
      "id": "P_001",
      "belief": "the implicit assumption (1 sentence)",
      "evidence": "what claims/quotes only make sense if this belief is held",
      "non_obviousness": 1-5,
      "why_interesting": "..."
    }
  ],
  "stance_shifts": [
    {
      "from": "speaker's position early in episode",
      "to": "speaker's position later (with timestamp)",
      "trigger": "what caused the shift if visible"
    }
  ]
}
```

## Rules

1. **Subtle tensions are textual**: look for hedge words ("well", "kind of", "I mean"), pushback markers ("but", "actually", "I'd push back"), self-correction ("wait, let me think about that").
2. **Presupposed beliefs ≠ paraphrase**. They are SECOND-ORDER. ✅ "Spiegel presupposes that distribution is the only durable moat" (because he keeps returning to it without arguing it). ❌ "Spiegel believes good design is important" (paraphrase, not presupposition).
3. **Non-obviousness ≥3 to include**. If it's something everyone in the field believes, it's not interesting.
4. **Stance shifts are rare**. Don't manufacture them. Only when speaker explicitly updates within the episode.

## Anti-patterns

- Don't list 10 presupposed beliefs. 2-4 is the right number for a 50-min interview.
- Don't restate what's already in `outline.presupposed_beliefs` — outline did a draft pass, you're refining it.
- Don't conflate "I'd love to think more about this" with stance shift. That's just curiosity.

Output ONLY JSON.
