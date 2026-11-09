import asyncio
import os
import re
import logging
import sqlite3
import datetime
import csv
import io
import aiohttp
from maxgram import Bot, Dispatcher, Router, F
from maxgram.filters import Command
from maxgram.fsm.context import FSMContext
from maxgram.fsm.storage.memory import MemoryStorage
from maxgram.fsm.state import State, StatesGroup
from maxgram.utils.keyboard import InlineKeyboardBuilder
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=os.environ.get("MAX_BOT_TOKEN"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

giga = GigaChat(
    credentials=os.environ.get("GIGACHAT_CREDENTIALS"),
    verify_ssl_certs=False,
    model="GigaChat-2-Max",
    scope="GIGACHAT_API_PERS"
)

admin_ids_str = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = []
if admin_ids_str:
    try:
        ADMIN_IDS = [int(i.strip()) for i in admin_ids_str.split(",") if i.strip().isdigit()]
        logger.info(f"✅ Загружены ID администраторов: {ADMIN_IDS}")
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга ADMIN_IDS: {e}")

KNOWLEDGE_FILE_ID = None
CONSENT_URL = "https://disk.yandex.ru/i/Our6yPmMIvXAig"
TECH_SUPPORT_EMAIL = "support@iscso.ru"
UOS_PHONE = "(383) 229-67-72"
KNOWLEDGE_FILE_PATH = "knowledge_base.pdf"
MAX_API_BASE = "https://platform-api2.max.ru"
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN")

DB_PATH = "/app/data/bot_history.db"

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            text TEXT,
            timestamp DATETIME
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"✅ База данных инициализирована: {DB_PATH}")

def save_message(user_id: int, role: str, text: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (user_id, role, text, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, role, text, datetime.datetime.now())
    )
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT user_id FROM messages")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

def get_user_history(user_id: int, limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT role, text, timestamp FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def get_text(message) -> str:
    if hasattr(message, 'body') and hasattr(message.body, 'text'):
        return message.body.text or ""
    return ""

def get_user_id(message):
    if hasattr(message, 'sender') and hasattr(message.sender, 'user_id'):
        return message.sender.user_id
    elif hasattr(message, 'from_user') and hasattr(message.from_user, 'id'):
        return message.from_user.id
    return None

# --- НОВАЯ ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ID ЧАТА ---
def get_chat_id(message):
    """Пытается найти ID чата в разных полях структуры сообщения MAX."""
    if hasattr(message, 'chat') and hasattr(message.chat, 'id'):
        return message.chat.id
    if hasattr(message, 'recipient'):
        if hasattr(message.recipient, 'chat_id'):
            return message.recipient.chat_id
        if hasattr(message.recipient, 'user_id'):
            return message.recipient.user_id
    if hasattr(message, 'dialog_id'):
        return message.dialog_id
    # Если ничего не нашли, возвращаем ID пользователя (для личных диалогов)
    return get_user_id(message)

def clean_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'[*#_]', '', text)
    text = re.sub(r'^\s*-\s+', '• ', text, flags=re.MULTILINE)
    text = text.replace('---', '')
    return text.strip()

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.callback(text="🚀 Начать работу", payload="menu:start_work")
    builder.callback(text="💬 Оставить обратную связь", payload="menu:feedback")
    builder.callback(text="🆘 Техподдержка и контакты", payload="menu:support")
    builder.callback(text="🔒 Политика конфиденциальности", payload="menu:privacy")
    builder.adjust(1)
    return builder

async def upload_knowledge_base():
    global KNOWLEDGE_FILE_ID
    try:
        if not os.path.exists(KNOWLEDGE_FILE_PATH):
            logger.warning(f"⚠️ Файл {KNOWLEDGE_FILE_PATH} не найден.")
            return

        with open(KNOWLEDGE_FILE_PATH, "rb") as f:
            uploaded = await giga.aupload_file(file=f, purpose="general")
            
            file_id = (
                getattr(uploaded, 'id', None) or
                getattr(uploaded, 'id_', None) or
                getattr(uploaded, 'file_id', None) or
                getattr(uploaded, 'uuid', None)
            )
            if not file_id and hasattr(uploaded, '__dict__'):
                file_id = uploaded.__dict__.get('id') or uploaded.__dict__.get('id_')

            if file_id:
                KNOWLEDGE_FILE_ID = file_id
                logger.info(f"✅ База знаний загружена. FILE_ID: {KNOWLEDGE_FILE_ID}")
            else:
                logger.warning(f"⚠️ ID файла не найден.")
                
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки базы знаний: {e}")

async def send_document_via_max_api(chat_id: int, file_path: str, filename: str, caption: str = None) -> bool:
    headers = {"Authorization": f"Bearer {MAX_BOT_TOKEN}"}

    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.post(
                f"{MAX_API_BASE}/uploads",
                headers=headers,
                json={"type": "file"}
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка получения URL для загрузки: {resp.status} {await resp.text()}")
                    return False
                data = await resp.json()
                upload_url = data.get("url")
                if not upload_url:
                    logger.error("В ответе API нет URL для загрузки")
                    return False

            with open(file_path, "rb") as f:
                form_data = aiohttp.FormData()
                form_data.add_field("file", f, filename=filename)
                async with session.post(upload_url, data=form_data) as upload_resp:
                    if upload_resp.status != 200:
                        logger.error(f"Ошибка загрузки файла: {upload_resp.status} {await upload_resp.text()}")
                        return False
                    upload_data = await upload_resp.json()
                    file_token = upload_data.get("token") or upload_data.get("file_id")
                    if not file_token:
                        logger.error("В ответе нет токена загруженного файла")
                        return False

            payload = {
                "chat_id": chat_id,
                "text": caption or filename,
                "attachments": [
                    {
                        "type": "file",
                        "payload": {
                            "file_id": file_token
                        }
                    }
                ]
            }
            async with session.post(
                f"{MAX_API_BASE}/messages",
                headers=headers,
                json=payload
            ) as msg_resp:
                if msg_resp.status != 200:
                    logger.error(f"Ошибка отправки сообщения с файлом: {msg_resp.status} {await msg_resp.text()}")
                    return False

        logger.info(f"✅ Документ {filename} успешно отправлен в чат {chat_id}")
        return True

    except Exception as e:
        logger.error(f"Исключение при отправке документа: {e}", exc_info=True)
        return False

class Registration(StatesGroup):
    waiting_for_consent = State()
    waiting_for_fio = State()
    waiting_for_org = State()
    registered = State()

class Feedback(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

class AdminStates(StatesGroup):
    waiting_for_file = State()

@router.bot_started()
@router.message(Command("start"))
async def start_handler(message, state: FSMContext):
    await state.clear()
    welcome_text = (
        "👋 Здравствуйте! Добро пожаловать в бот «Активный город».\n\n"
        "Я — ваш интеллектуальный помощник для ТОС и НКО Новосибирской области.\n"
        "Сервис разработан АНО «ИСКСО» при поддержке Управления общественных связей мэрии города Новосибирска.\n\n"
        f"Пожалуйста, ознакомьтесь с документом:\n{CONSENT_URL}\n\n"
        "Нажимая кнопку ниже, вы подтверждаете, что ознакомились и согласны с условиями.\n\n"
        "⚠️ Ответы генерируются с помощью ИИ и носят справочный характер."
    )
    builder = InlineKeyboardBuilder()
    builder.callback(text="✅ Я согласен", payload="consent_agree")
    await state.set_state(Registration.waiting_for_consent)
    await message.answer(text=welcome_text, keyboard=builder)

@router.message_callback(F.payload == "consent_agree")
async def process_consent(callback, state: FSMContext):
    await callback.answer(notification="Спасибо за согласие")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.set_state(Registration.waiting_for_fio)
    await callback.message.answer("Отлично! Пожалуйста, напишите ваши ФИО:")

@router.message(Registration.waiting_for_fio)
async def process_fio(message, state: FSMContext):
    text = get_text(message)
    if not text:
        return
    max_user_id = get_user_id(message)
    await state.update_data(fio=text, max_user_id=max_user_id)
    await state.set_state(Registration.waiting_for_org)
    await message.answer(f"Спасибо, {text}! Очень приятно познакомиться.\n\nПодскажите, пожалуйста, какую организацию (ТОС или НКО) вы представляете?")

@router.message(Registration.waiting_for_org)
async def process_org(message, state: FSMContext):
    text = get_text(message)
    if not text:
        return
    await state.update_data(org=text)
    data = await state.get_data()
    logger.info(f"НОВЫЙ ПОЛЬЗОВАТЕЛЬ: ФИО={data.get('fio')} | Орг={data.get('org')} | MAX_ID={data.get('max_user_id')}")
    await state.set_state(Registration.registered)
    await message.answer(
        "🎉 Спасибо! Регистрация успешно завершена.\n\n"
        "Теперь я в вашем распоряжении. Вы можете задать мне любой вопрос или воспользоваться меню.",
        keyboard=get_main_menu()
    )

@router.message(Command("menu"))
async def menu_handler(message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != Registration.registered:
        await message.answer("Пожалуйста, сначала пройдите регистрацию: /start")
        return
    await message.answer("Главное меню:", keyboard=get_main_menu())

@router.message_callback(F.payload == "menu:start_work")
async def menu_start_work(callback, state: FSMContext):
    await callback.answer(notification="Задайте ваш вопрос")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Я вас внимательно слушаю. Напишите, чем я могу вам помочь?")

@router.message_callback(F.payload == "menu:support")
async def menu_support(callback):
    await callback.answer(notification="Загружаю контакты...")
    try:
        await callback.message.delete()
    except Exception:
        pass
    support_text = (
        "🆘 Мы всегда готовы прийти на помощь!\n\n"
        "Если у вас возникли технические сложности с ботом:\n"
        f"📧 Напишите нам: {TECH_SUPPORT_EMAIL}\n\n"
        "Если у вас вопросы общего характера (о городских событиях, мероприятиях или мерах поддержки):\n"
        f"📞 Позвоните в Управление общественных связей мэрии: {UOS_PHONE}"
    )
    await callback.message.answer(text=support_text, keyboard=get_main_menu())

@router.message_callback(F.payload == "menu:privacy")
async def menu_privacy(callback):
    await callback.answer(notification="Ссылка на политику")
    await callback.message.answer(f"🔒 Политика конфиденциальности:\n{CONSENT_URL}")

@router.message_callback(F.payload == "menu:feedback")
async def menu_feedback(callback, state: FSMContext):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.set_state(Feedback.waiting_for_rating)
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.callback(text=f"{i} ⭐", payload=f"rate:{i}")
    builder.adjust(5)
    await callback.message.answer("Нам очень важно ваше мнение! Оцените, пожалуйста, работу бота от 1 до 5:", keyboard=builder)

@router.message_callback(F.payload.startswith("rate:"))
async def process_rating(callback, state: FSMContext):
    rating = callback.payload.split(":")[1]
    await state.update_data(rating=rating)
    await callback.answer(notification=f"Спасибо за оценку {rating}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.set_state(Feedback.waiting_for_comment)
    await callback.message.answer(f"Большое спасибо за оценку {rating}!\n\nНапишите, пожалуйста, ваш отзыв или пожелание:")

@router.message(Feedback.waiting_for_comment)
async def process_feedback_comment(message, state: FSMContext):
    comment = get_text(message)
    if not comment:
        return
    data = await state.get_data()
    rating = data.get('rating', 'не указана')
    user_id = data.get('max_user_id', 'неизвестен')
    logger.info(f"ОБРАТНАЯ СВЯЗЬ: Оценка={rating} | Отзыв={comment} | MAX_ID={user_id}")
    await state.set_state(Registration.registered)
    await message.answer(
        "🙏 Сердечно благодарим вас за отзыв! Мы обязательно учтем его в нашей работе, чтобы стать еще лучше.",
        keyboard=get_main_menu()
    )

@router.message(Command("admin"))
async def admin_panel(message):
    user_id = get_user_id(message)
    
    if user_id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    users = get_all_users()
    if not users:
        await message.answer("📭 История диалогов пока пуста.")
        return

    text = "👑 Админ-панель\n\n"
    text += f"Всего пользователей: {len(users)}\n\n"
    text += "Доступные команды:\n"
    text += "/history ID_ПОЛЬЗОВАТЕЛЯ — посмотреть историю\n"
    text += "/upload — загрузить новый PDF с базой знаний\n"
    text += "/export — выгрузить всю историю в CSV файл\n\n"
    text += "Доступные ID:\n"
    text += ", ".join([f"{u}" for u in users[:20]])
    
    await message.answer(text=text)

@router.message(Command("history"))
async def show_user_history(message):
    user_id = get_user_id(message)
    
    if user_id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа.")
        return

    parts = get_text(message).split()
    if len(parts) < 2:
        await message.answer("Использование: /history ID_ПОЛЬЗОВАТЕЛЯ")
        return
    
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return

    history = get_user_history(target_id, limit=30)
    if not history:
        await message.answer(f"Для пользователя {target_id} история не найдена.")
        return

    text = f"📜 История диалога с {target_id} (последние 30 сообщений)\n\n"
    for role, msg_text, ts in reversed(history):
        time_str = ts.strftime("%d.%m %H:%M") if ts else ""
        prefix = "👤" if role == "user" else "🤖"
        short_text = msg_text[:150] + "..." if len(msg_text) > 150 else msg_text
        text += f"[{time_str}] {prefix}: {short_text}\n\n"

    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text=text[i:i+4000])
    else:
        await message.answer(text=text)

@router.message(Command("export"))
async def export_history(message):
    user_id = get_user_id(message)
    if user_id not in ADMIN_IDS:
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, role, text, timestamp FROM messages ORDER BY timestamp")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await message.answer("История пуста.")
        return

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(['User ID', 'Role', 'Text', 'Timestamp'])
    for row in rows:
        writer.writerow(row)

    file_path = "exported_history.csv"
    with open(file_path, "w", encoding="utf-8-sig") as f:
        f.write(output.getvalue())

    # Используем универсальную функцию получения chat_id
    chat_id = get_chat_id(message)
    if not chat_id:
        await message.answer("Не удалось определить ID чата для отправки файла.")
        return

    success = await send_document_via_max_api(
        chat_id=chat_id,
        file_path=file_path,
        filename="history.csv",
        caption="📊 История диалогов"
    )

    if not success:
        await message.answer("Файл сформирован, но отправить его не удалось. Попробуйте позже или обратитесь в поддержку.")

@router.message(Command("upload"))
async def admin_upload_start(message, state: FSMContext):
    user_id = get_user_id(message)
    if user_id not in ADMIN_IDS:
        return
    
    await state.set_state(AdminStates.waiting_for_file)
    await message.answer("📎 Пожалуйста, отправьте мне PDF-файл с обновленной базой знаний. Я его скачаю и обновлю свой «мозг».")

@router.message(AdminStates.waiting_for_file)
async def process_admin_file(message, state: FSMContext):
    user_id = get_user_id(message)
    if user_id not in ADMIN_IDS:
        return

    attachments = getattr(message.body, 'attachments', None) or []
    file_attachment = None

    for att in attachments:
        att_type = getattr(att, 'type', None) or (att.get('type') if isinstance(att, dict) else None)
        if att_type == 'file':
            file_attachment = att
            break

    if not file_attachment:
        await message.answer("Вы не прикрепили файл. Пожалуйста, отправьте документ в формате PDF.")
        return

    payload = getattr(file_attachment, 'payload', None) or (file_attachment.get('payload') if isinstance(file_attachment, dict) else None)
    if not payload:
        await message.answer("Не удалось прочитать данные файла.")
        return

    file_token = (
        getattr(payload, 'token', None) or
        getattr(payload, 'file_id', None) or
        (payload.get('token') if isinstance(payload, dict) else None) or
        (payload.get('file_id') if isinstance(payload, dict) else None)
    )

    if not file_token:
        await message.answer("Не удалось распознать ID или токен файла.")
        return

    await message.answer("⏳ Скачиваю ваш документ и загружаю его в базу знаний. Подождите немного...")

    url = f"{MAX_API_BASE}/files/{file_token}"
    headers = {"Authorization": f"Bearer {MAX_BOT_TOKEN}"}

    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(KNOWLEDGE_FILE_PATH, "wb") as f:
                        f.write(content)
                else:
                    await message.answer(f"Ошибка скачивания файла (код {resp.status}).")
                    return

        global KNOWLEDGE_FILE_ID
        with open(KNOWLEDGE_FILE_PATH, "rb") as f:
            uploaded = await giga.aupload_file(file=f, purpose="general")
            
            new_file_id = (
                getattr(uploaded, 'id', None) or
                getattr(uploaded, 'id_', None) or
                getattr(uploaded, 'file_id', None) or
                getattr(uploaded, 'uuid', None)
            )
            if not new_file_id and hasattr(uploaded, '__dict__'):
                new_file_id = uploaded.__dict__.get('id') or uploaded.__dict__.get('id_')

            if new_file_id:
                KNOWLEDGE_FILE_ID = new_file_id
                await message.answer("✅ Успешно! Новый документ загружен, и я уже начал использовать его для ответов на вопросы.")
                logger.info(f"Админ обновил базу знаний. Новый FILE_ID: {KNOWLEDGE_FILE_ID}")
            else:
                await message.answer("⚠️ Файл скачан, но не удалось получить его ID для GigaChat.")
                
    except Exception as e:
        logger.error(f"Ошибка в админ-загрузке файла: {e}", exc_info=True)
        await message.answer(f"🛠 Произошла ошибка при обработке файла: {e}")
    
    finally:
        await state.set_state(Registration.registered)

@router.message(Registration.registered)
async def ai_handler(message):
    user_text = get_text(message)
    if not user_text:
        return

    if user_text.startswith("/"):
        return

    user_id = get_user_id(message)
    if not user_id:
        return

    save_message(user_id, "user", user_text)

    thinking_message = await message.answer("⏳ Секундочку, мне нужно немного подумать...")

    try:
        prompt_with_kb = (
            "Ты — очень вежливый, заботливый и дружелюбный ИИ-ассистент «Активный город». "
            "Твоя целевая аудитория — активисты ТОС, представители НКО и люди старшего поколения. Общайся просто, тепло и уважительно.\n\n"
            "Твои правила:\n"
            "1. Если пользователь задает светский вопрос (например, 'Как дела?', 'Привет', 'Кто ты?'), вежливо ответь ему и предложи свою помощь по вопросам ТОС, НКО, грантам или отчетности.\n"
            "2. Если пользователь задает вопрос по профилю (о мерах поддержки, грантах, отчетности и т.д.), найди ответ в прикрепленном документе. Переформулируй текст из документа проще, чтобы он был понятен, но НЕ меняй суть и факты.\n"
            f"3. Если в документе НЕТ ответа на профильный вопрос, строго ответь фразой: «К сожалению, на сегодняшний день такой информацией я не располагаю. Пожалуйста, обратитесь в Управление общественных связей по телефону {UOS_PHONE}, там вам обязательно подскажут и помогут».\n"
            "4. Не выдумывай факты, не бери информацию из интернета.\n\n"
            f"Вопрос пользователя: {user_text}"
        )
        
        chat_request = Chat(
            messages=[
                Messages(
                    role=MessagesRole.USER,
                    content=prompt_with_kb,
                    attachments=[KNOWLEDGE_FILE_ID] if KNOWLEDGE_FILE_ID else []
                )
            ]
        )
        response = await giga.achat(chat_request)
        
        raw_answer = response.choices[0].message.content
        clean_answer = clean_text(raw_answer)
        
        disclaimer = f"\n\n(Ответ сгенерирован ИИ. При возникновении ошибок пишите: {TECH_SUPPORT_EMAIL})"
        answer = clean_answer + disclaimer
        
    except Exception as e:
        logger.error(f"!!! ОШИБКА GIGACHAT: {e}", exc_info=True)
        answer = f"🛠 Ой, кажется, у меня возникла небольшая техническая заминка. Пожалуйста, попробуйте чуть позже или напишите нам: {TECH_SUPPORT_EMAIL}"

    try:
        await thinking_message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение: {e}")

    save_message(user_id, "bot", answer)
    await message.answer(text=answer)

@router.message(Registration.waiting_for_consent)
async def block_until_consent(message):
    await message.answer("Пожалуйста, сначала нажмите кнопку «✅ Я согласен» в сообщении выше, чтобы мы могли начать.")

@router.message()
async def fallback_handler(message, state: FSMContext):
    current_state = await state.get_state()
    logger.info(f">>> FALLBACK: состояние={current_state}")
    if current_state is None:
        await start_handler(message, state)
    else:
        await message.answer("Я вас не совсем понял. Пожалуйста, нажмите /menu для выбора действия или /start для перезапуска.")

if __name__ == "__main__":
    print("Бот запущен...")
    async def main():
        init_db()
        await upload_knowledge_base()
        await dp.start_polling(bot)
    
    asyncio.run(main())
