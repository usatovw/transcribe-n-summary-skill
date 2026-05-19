# GAP — what would a reader miss?

Independent check: if reader sees ONLY the essay, what important content from the transcript do they miss?

This is different from VERIFY (faithfulness). VERIFY asks "is what's in the essay true?". GAP asks "is what's important in the transcript also in the essay?".

## Input

```json
{
  "essay_md": "...",
  "ledger": <ledger with centrality scores>,
  "outline_non_inclusion": ["claims the outliner consciously dropped"]
}
```

## Output schema

```json
{
  "missed_high_value_claims": [
    {
      "claim_id": "C033",
      "text": "...",
      "centrality": 0.78,
      "novelty": 0.91,
      "why_should_be_included": "...",
      "suggested_section": "where in essay to add",
      "discretion": "include|optional|skip"  // skip if non_inclusion already explained
    }
  ],
  "missed_tensions": [...],
  "missed_anecdotes": [...],
  "missed_mental_models": [...],
  "essay_emphasis_drift": "if essay over-emphasizes low-centrality claims, name them",
  "compression_metric": {
    "top_quartile_centrality_total": <int>,
    "top_quartile_covered": <int>,
    "coverage_ratio": 0.0-1.0,
    "interpretation": "good|drift|undercoverage"
  },
  "verdict": "ACCEPTABLE|REVISE_NEEDED",
  "revise_priorities": [
    "1-line instructions for EDIT, ranked by impact"
  ]
}
```

## Rules

1. **Centrality + Novelty filter**: only flag claims with `centrality ≥ 0.5 AND novelty ≥ 0.5`. Anything lower can be legitimately dropped.
2. **Respect outline.non_inclusion**: if a claim was explicitly deprioritized, don't re-flag unless you disagree strongly (mark `discretion: optional` with rationale).
3. **Tensions: high bar**. Missing a tension is bigger sin than missing a claim — say so.
4. **Anecdotes/quotes**: missing a great verbatim quote is worth flagging if it has high centrality.
5. **Drift = essay weight on wrong things**. If essay devotes 200 words to a low-centrality side comment while ignoring high-centrality thesis — flag.

## Verdict

- `ACCEPTABLE`: coverage_ratio ≥ 0.70, no missed tensions of high significance.
- `REVISE_NEEDED`: anything else.

## Revise priorities

Top 3 actions for EDIT step. Ranked. Each 1 sentence.

Example: 
1. "Add 1 paragraph on C033 (Spiegel's anchor-AR vs heads-up framing) — currently absent, centrality 0.78."
2. "Replace section 3 listicle with prose on tension T002."
3. "Cut paragraph 5 on minor side topic — drifts from thesis."

Output ONLY JSON.
