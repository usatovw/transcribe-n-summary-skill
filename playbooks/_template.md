---
source: example.com
source_type: video|podcast|article|voice
auth_required: false
last_verified: YYYY-MM-DD
fallback_chain: [manual_subs, auto_subs, whisper_audio]
known_issues: []
---

# Playbook: {source name}

## URL recognition

Pattern: `re.compile(r'^https?://(www\.)?example\.com/...')` — какие URL формы относятся к этому источнику.

## Metadata extraction

Команда / функция которая возвращает: `{title, author, upload_date, duration_seconds, description, chapters?, lang?}`.

```bash
# example
curl -sS "$URL" | python3 -c "..."
```

## Transcript / audio acquisition

### 1. Manual subtitles (если есть)
```bash
# command
```

### 2. Auto-generated subtitles
```bash
# command
```

### 3. Audio download → faster-whisper
```bash
# command
```

## Whisper config overrides

Что переопределяется относительно дефолтов в `config.yaml`:
- `language`: ...
- `initial_prompt`: ...
- `hotwords` (avoid если возможно): ...

## Known issues

- ...

## Sample command (verified working)

```bash
# polly_darcy_episode example
```

## Failure log

| date | failure | cause | fix |
|------|---------|-------|-----|
| 2026-04-30 | 403 on download | datacenter IP ban | added cookies fallback |
