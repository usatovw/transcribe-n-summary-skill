# LEDGER — dedup, score, build claim graph

You receive raw extracts from N chunks. Build a single canonical claim ledger.

## Input

```json
{
  "extracts": [<chunk_extract_1>, <chunk_extract_2>, ...],
  "prior_episode_claims": [<top claims from memory/episodes.jsonl, if any>]
}
```

## Output schema

```json
{
  "ledger": [
    {
      "id": "C001",  // canonical, capital C
      "text": "...",
      "speaker": "guest",
      "verbatim_anchor": "...",
      "ts": "HH:MM:SS",
      "type": "...",
      "merged_from": ["c001", "c042"],  // chunk-level IDs
      "novelty": 0.0-1.0,  // vs prior_episode_claims
      "centrality": 0.0-1.0,  // PageRank on supports/contradicts graph
      "supports": ["C034"],
      "contradicts": ["C055"]
    }
  ],
  "quotes": [<canonical quotes, dedup>],
  "tensions": [<canonical, with C-prefix evidence_ids>],
  "mental_models": [<canonical, dedup by name>],
  "graph_summary": {
    "nodes": <int>,
    "edges": <int>,
    "top_central": ["C001", "C014", ...]  // top 10 by centrality
  }
}
```

## Rules

1. **Dedup claims**: if two claims have ≥80% semantic overlap, merge. Pick the most precise wording. List all source IDs in `merged_from`.
2. **Speaker conflict**: if the same claim appears from different speakers, keep separate entries (it's significant who said it).
3. **Novelty scoring**: 1.0 = no prior episode mentioned anything similar; 0.0 = exact match with prior. Use term overlap heuristic.
4. **Build graph**: scan claims for explicit supports/contradicts (markers: "exactly", "but actually", "I disagree", "this is why"). Don't invent edges.
5. **Centrality**: simulate PageRank with damping=0.85 over supports edges. Top 25% = "high centrality".
6. **Quote dedup**: byte-equal verbatim → merge. Slight variations (punctuation only) → also merge, keep cleanest.
7. **Tensions canonical**: if same tension surfaces in multiple chunks, merge; but keep `evidence_quote_ids` from all sources.

## Anti-patterns

- Don't merge claims that share words but mean different things ("design is important" vs "design is the moat")
- Don't pad novelty with claims that are simply common knowledge — those are 0.1, not 0.5
- Don't create graph edges based on topic similarity. Only explicit logical relationships.

Output ONLY JSON.
