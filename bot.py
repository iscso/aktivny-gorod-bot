import asyncio
import os
import logging
from maxgram import Bot, Dispatcher, Router, F
from maxgram.filters import Command
from maxgram.fsm.context import FSMContext
from maxgram.fsm.storage.memory import MemoryStorage
from maxgram.fsm.state import State, StatesGroup
from maxgram.utils.keyboard import InlineKeyboardBuilder
from gigachat import GigaChat

# Настройка логирования для Bothost
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Инициализация ---
bot = Bot(token=os.environ.get("MAX_BOT_TOKEN"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Инициализация GigaChat (отключена проверка SSL для облака)
giga = GigaChat(
    credentials=os.environ.get("GIGACHAT_CREDENTIALS"),
    verify_ssl_certs=False,
    model="GigaChat"
)

# --- Определение состояний (FSM) ---
class Registration(StatesGroup):
    waiting_for_consent = State()
    waiting_for_fio = State()
    waiting_for_org = State()
    registered = State()

# --- ССЫЛКА НА ВАШ PDF ---
CONSENT_URL = "https://disk.yandex.ru/i/Our6yPmMIvXAig"

# --- Безопасное получение текста сообщения ---
def get_text(message) -> str:
    if hasattr(message, 'body') and hasattr(message.body, 'text'):
        return message.body.text or ""
    return ""

# ==========================================
# СЦЕНАРИЙ РЕГИСТРАЦИИ
# ==========================================

@router.bot_started()
@router.message(Command("start"))
async def start_handler(message, state: FSMContext):
    await state.clear()
    
    welcome_text = (
        "👋 Добро пожаловать в бот «Активный город»!\n\n"
        "Сервис разработан АНО «ИСКСО» при поддержке Управления общественных связей мэрии города Новосибирска.\n\n"
        "Для начала работы, пожалуйста, ознакомьтесь с документом:\n"
        f"{CONSENT_URL}\n\n"
        "Нажимая кнопку ниже, вы подтверждаете, что ознакомились и согласны с условиями."
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
    await callback.message.answer(
        "Отлично! Теперь, пожалуйста, напишите ваши ФИО (например: Иванов Иван Иванович):"
    )

@router.message(Registration.waiting_for_fio)
async def process_fio(message, state: FSMContext):
    text = get_text(message)
    if not text:
        return
        
    await state.update_data(fio=text)
    await state.set_state(Registration.waiting_for_org)
    await message.answer(
        f"Спасибо, {text}!\n\n"
        "Теперь укажите организацию, которую вы представляете:"
    )

@router.message(Registration.waiting_for_org)
async def process_org(message, state: FSMContext):
    text = get_text(message)
    if not text:
        return

    await state.update_data(org=text)
    data = await state.get_data()
    
    logger.info(f"НОВЫЙ ПОЛЬЗОВАТЕЛЬ: ФИО: {data.get('fio')} | Орг: {data.get('org')}")
    
    await state.set_state(Registration.registered)
    
    await message.answer(
        "🎉 Регистрация завершена!\n"
        "Теперь вы можете задавать любые вопросы о городских событиях, волонтерстве и мерах поддержки."
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
            "Ты — дружелюбный ассистент «Активный город». "
            "Помогай горожанам находить события, волонтёрские возможности и меры поддержки.\n"
            f"Вопрос пользователя: {user_text}"
        )
        
        # Исправленный вызов: передаем строку напрямую
        response = await giga.achat.create(prompt)
        
        # Универсальная распаковка ответа (на случай разных версий SDK)
        try:
            answer = response.messages[0].content[0].text
        except (AttributeError, IndexError):
            try:
                answer = response.choices[0].message.content
            except (AttributeError, IndexError):
                answer = str(response)
        
    except Exception as e:
        logger.error(f"!!! ОШИБКА GIGACHAT: {type(e).__name__}: {e}", exc_info=True)
        # ВРЕМЕННО ДЛЯ ОТЛАДКИ
        answer = f"🛠 РЕЖИМ ОТЛАДКИ\nТип ошибки: {type(e).__name__}\nТекст: {e}"

    await message.answer(text=answer)

# Блокировка для тех, кто не нажал кнопку
@router.message(Registration.waiting_for_consent)
async def block_until_consent(message):
    await message.answer("Пожалуйста, сначала нажмите кнопку «✅ Я согласен» в сообщении выше, чтобы продолжить.")

# ==========================================
# ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("Бот запущен...")
    asyncio.run(dp.start_polling(bot))
