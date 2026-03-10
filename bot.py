import os
import logging
import subprocess
import tempfile
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler, CallbackQueryHandler
import speech_recognition as sr
from pydub import AudioSegment
from gigachat import GigaChat

# ========== НАСТРОЙКИ ==========
# ЗАМЕНИТЕ НА ВАШИ ДАННЫЕ С developers.sber.ru
GIGACHAT_CREDENTIALS = "ВАШ_CLIENT_ID:ВАШ_CLIENT_SECRET"
# ===============================

# Загрузка токена
def load_token():
    env_path = Path('.env')
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith('BOT_TOKEN'):
                    return line.strip().split('=')[1].strip("'\"")
    return os.environ.get('BOT_TOKEN')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверка FFmpeg
def check_ffmpeg():
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                               capture_output=True, 
                               text=True, 
                               timeout=5)
        logger.info(f"✅ FFmpeg найден")
        return True
    except Exception as e:
        logger.error(f"❌ FFmpeg НЕ найден: {e}")
        return False

FFMPEG_OK = check_ffmpeg()

# Инициализация GigaChat
def init_gigachat():
    try:
        giga = GigaChat(credentials=GIGACHAT_CREDENTIALS, verify_ssl_certs=False)
        logger.info("✅ GigaChat подключен")
        return giga
    except Exception as e:
        logger.error(f"❌ Ошибка GigaChat: {e}")
        return None

GIGA = init_gigachat()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎤 Распознать речь", callback_data='speech')],
        [InlineKeyboardButton("🤖 Спросить нейросеть", callback_data='ask_ai')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Привет! Я обновленный бот с нейросетью!\n\n"
        "🎤 Отправь голосовое - распознаю речь\n"
        "🤖 Напиши вопрос - отвечу через GigaChat",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'speech':
        await query.edit_message_text("🎤 Отправьте голосовое сообщение")
    elif query.data == 'ask_ai':
        await query.edit_message_text("🤖 Напишите ваш вопрос")

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FFMPEG_OK:
        await update.message.reply_text("⚠️ Бот временно недоступен")
        return

    processing_msg = await update.message.reply_text("⏳ Обрабатываю аудио...")

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Скачиваем
            voice_file = await update.message.voice.get_file()
            ogg_path = os.path.join(temp_dir, "voice.ogg")
            wav_path = os.path.join(temp_dir, "voice.wav")
            
            await voice_file.download_to_drive(ogg_path)
            
            # Конвертируем
            await processing_msg.edit_text("⏳ Конвертирую аудио...")
            audio = AudioSegment.from_ogg(ogg_path)
            audio = audio.set_frame_rate(16000).set_channels(1)
            audio.export(wav_path, format="wav")
            
            # Распознаем
            await processing_msg.edit_text("⏳ Распознаю речь...")
            recognizer = sr.Recognizer()
            
            with sr.AudioFile(wav_path) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio_data = recognizer.record(source)
                
                text = recognizer.recognize_google(audio_data, language="ru-RU")
                await processing_msg.edit_text(f"📝 **Распознано:**\n\n{text}")
                
        except sr.UnknownValueError:
            await processing_msg.edit_text("😕 Не удалось распознать речь")
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await processing_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста - отправка в нейросеть"""
    if not GIGA:
        await update.message.reply_text("❌ Нейросеть не подключена. Проверьте credentials")
        return
    
    user_text = update.message.text
    
    # Пропускаем команды
    if user_text.startswith('/'):
        return
    
    processing_msg = await update.message.reply_text("🤔 Думаю...")
    
    try:
        response = GIGA.chat(user_text)
        ai_answer = response.choices[0].message.content
        await processing_msg.edit_text(f"🤖 **Ответ:**\n\n{ai_answer}")
    except Exception as e:
        logger.error(f"Ошибка GigaChat: {e}")
        await processing_msg.edit_text("❌ Ошибка при обращении к нейросети")

def main():
    TOKEN = load_token()
    if not TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    logger.info("🚀 Обновленный бот с нейросетью запущен!")
    app.run_polling()

if __name__ == '__main__':
    main()
