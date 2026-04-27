# Планировщик

Бот для автоматизации ежедневного сбора информации о готовности команды к встрече.  
Каждый день в **18:00 МСК** задаёт вопрос с вариантами ответа, а в **20:00 МСК** присылает сводку всем участникам.

##

- Регистрация пользователей и настройка ника
- Личное расписание: установка времени присутствия на каждый день недели
- Общее расписание: просмотр времени всех участников
- Ежедневный опрос с кнопками:
  - Готов / Скорее готов / Скорее не готов / Не готов
- При ответе «Готов» — запрос удобного времени (ЧЧ:ММ)
- Автоматическая сводка в 20:00 МСК для всех участников
- Подписка / отписка от ежедневных вопросов
- Просмотр своего расписания

## 

- **Python 3.10+**
- `python-telegram-bot` v20 — работа с Telegram API
- `aiosqlite` — асинхронная работа с SQLite
- `zoneinfo` — часовые пояса (Москва, UTC+3)
- SQLite с режимом **WAL** и индексами

## Установка и запуск
1. Клонировать репозиторий
bash
git clone https://github.com/yourusername/planner-bot.git
cd planner-bot
2. Создать виртуальное окружение
bash
python -m venv venv
source venv/bin/activate   # Linux/macOS
venv\Scripts\activate      # Windows
3. Установить зависимости
bash
pip install python-telegram-bot aiosqlite
4. Получить токен бота
Создайте переменную окружения или вставьте прямо в код (не рекомендуется для продакшена):
bash
export BOT_TOKEN="ваш_токен_бота"
Либо замените в файле planner_bot.py:
BOT_TOKEN = "ваш_токен_бота"
6. Запустить бота
bash
python planner_bot.py

## Команды бота

/start	Показать общее расписание и главное меню
/help	Показать справку
/my_schedule	Показать только моё расписание
/subscribe	Подписаться на ежедневные вопросы (включено по умолчанию)
/unsubscribe	Отписаться от ежедневных вопросов

## Структура базы данных
Файл: data/planner.db

Таблица users
user_id (INTEGER, PRIMARY KEY)

username (TEXT)

nickname (TEXT)

subscribed (INTEGER, 1/0)

created_at (TIMESTAMP)

Таблица user_times
user_id (INTEGER)

day_of_week (INTEGER, 0–6)

start_time (TEXT, ЧЧ:ММ)

PRIMARY KEY (user_id, day_of_week)

Таблица daily_responses
user_id (INTEGER)

response_date (TEXT, YYYY-MM-DD)

status (TEXT)

custom_time (TEXT, опционально)

responded_at (TEXT, ISO)

PRIMARY KEY (user_id, response_date)

Таблица daily_question_sent
user_id (INTEGER)

sent_date (TEXT, YYYY-MM-DD)

PRIMARY KEY (user_id, sent_date)

Индексы: на response_date, sent_date, user_id в user_times.

## Пример работы
Пользователь запускает /start → видит общее расписание и кнопки.
Настраивает ник и своё время на каждый день.
В 18:00 МСК бот присылает сообщение с вопросом и кнопками.
Пользователь нажимает «Скорее готов» → ответ сохраняется.
В 20:00 МСК все участники получают сводку вида:
Сводка за сегодня:
• Анна: Скорее готов в 18:02
• Иван: Готов (19:30) в 18:05
• Мария: Не готов в 18:10
Любой может в любой момент нажать «Моё расписание» или изменить время на определённый день.

## Файлы проекта
text
planner_bot.py        # Основной код бота
data/
  planner.db          # База данных (создаётся автоматически)
  bot_persistence     # Файл персистентности Pickle
planner_bot.log       # Лог-файл
start_bot.bat         # (Windows) скрипт запуска
