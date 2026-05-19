---
source: media.rss.com/diveclub
source_type: podcast
auth_required: false
last_verified: 2026-04-30
fallback_chain: [rss_enclosure_direct, whisper_audio]
known_issues: []
---

# Playbook: DIVE Club 🤿 (by Ridd)

Design interview podcast. ~50-55 min English episodes. Latest guests include Polly D'Arcy (Wealthsimple), Brandon Jacoby, Brian Lovin, Ian Silber (OpenAI), Marvin Schwaibold (Shopify), Flora Guo, Kris Puckett, Josh Puckett, Luis Ouriach, Cameron Worboys, Jamey Gannon, Katie Dill (Stripe), Julien Martin (Amo), Tommy Smith, Ryan Stephen.

## URL recognition

```python
patterns = [
    r"https?://(?:www\.)?youtube\.com/@joindiveclub",  # YouTube — banned for our VPS, prefer RSS
    r"https?://media\.rss\.com/diveclub/.*",
    r"https?://content\.rss\.com/episodes/207227/.*",
]
```

If user gives YouTube URL — translate to RSS by matching episode title.

## Episode list (RSS, no auth)

```bash
curl -sS "https://media.rss.com/diveclub/feed.xml" -o /tmp/dive.xml
python3 <<'EOF'
import xml.etree.ElementTree as ET
t = ET.parse('/tmp/dive.xml')
items = t.find('channel').findall('item')
for it in items[:20]:
    title = it.find('title').text or ''
    pub = (it.find('pubDate').text or '')[:16]
    enc = it.find('enclosure')
    url = enc.attrib['url'] if enc is not None else ''
    dur = it.find('{http://www.itunes.com/dtds/podcast-1.0.dtd}duration')
    print(f"{title[:80]} | {pub} | {dur.text if dur is not None else '?'}s")
    print(f"  {url}")
EOF
```

## Audio download (no auth, direct curl)

```bash
curl -L --max-time 300 -o "${EPISODE_ID}.mp3" "$ENCLOSURE_URL"
```

55-min episode = ~50MB, ~3 sec download from rss.com CDN.

## Whisper config

```yaml
language: en  # podcast is English
initial_prompt: |
  DIVE Club design interview podcast hosted by Ridd.
  Topics: design craft, hiring, design leadership, AI tools for designers, taste, prototyping, design systems.
```

## Sample command (verified 2026-04-30, Polly D'Arcy episode)

```bash
# 1. Get episode metadata
curl -sS "https://media.rss.com/diveclub/feed.xml" \
  | python3 -c "..." | head -1
# title: "Polly D'Arcy - Going from IC to VP of Design at Wealthsimple"
# enclosure: https://content.rss.com/episodes/207227/2774321/...

# 2. Download
curl -L -o polly.mp3 "https://content.rss.com/episodes/207227/2774321/diveclub/2026_04_28_12_49_53_cbc35b60-66d1-4460-bd3d-d8fc769d87cb.mp3"

# 3. Pipeline as local file
.venv/bin/python pipeline.py /path/to/polly.mp3 --lang en
```

## Failure log

| Date | Failure | Cause | Fix |
|------|---------|-------|-----|
| 2026-04-30 | YouTube ban (vdYBohOQYm0) | datacenter IP ban | switched to RSS feed (no auth, no ban) |
