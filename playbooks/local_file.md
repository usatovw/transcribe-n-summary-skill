---
source: local
source_type: any
auth_required: false
last_verified: 2026-04-30
fallback_chain: [whisper_audio]
known_issues: []
---

# Playbook: Local file

## Recognition

Когда `/transcribe path/to/file` или path detected.

```python
audio_exts = {'.mp3', '.opus', '.m4a', '.wav', '.ogg', '.flac'}
video_exts = {'.mp4', '.mkv', '.webm', '.mov'}
```

## Metadata extraction

```bash
ffprobe -v quiet -print_format json -show_format -show_streams "$FILE" \
  | python3 -c "..."
```

Если video → `ffmpeg -vn -acodec copy` для извлечения audio.

## Whisper

Auto-detect language. Initial prompt пустой по умолчанию; пользователь может задать через `--prompt "..."`.

## Sample

```bash
.venv/bin/python pipeline.py /path/to/recording.mp3 --lang ru --prompt "Встреча про дизайн систем. Участники: Иван, Петр."
```
