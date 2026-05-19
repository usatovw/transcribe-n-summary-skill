---
source: rss
source_type: podcast
auth_required: false
last_verified: 2026-04-30
fallback_chain: [rss_enclosure_direct, whisper_audio]
known_issues:
  - Некоторые feeds requires User-Agent
---

# Playbook: Direct RSS

## URL recognition

```python
patterns = [
    r'^https?://.+\.(rss|xml)$',
    r'^https?://.+/feed/?$',
    r'^https?://.+/feed\.xml$',
]
# Also: when user explicitly says "RSS feed" or pastes feed URL
```

## Episode list

```bash
curl -sS -A "Mozilla/5.0" "$FEED_URL" > feed.xml
python3 <<'EOF'
import sys, xml.etree.ElementTree as ET
tree = ET.parse('feed.xml')
ns = {'itunes':'http://www.itunes.com/dtds/podcast-1.0.dtd'}
for item in tree.findall('.//item')[:20]:
    title = item.find('title').text
    enc = item.find('enclosure')
    url = enc.attrib['url'] if enc is not None else ''
    pub = item.find('pubDate').text if item.find('pubDate') is not None else ''
    dur = item.find('itunes:duration', ns)
    dur_text = dur.text if dur is not None else ''
    print(f"{title}|{pub}|{dur_text}|{url}")
EOF
```

## Audio download

```bash
curl -L -o "${EPISODE_ID}.mp3" "$ENCLOSURE_URL"
ffmpeg -i "${EPISODE_ID}.mp3" -ar 16000 -ac 1 \
  -af loudnorm=I=-16:TP=-1.5:LRA=11 "${EPISODE_ID}.wav"
```

## Whisper

Auto-detect language. Initial prompt из feed metadata (show title, host если в `itunes:author`).

## Sample (verified 2026-04-30)

Lenny's Podcast feed:
```
https://anchor.fm/s/22951cdc/podcast/rss
```
