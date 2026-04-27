import logging
import asyncio
import aiosqlite
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import re
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, PicklePersistence
)
import os

# ==================== Константы ====================
DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
TIME_PATTERN = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')  # ЧЧ:ММ

# Тексты сообщений
MSG_WELCOME = "/help для списка команд."
MSG_HELP = (
    " *Бот-планировщик*\n\n"
    "• /start — показать расписание\n"
    "• /help — эта справка\n"
    "• /my_schedule — моё личное расписание\n"
    "• /subscribe — подписаться на ежедневные вопросы\n"
    "• /unsubscribe — отписаться от вопросов\n\n"
    "Каждый день в 18:00 МСК вы получите вопрос о готовности к встрече.\n"
    "В 20:00 МСК придёт сводка ответов всех участников."
)
MSG_SCHEDULE_TITLE = "**Стандартное расписание на неделю:**\n\n"
MSG_MY_SCHEDULE_TITLE = "**Моё расписание на неделю:**\n\n"
MSG_NO_INFO = "  • Нет информации"
MSG_CHANGE_NICK_PROMPT = "Введите новый никнейм:"
MSG_NICK_CHANGED = "Никнейм изменён на: {}"
MSG_SET_TIME_PROMPT = "Введи время для {} в формате ЧЧ:ММ (например, 19:30):"
MSG_TIME_SET = "Время для {} установлено: {}"
MSG_INVALID_TIME = " Неверный формат времени. Используйте ЧЧ:ММ (например, 19:30)"
MSG_DAILY_QUESTION = " **Встреча сегодня**\n\nГотовы ли вы?"
MSG_READY_CUSTOM_TIME = "Введи удобное время в формате ЧЧ:ММ:"
MSG_RESPONSE_RECORDED = " Ваш ответ записан: {}"
MSG_RESPONSE_UPDATED = " Ваш ответ изменён на: {}"
MSG_CUSTOM_TIME_SAVED = "Заметано. Записал время: {}"
MSG_SUBSCRIBED = " Вы подписались на ежедневные вопросы."
MSG_UNSUBSCRIBED = " Вы отписались от ежедневных вопросов. Чтобы снова получать вопросы, используйте /subscribe"
MSG_NO_RESPONSES_TODAY = "**Сводка за сегодня:**\n\nНет ответов от пользователей."

# Callback data
CB_SHOW_SCHEDULE = "show_schedule"
CB_MY_SCHEDULE = "my_schedule"
CB_CHANGE_NICK = "change_nick"
CB_SET_TIME_PREFIX = "set_time_"
CB_READY = "ready"
CB_PROBABLY_READY = "probably_ready"
CB_PROBABLY_NOT_READY = "probably_not_ready"
CB_NOT_READY = "not_ready"

# ==================== Enum для статусов ====================
class ResponseStatus(Enum):
    READY = "Готов"
    PROBABLY_READY = "Скорее готов"
    PROBABLY_NOT_READY = "Скорее не готов"
    NOT_READY = "Не готов"

    @classmethod
    def from_callback(cls, callback_data: str) -> Optional['ResponseStatus']:
        mapping = {
            CB_READY: cls.READY,
            CB_PROBABLY_READY: cls.PROBABLY_READY,
            CB_PROBABLY_NOT_READY: cls.PROBABLY_NOT_READY,
            CB_NOT_READY: cls.NOT_READY,
        }
        return mapping.get(callback_data)

# ==================== Настройки ====================
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
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

def get_moscow_time() -> datetime:
    """Текущее московское время (timezone-aware)."""
    return datetime.now(MOSCOW_TZ)

# ==================== Работа с базой данных ====================
async def init_db():
    """Создать таблицы и индексы, включить WAL."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем WAL для лучшей производительности
        await db.execute("PRAGMA journal_mode=WAL")
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                nickname TEXT,
                subscribed INTEGER DEFAULT 1,
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
        # Индексы
        await db.execute("CREATE INDEX IF NOT EXISTS idx_responses_date ON daily_responses(response_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_question_sent_date ON daily_question_sent(sent_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_times_user ON user_times(user_id)")
        
        await db.commit()

async def get_user(user_id: int) -> Optional[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cur:
            return await cur.fetchone()

async def create_user(user_id: int, username: str, nickname: str = None):
    if nickname is None:
        nickname = username or f"User_{user_id}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, username, nickname) VALUES (?, ?, ?)',
            (user_id, username, nickname)
        )
        await db.commit()

async def update_nickname(user_id: int, nickname: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE users SET nickname = ? WHERE user_id = ?',
            (nickname, user_id)
        )
        await db.commit()

async def get_subscription_status(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT subscribed FROM users WHERE user_id = ?', (user_id,)) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else True  # по умолчанию подписан

async def set_subscription(user_id: int, subscribed: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE users SET subscribed = ? WHERE user_id = ?',
            (1 if subscribed else 0, user_id)
        )
        await db.commit()

async def get_user_time(user_id: int, day_of_week: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT start_time FROM user_times WHERE user_id = ? AND day_of_week = ?',
            (user_id, day_of_week)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def set_user_time(user_id: int, day_of_week: int, start_time: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR REPLACE INTO user_times (user_id, day_of_week, start_time) VALUES (?, ?, ?)',
            (user_id, day_of_week, start_time)
        )
        await db.commit()

async def get_all_users_times() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT u.nickname, ut.day_of_week, ut.start_time
            FROM users u
            LEFT JOIN user_times ut ON u.user_id = ut.user_id
            ORDER BY u.nickname, ut.day_of_week
        ''') as cur:
            return await cur.fetchall()

async def get_user_times(user_id: int) -> List[Tuple[int, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT day_of_week, start_time FROM user_times WHERE user_id = ? ORDER BY day_of_week',
            (user_id,)
        ) as cur:
            return await cur.fetchall()

async def save_daily_response(user_id: int, status: str, custom_time: str = None):
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

async def get_today_responses() -> List[Tuple]:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM users') as cur:
            return [row[0] for row in await cur.fetchall()]

async def mark_question_sent(user_id: int) -> bool:
    """Пометить вопрос как отправленный. Вернуть True если успешно, False если уже отправлялся сегодня."""
    today = get_moscow_time().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT 1 FROM daily_question_sent WHERE user_id = ? AND sent_date = ?',
            (user_id, today)
        ) as cur:
            if await cur.fetchone():
                return False
        await db.execute(
            'INSERT INTO daily_question_sent (user_id, sent_date) VALUES (?, ?)',
            (user_id, today)
        )
        await db.commit()
        return True

# ==================== Вспомогательные функции ====================
def format_time_validation(time_str: str) -> bool:
    """Проверить формат времени ЧЧ:ММ."""
    return bool(TIME_PATTERN.match(time_str))

def format_schedule_for_user(nickname: str, times_per_day: Dict[int, str]) -> str:
    """Формирует строку расписания для одного пользователя."""
    message = f"**{nickname}:**\n"
    for day_num in range(7):
        time_str = times_per_day.get(day_num, None)
        if time_str:
            message += f"  • {DAYS_RU[day_num]}: {time_str}\n"
        else:
            message += f"  • {DAYS_RU[day_num]}: не указано\n"
    return message + "\n"

def get_main_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню (расписание)."""
    keyboard = [
        [InlineKeyboardButton("Сменить ник", callback_data=CB_CHANGE_NICK)],
        [InlineKeyboardButton("Моё расписание", callback_data=CB_MY_SCHEDULE)],
        [
            InlineKeyboardButton("Пн", callback_data=f"{CB_SET_TIME_PREFIX}0"),
            InlineKeyboardButton("Вт", callback_data=f"{CB_SET_TIME_PREFIX}1"),
            InlineKeyboardButton("Ср", callback_data=f"{CB_SET_TIME_PREFIX}2"),
        ],
        [
            InlineKeyboardButton("Чт", callback_data=f"{CB_SET_TIME_PREFIX}3"),
            InlineKeyboardButton("Пт", callback_data=f"{CB_SET_TIME_PREFIX}4"),
            InlineKeyboardButton("Сб", callback_data=f"{CB_SET_TIME_PREFIX}5"),
        ],
        [
            InlineKeyboardButton("Вс", callback_data=f"{CB_SET_TIME_PREFIX}6"),
            InlineKeyboardButton("Обновить", callback_data=CB_SHOW_SCHEDULE),
            InlineKeyboardButton("❌ Отписаться", callback_data="unsubscribe"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Назад'."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data=CB_SHOW_SCHEDULE)]])

def get_daily_question_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для ежедневного вопроса."""
    keyboard = [
        [InlineKeyboardButton("Готов", callback_data=CB_READY),
         InlineKeyboardButton("Скорее готов", callback_data=CB_PROBABLY_READY)],
        [InlineKeyboardButton("Скорее не готов", callback_data=CB_PROBABLY_NOT_READY),
         InlineKeyboardButton("Не готов", callback_data=CB_NOT_READY)]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== Обработчики команд ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    if not await get_user(user_id):
        await create_user(user_id, username)
    await show_schedule(update, context)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MSG_HELP, parse_mode='Markdown')

async def cmd_my_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        await cmd_start(update, context)
        return
    nickname = user[2]  # nickname
    user_times = await get_user_times(user_id)
    times_dict = {day: time for day, time in user_times}
    message = MSG_MY_SCHEDULE_TITLE + format_schedule_for_user(nickname, times_dict)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Общее расписание", callback_data=CB_SHOW_SCHEDULE)]])
    await update.message.reply_text(message, reply_markup=keyboard, parse_mode='Markdown')

async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await set_subscription(user_id, True)
    await update.message.reply_text(MSG_SUBSCRIBED)

async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await set_subscription(user_id, False)
    await update.message.reply_text(MSG_UNSUBSCRIBED)

# ==================== Отображение расписания ====================
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать общее расписание с главной клавиатурой."""
    user_times = await get_all_users_times()
    schedule_by_day = {i: [] for i in range(7)}
    for nickname, day, start_time in user_times:
        if start_time and day is not None:
            schedule_by_day[day].append(f"{nickname}: {start_time}")

    message = MSG_SCHEDULE_TITLE
    for day_num in range(7):
        message += f"**{DAYS_RU[day_num]}:**\n"
        if schedule_by_day[day_num]:
            for entry in schedule_by_day[day_num]:
                message += f"  • {entry}\n"
        else:
            message += MSG_NO_INFO + "\n"
        message += "\n"

    reply_markup = get_main_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        # Сбрасываем флаги ожидания, если были
        context.user_data.pop('awaiting_nickname', None)
        context.user_data.pop('awaiting_time', None)
        context.user_data.pop('awaiting_custom_time', None)
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

# ==================== Обработчики callback'ов ====================
async def handle_show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_schedule(update, context)

async def handle_my_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = await get_user(user_id)
    if not user:
        await show_schedule(update, context)
        return
    nickname = user[2]
    user_times = await get_user_times(user_id)
    times_dict = {day: time for day, time in user_times}
    message = MSG_MY_SCHEDULE_TITLE + format_schedule_for_user(nickname, times_dict)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Общее расписание", callback_data=CB_SHOW_SCHEDULE)]])
    await query.edit_message_text(message, reply_markup=keyboard, parse_mode='Markdown')

async def handle_change_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        MSG_CHANGE_NICK_PROMPT,
        reply_markup=get_back_keyboard()
    )
    context.user_data['awaiting_nickname'] = True

async def handle_set_time(update: Update, context: ContextTypes.DEFAULT_TYPE, day: int):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        MSG_SET_TIME_PROMPT.format(DAYS_RU[day]),
        reply_markup=get_back_keyboard()
    )
    context.user_data['awaiting_time'] = day

async def handle_daily_response(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str):
    query = update.callback_query
    await query.answer()
    status_enum = ResponseStatus.from_callback(callback_data)
    if not status_enum:
        return
    status = status_enum.value

    user_id = query.from_user.id
    # Проверяем, был ли уже ответ сегодня
    today_responses = await get_today_responses()
    existing = next((r for r in today_responses if r[0] == user_id), None)
    is_update = existing is not None

    if status_enum == ResponseStatus.READY:
        # Запрашиваем кастомное время
        await query.edit_message_text(
            MSG_READY_CUSTOM_TIME,
            reply_markup=get_back_keyboard()
        )
        context.user_data['awaiting_custom_time'] = True
        context.user_data['temp_response_status'] = status
    else:
        await save_daily_response(user_id, status)
        msg = MSG_RESPONSE_UPDATED if is_update else MSG_RESPONSE_RECORDED
        await query.edit_message_text(
            msg.format(status),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data=CB_SHOW_SCHEDULE)]])
        )

async def handle_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await set_subscription(user_id, False)
    await query.edit_message_text(
        MSG_UNSUBSCRIBED,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data=CB_SHOW_SCHEDULE)]])
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный диспетчер callback'ов."""
    query = update.callback_query
    data = query.data

    # Обработка каждого callback'а в отдельной функции
    if data == CB_SHOW_SCHEDULE:
        await handle_show_schedule(update, context)
    elif data == CB_MY_SCHEDULE:
        await handle_my_schedule(update, context)
    elif data == CB_CHANGE_NICK:
        await handle_change_nick(update, context)
    elif data.startswith(CB_SET_TIME_PREFIX):
        day = int(data.split("_")[2])
        await handle_set_time(update, context, day)
    elif data in [CB_READY, CB_PROBABLY_READY, CB_PROBABLY_NOT_READY, CB_NOT_READY]:
        await handle_daily_response(update, context, data)
    elif data == "unsubscribe":
        await handle_unsubscribe(update, context)

# ==================== Обработчик текстовых сообщений ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if context.user_data.get('awaiting_nickname'):
        # Изменение ника
        await update_nickname(user_id, text)
        context.user_data['awaiting_nickname'] = False
        await update.message.reply_text(
            MSG_NICK_CHANGED.format(text),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data=CB_SHOW_SCHEDULE)]])
        )

    elif context.user_data.get('awaiting_time') is not None:
        day = context.user_data['awaiting_time']
        if format_time_validation(text):
            await set_user_time(user_id, day, text)
            context.user_data['awaiting_time'] = None
            await update.message.reply_text(
                MSG_TIME_SET.format(DAYS_RU[day], text),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data=CB_SHOW_SCHEDULE)]])
            )
        else:
            await update.message.reply_text(MSG_INVALID_TIME)

    elif context.user_data.get('awaiting_custom_time'):
        if format_time_validation(text):
            status = context.user_data.get('temp_response_status', ResponseStatus.READY.value)
            await save_daily_response(user_id, status, text)
            context.user_data['awaiting_custom_time'] = False
            context.user_data.pop('temp_response_status', None)
            await update.message.reply_text(
                MSG_CUSTOM_TIME_SAVED.format(text),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К расписанию", callback_data=CB_SHOW_SCHEDULE)]])
            )
        else:
            await update.message.reply_text(MSG_INVALID_TIME)

# ==================== Ежедневные задачи ====================
async def send_question(context: ContextTypes.DEFAULT_TYPE):
    """Отправка ежедневного вопроса всем подписанным пользователям в 18:00 МСК."""
    user_ids = await get_all_users()
    for user_id in user_ids:
        if await get_subscription_status(user_id):
            await send_question_to_user(user_id, context)

async def send_question_to_user(user_id: int, context: ContextTypes.DEFAULT_TYPE, force: bool = False):
    """Отправить вопрос конкретному пользователю (force для ручного вызова)."""
    if not force and not await mark_question_sent(user_id):
        logging.info(f"Вопрос уже отправлялся пользователю {user_id} сегодня, пропуск.")
        return
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=MSG_DAILY_QUESTION,
            reply_markup=get_daily_question_keyboard(),
            parse_mode='Markdown'
        )
        logging.info(f"Ежедневный вопрос отправлен пользователю {user_id}")
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

async def send_summary(context: ContextTypes.DEFAULT_TYPE):
    """Отправка сводки всем зарегистрированным пользователям в 20:00 МСК."""
    responses = await get_today_responses()
    if not responses:
        summary_message = MSG_NO_RESPONSES_TODAY
    else:
        summary_message = "**Сводка за сегодня:**\n\n"
        for user_id_r, nickname, status, custom_time, responded_at in responses:
            try:
                dt = datetime.fromisoformat(responded_at)
                time_str = dt.strftime('%H:%M')
            except (ValueError, TypeError):
                time_str = str(responded_at)
            if custom_time:
                summary_message += f"• **{nickname}**: {status} ({custom_time}) в {time_str}\n"
            else:
                summary_message += f"• **{nickname}**: {status} в {time_str}\n"

    user_ids = await get_all_users()
    for uid in user_ids:
        try:
            keyboard = [[InlineKeyboardButton("📅 К расписанию", callback_data=CB_SHOW_SCHEDULE)]]
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

# ==================== Настройка планировщика ====================
def setup_jobs(application: Application):
    job_queue = application.job_queue
    if job_queue is None:
        logging.warning("JobQueue не доступен")
        return
    # Используем московский часовой пояс
    job_queue.run_daily(
        send_question,
        time=time(18, 0, tzinfo=MOSCOW_TZ),
        name="daily_question"
    )
    job_queue.run_daily(
        send_summary,
        time=time(20, 0, tzinfo=MOSCOW_TZ),
        name="daily_summary"
    )
    logging.info("Планировщик заданий настроен (МСК)")

# ==================== Инициализация и запуск ====================
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

    # Команды
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("my_schedule", cmd_my_schedule))
    application.add_handler(CommandHandler("subscribe", cmd_subscribe))
    application.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))

    # Обработчики
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    setup_jobs(application)

    logging.info("Бот запущен и готов к работе!")
    print("=" * 50)
    print("Планировщик запущен!")
    print("Бот работает в фоновом режиме")
    print("Ежедневные уведомления (МСК):")
    print("  - 18:00: вопрос о встрече")
    print("  - 20:00: сводка по ответам")
    print("=" * 50)

    application.run_polling()

if __name__ == '__main__':
    main()
