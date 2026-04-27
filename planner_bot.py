import logging
import asyncio
import aiosqlite
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, PicklePersistence
)
import os

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("planner_bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

BOT_TOKEN = os.environ["BOT_TOKEN"]

DB_PATH = 'data/planner.db'

def get_moscow_time():
    """Получение московского времени (UTC+3)"""
    return datetime.utcnow() + timedelta(hours=3)

# ---------------------------------------------------------------------------
# Работа с базой данных
# ---------------------------------------------------------------------------

async def init_db():
    """Создать таблицы БД если не существуют."""

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                nickname TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_times (
                user_id INTEGER,
                day_of_week INTEGER,
                start_time TEXT,
                PRIMARY KEY (user_id, day_of_week),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS daily_responses (
                user_id INTEGER,
                response_date TEXT,
                status TEXT,
                custom_time TEXT,
                responded_at TEXT,
                PRIMARY KEY (user_id, response_date)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS daily_question_sent (
                user_id INTEGER,
                sent_date TEXT,
                PRIMARY KEY (user_id, sent_date)
            )
        ''')
        await db.commit()


async def get_user(user_id: int) -> Optional[tuple]:
    """Вернуть запись пользователя или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cur:
            return await cur.fetchone()


async def create_user(user_id: int, username: str, nickname: str = None):
    """Создать или обновить пользователя."""
    if nickname is None:
        nickname = username or f"User_{user_id}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR REPLACE INTO users (user_id, username, nickname) VALUES (?, ?, ?)',
            (user_id, username, nickname)
        )
        await db.commit()


async def update_nickname(user_id: int, nickname: str):
    """Обновить никнейм пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE users SET nickname = ? WHERE user_id = ?',
            (nickname, user_id)
        )
        await db.commit()


async def get_user_time(user_id: int, day_of_week: int) -> Optional[str]:
    """Вернуть время пользователя для указанного дня недели или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT start_time FROM user_times WHERE user_id = ? AND day_of_week = ?',
            (user_id, day_of_week)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_user_time(user_id: int, day_of_week: int, start_time: str):
    """Установить время пользователя для указанного дня недели."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR REPLACE INTO user_times (user_id, day_of_week, start_time) VALUES (?, ?, ?)',
            (user_id, day_of_week, start_time)
        )
        await db.commit()


async def get_all_users_times() -> List[tuple]:
    """Вернуть расписание всех пользователей на неделю."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT u.nickname, ut.day_of_week, ut.start_time
            FROM users u
            LEFT JOIN user_times ut ON u.user_id = ut.user_id
            ORDER BY u.nickname, ut.day_of_week
        ''') as cur:
            return await cur.fetchall()


async def save_daily_response(user_id: int, status: str, custom_time: str = None):
    """Сохранить или обновить ответ пользователя за сегодня."""
    today = get_moscow_time().date().isoformat()
    now = get_moscow_time().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            '''INSERT OR REPLACE INTO daily_responses
               (user_id, response_date, status, custom_time, responded_at)
               VALUES (?, ?, ?, ?, ?)''',
            (user_id, today, status, custom_time, now)
        )
        await db.commit()


async def get_today_responses() -> List[tuple]:
    """Вернуть все ответы пользователей за сегодня."""
    today = get_moscow_time().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT dr.user_id, u.nickname, dr.status, dr.custom_time, dr.responded_at
            FROM daily_responses dr
            JOIN users u ON dr.user_id = u.user_id
            WHERE dr.response_date = ?
            ORDER BY dr.responded_at
        ''', (today,)) as cur:
            return await cur.fetchall()


async def get_all_users() -> List[int]:
    """Вернуть список user_id всех зарегистрированных пользователей."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM users') as cur:
            return [row[0] for row in await cur.fetchall()]


async def mark_question_sent(user_id: int) -> bool:
    """Пометить вопрос как отправленный. Вернуть False если уже отправлялся сегодня."""
    today = get_moscow_time().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT 1 FROM daily_question_sent WHERE user_id = ? AND sent_date = ?',
            (user_id, today)
        ) as cur:
            already_sent = await cur.fetchone()
        if already_sent:
            return False
        await db.execute(
            'INSERT INTO daily_question_sent (user_id, sent_date) VALUES (?, ?)',
            (user_id, today)
        )
        await db.commit()
        return True


# ---------------------------------------------------------------------------
# Обработчики сообщений
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Зарегистрировать пользователя и показать расписание."""
    user_id = update.effective_user.id
    username = update.effective_user.username

    if not await get_user(user_id):
        await create_user(user_id, username)

    await show_schedule(update, context)


async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать расписание на неделю с кнопками управления."""
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    user_times = await get_all_users_times()

    schedule_by_day = {i: [] for i in range(7)}
    for nickname, day, start_time in user_times:
        if start_time and day is not None:
            schedule_by_day[day].append(f"{nickname}: {start_time}")

    message = "**Стандартное расписание на неделю:**\n\n"
    for day_num in range(7):
        message += f"**{days[day_num]}:**\n"
        if schedule_by_day[day_num]:
            for entry in schedule_by_day[day_num]:
                message += f"  • {entry}\n"
        else:
            message += "  • Нет информации\n"
        message += "\n"

    keyboard = [
        [InlineKeyboardButton("Сменить ник", callback_data="change_nick")],
        [
            InlineKeyboardButton("Пн обн время", callback_data="set_time_0"),
            InlineKeyboardButton("Вт обн время", callback_data="set_time_1"),
            InlineKeyboardButton("Ср обн время", callback_data="set_time_2"),
        ],
        [
            InlineKeyboardButton("Чт обн время", callback_data="set_time_3"),
            InlineKeyboardButton("Пт обн время", callback_data="set_time_4"),
            InlineKeyboardButton("Сб обн время", callback_data="set_time_5"),
        ],
        [
            InlineKeyboardButton("Вс обн время", callback_data="set_time_6"),
            InlineKeyboardButton("Обновить", callback_data="show_schedule"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data == "show_schedule":
        await show_schedule(update, context)

    elif data == "change_nick":
        await query.edit_message_text(
            "Введите новый никнейм:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="show_schedule")]])
        )
        context.user_data['awaiting_nickname'] = True

    elif data.startswith("set_time_"):
        day = int(data.split("_")[2])
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        await query.edit_message_text(
            f"Введи время для {days[day]} в формате ЧЧ:ММ (например, 19:30):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="show_schedule")]])
        )
        context.user_data['awaiting_time'] = day

    elif data in ["ready", "probably_ready", "probably_not_ready", "not_ready"]:
        if data == "ready":
            await query.edit_message_text(
                "Введи удобное время в формате ЧЧ:ММ:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="daily_question")]])
            )
            context.user_data['awaiting_custom_time'] = True
        else:
            status_map = {
                "probably_ready": "Скорее готов",
                "probably_not_ready": "Скорее не готов",
                "not_ready": "Не готов"
            }
            await save_daily_response(user_id, status_map[data])
            await query.edit_message_text(
                f"Ваш ответ: {status_map[data]}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data="show_schedule")]])
            )

    elif data == "daily_question":
        await send_daily_question_to_user(user_id, context, force=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if context.user_data.get('awaiting_nickname'):
        await update_nickname(user_id, text)
        context.user_data['awaiting_nickname'] = False
        await update.message.reply_text(
            f"Никнейм изменен на: {text}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data="show_schedule")]])
        )

    elif context.user_data.get('awaiting_time') is not None:
        day = context.user_data['awaiting_time']
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        try:
            datetime.strptime(text, '%H:%M')
            await set_user_time(user_id, day, text)
            context.user_data['awaiting_time'] = None
            await update.message.reply_text(
                f"Время для {days[day]} установлено: {text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data="show_schedule")]])
            )
        except ValueError:
            await update.message.reply_text("Неверный формат времени. Используйте ЧЧ:ММ (например, 19:30)")

    elif context.user_data.get('awaiting_custom_time'):
        try:
            datetime.strptime(text, '%H:%M')
            await save_daily_response(user_id, "Готов", text)
            context.user_data['awaiting_custom_time'] = False
            await update.message.reply_text(
                f"Заметано. Записал время: {text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data="show_schedule")]])
            )
        except ValueError:
            await update.message.reply_text("Неверный формат времени. Используйте ЧЧ:ММ (например, 19:30)")


async def send_daily_question(context: ContextTypes.DEFAULT_TYPE):
    """Отправка ежедневного вопроса всем пользователям в 18:00 МСК"""
    user_ids = await get_all_users()
    for user_id in user_ids:
        await send_daily_question_to_user(user_id, context)


async def send_daily_question_to_user(
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    force: bool = False
):
    """Отправить ежедневный вопрос конкретному пользователю.

    force=True — ручной вызов через кнопку, проверка дубликата пропускается.
    """
    if not force:
        already_sent = not await mark_question_sent(user_id)
        if already_sent:
            logging.info(f"Вопрос уже отправлялся пользователю {user_id} сегодня, пропуск.")
            return

    try:
        keyboard = [
            [
                InlineKeyboardButton("Готов", callback_data="ready"),
                InlineKeyboardButton("Скорее готов", callback_data="probably_ready"),
            ],
            [
                InlineKeyboardButton("Скорее не готов", callback_data="probably_not_ready"),
                InlineKeyboardButton("Не готов", callback_data="not_ready"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=user_id,
            text="🕔 **Отсос сегодня**\n\nГотов сосать?",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        logging.info(f"Ежедневный вопрос отправлен пользователю {user_id}")
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")


async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """Отправка сводки в 20:00 МСК"""
    responses = await get_today_responses()

    if not responses:
        summary_message = "**Сводка за сегодня:**\n\nНет ответов от пользователей."
    else:
        summary_message = "**Сводка за сегодня:**\n\n"
        for user_id_r, nickname, status, custom_time, responded_at in responses:
            # responded_at хранится как ISO-строка
            try:
                dt = datetime.fromisoformat(responded_at)
                time_str = dt.strftime('%H:%M')
            except (ValueError, TypeError):
                time_str = str(responded_at)

            if custom_time:
                summary_message += f"• **{nickname}**: {status} ({custom_time}) в {time_str}\n"
            else:
                summary_message += f"• **{nickname}**: {status} в {time_str}\n"

    today_responders: Dict[int, str] = {row[0]: row[2] for row in responses}

    user_ids = await get_all_users()
    for uid in user_ids:
        status = today_responders.get(uid)
        if status and status != "Не готов":
            try:
                keyboard = [[InlineKeyboardButton("📅 К расписанию", callback_data="show_schedule")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await context.bot.send_message(
                    chat_id=uid,
                    text=summary_message,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                logging.info(f"Сводка отправлена пользователю {uid}")
            except Exception as e:
                logging.error(f"Не удалось отправить сводку пользователю {uid}: {e}")


def setup_jobs(application: Application):
    job_queue = application.job_queue

    job_queue.run_daily(
        send_daily_question,
        time=time(hour=15, minute=0),  # 18:00 МСК = 15:00 UTC
        name="daily_question"
    )
    job_queue.run_daily(
        send_daily_summary,
        time=time(hour=17, minute=0),  # 20:00 МСК = 17:00 UTC
        name="daily_summary"
    )
    logging.info("Планировщик заданий настроен")


async def post_init(application: Application):
    await init_db()
    logging.info("База данных инициализирована")


def main():
    os.makedirs('data', exist_ok=True)

    persistence = PicklePersistence(filepath='data/bot_persistence')

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    setup_jobs(application)

    logging.info("Бот запущен и готов к работе!")
    print("=" * 50)
    print("Планировщик запущен!")
    print("Бот работает в фоновом режиме")
    print("Ежедневные уведомления:")
    print("  - 18:00 МСК: вопрос о встрече")
    print("  - 20:00 МСК: сводка по ответам")
    print("=" * 50)

    application.run_polling()


if __name__ == '__main__':
    if not os.path.exists("start_bot.bat"):
        with open("start_bot.bat", "w", encoding='utf-8') as f:
            f.write('@echo off\nchcp 65001 > nul\necho Запуск бота-планировщика...\npython planner_bot.py\npause\n')

    main()


