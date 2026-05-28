# Отчёт о реализации MVP VIBE-SERCH

Дата: 2026-05-27

## Статус: УСПЕХ

Все 5 фаз разработки (0-4) выполнены. Фаза 5 (обкатка) не реализована - требует 14 дней ручной работы.

## Что реализовано

- **Фаза 0: Фундамент** - pyproject.toml, структура src/, Pydantic-модели (6 шт.), LLM-клиент с retry и cost tracking, конфигурация из .env через pydantic-settings
- **Фаза 1: Reddit-сканер** - PRAW-сканер 5 сабреддитов (hot+top), фильтр по score, source_tier по домену (5 уровней), дедупликация по URL, graceful error handling
- **Фаза 2: Фильтрация + верификация (ЯДРО)** - Haiku-фильтрация (1 вызов на все сигналы), Sonnet-верификация с информационной асимметрией (2 вызова: аналитик + верификатор без контекста), trust_score, threshold > 0.5, top 7
- **Фаза 3: Генерация дайджеста** - Opus генерирует Method Cards на русском, нормализация категорий/hype_rating, форматирование для Telegram с тримингом под 4096 символов
- **Фаза 4: Доставка** - Telegram-бот с retry и сплитом длинных сообщений, main.py как оркестратор с timing и логированием в stdout + файл

## Созданные файлы

### Исходный код (9 файлов)
| Файл | Описание |
|---|---|
| `src/__init__.py` | Пакет |
| `src/__main__.py` | Точка входа `python -m src` |
| `src/config.py` | Settings из .env (SecretStr для секретов) |
| `src/llm.py` | LLM-клиент: Haiku/Sonnet/Opus, retry, cost tracking |
| `src/models.py` | 6 Pydantic-моделей: RawSignal, AnalysisResult, VerificationResult, VerifiedFact, MethodCard, DailyDigest |
| `src/scanner.py` | Reddit-сканер: 5 сабреддитов, hot+top, source_tier |
| `src/pipeline.py` | Фильтрация (Haiku) + верификация (Sonnet x2) + генерация (Opus) |
| `src/telegram.py` | Отправка в канал: retry, сплит, async |
| `src/main.py` | Оркестратор: 5 шагов с timing и логированием |

### Тесты (7 файлов, 105 тестов)
| Файл | Тестов | Покрытие |
|---|---|---|
| `tests/test_config.py` | 4 | Settings, defaults |
| `tests/test_llm.py` | 7 | Cost calculation |
| `tests/test_models.py` | 15 | Все модели, валидация |
| `tests/test_scanner.py` | 15 | Source tier, scan, dedup |
| `tests/test_pipeline.py` | 45 | JSON parsing, trust score, filter, verify, threshold, digest, format |
| `tests/test_telegram.py` | 14 | Split, retry, send |
| `tests/test_main.py` | 5 | Pipeline orchestration |

### Конфигурация
| Файл | Описание |
|---|---|
| `pyproject.toml` | Python 3.12+, 6 runtime + 2 dev зависимостей |
| `ruff.toml` | Линтер: line-length 100, py312 |
| `.env.example` | Шаблон переменных (без реальных ключей) |
| `.gitignore` | .env, .venv, logs/, __pycache__ |

## Как запустить

```bash
# 1. Установка
cd /Users/artemiimiller/VIBE-SERCH
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Настройка
cp .env.example .env
# Заполнить: ANTHROPIC_API_KEY, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

# 3. Запуск полного пайплайна
python -m src.main

# 4. Проверки
ruff check src/ tests/
pytest -v
```

## Результаты проверок

- **ruff check**: All checks passed
- **pytest**: 105/105 passed, 0 warnings
- **Security review**: SecretStr для секретов, .env в .gitignore, logs/ в .gitignore, нет хардкода ключей, нет инъекций

## Стоимость одного прогона

```
Этап                 | Модель | Вызовов | ~Стоимость
Фильтрация           | Haiku  | 1       | $0.02
Верификация (x20)    | Sonnet | 40      | ~$1.00
Генерация дайджеста  | Opus   | 1       | $0.25
                                 Итого:   ~$1.27/день
                                 В месяц:  ~$38/месяц
```

Стоимость верификации удвоилась ($1 вместо $0.50) из-за исправления: 2 вызова Sonnet на сигнал вместо 1 (информационная асимметрия для борьбы с sycophancy bias). Это ЯДРО продукта.

## Исправления security review

- Секреты (api_key, client_secret, bot_token) обернуты в SecretStr
- .env.example очищен от паттернов реальных ключей
- logs/ добавлен в .gitignore
- Exception handler в pipeline.py сужен (убран catch-all Exception)

## Известные ограничения

- `total_cost` не агрегируется программно (только в логах LLM-вызовов)
- LLM-клиент создается заново на каждый вызов (стоит кешировать для 20+ вызовов)
- Prompt injection через контент Reddit-постов (митигация: truncation + min_score фильтр)
- Нет e2e теста с реальными API (требует ключи)
- Фаза 5 (обкатка) не реализована - 14 дней ручной работы

## Что дальше

1. **Ротация секретов** - OPENROUTER_API_KEY и TELEGRAM_BOT_TOKEN были видны в .env
2. **Обкатка (Фаза 5)** - 7 дней shadow mode + 7 дней soft launch
3. **Кеширование LLM-клиента** - одна инициализация на весь пайплайн
4. **Агрегация cost** - суммировать стоимость всех вызовов в DailyDigest
5. **Cron** - настроить `0 4 * * * python -m src.main` для ежедневного запуска
