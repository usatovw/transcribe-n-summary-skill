# transcribe

**Длинное аудио/видео → эссе-лонгрид в стиле slow journalism, с проверенными цитатами и каноническим claim ledger.**

60-минутный подкаст или YouTube-интервью → 800–1200 слов эссе на русском, со ссылками `[c14]` на источники и таймкодами `[t=12:34]`. Каждое предложение прошло Chain-of-Verification против транскрипта.

Работает с YouTube, Apple Podcasts, RSS-фидами и локальными файлами.

> **Default output language is Russian.** Это часть дизайна — стилевые анкеры в `style/gold/` русские, антибарнум-эвристика откалибрована на русскую прозу. Для английского нужно: `compose.output_lang: en` в `config.yaml` + положить английские эссе в `style/gold/`. См. [Configuration](#configuration).

## Чем отличается от «whisper + ChatGPT»

| | этот pipeline | whisper + LLM ad-hoc |
|---|---|---|
| Output | эссе со claim-уровневой проверкой каждого предложения | пересказ без верификации |
| Источник истины | `claim_ledger.json` — атомарные claims с centrality / novelty / supports-graph | сама прокатка |
| Faithfulness | Chain-of-Verification: каждое предложение SUPPORTED/UNSUPPORTED/CONTRADICTED + ≥95% supported для PASS | "trust the LLM" |
| Цитаты | byte-for-byte из транскрипта, 12–15% от слов эссе, таймкоды сверены с SRT | свободный пересказ |
| Anti-Barnum | фильтр generic-утверждений ("X подчёркивает важность фокуса") | typical LLM tone |
| Resume | checkpoint после каждого из 9 шагов; пайплайн рестартуется без потери дорогих фаз (Whisper, extract) | начинай заново |
| Cross-episode | episodic memory с embedding-similarity retrieval; параллели между выпусками одного шоу | нет |

## Пример вывода

Сэмпл реального прогона (5-минутный NPR News Now → русский эссе): [`samples/npr-news/`](./samples/npr-news/).

Фрагмент начала:

```markdown
# Четыре сюжета, три процедурных тупика и одна вспышка Эболы

*Пятиминутный ночной выпуск NPR 19 мая 2026 года устроен так, что три из четырёх
его историй — о механизмах, которые закрывают вопрос «по чьему поручению?»
раньше, чем его успевают задать.*

Минюст США объявил в понедельник о фонде почти на 1,8 миллиарда долларов [c1][c3] —
и одна структурная деталь съедает все остальные: «it's not clear if identities of
people to win from this fund will ever be reported in public» [t=00:00:50] [c7]. …
```

## Quickstart

```bash
git clone https://github.com/usatovw/transcribe-n-summary-skill.git
cd transcribe
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**System dependencies:**

| OS | команда |
|---|---|
| Debian/Ubuntu | `sudo apt install ffmpeg && pip install -U yt-dlp` |
| macOS (Homebrew) | `brew install ffmpeg yt-dlp` |
| Windows | используй [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) — Whisper и yt-dlp в нативном Windows работают, но мы не тестировали |

**Claude auth** — нужно одно из двух:

| вариант | как настроить | особенности |
|---|---|---|
| Anthropic API key (рекомендуется) | `export ANTHROPIC_API_KEY=sk-ant-...` ([console.anthropic.com](https://console.anthropic.com/)) | metered; включает prompt caching → дешевле на multi-call pipeline |
| `claude` CLI ([Claude Code](https://claude.com/claude-code)) | установить CLI, авторизоваться | использует существующую подписку Pro/Max; без prompt-caching; подспорье если ключа нет |

Pipeline сам выберет API key если он есть, иначе fallback на CLI.

**Первый запуск:**

```bash
python pipeline.py "https://podcasts.apple.com/us/podcast/some-podcast/id123456789"
```

> ⚠️ **Первый запуск качает ~2 GB моделей**: PyTorch + sentence-transformers MiniLM (~500 MB) + Whisper `medium` (~1.5 GB). HuggingFace cache в `~/.cache/huggingface/`. Это разовая загрузка.

## Output

Артефакты в `./transcribe/<video_id>/output/`:

```
output/
├── summary.md          # эссе + appendix цитат
├── key_takeaways.md    # деривация из ledger — top-10 claims списком
├── claim_ledger.json   # каноничный ledger: claims, quotes, tensions, graph
├── transcript.srt      # таймкодированный транскрипт
├── transcript.json     # raw сегменты Whisper
├── meta.json           # title, channel, duration, language
├── verify.json         # per-sentence Chain-of-Verification
└── gap.json            # coverage analysis: что не вошло
```

`summary.md` — одна из деривация ledger; `key_takeaways.md` — другая. Можно написать свою (тред в твиттер, краткий abstract) поверх того же `claim_ledger.json` — это и есть смысл "ledger as primary artifact".

## Как Claude Code skill

Репо одновременно — [Claude Code](https://claude.com/claude-code) skill:

```bash
git clone https://github.com/usatovw/transcribe-n-summary-skill.git ~/.claude/skills/transcribe
```

В Claude Code: `/transcribe <url-or-path>`.

## Configuration

`config.yaml` сгруппирован: сверху часто-меняемое, снизу — тюнинг.

**Часто:**
- `claude.model_per_step` — pipeline по умолчанию **смешивает модели**: schema-driven шаги (`extract/ledger/plan/gap`) на Sonnet 4.6, craft/judgment шаги (`tensions/compose/verify/edit`) на Opus 4.7. Это ~40% быстрее и дешевле full-Opus pipeline без видимой потери качества эссе. Для production-quality пиши всё на `claude-opus-4-7`. Для дешёвых smoke-test'ов — на `claude-haiku-4-5` (real quality drop).
- `whisper.model` — `medium` (1.5 GB peak RAM, безопасно для 4 GB). `small` если памяти мало. `large-v3-turbo` если ≥6 GB или GPU. **Pipeline сам downgrade'ит** под доступную RAM + переключается на chunked mode для long-form (>60 min) — см. preflight в `pipeline.py`.
- `compose.output_lang` — `ru` по умолчанию. `en` если положил английские gold-эссе.
- `youtube.cookies_file` — путь к cookies.txt если YouTube блокирует твой IP (см. [Troubleshooting](#troubleshooting)).

**Стоимость и скорость:**
- С **`ANTHROPIC_API_KEY`** (SDK путь) — system prompts кешируются (`cache_control: ephemeral`), повторные верифай/эдит итерации в 2-3× дешевле. Это правильный путь для opensource юзеров.
- С **`claude` CLI fallback** — caching недоступен (CLI `--print` не expose `cache_control`). Работает, но медленнее и дороже. Используй если нет API key и есть Claude Code MAX/Pro.

**Тюнинг:** `whisper.vad_*`, `chunking.cosine_threshold`, `extract.semaphore`, `compose.verbatim_quote_ratio_*`, `verify.faithfulness_min`, `verify.edit_loop_max`.

## Bootstrap (важно перед production)

Качество сильно зависит от трёх вещей:

1. **`style/gold/`** — репо ставит **один** русский пример (`for-designers.md`) для демонстрации. **Положи ≥3 эссе в твоём целевом голосе** перед production. С N=1 модель скатывается в mode-collapse.
2. **`playbooks/youtube.md`** — проверь на одной живой YouTube ссылке от твоего IP. Anti-bot поведение IP-зависимое.
3. **`prompts/06_verify.md`** — откалибруй на ≥1 успешном эссе с ручным review (особенно threshold по timestamp_drift и ambition_vs_source).

При старте pipeline покажет warning если `style/gold/` имеет меньше 3 файлов.

## Добавление нового источника

YouTube/Apple/RSS/local file — встроены. Для нового источника (Spotify, Castbox, Yandex Music и т.д.):

1. Добавь playbook под `playbooks/_learned/<name>.md` (см. `playbooks/_template.md`).
2. Добавь regex в `PATTERNS` в `source_router.py`.
3. Напиши `@register_source("<name>")` функцию в `pipeline.py` рядом с существующими `_acquire_*` — она возвращает `{"audio_path": ..., "meta": ..., "needs_whisper": True}`.

См. `playbooks/_learned/diveclub.md` как живой пример (DIVE Club по RSS).

**Yandex Music**: публичного API без OAuth нет. Альтернатива — скачать mp3 вручную, прогнать как local file: `python pipeline.py /path/to/episode.mp3 --lang ru`.

## Troubleshooting

### YouTube: "Sign in to confirm you're not a bot"
Твой IP в datacenter blacklist YouTube. Pipeline ловит это в pre-flight и не запускает Whisper зря. Нужно одно из:

- **Cookies** — экспортируй сессию через "Get cookies.txt LOCALLY" extension (Chrome/Firefox), сохрани в `~/.config/yt-dlp/cookies.txt`.
- **Residential proxy** — `export YT_PROXY=http://user:pass@host:port`.

Альтернатива: если это подкаст с YouTube, найди его RSS через `https://itunes.apple.com/search?term=<name>&entity=podcast` и используй Apple Podcasts URL — там no anti-bot.

### Whisper убит OOM
`config.yaml` → `whisper.model: small` (≈700 MB peak). Если всё равно умирает — добавь swap:
```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
```

### Первый pip install зависает
Это не зависание — `sentence-transformers` тянет PyTorch (~500 MB) и `faster-whisper` тянет CUDA-зависимости на Linux. Жди 2–5 мин на быстром интернете.

### Whisper грузит модель из HuggingFace каждый раз
Модели кэшируются в `~/.cache/huggingface/`. Если кэш чистится — поставь `HF_HOME=/persistent/path/huggingface_cache`.

### Cookies истекли
Симптом: `yt-dlp` начинает падать с 403/anti-bot после рабочего периода. YouTube session cookies живут ~30 дней. Перевыгрузи их.

### Pipeline лопается посередине
Любой шаг рестартуется без потери предыдущих:
```bash
python pipeline.py <url> --resume  # это по умолчанию
python pipeline.py <url> --from-step 4  # форсирует rerun начиная с шага 4
python pipeline.py <url> --no-resume  # полный rerun
```
`--from-step N` автоматически инвалидирует чекпоинты шагов ≥N (downstream).

### Длинные пробеги
```bash
tmux new-session -d -s transcribe \
  "python pipeline.py /path/to/long.mp3 2>&1 | tee pipeline.log"
tmux attach -t transcribe  # Ctrl+B, D — detach
```

## Limitations (честно)

- **60-минутное аудио = 30–90 минут end-to-end на CPU**, Whisper доминирует. С GPU Whisper падает до ~3 мин.
- **Unknown source URLs**: автоматический playbook drafting **не реализован** (есть в roadmap). Для нового источника — manual playbook в `_learned/` + regex в `source_router.PATTERNS`.
- **Episodic memory**: cross-episode retrieval работает (embedding cosine over channel+title+claims), но матчит только если канал/имя источника совпадает примерно. Совсем разные шоу не свяжет.
- **TF-IDF novelty и PageRank centrality** упомянуты в SKILL.md в концептуальной части — на практике novelty/centrality сейчас оценивает LLM в одном вызове на step LEDGER (term-overlap heuristic), не отдельный TF-IDF/PageRank pass. Метрики попадают в ledger и используются дальше, но реализация проще чем академическая формулировка.
- **Russian YouTube auto-subs ненадёжны** — для RU контента Whisper-им всегда.
- **YouTube anti-bot** на datacenter IP — нужны cookies или residential proxy.
- **Короткие источники (<10 мин)**: эссе по дизайну сдержано в обобщениях, ambition_vs_source check в `verify` помечает overreach как FAIL.

## Architecture deep-dive

См. [`SKILL.md`](./SKILL.md): полная диаграмма 9 шагов, prompt responsibilities, prior art ([Linear-Refine](https://www.anthropic.com/research/building-effective-agents), [CoVe](https://arxiv.org/abs/2309.11495), element-aware extraction).

## License

MIT. См. [LICENSE](./LICENSE).
