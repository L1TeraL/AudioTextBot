import os
import logging
import subprocess
import tempfile
import time
import hashlib
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler, CallbackQueryHandler
import speech_recognition as sr
from pydub import AudioSegment
from gigachat import GigaChat

# ========== НАСТРОЙКА ЛОГОВ С РОТАЦИЕЙ ==========
def setup_logging():
    """Настройка логирования с ротацией файлов"""
    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Файловый handler с ротацией (10 MB, 5 бэкапов)
    file_handler = RotatingFileHandler(
        'bot.log', 
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(log_formatter)
    
    # Консольный handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    
    # Настройка корневого логгера
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()
# ==============================================

# ========== МЕТРИКИ ==========
class Metrics:
    """Сбор метрик работы бота"""
    def __init__(self):
        self.commands = defaultdict(int)
        self.errors = defaultdict(int)
        self.response_times = []
        self.users = set()
        self.tokens_used = 0
        self.start_time = time.time()
    
    def track_command(self, command):
        self.commands[command] += 1
    
    def track_error(self, error_type):
        self.errors[error_type] += 1
        logger.error(f"Error tracked: {error_type}")
    
    def track_response_time(self, seconds):
        self.response_times.append(seconds)
        if len(self.response_times) > 1000:
            self.response_times = self.response_times[-1000:]
    
    def track_user(self, user_id):
        self.users.add(user_id)
    
    def track_tokens(self, count):
        self.tokens_used += count
    
    def get_stats(self):
        avg_time = sum(self.response_times) / len(self.response_times) if self.response_times else 0
        uptime = time.time() - self.start_time
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        
        return {
            "users": len(self.users),
            "commands": dict(self.commands),
            "errors": dict(self.errors),
            "avg_response": f"{avg_time:.2f}s",
            "total_requests": sum(self.commands.values()),
            "tokens_used": self.tokens_used,
            "uptime": f"{hours}ч {minutes}м"
        }
    
    def stats_text(self):
        stats = self.get_stats()
        return (
            f"📊 **Статистика бота**\n\n"
            f"👥 Пользователей: {stats['users']}\n"
            f"📝 Всего запросов: {stats['total_requests']}\n"
            f"⏱ Средний ответ: {stats['avg_response']}\n"
            f"🎯 Токенов потрачено: {stats['tokens_used']}\n"
            f"⏰ Аптайм: {stats['uptime']}\n"
            f"❌ Ошибок: {sum(stats['errors'].values())}"
        )

metrics = Metrics()
# ===========================

# ========== КЭШ ==========
class ResponseCache:
    """Кэш для ответов нейросети"""
    def __init__(self, ttl=3600):  # 1 час по умолчанию
        self.cache = {}
        self.ttl = ttl
        logger.info(f"✅ Кэш инициализирован (TTL: {ttl}с)")
    
    def _get_key(self, text, user_id):
        """Генерация ключа кэша"""
        content = f"{text}:{user_id}".encode()
        return hashlib.md5(content).hexdigest()
    
    def get(self, text, user_id):
        """Получить ответ из кэша"""
        key = self._get_key(text, user_id)
        if key in self.cache:
            result, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                logger.info(f"🎯 Cache HIT for key {key[:8]}")
                return result
            else:
                del self.cache[key]
                logger.info(f"⌛ Cache EXPIRED for key {key[:8]}")
        logger.info(f"💔 Cache MISS for key {key[:8]}")
        return None
    
    def set(self, text, user_id, value):
        """Сохранить ответ в кэш"""
        key = self._get_key(text, user_id)
        self.cache[key] = (value, time.time())
        logger.info(f"💾 Cache SAVED for key {key[:8]}")
    
    def clear(self):
        """Очистить кэш"""
        self.cache.clear()
        logger.info("🧹 Кэш очищен")
    
    def size(self):
        return len(self.cache)

cache = ResponseCache(ttl=1800)  # 30 минут
# ========================

# ========== ДЕКОРАТОРЫ ==========
def log_command(func):
    """Декоратор для логирования команд"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        command = func.__name__
        
        # Логируем начало
        logger.info(f"▶️ Команда '{command}' от {user.first_name} (@{user.username or 'no username'}) [ID:{user.id}]")
        
        # Метрики
        metrics.track_command(command)
        metrics.track_user(user.id)
        
        # Замеряем время
        start = time.time()
        
        try:
            result = await func(update, context, *args, **kwargs)
            
            # Логируем успех
            duration = time.time() - start
            metrics.track_response_time(duration)
            logger.info(f"✅ Команда '{command}' выполнена за {duration:.2f}с")
            
            return result
            
        except Exception as e:
            # Логируем ошибку
            duration = time.time() - start
            error_type = type(e).__name__
            metrics.track_error(error_type)
            logger.error(f"❌ Ошибка в '{command}': {error_type} - {str(e)}", exc_info=True)
            raise
    
    return wrapper

def measure_time(func):
    """Декоратор для замера времени выполнения"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.time()
        result = await func(*args, **kwargs)
        duration = time.time() - start
        logger.debug(f"⏱ {func.__name__} выполнилась за {duration:.3f}с")
        return result
    return wrapper

def retry_on_error(max_retries=3, delay=1):
    """Декоратор для повторных попыток при ошибках"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"⚠️ Попытка {attempt + 1}/{max_retries} для {func.__name__} не удалась: {e}")
                    await asyncio.sleep(delay * (attempt + 1))  # Увеличиваем задержку
            return None
        return wrapper
    return decorator
# ================================

# Загрузка токена
def load_token():
    env_path = Path('.env')
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith('BOT_TOKEN'):
                    return line.strip().split('=')[1].strip("'\"")
    return os.environ.get('BOT_TOKEN')

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

# ========== ОБРАБОТЧИКИ ==========
@log_command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎤 Распознать речь", callback_data='speech')],
        [InlineKeyboardButton("🤖 Спросить нейросеть", callback_data='ask_ai')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')],
        [InlineKeyboardButton("🧹 Очистить кэш", callback_data='clear_cache')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Привет! Я бот с нейросетью GigaChat!\n\n"
        "🎤 Отправь голосовое - распознаю речь\n"
        "🤖 Напиши вопрос - отвечу через нейросеть\n"
        "📊 Статистика работы бота",
        reply_markup=reply_markup
    )

@log_command
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'speech':
        await query.edit_message_text("🎤 Отправьте голосовое сообщение")
    
    elif query.data == 'ask_ai':
        await query.edit_message_text("🤖 Напишите ваш вопрос")
    
    elif query.data == 'stats':
        await query.edit_message_text(
            metrics.stats_text() + f"\n\n🗄 Кэш: {cache.size()} записей",
            parse_mode='Markdown'
        )
    
    elif query.data == 'clear_cache':
        cache.clear()
        await query.edit_message_text("🧹 Кэш очищен!")

@log_command
@measure_time
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
            metrics.track_error("UnknownValueError")
            await processing_msg.edit_text("😕 Не удалось распознать речь")
        except Exception as e:
            metrics.track_error(type(e).__name__)
            logger.error(f"Ошибка: {e}", exc_info=True)
            await processing_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

@log_command
@measure_time
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста - отправка в нейросеть с кэшированием"""
    if not GIGA:
        await update.message.reply_text("❌ Нейросеть не подключена")
        return
    
    user = update.effective_user
    user_text = update.message.text
    
    if user_text.startswith('/'):
        return
    
    # Проверяем кэш
    cached_response = cache.get(user_text, user.id)
    if cached_response:
        await update.message.reply_text(f"🤖 **Ответ (из кэша):**\n\n{cached_response}")
        return
    
    processing_msg = await update.message.reply_text("🤔 Думаю...")
    
    try:
        response = GIGA.chat(user_text)
        ai_answer = response.choices[0].message.content
        
        # Сохраняем в кэш
        cache.set(user_text, user.id, ai_answer)
        
        # Считаем токены (примерно)
        tokens_count = len(user_text) + len(ai_answer)
        metrics.track_tokens(tokens_count)
        
        await processing_msg.edit_text(f"🤖 **Ответ:**\n\n{ai_answer}")
        
    except Exception as e:
        metrics.track_error(type(e).__name__)
        logger.error(f"Ошибка GigaChat: {e}", exc_info=True)
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
    
    logger.info("🚀 Бот с декораторами, ротацией логов, кэшем и метриками запущен!")
    logger.info(f"📊 Метрики: {metrics.get_stats()}")
    logger.info(f"🗄 Размер кэша: {cache.size()}")
    
    app.run_polling()

if __name__ == '__main__':
    main()

