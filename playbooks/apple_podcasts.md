---
source: podcasts.apple.com
source_type: podcast
auth_required: false
last_verified: 2026-04-29  # Lenny's Podcast / Spiegel episode
fallback_chain: [rss_enclosure_direct, whisper_audio]
known_issues:
  - Apple Podcasts не имеет публичного transcript API
  - RSS URL иногда скрыт; нужен parse страницы эпизода
---

# Playbook: Apple Podcasts

## URL recognition

```python
patterns = [
    r'^https?://podcasts\.apple\.com/[a-z]{2}/podcast/[^/]+/id\d+',
    r'^https?://podcasts\.apple\.com/[a-z]{2}/podcast/[^/]+/id\d+\?i=\d+',  # specific episode
]
```

## Metadata extraction

Распарсить страницу эпизода — нет JSON API, есть `<meta>` теги и schema.org JSON-LD.

```bash
curl -sS -A "Mozilla/5.0" "$URL" \
  | python3 -c "
import sys, re, json
html = sys.stdin.read()
# Try schema.org JSON-LD
m = re.search(r'<script type=\"application/ld\+json\">(.+?)</script>', html, re.S)
if m:
    d = json.loads(m.group(1))
    print(json.dumps({
        'title': d.get('name',''),
        'description': d.get('description',''),
        'duration_iso': d.get('timeRequired',''),
        'date': d.get('datePublished',''),
        'show': d.get('partOfSeries',{}).get('name','') if isinstance(d.get('partOfSeries'), dict) else '',
    }, indent=2, ensure_ascii=False))
"
```

## RSS feed extraction

Apple-side нет direct download. Найти RSS подкаста (часто в описании показа):

```bash
# 1. iTunes Search API даёт feedUrl by collectionId
COLLECTION_ID=$(echo "$URL" | grep -oE 'id[0-9]+' | head -1 | tr -d 'id')
curl -sS "https://itunes.apple.com/lookup?id=$COLLECTION_ID&entity=podcast" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['feedUrl'])"

# 2. Затем парсить RSS, найти конкретный эпизод по title или GUID
curl -sS "$FEED_URL" \
  | python3 -c "
import sys, xml.etree.ElementTree as ET
# Find <item><enclosure url='...'> matching episode title
"
```

## Audio acquisition

```bash
# Direct curl с RSS enclosure URL (нет ban на Apple-side)
curl -L -o "${EPISODE_ID}.mp3" "$ENCLOSURE_URL"

# Convert to whisper-friendly format
ffmpeg -i "${EPISODE_ID}.mp3" \
  -ar 16000 -ac 1 \
  -af loudnorm=I=-16:TP=-1.5:LRA=11 \
  "${EPISODE_ID}.wav"
```

## Whisper config overrides

```yaml
language: auto-detect  # подкасты могут быть на разных языках
initial_prompt: |
  Podcast: {show_title}.
  Episode: {episode_title}.
  Host: {host}.
  Guest: {guest_if_known}.
```

## Sample command (verified 2026-04-29, Lenny's Podcast / Spiegel)

```bash
# Получили feed:
COLLECTION_ID=1577262226  # Lenny's Podcast
FEED_URL=$(curl -sS "https://itunes.apple.com/lookup?id=$COLLECTION_ID&entity=podcast" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['feedUrl'])")

# Скачали эпизод (Spiegel, ~67MB, 70 min)
curl -L -o spiegel.mp3 "$ENCLOSURE_URL"

# Транскрибировали через chunks по 5 мин (см. archive)
# В новой версии — single-pass через faster-whisper большая-3-turbo, без внешнего chunking
```

## Failure log

| Date | Failure | Cause | Fix |
|------|---------|-------|-----|
| 2026-04-29 | small whisper модель = ошибки в именах | model size insufficient | upgrade to large-v3-turbo + initial_prompt |
| 2026-04-29 | external chunking без overlap → обрывы | manual ffmpeg cut | drop external chunking, use faster-whisper native |
