# Monthly Reaction Leaderboard

Локальная утилита и Discord-бот для сбора статистики реакций за календарный месяц.
Считает количество реакций кастомным эмодзи (`:EBALO:` по умолчанию) на сообщениях
в заданных текстовых каналах одной гильдии и сохраняет результат в **SQLite**
(источник истины). Просмотр — Discord slash и CLI (`verify`, `messages`, `channels-top`).

## v2 (бот)

- Официальный **bot token** в `.env` (`DISCORD_BOT_TOKEN`) — не коммитить файл `.env`.
- CLI: одноразовый прогон без постоянного gateway (`python -m bot.cli run`).
- Бот 24/7: slash `/recalculate_leaderboard`, `/show_leaderboard` (TOP из БД), ежемесячный автозапуск 1-го числа 00:05 (МСК по умолчанию).

## Требования

- Python 3.11+
- Application в [Discord Developer Portal](https://discord.com/developers/applications)
- Scopes при инвайте: `bot`, `applications.commands`
- Права: Read Message History, Send Messages, Use Application Commands

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Заполните `.env`:

- `DISCORD_BOT_TOKEN` — Bot → Token в Developer Portal
- `GUILD_ID`, `STATS_CHANNEL_IDS`
- `LEADERBOARD_CHANNEL_ID` — канал для embed (для бота)
- `MANUAL_RECALC_ROLE_ID` — опционально, кто может вызывать slash (иначе только Administrator)

## CLI (скан без gateway)

```powershell
python -m bot.cli run --year 2026 --month 3
```

Результат:

- база `./data/leaderboard.db` (или `DATABASE_PATH`) — полный рейтинг за месяц;
- TOP-N в терминале (`LEADERBOARD_TOP_N`, по умолчанию 10).

Если скан прервался (Ctrl+C, сбой канала, потеря сети), повторите его с
`--resume` — он продолжит с того же `run_id`, пропустив уже завершённые каналы:

```powershell
python -m bot.cli run --year 2026 --month 3 --resume
```

При неуспешном скане команда завершается кодом `2`, данные в БД **не меняются**.

## Discord-бот (прод, 24/7)

```powershell
python -m bot.main
```

- `/recalculate_leaderboard` — скан Discord и обновление БД (`year`, `month`, `post_results`, `assign_roles`, `resume`)
- `/show_leaderboard` — TOP **5** за месяц по **каналу** (`year`, `month`, `channel`) из SQLite, без скана
- Прогресс скана обновляется прямо в ephemeral-ответе команды
- 1-го числа в 00:05 (`LEADERBOARD_TIMEZONE`) — пересчёт **предыдущего** месяца, embed в `LEADERBOARD_CHANNEL_ID`, перевыдача роли **Рофлер** (TOP-3 «дурка» + TOP-2 «рофлинки»)
- Успех перевыдачи → текст в `ROLE_NOTIFY_CHANNEL_ID` (кликабельные роль и пользователи)
- Ошибка перевыдачи → текст в `ROLE_ERROR_CHANNEL_ID`
- Если месячный **скан** не завершился, бот пишет в `LEADERBOARD_CHANNEL_ID`

**Прод на Linux:** systemd unit и чеклист миграции с cron → сервис — [docs/DEPLOY.md](docs/DEPLOY.md).  
CLI оставьте для отладки; **не** ставьте cron на monthly job, если бот уже запущен как сервис.

## Офлайн-команды (без Discord)

```powershell
python -m bot.cli verify --year 2026 --month 3
python -m bot.cli messages --year 2026 --month 5 --user-id 123456789012345678
python -m bot.cli channels-top --year 2026 --month 5
```

## Логика подсчёта

- Сообщения с датой внутри месяца `[начало 00:00, начало следующего месяца 00:00)` в `LEADERBOARD_TIMEZONE`.
- Скан пишет во временную таблицу `messages_staging` (по `run_id`); рабочая
  таблица `messages` обновляется одной транзакцией только при полном успехе.
  Если хоть один канал failed/incomplete (при `SCAN_STRICT_CHANNELS=true`) —
  коммита нет, прежние данные сохраняются, скан можно продолжить (`--resume`).
- `reaction_count` — сумма `count` по всем эмодзи из `LEADERBOARD_EMOJIS` на сообщении (напр. EBALO+ROFL).
- Только обычные текстовые каналы.
- Пользователи из `EXCLUDED_USER_IDS` не сканируются и не попадают в TOP; после пересчёта месяца их старые строки в БД для этого периода исчезают.

## Переменные окружения

| Переменная | Обязательная | Назначение |
|------------|--------------|------------|
| `DISCORD_BOT_TOKEN` | да | Bot token (только в `.env`) |
| `GUILD_ID` | да | ID гильдии |
| `STATS_CHANNEL_IDS` | да | ID текстовых каналов через запятую |
| `IGNORE_CHANNEL_IDS` | нет | Исключения из списка каналов |
| `EXCLUDED_USER_IDS` | нет | ID пользователей, не попадающих в статистику и TOP |
| `LEADERBOARD_CHANNEL_ID` | нет | Канал для embed после job бота |
| `MANUAL_RECALC_ROLE_ID` | нет | Роль для slash (кроме Administrator) |
| `ROLE_ROFLER_ID` | для ролей | ID роли «Рофлер» (снимается у всех, выдаётся победителям) |
| `ROLE_NOTIFY_CHANNEL_ID` | для ролей | Канал успешной перевыдачи |
| `ROLE_ERROR_CHANNEL_ID` | для ролей | Канал ошибок перевыдачи |
| `ROLE_DURKICHI_CHANNEL_ID` | для ролей | Канал TOP-3 (Дуркичи), из `STATS_CHANNEL_IDS` |
| `ROLE_DURKICHI_TOP_N` | нет | Размер TOP, по умолчанию `3` |
| `ROLE_ROFLINKICHI_CHANNEL_ID` | для ролей | Канал TOP-2 (Рофлинкичи), из `STATS_CHANNEL_IDS` |
| `ROLE_ROFLINKICHI_TOP_N` | нет | Размер TOP, по умолчанию `2` |
| `LEADERBOARD_TIMEZONE` | нет | IANA TZ, по умолчанию `Europe/Moscow` |
| `LEADERBOARD_EMOJIS` | нет | Имена эмодзи через запятую (сумма на сообщение), напр. `EBALO,ROFL`; можно `LEADERBOARD_EMOJI` для одного |
| `DATABASE_PATH` | нет | SQLite, по умолчанию `./data/leaderboard.db` |
| `LEADERBOARD_TOP_N` | нет | Строк в TOP в терминале/embed, по умолчанию `10` |
| `SCAN_BATCH_SIZE` | нет | Размер батча записи в staging, по умолчанию `100` |
| `SCAN_PROGRESS_EVERY` | нет | Шаг прогресса (сообщений), по умолчанию `500` |
| `SCAN_MAX_MESSAGES_PER_CHANNEL` | нет | Лимит сообщений на канал; `0` = без лимита (превышение → incomplete) |
| `SCAN_FETCH_IF_EMPTY_REACTIONS` | нет | Дозапрос сообщений без кешированных реакций, по умолчанию `false` |
| `SCAN_CHECKPOINT_DIR` | нет | Каталог checkpoint/lock, по умолчанию `./data` |
| `SCAN_RETRY_MAX_ATTEMPTS` | нет | Попыток ретрая канала при 429/5xx, по умолчанию `5` |
| `SCAN_CHANNEL_DELAY_SEC` | нет | Пауза между каналами, по умолчанию `0.5` |
| `SCAN_STRICT_CHANNELS` | нет | `true` (по умолчанию): любой проблемный канал блокирует коммит |

## Устойчивость и восстановление

| Сбой | Что происходит | Что делать |
|------|----------------|------------|
| Обрыв WebSocket / временный 429/5xx | `discord.py` сам переподключает gateway; скан каналов повторяет запрос (`SCAN_RETRY_MAX_ATTEMPTS`, экспоненциальный backoff) | Ничего, восстановление автоматическое |
| Канал недоступен (403/404) | `SCAN_STRICT_CHANNELS=true` → канал `failed`, коммита нет; `false` → канал `skipped` | Проверить права бота, затем `--resume` |
| Падение процесса / перезагрузка сервера mid-scan | На диске остаётся checkpoint (`SCAN_CHECKPOINT_DIR`) + строки в `messages_staging`; прод-таблица `messages` не тронута | `python -m bot.cli run ... --resume` или slash с `resume: true` — завершённые каналы пропускаются |
| Месячный job упал | Бот остаётся online (`tasks.loop` не гаснет), пишет уведомление в `LEADERBOARD_CHANNEL_ID`, БД без изменений | Запустить пересчёт вручную с `resume` |
| Перевыдача роли не удалась | Сообщение в `ROLE_ERROR_CHANNEL_ID`, скан/БД уже могли успеть | Исправить права (роль бота выше «Рофлер», Manage Roles), повторить с `assign_roles: true` |

Бот **сам не догоняет** прерванный скан после рестарта процесса — нужен ручной `--resume`. Коммит в прод атомарный (`BEGIN IMMEDIATE`): при ошибке всё откатывается, частичный скан никогда не перезаписывает данные.

## Тесты

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

Покрывают ретраи Discord API, изоляцию сбоев по каналам, checkpoint/resume после обрыва, атомарность коммита в SQLite и коды выхода CLI. Discord-вызовы заменены фейками (`tests/fakes/`), реальный gateway не нужен.

Подробный план v2: [docs/V2_PLAN.md](docs/V2_PLAN.md).
