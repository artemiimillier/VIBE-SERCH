# Deploy на Vercel Hobby

Дата: 2026-05-28
Цель: получить публичную ссылку на работающий VIBE-SERCH в внешнем контуре.

## Решения

- Платформа: Vercel Hobby (бесплатно, 60s лимит на функцию).
- UI: read-only. Кнопка ручного запуска пайплайна удаляется.
- Триггер пайплайна: Vercel Cron (раз в сутки по конфигу cron_hour/cron_minute, но в UTC — Vercel не поддерживает таймзоны).
- Хранилище стейта: Vercel KV (Upstash Redis), ключ `vibeserch:last_digest` (JSON) и `vibeserch:last_status`.
- Структура: `api/` директория с serverless-функциями + `vercel.json` + `requirements.txt`.

## Шаги

1. plans/2026-05-28_vercel-deploy.md (этот файл).
2. Создать `src/storage.py` — абстракция KV (get/set last_digest, last_status). Использует `upstash-redis` через REST API.
3. Удалить `src/scheduler.py`. Убрать импорт и вызов `start_scheduler()` из web.py.
4. Создать `api/index.py` — отдаёт index.html.
5. Создать `api/cron.py` — handler для Vercel Cron, запускает пайплайн целиком, пишет результат в KV.
6. Создать `api/status.py` — GET статус из KV.
7. Создать `api/digest.py` — GET последний дайджест из KV.
8. Сократить пайплайн под 60s:
   - `MAX_SIGNALS_TO_VERIFY = 8` (вместо текущих ~20 после фильтра).
   - `VERIFY_WORKERS = 4` (как есть).
   - Бюджет: scan ~10s + filter ~5s + verify 8×~5s/4 параллели = ~10s + digest ~10s + tg ~2s = ~37s. Запас 23s.
9. `requirements.txt` сгенерировать из pyproject.toml зависимостей.
10. `vercel.json`:
    - rewrites `/` → `/api/index`, `/api/cron/status` → `/api/status`, `/api/cron/digest` → `/api/digest`.
    - crons: `[{ "path": "/api/cron", "schedule": "0 6 * * *" }]` (6:00 UTC = 9:00 MSK).
    - functions: `maxDuration: 60` для `api/cron.py`.
11. Обновить `index.html`: удалить `runBtn`, `controls`, `startPipeline()`, `progress-area` логику, оставить poll `/api/cron/status` и `/api/cron/digest`.
12. Тест локально: `vercel dev` + проверить рендер index, fetch status/digest (пустые ОК), trigger cron вручную.
13. Закоммитить, запушить в `origin/main` (после явного разрешения).
14. `vercel link` + `vercel deploy --prod` или подключить GitHub в дашборде.
15. Добавить env vars в Vercel: ANTHROPIC_API_KEY, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID.
16. Создать KV storage в Vercel дашборде, подключить к проекту (`KV_REST_API_URL`, `KV_REST_API_TOKEN` инжектятся автоматически).
17. Финальный deploy → получить URL.

## Критерии успеха

- Открывается главная страница со сценой и HUD.
- `/api/cron/status` отдаёт JSON.
- `/api/cron/digest` отдаёт JSON (после первого cron).
- Ручной запуск cron через `vercel.com` UI или curl с правильным `CRON_SECRET` создаёт дайджест в KV за <60s.

## Риски

- Reddit PRAW в холодном старте: первый коннект может занять 5-10s. Митигация: если будет таймаут, кешировать PRAW клиент в module scope.
- Telegram отправка из serverless: длинная блокирующая операция. Если будет узко, вынести в отдельную cron-функцию.
- 60s лимит: если пайплайн превысит, уменьшить MAX_SIGNALS_TO_VERIFY до 5.
- praw как зависимость весит много (~100MB вместе с requests). Vercel ограничивает 250MB на функцию. Проверить.

## Что НЕ делаем в этом плане

- Postgres (не нужен для MVP, KV хватит).
- Очередь задач (QStash/Inngest) — это план Б если Hobby не вытянет.
- Локальная разработка через docker-compose остаётся работать как было.
