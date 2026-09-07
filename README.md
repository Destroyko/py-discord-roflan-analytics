# Monthly Reaction Leaderboard

Discord-бот и CLI для месячной статистики реакций кастомным эмодзи (`:EBALO:` по умолчанию)
в заданных текстовых каналах одной гильдии. Результат хранится в **SQLite**; просмотр —
slash-команды и CLI (`verify`, `messages`, `channels-top`).

## Требования

- Python 3.11+
- Application в [Discord Developer Portal](https://discord.com/developers/applications)
- Инвайт бота: scopes `bot`, `applications.commands`
- Права бота: View Channels, Read Message History, Send Messages, Embed Links

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Заполните `.env` (шаблон — [.env.example](.env.example)). Минимум:

- `DISCORD_BOT_TOKEN` — Bot → Token в Developer Portal (не коммитить `.env`)
- `GUILD_ID`, `STATS_CHANNEL_IDS`
- `LEADERBOARD_CHANNEL_ID` — канал для embed и алертов (для бота)

**Прод на Linux:** systemd — [docs/DEPLOY.md](docs/DEPLOY.md).

## Бот (24/7)

```powershell
python -m bot.main
```

Запускайте как systemd-сервис на проде; **не** дублируйте monthly job через cron.

### Расписание

| Когда | Что |
|-------|-----|
| Ежедневно (`DAILY_SYNC_HOUR`, по умолчанию 04:00 МСК) | Инкрементальное обновление **текущего** месяца в БД |
| 1-го числа (`LEADERBOARD_MONTHLY_RUN_HOUR`, по умолчанию 10:00 МСК) | Полный пересчёт **предыдущего** месяца; embed в `LEADERBOARD_CHANNEL_ID` — **топ 5** по каналам `ROLE_DURKICHI_CHANNEL_ID` и `ROLE_ROFLINKICHI_CHANNEL_ID` |

### Slash-команды

| Команда | Описание |
|---------|----------|
| `/show_leaderboard` | TOP по дурке и рофлинкам за месяц из SQLite (без скана, как в `LEADERBOARD_CHANNEL_ID`); ответ только вам. Год/месяц не обязательны — по умолчанию текущий месяц. Будущий месяц или период раньше первых данных бота — шутливая отписка вместо TOP; совсем некорректный месяц (не 1–12) — команда молча ничего не отвечает |
| `/recalculate_leaderboard` | Полный скан месяца; доступ: Administrator или `MANUAL_RECALC_ROLE_IDS`; прогресс и итог — только вам (`post_results`, `resume`) |

При старте в лог пишется **permission audit** (какие права есть и чего не хватает).

## CLI

Одноразовый скан без постоянного gateway:

```powershell
python -m bot.cli run --year 2026 --month 6
```

- База `DATABASE_PATH` (по умолчанию `./data/leaderboard.db`)
- TOP-N в терминале (`LEADERBOARD_TOP_N`, по умолчанию 10)

Прерванный скан — продолжить с `--resume`. Код выхода: `0` успех, `1` ошибка, `2` скан не завершён (БД не менялась).

Офлайн (Discord не нужен):

```powershell
python -m bot.cli verify --year 2026 --month 6
python -m bot.cli messages --year 2026 --month 6 --user-id 123456789012345678
python -m bot.cli channels-top --year 2026 --month 6
```

## Логика подсчёта

- Месяц — полуинтервал `[начало 00:00, начало следующего месяца 00:00)` в `LEADERBOARD_TIMEZONE`.
- Полный скан: staging → атомарный commit в `messages` только при успехе всех каналов (`SCAN_STRICT_CHANNELS=true` по умолчанию).
- Daily sync: обновление известных постов по `message_id`, дописывание новых, удаление из БД если пост снят на сервере.
- `reaction_count` — сумма реакций по эмодзи из `LEADERBOARD_EMOJIS` на сообщении.
- `EXCLUDED_USER_IDS` — не сканируются и не в TOP.
- `USER_CHECK_API_URL` — сторонняя проверка пользователя **на момент показа** TOP (публичный топ 3+2, `/show_leaderboard`); флаг не хранится и не режет сами данные, только вид списка (с подтягиванием следующего по рангу).

`/recalculate_leaderboard` и `/show_leaderboard` показывают одинаковый **топ по дурке и рофлинкам** (как в `LEADERBOARD_CHANNEL_ID`); разница в том, что `/show_leaderboard` не сканирует Discord заново, а берёт то, что уже есть в SQLite.

Роль «Рофлер» бот **не выдаёт и не снимает сам** — только выводит топ 3+2 в embed; выдачу роли победителям делает администратор вручную.

## Переменные окружения

### Обязательные

| Переменная | Назначение |
|------------|------------|
| `DISCORD_BOT_TOKEN` | Bot token |
| `GUILD_ID` | ID гильдии |
| `STATS_CHANNEL_IDS` | ID текстовых каналов через запятую |

### Часто настраивают

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `LEADERBOARD_CHANNEL_ID` | — | Embed (топ по дурке/рофлинкам) и алерты при сбое monthly-скана |
| `LEADERBOARD_CHANNEL_TOP_N` | `5` | Строк TOP на канал в embed / ответе `/recalculate_leaderboard` |
| `LEADERBOARD_EMOJIS` | `EBALO` | Имена эмодзи через запятую |
| `LEADERBOARD_TIMEZONE` | `Europe/Moscow` | Границы месяца и расписание |
| `LEADERBOARD_TOP_N` | `10` | Строк в CLI guild-wide TOP |
| `DATABASE_PATH` | `./data/leaderboard.db` | SQLite |
| `MANUAL_RECALC_ROLE_IDS` | — | Роли для `/recalculate_leaderboard` (Administrator всегда может) |
| `DAILY_SYNC_ENABLED` | `true` | Ежедневный инкрементальный sync |
| `DAILY_SYNC_HOUR` / `MINUTE` | `4` / `0` | Время daily sync |
| `LEADERBOARD_MONTHLY_RUN_HOUR` / `MINUTE` | `10` / `0` | Monthly job, 1-е число |
| `IGNORE_CHANNEL_IDS` | — | Исключить каналы из `STATS_CHANNEL_IDS` |
| `EXCLUDED_USER_IDS` | — | Исключить пользователей из статистики |
| `USER_CHECK_API_URL` | — | Скрывать пользователя из показанного TOP по внешнему API (см. выше) |
| `USER_CHECK_API_TOKEN` | — | Bearer-токен для `USER_CHECK_API_URL` |
| `USER_CHECK_API_TIMEOUT_SEC` | `5` | Таймаут одного запроса; при ошибке/таймауте считаем «не исключён» |

### Каналы дурки/рофлинок

`ROLE_DURKICHI_CHANNEL_ID`, `ROLE_ROFLINKICHI_CHANNEL_ID` — оба в `STATS_CHANNEL_IDS`; нужны для embed в `LEADERBOARD_CHANNEL_ID` (топ `LEADERBOARD_CHANNEL_TOP_N` по каждому). Названия переменных исторические — сама роль «Рофлер» ботом не выдаётся.

### Тонкая настройка скана

Имеют разумные дефолты; менять редко: `SCAN_BATCH_SIZE`, `SCAN_PROGRESS_EVERY`, `SCAN_MAX_MESSAGES_PER_CHANNEL`, `SCAN_CHECKPOINT_DIR`, `SCAN_RETRY_MAX_ATTEMPTS`, `SCAN_CHANNEL_DELAY_SEC`, `SCAN_STRICT_CHANNELS`, `SCAN_FETCH_IF_EMPTY_REACTIONS`, `DAILY_SYNC_FETCH_BATCH_SIZE`, `DAILY_SYNC_MESSAGE_DELAY_SEC`.

## Устойчивость

| Сбой | Действие |
|------|----------|
| Обрыв сети / 429 при скане | Ретраи по каналу; gateway переподключается сам |
| Падение mid-scan | `python -m bot.cli run … --resume` |
| Monthly job не завершился | Алерт в `LEADERBOARD_CHANNEL_ID`; `--resume` |

Бот **не** продолжает прерванный скан после рестарта сам — нужен `--resume`.

## Тесты

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```
