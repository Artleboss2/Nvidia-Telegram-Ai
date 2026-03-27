import os
import json
import sqlite3
import logging
import time
import threading
import io
import base64
from contextlib import contextmanager
from openai import OpenAI
import telebot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from PIL import Image

try:
    import fitz
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
VISION_MODEL_ID = os.getenv("VISION_MODEL_ID", "nvidia/llama-3.2-11b-vision-instruct")
DB_PATH = os.getenv("DB_PATH", "/app/data/memory.db")

MODELS = {
    "flash": "meta/llama-3.1-8b-instruct",
    "pro": "meta/llama-3.1-70b-instruct",
    "ultra": "meta/llama-3.1-405b-instruct"
}

raw_ids = os.getenv("ADMIN_USER_ID", "")
ALLOWED_IDS = [int(i.strip()) for i in raw_ids.split(",") if i.strip()]

def ensure_data_dir():
    data_dir = os.path.dirname(DB_PATH)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)

ensure_data_dir()

if not TELEGRAM_TOKEN or not NVIDIA_API_KEY:
    exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
nvidia_client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL, max_retries=5)

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                user_id INTEGER PRIMARY KEY,
                summary TEXT DEFAULT '',
                last_messages_json TEXT DEFAULT '[]',
                exchange_count INTEGER DEFAULT 0,
                current_model TEXT DEFAULT 'meta/llama-3.1-8b-instruct'
            )
        """)
        try:
            conn.execute("ALTER TABLE memory ADD COLUMN current_model TEXT DEFAULT 'meta/llama-3.1-8b-instruct'")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE TABLE IF NOT EXISTS retry_cache (msg_id TEXT PRIMARY KEY, text TEXT)")
        conn.commit()

def is_allowed(user_id: int):
    return user_id in ALLOWED_IDS if ALLOWED_IDS else True

def get_user_memory(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memory WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"summary": "", "last_messages": [], "exchange_count": 0, "model": MODELS["flash"]}
    
    try:
        model = row["current_model"] if "current_model" in row.keys() else MODELS["flash"]
    except:
        model = MODELS["flash"]
        
    try:
        messages = json.loads(row["last_messages_json"] or "[]")
    except:
        messages = []
    return {
        "summary": row["summary"] or "",
        "last_messages": messages,
        "exchange_count": row["exchange_count"] or 0,
        "model": model or MODELS["flash"]
    }

def save_user_memory(user_id: int, summary: str, last_messages: list, exchange_count: int, model: str = None):
    with get_db() as conn:
        if model:
            conn.execute("UPDATE memory SET current_model = ? WHERE user_id = ?", (model, user_id))
        conn.execute("""
            INSERT INTO memory (user_id, summary, last_messages_json, exchange_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                summary = excluded.summary,
                last_messages_json = excluded.last_messages_json,
                exchange_count = excluded.exchange_count
        """, (user_id, summary, json.dumps(last_messages, ensure_ascii=False), exchange_count))
        conn.commit()

def call_nvidia_api(system_prompt: str, messages: list, model):
    payload = [{"role": "system", "content": system_prompt}] + messages
    res = nvidia_client.chat.completions.create(
        model=model,
        messages=payload,
        temperature=0.6,
        max_tokens=1024,
        timeout=60
    )
    return res.choices[0].message.content.strip()

def animate_thinking(chat_id, message_id, stop_event):
    frames = ["Réflexion", "Réflexion.", "Réflexion..", "Réflexion..."]
    idx = 0
    while not stop_event.is_set():
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=frames[idx % 4])
            idx += 1
            time.sleep(1)
        except: break

@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    if not is_allowed(message.from_user.id): return
    bot.send_message(message.chat.id, "Assistant Arthur prêt.\nCommandes: /model, /reset")

@bot.message_handler(commands=["model"])
def handle_model_command(message: Message):
    if not is_allowed(message.from_user.id): return
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Léger (8B)", callback_data="setmod:flash"))
    markup.row(InlineKeyboardButton("Équilibré (70B)", callback_data="setmod:pro"))
    markup.row(InlineKeyboardButton("Ultra (405B)", callback_data="setmod:ultra"))
    bot.send_message(message.chat.id, "Choisissez la puissance de l'IA :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmod:"))
def callback_set_model(call):
    key = call.data.split(":")[1]
    new_model = MODELS.get(key)
    if new_model:
        with get_db() as conn:
            conn.execute("INSERT INTO memory (user_id, current_model) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET current_model=excluded.current_model", (call.from_user.id, new_model))
            conn.commit()
        bot.answer_callback_query(call.id, "Modèle mis à jour !")
        bot.edit_message_text(f"Modèle actif : {key.upper()}", chat_id=call.message.chat.id, message_id=call.message.message_id)

@bot.message_handler(commands=["reset"])
def handle_reset(message: Message):
    if not is_allowed(message.from_user.id): return
    with get_db() as conn:
        conn.execute("UPDATE memory SET summary='', last_messages_json='[]', exchange_count=0 WHERE user_id=?", (message.from_user.id,))
        conn.commit()
    bot.send_message(message.chat.id, "Mémoire réinitialisée.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("retry:"))
def handle_retry(call):
    retry_id = call.data.split(":")[1]
    with get_db() as conn:
        row = conn.execute("SELECT text FROM retry_cache WHERE msg_id = ?", (retry_id,)).fetchone()
    if row:
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        call.message.text = row["text"]
        call.message.from_user.id = call.from_user.id
        handle_message(call.message)

@bot.message_handler(content_types=["document", "photo", "voice", "audio"])
def handle_files(message: Message):
    if not is_allowed(message.from_user.id): return
    if message.document:
        if not PDF_SUPPORT:
            bot.reply_to(message, "Analyse PDF désactivée.")
            return
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        if "pdf" in (message.document.mime_type or ""):
            try:
                doc = fitz.open(stream=downloaded, filetype="pdf")
                text = "".join([p.get_text() for p in doc])
                doc.close()
                message.text = f"[Contenu PDF]\n{text[:3000]}"
                handle_message(message)
            except: bot.reply_to(message, "Erreur PDF.")
    elif message.photo:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        img = Image.open(io.BytesIO(downloaded))
        if img.mode != 'RGB': img = img.convert('RGB')
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85)
        b64 = base64.b64encode(output.getvalue()).decode('utf-8')
        status = bot.send_message(message.chat.id, "Analyse image...")
        try:
            res = call_nvidia_api("Expert Vision", [{"role":"user","content":[{"type":"text","text":"Décris précisément cette image."},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}], VISION_MODEL_ID)
            bot.edit_message_text(res, message.chat.id, status.message_id)
        except: bot.edit_message_text("Erreur Vision API.", message.chat.id, status.message_id)
    elif message.voice or message.audio:
        bot.reply_to(message, "Transcription audio non configurée (Whisper requis).")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: Message):
    if not is_allowed(message.from_user.id): return
    uid, txt = message.from_user.id, message.text.strip()
    status_msg = bot.send_message(message.chat.id, "Réflexion")
    stop_event = threading.Event()
    anim = threading.Thread(target=animate_thinking, args=(message.chat.id, status_msg.message_id, stop_event))
    anim.start()
    mem = get_user_memory(uid)
    context = mem["last_messages"][-6:] + [{"role": "user", "content": txt}]
    try:
        reply = call_nvidia_api(f"Tu es un assistant utile. Mémoire: {mem['summary']}", context, mem["model"])
        stop_event.set()
        anim.join()
        try: bot.edit_message_text(reply, message.chat.id, status_msg.message_id, parse_mode="Markdown")
        except: bot.edit_message_text(reply, message.chat.id, status_msg.message_id)
        context.append({"role": "assistant", "content": reply})
        save_user_memory(uid, mem["summary"], context, mem["exchange_count"] + 1)
    except Exception as e:
        stop_event.set()
        if anim.is_alive(): anim.join()
        rid = str(int(time.time()))
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO retry_cache (msg_id, text) VALUES (?,?)", (rid, txt))
            conn.commit()
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Réessayer", callback_data=f"retry:{rid}"))
        bot.edit_message_text("Erreur API NVIDIA.", message.chat.id, status_msg.message_id, reply_markup=markup)

if __name__ == "__main__":
    init_db()
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
