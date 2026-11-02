import asyncio
import os
import re
import logging
from maxgram import Bot, Dispatcher, Router, F
from maxgram.filters import Command
from maxgram.fsm.context import FSMContext
from maxgram.fsm.storage.memory import MemoryStorage
from maxgram.fsm.state import State, StatesGroup
from maxgram.utils.keyboard import InlineKeyboardBuilder
from gigachat import GigaChat

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

# --- Состояния ---
class Registration(StatesGroup):
    waiting_for_consent = State()
    waiting_for_fio = State()
    waiting_for_org = State()
    registered = State()

class Feedback(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()

CONSENT_URL = "https://disk.yandex.ru/i/Our6yPmMIvXAig"

def get_text(message) -> str:
    if hasattr(message, 'body') and hasattr(message.body, 'text'):
        return message.body.text or ""
    return ""

def clean_text(text: str) -> str:
    if not text:
        return text
    return re.sub(r'[*#_]', '', text)

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.callback(text="🚀 Начать работу", payload="menu:start_work")
    builder.callback(text="💬 Оставить обратную связь", payload="menu:feedback")
    builder.callback(text="🔒 Политика конфиденциальности", payload="menu:privacy")
    builder.adjust(1)
    return builder

# ==========================================
# СТАРТ И РЕГИСТРАЦИЯ
# ==========================================

@router.bot_started()
@router.message(Command("start"))
async def start_handler(message, state: FSMContext):
    await state.clear()
    welcome_text = (
        "👋 Добро пожаловать в бот «Активный город»!\n\n"
        "Это интеллектуальный помощник для ТОС и НКО Новосибирской области.\n"
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
    await callback.answer(notification="Согласие принято")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.set_state(Registration.waiting_for_fio)
    await callback.message.answer("Отлично! Напишите ваши ФИО:")

@router.message(Registration.waiting_for_fio)
async def process_fio(message, state: FSMContext):
    text = get_text(message)
    if not text:
        return
    max_user_id = None
    if hasattr(message, 'sender') and hasattr(message.sender, 'user_id'):
        max_user_id = message.sender.user_id
    elif hasattr(message, 'from_user') and hasattr(message.from_user, 'id'):
        max_user_id = message.from_user.id
    await state.update_data(fio=text, max_user_id=max_user_id)
    await state.set_state(Registration.waiting_for_org)
    await message.answer(f"Спасибо, {text}!\n\nУкажите организацию (ТОС или НКО):")

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
        "🎉 Регистрация завершена!\n\n"
        "Теперь вы можете задавать вопросы или воспользоваться меню.",
        keyboard=get_main_menu()
    )

# ==========================================
# МЕНЮ И ОБРАТНАЯ СВЯЗЬ
# ==========================================

@router.message(Command("menu"))
async def menu_handler(message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != Registration.registered:
        await message.answer("Пожалуйста, сначала пройдите регистрацию: /start")
        return
    await message.answer("Главное меню:", keyboard=get_main_menu())

@router.message_callback(F.payload == "menu:start_work")
async def menu_start_work(callback, state: FSMContext):
    await callback.answer(notification="Меню закрыто. Задайте ваш вопрос!")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Я слушаю ваш вопрос. Чем могу помочь?")

@router.message_callback(F.payload == "menu:privacy")
async def menu_privacy(callback):
    await callback.answer(notification="Ссылка на политику")
    await callback.message.answer(f"🔒 Политика конфиденциальности и согласие на обработку данных:\n{CONSENT_URL}")

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
    await callback.message.answer("Оцените, пожалуйста, работу бота от 1 до 5:", keyboard=builder)

@router.message_callback(F.payload.startswith("rate:"))
async def process_rating(callback, state: FSMContext):
    rating = callback.payload.split(":")[1]
    await state.update_data(rating=rating)
    await callback.answer(notification=f"Вы поставили {rating}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.set_state(Feedback.waiting_for_comment)
    await callback.message.answer(f"Спасибо за оценку {rating}!\n\nНапишите, пожалуйста, ваш отзыв или пожелание:")

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
        "🙏 Спасибо за ваш отзыв! Мы обязательно учтём его в работе.",
        keyboard=get_main_menu()
    )

# ==========================================
# ОСНОВНОЙ ФУНКЦИОНАЛ (GigaChat)
# ==========================================

@router.message(Registration.registered)
async def ai_handler(message):
    user_text = get_text(message)
    if not user_text:
        return
    try:
        prompt = (
            "Ты — экспертный ИИ-ассистент «Активный город». "
            "Твоя целевая аудитория — активисты ТОС и НКО Новосибирской области.\n"
            "Помогай писать посты для соцсетей, готовить презентации, придумывать идеи для картинок, "
            "оформлять грантовые заявки и отвечать на вопросы по организации мероприятий.\n\n"
            "ПРАВИЛО: Пиши структурированно. НЕ используй Markdown (звёздочки, решётки, подчёркивания).\n\n"
            f"Запрос пользователя: {user_text}"
        )
        response = await giga.achat.create(prompt)
        raw_answer = response.messages[0].content[0].text
        clean_answer = clean_text(raw_answer)
        disclaimer = "\n\n---\n(Ответ сгенерирован с помощью технологий искусственного интеллекта и носит справочный характер)"
        answer = clean_answer + disclaimer
    except Exception as e:
        logger.error(f"!!! ОШИБКА GIGACHAT: {type(e).__name__}: {e}", exc_info=True)
        answer = f"🛠 РЕЖИМ ОТЛАДКИ\nТип ошибки: {type(e).__name__}\nТекст: {e}"
    await message.answer(text=answer)

@router.message(Registration.waiting_for_consent)
async def block_until_consent(message):
    await message.answer("Пожалуйста, сначала нажмите кнопку «✅ Я согласен» в сообщении выше.")

@router.message(F.text)
async def fallback_handler(message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await start_handler(message, state)
    else:
        await message.answer("Я вас не совсем понял. Нажмите /start, чтобы обновить меню.")

if __name__ == "__main__":
    print("Бот запущен...")
    asyncio.run(dp.start_polling(bot))
