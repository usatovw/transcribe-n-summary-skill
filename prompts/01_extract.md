# EXTRACT — element-aware atomic claim extraction

You are a transcript reader. Your output is a strict JSON schema, nothing else.

For the transcript chunk below, extract these elements. Be exhaustive within the chunk; do NOT paraphrase or generalize beyond what is said.

## Schema

```json
{
  "speakers": ["host", "guest"],
  "claims": [
    {
      "id": "c001",
      "text": "atomic claim, single proposition, paraphrased for clarity",
      "speaker": "host|guest",
      "verbatim_anchor": "exact substring from transcript that supports this claim",
      "ts_start": "HH:MM:SS",
      "ts_end": "HH:MM:SS",
      "type": "thesis|example|definition|opinion|fact|prediction"
    }
  ],
  "quotes": [
    {
      "id": "q001",
      "verbatim": "exact word-for-word from transcript",
      "speaker": "host|guest",
      "ts": "HH:MM:SS",
      "why_quotable": "what makes this line worth quoting (1 phrase): vivid framing, contrarian, dense formulation, etc."
    }
  ],
  "tensions": [
    {
      "id": "t001",
      "description": "what the speakers disagree on or where one updates view",
      "speaker_a_position": "...",
      "speaker_b_position": "...",
      "evidence_quote_ids": ["q003", "q007"]
    }
  ],
  "anecdotes": [
    {
      "id": "a001",
      "summary": "1-2 sentence story summary",
      "ts_start": "HH:MM:SS",
      "ts_end": "HH:MM:SS",
      "claim_supported": "c014"
    }
  ],
  "open_questions": [
    "questions raised but not answered in this chunk"
  ],
  "mental_models": [
    {
      "id": "m001",
      "name": "implicit framework or worldview the speaker operates in",
      "explicit_signals": ["..."],
      "speaker": "guest"
    }
  ],
  "named_entities": {
    "persons": [],
    "companies": [],
    "products": [],
    "concepts": []
  }
}
```

## Rules

1. **Atomic claims**: one proposition each. "X works at Y and believes Z" → split into two claims.
2. **Verbatim quotes**: byte-for-byte. If you "improve" punctuation, mark `[edited]`.
3. **No filler**: "yeah, right, you know, I mean" → exclude unless they signal something (hedging, surprise).
4. **Tension is rare**: only mark when there's actual disagreement, hedging, or view update. Don't invent it.
5. **Mental models — one per chunk maximum**, only if explicit signal. Don't read minds.
6. **Speaker labels**: use only `host` and `guest`. If multiple guests, append `_2`, `_3`.
7. **No interpretation**: do not infer "guest implies X". Stick to what is said. Implicit beliefs come in a later step.
8. **Output ONLY the JSON**, no preamble, no explanation.

## Quality bar

If the chunk has nothing extractable (intro music, ads), return:
```json
{"speakers": [], "claims": [], "quotes": [], "tensions": [], "anecdotes": [], "open_questions": [], "mental_models": [], "named_entities": {"persons":[],"companies":[],"products":[],"concepts":[]}}
```

Don't pad. Empty is fine.
