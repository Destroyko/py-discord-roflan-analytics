# Деплой: сервис 24/7 + CLI для отладки

Продакшен: **`python -m bot.main`** как systemd-сервис (slash, embed, monthly job).  
CLI (`python -m bot.cli …`) остаётся для ручного скана и офлайн-аналитики — **не** ставьте cron на monthly job, если бот уже запущен.

---

## Архитектура

| Режим | Команда | Когда |
|-------|---------|--------|
| **Прод** | `python -m bot.main` | Always-on через systemd |
| **Отладка / ручной скан** | `python -m bot.cli run …` | По необходимости с консоли |
| **Офлайн из SQLite** | `python -m bot.cli verify …` и др. | Без Discord |

Оба режима используют один `.env`, одну БД (`DATABASE_PATH`) и один каталог checkpoint (`SCAN_CHECKPOINT_DIR`).

---

## Чеклист: переход с cron + CLI на сервис

### 1. Подготовка на сервере

- [ ] Python 3.11+, git clone в каталог, напр. `/opt/roflan-analytics`
- [ ] venv и зависимости:

```bash
cd /opt/roflan-analytics
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

- [ ] `.env` из `.env.example`, заполнены минимум:
  - `DISCORD_BOT_TOKEN`
  - `GUILD_ID`, `STATS_CHANNEL_IDS`
  - `LEADERBOARD_CHANNEL_ID` (embed и алерты при сбое monthly **скана**)
  - `ROLE_ROFLER_ID`, `ROLE_NOTIFY_CHANNEL_ID`, `ROLE_ERROR_CHANNEL_ID`
  - `ROLE_DURKICHI_CHANNEL_ID`, `ROLE_ROFLINKICHI_CHANNEL_ID` (оба в `STATS_CHANNEL_IDS`)
  - `MANUAL_RECALC_ROLE_ID` (опционально)
  - Права бота: **Manage Roles**, роль бота выше «Рофлер»
- [ ] Каталоги для данных существуют и доступны на запись:

```bash
mkdir -p data
```

- [ ] Бот приглашён на сервер: scopes `bot`, `applications.commands`; права Read Message History, Send Messages, Use Application Commands

### 2. Отключить cron (важно)

Если раньше monthly job шёл через cron + `bot.cli run`:

- [ ] Найти задание: `crontab -l` или `/etc/cron.d/…`
- [ ] Закомментировать или удалить строку с `bot.cli run`
- [ ] **Не** дублировать scheduler: monthly job уже внутри бота (1-е число 00:05 в `LEADERBOARD_TIMEZONE`)

Пример того, что **убрать**:

```cron
5 0 1 * * cd /opt/roflan-analytics && .venv/bin/python -m bot.cli run --year ... --month ...
```

### 3. Установить systemd

- [ ] Создать системного пользователя (без login shell):

```bash
sudo useradd --system --home /opt/roflan-analytics --shell /usr/sbin/nologin roflan-bot
sudo chown -R roflan-bot:roflan-bot /opt/roflan-analytics
```

- [ ] Отредактировать пути в [`deploy/roflan-leaderboard-bot.service`](../deploy/roflan-leaderboard-bot.service) под ваш сервер
- [ ] Установить unit:

```bash
sudo cp deploy/roflan-leaderboard-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable roflan-leaderboard-bot
sudo systemctl start roflan-leaderboard-bot
```

- [ ] Проверить статус и логи:

```bash
sudo systemctl status roflan-leaderboard-bot
journalctl -u roflan-leaderboard-bot -f
```

В логах ожидается: `Logged in as …`, `Synced N application command(s).`, `Next monthly leaderboard at …`

### 4. Проверка в Discord

- [ ] Бот **online** в списке участников
- [ ] `/show_leaderboard` — TOP из БД (если месяц уже был посчитан)
- [ ] `/recalculate_leaderboard` — тестовый пересчёт (нужны права Admin или `MANUAL_RECALC_ROLE_ID`)
- [ ] После успешного job — embed в `LEADERBOARD_CHANNEL_ID`
- [ ] После перевыдачи роли — сообщение в `ROLE_NOTIFY_CHANNEL_ID` (или ошибка в `ROLE_ERROR_CHANNEL_ID`)

### 5. Миграция данных (если БД уже была на cron)

- [ ] Скопировать существующий `leaderboard.db` и checkpoint-файлы из `data/` на сервер в тот же `DATABASE_PATH` / `SCAN_CHECKPOINT_DIR`
- [ ] Права: `roflan-bot` должен читать/писать эти файлы

### 6. После деплоя

- [ ] Убедиться, что cron **не** запускает `bot.cli run` параллельно с ботом (checkpoint-lock от второго запуска защитит данные, но job упадёт с «already in progress»)
- [ ] Зафиксировать процедуру resume при обрыве скана (см. ниже)

---

## CLI: когда использовать

**Ручной скан без gateway** (удобно для сверки цифр или если сервис временно остановлен):

```bash
cd /opt/roflan-analytics
sudo -u roflan-bot ./.venv/bin/python -m bot.cli run --year 2026 --month 3
```

**Продолжить прерванный скан** (после падения процесса или Ctrl+C):

```bash
./.venv/bin/python -m bot.cli run --year 2026 --month 3 --resume
```

Или в Discord: `/recalculate_leaderboard` с `resume: true`.

**Офлайн-команды** (Discord не нужен):

```bash
./.venv/bin/python -m bot.cli verify --year 2026 --month 3
./.venv/bin/python -m bot.cli messages --year 2026 --month 5 --user-id <id>
./.venv/bin/python -m bot.cli channels-top --year 2026 --month 5
```

Exit code CLI: `0` — успех, `1` — ошибка, `2` — скан не завершён, БД не изменена.

---

## Восстановление после сбоев

| Ситуация | Действие |
|----------|----------|
| Сервис упал, скан не шёл | `systemctl restart roflan-leaderboard-bot` |
| Сервис упал **во время** скана | Restart сервиса, затем `--resume` или slash `resume: true` |
| Monthly job не дошёл до конца | Алерт в `LEADERBOARD_CHANNEL_ID`; ручной пересчёт с `resume` |
| «already in progress» | Не начинать новый скан — только `resume`, либо удалить checkpoint вручную (осознанно) |

Бот **не** автоматически продолжает прерванный скан после рестарта — это by design (атомарный commit в SQLite).

---

## Обновление версии

```bash
cd /opt/roflan-analytics
sudo -u roflan-bot git pull
sudo -u roflan-bot ./.venv/bin/pip install -r requirements.txt
sudo systemctl restart roflan-leaderboard-bot
journalctl -u roflan-leaderboard-bot -n 50
```

---

## Windows (локальная разработка)

systemd на Windows нет. Для локального 24/7:

```powershell
python -m bot.main
```

Или Task Scheduler / отдельное окно терминала. Прод обычно на Linux VPS.

---

## Мониторинг (рекомендации)

- **systemd**: `Restart=always` уже в unit-файле
- **Discord**: бот должен быть online; раз в месяц — embed 1-го числа
- **Логи**: `journalctl -u roflan-leaderboard-bot --since today`
- Опционально: внешний healthcheck (uptime-kuma, cron `systemctl is-active`, алерт если не `active`)

---

## Файлы деплоя

| Файл | Назначение |
|------|------------|
| [`deploy/roflan-leaderboard-bot.service`](../deploy/roflan-leaderboard-bot.service) | systemd unit |
| [`.env.example`](../.env.example) | шаблон конфигурации |
| [`README.md`](../README.md) | команды и переменные окружения |
