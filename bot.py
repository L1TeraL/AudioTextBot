import os
import logging
import subprocess
import tempfile
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
import speech_recognition as sr
from pydub import AudioSegment

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для распознавания голосовых сообщений.\n\n"
        "📢 Отправь мне голосовое сообщение, а я расшифрую его в текст."
    )


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FFMPEG_OK:
        await update.message.reply_text("⚠️ Бот временно недоступен")
        return

    processing_msg = await update.message.reply_text("⏳ Обрабатываю аудио...")

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            voice_file = await update.message.voice.get_file()
            ogg_path = os.path.join(temp_dir, "voice.ogg")
            wav_path = os.path.join(temp_dir, "voice.wav")

            await voice_file.download_to_drive(ogg_path)

            await processing_msg.edit_text("⏳ Конвертирую аудио...")
            audio = AudioSegment.from_ogg(ogg_path)
            audio = audio.set_frame_rate(16000).set_channels(1)
            audio.export(wav_path, format="wav")

            await processing_msg.edit_text("⏳ Распознаю речь...")
            recognizer = sr.Recognizer()

            with sr.AudioFile(wav_path) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio_data = recognizer.record(source)

                try:
                    text = recognizer.recognize_google(audio_data, language="ru-RU")
                    await processing_msg.edit_text(f"📝 **Распознано:**\n\n{text}", parse_mode='Markdown')
                except sr.UnknownValueError:
                    await processing_msg.edit_text("😕 Не удалось распознать речь")

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await processing_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")


def main():
    TOKEN = os.environ.get('BOT_TOKEN')
    if not TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    logger.info("🚀 Бот запущен на Koyeb!")
    app.run_polling()


if __name__ == '__main__':
    main()