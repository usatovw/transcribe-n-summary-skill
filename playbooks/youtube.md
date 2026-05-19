---
source: youtube.com
source_type: video
auth_required: true  # cookies или residential proxy
last_verified: 2026-04-30
fallback_chain: [manual_subs, auto_subs_en_only, whisper_audio]
known_issues:
  - VPS-IP бан → нужен cookies.txt или residential proxy
  - Auto-subs RU плохого качества → всегда whisper для RU
  - JS runtime warning → не критично, listing работает
---

# Playbook: YouTube

## URL recognition

```python
patterns = [
    r'^https?://(www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})',
    r'^https?://(www\.)?youtu\.be/([A-Za-z0-9_-]{11})',
    r'^https?://(www\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})',
    r'^https?://(www\.)?youtube\.com/@[A-Za-z0-9_-]+/videos/?$',  # channel listing
]
```

## Channel listing (без auth)

```bash
yt-dlp --flat-playlist --playlist-end "$N" \
  --match-filter "duration > 65" \
  --print "%(id)s|%(duration)s|%(title)s" \
  "https://www.youtube.com/@HANDLE/videos"
```

`/videos` отдаёт хронологически, последние сверху. Shorts фильтруются по `duration > 65`.

## Metadata extraction

```bash
yt-dlp --skip-download --print-json --cookies "$COOKIES_FILE" "$URL" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({
      'title': d['title'], 'channel': d['channel'],
      'upload_date': d['upload_date'], 'duration': d['duration'],
      'description': d.get('description','')[:1000],
      'chapters': d.get('chapters',[]),
      'lang': d.get('language','en'),
      'video_id': d['id'],
    }, indent=2, ensure_ascii=False))"
```

## Transcript fallback chain

### 1. Manual subtitles (best quality, rare)
```bash
yt-dlp --cookies "$COOKIES_FILE" --skip-download \
  --write-subs --sub-langs "ru,en" \
  --convert-subs srt -o "%(id)s.%(ext)s" "$URL"
```
Принимаем если файл `*.ru.srt` или `*.en.srt` существует и количество строк > duration_seconds * 0.05 (sanity).

### 2. Auto-subs (English only — Russian quality bad)
```bash
yt-dlp --cookies "$COOKIES_FILE" --skip-download \
  --write-auto-subs --sub-langs "en" \
  --convert-subs srt -o "%(id)s.%(ext)s" "$URL"
```
Russian auto-subs — **всегда отбрасываем**, fallback на whisper.

### 3. Whisper audio fallback
```bash
yt-dlp --cookies "$COOKIES_FILE" \
  -f "bestaudio[ext=m4a]/bestaudio" \
  -x --audio-format opus --audio-quality 5 \
  --download-archive "$ARCHIVE" \
  -o "%(id)s.%(ext)s" "$URL"
```
Then `ffmpeg -ar 16000 -ac 1 -af loudnorm=I=-16:TP=-1.5:LRA=11` → faster-whisper.

## Whisper config overrides

```yaml
language: en  # default for DIVE Club; auto-detect для unknown channels
initial_prompt: |
  Podcast interview about design and technology.
  Speakers: {host_name}, {guest_name}.
  Topics: design, product, AI, UX, UI.
  Companies mentioned: {from_description}.
```

`initial_prompt` собирается из metadata: title (имена) + description (компании, термины). Cap 244 tokens.

## Cookies setup

```bash
# Local browser export → ~/.config/yt-dlp/cookies.txt
# Use 'Get cookies.txt LOCALLY' Chrome/Firefox extension
# Logged-in YouTube session required

# Health-check (раз в сутки в cron)
yt-dlp --cookies ~/.config/yt-dlp/cookies.txt \
  --simulate "https://youtu.be/dQw4w9WgXcQ" \
  || echo "ALERT cookies refresh needed"
```

## Residential proxy (alternative to cookies)

```bash
export YT_PROXY="http://user:pass@host:port"
yt-dlp --proxy "$YT_PROXY" ...
```

## Known issues / failure log

| Date | Failure | Cause | Fix |
|------|---------|-------|-----|
| 2026-04-30 | "Sign in to confirm you're not a bot" | Vultr datacenter IP banned | use cookies or residential proxy |
| 2026-04-30 | JS runtime warning | yt-dlp 2026.x deprecation | install `deno` or `--js-runtimes node` (not blocking yet) |

## Sample command (DIVE Club, last verified 2026-04-30 listing, audio ban)

```bash
# Listing works without auth:
yt-dlp --flat-playlist --playlist-end 10 \
  --match-filter "duration > 65" \
  --print "%(id)s|%(duration)s|%(title)s" \
  "https://www.youtube.com/@joindiveclub/videos"

# Audio download requires cookies:
yt-dlp --cookies ~/.config/yt-dlp/cookies.txt \
  -f bestaudio -x --audio-format opus -o "%(id)s.%(ext)s" \
  "https://youtu.be/vdYBohOQYm0"
```
