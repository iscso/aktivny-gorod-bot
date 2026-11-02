import asyncio
import os
from maxgram import Bot, Dispatcher, Router, F
from maxgram.filters import Command
from maxgram.fsm import FSMContext, MemoryStorage
from maxgram.fsm.state import State, StatesGroup
from maxgram.utils.keyboard import InlineKeyboardBuilder
from gigachat import GigaChat

# --- Инициализация ---
bot = Bot(token=os.environ.get("MAX_BOT_TOKEN"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

giga = GigaChat(credentials=os.environ.get("GIGACHAT_CREDENTIALS"))

# --- Определение состояний (FSM) ---
class Registration(StatesGroup):
    waiting_for_consent = State()
    waiting_for_fio = State()
    waiting_for_org = State()
    registered = State()

# --- ССЫЛКА НА ВАШ PDF ---
CONSENT_URL = "https://disk.yandex.ru/i/Our6yPmMIvXAig" 

# ==========================================
# СЦЕНАРИЙ РЕГИСТРАЦИИ
# ==========================================

@router.bot_started()
@router.message(Command("start"))
async def start_handler(message, state: FSMContext):
    await state.clear()
    
    welcome_text = (
        "👋 Добро пожаловать в бот «Активный город»!\n\n"
        "Сервис разработан АНО «ИСКСО» (Институт стратегических коммуникаций и связей с общественностью) "
        "при поддержке Управления общественных связей мэрии города Новосибирска.\n\n"
        "Для начала работы, пожалуйста, ознакомьтесь с документом:\n"
        f"📄 [Скачать согласие на обработку ПДн]({CONSENT_URL})\n\n"
        "Нажимая кнопку ниже, вы подтверждаете, что ознакомились и согласны с условиями."
    )
    
    builder = InlineKeyboardBuilder()
    builder.callback(text="✅ Я согласен", payload="consent_agree")
    
    await state.set_state(Registration.waiting_for_consent)
    await message.answer(text=welcome_text, keyboard=builder, disable_web_page_preview=False)

@router.message_callback(F.payload == "consent_agree")
async def process_consent(callback, state: FSMContext):
    await callback.answer(notification="Согласие принято")
    await callback.message.delete()
    
    await state.set_state(Registration.waiting_for_fio)
    await callback.message.answer(
        "Отлично! Теперь, пожалуйста, напишите ваши **ФИО** (например: Иванов Иван Иванович):"
    )

@router.message(Registration.waiting_for_fio)
async def process_fio(message, state: FSMContext):
    await state.update_data(fio=message.text)
    await state.set_state(Registration.waiting_for_org)
    await message.answer(
        f"Спасибо, {message.text}!\n\n"
        "Теперь укажите **организацию**, которую вы представляете:"
    )

@router.message(Registration.waiting_for_org)
async def process_org(message, state: FSMContext):
    await state.update_data(org=message.text)
    data = await state.get_data()
    
    print(f"НОВЫЙ ПОЛЬЗОВАТЕЛЬ: ФИО: {data['fio']} | Орг: {data['org']} | ID: {message.from_user.id}")
    
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
    user_text = message.text
    if not user_text:
        return

    try:
        prompt = (
            "Ты — дружелюбный ассистент «Активный город». "
            "Помогай горожанам находить события, волонтёрские возможности и меры поддержки.\n"
            f"Вопрос пользователя: {user_text}"
        )
        response = await giga.achat.create(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        answer = response.messages[0].content[0].text
    except Exception as e:
        answer = "Извините, сервис временно недоступен. Попробуйте позже."
        print(f"GigaChat error: {e}")

    await message.answer(text=answer)

@router.message(Registration.waiting_for_consent)
async def block_until_consent(message):
    await message.answer("Пожалуйста, сначала нажмите кнопку «✅ Я согласен» в сообщении выше, чтобы продолжить.")

# ==========================================
# ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("Бот запущен...")
    asyncio.run(dp.start_polling(bot))
