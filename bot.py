import os
import json
import sqlite3
import logging
import time
import threading
import io
from contextlib import contextmanager
from openai import OpenAI
import telebot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
NVIDIA_API_KEY   = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL  = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
MODEL_ID         = os.getenv("MODEL_ID", "meta/llama-3.1-8b-instruct")
DB_PATH          = os.getenv("DB_PATH", "/app/data/memory.db")
MEMORY_THRESHOLD = int(os.getenv("MEMORY_THRESHOLD", "10"))

raw_ids = os.getenv("ADMIN_USER_ID", "")
ALLOWED_IDS = [int(i.strip()) for i in raw_ids.split(",") if i.strip()]

def ensure_data_dir():
    data_dir = os.path.dirname(DB_PATH)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)

ensure_data_dir()

if not TELEGRAM_TOKEN or not NVIDIA_API_KEY:
    log.error("ERREUR CRITIQUE : Variables manquantes.")
    time.sleep(10)
    exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

nvidia_client = OpenAI(
    api_key=NVIDIA_API_KEY,
    base_url=NVIDIA_BASE_URL,
    max_retries=1
)

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
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    user_id            INTEGER PRIMARY KEY,
                    summary            TEXT    DEFAULT '',
                    last_messages_json TEXT    DEFAULT '[]',
                    exchange_count     INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE TABLE IF NOT EXISTS retry_cache (msg_id TEXT PRIMARY KEY, text TEXT)")
            conn.commit()
    except Exception as e:
        log.error(f"Erreur DB : {e}")

def is_allowed(user_id: int):
    return user_id in ALLOWED_IDS if ALLOWED_IDS else True

def get_user_memory(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memory WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"summary": "", "last_messages": [], "exchange_count": 0}
    try:
        messages = json.loads(row["last_messages_json"] or "[]")
    except:
        messages = []
    return {"summary": row["summary"] or "", "last_messages": messages, "exchange_count": row["exchange_count"] or 0}

def save_user_memory(user_id: int, summary: str, last_messages: list, exchange_count: int):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO memory (user_id, summary, last_messages_json, exchange_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                summary = excluded.summary,
                last_messages_json = excluded.last_messages_json,
                exchange_count = excluded.exchange_count
        """, (user_id, summary, json.dumps(last_messages, ensure_ascii=False), exchange_count))
        conn.commit()

def call_nvidia_api(system_prompt: str, messages: list):
    payload = [{"role": "system", "content": system_prompt}] + messages
    res = nvidia_client.chat.completions.create(
        model=MODEL_ID,
        messages=payload,
        temperature=0.5,
        max_tokens=1024,
        timeout=30
    )
    return res.choices[0].message.content.strip()

def animate_thinking(chat_id, message_id, stop_event):
    frames = ["Reflexion", "Reflexion.", "Reflexion..", "Reflexion..."]
    idx = 0
    while not stop_event.is_set():
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"*{frames[idx % 4]}*", parse_mode="Markdown")
            idx += 1
            time.sleep(1.0)
        except:
            break

@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    if not is_allowed(message.from_user.id): return
    bot.send_message(message.chat.id, "Assistant Rapide\nJe peux maintenant lire tes fichiers texte et tes messages.")

@bot.message_handler(commands=["reset"])
def handle_reset(message: Message):
    if not is_allowed(message.from_user.id): return
    with get_db() as conn:
        conn.execute("DELETE FROM memory WHERE user_id = ?", (message.from_user.id,))
        conn.commit()
    bot.send_message(message.chat.id, "Memoire effacee.")

@bot.message_handler(commands=["clear"])
def handle_clear(message: Message):
    if not is_allowed(message.from_user.id): return
    status = bot.send_message(message.chat.id, "Nettoyage...")
    for i in range(message.message_id, message.message_id - 100, -1):
        try: bot.delete_message(message.chat.id, i)
        except: continue
    bot.edit_message_text(chat_id=message.chat.id, message_id=status.message_id, text="Ecran nettoye !")

@bot.callback_query_handler(func=lambda call: call.data.startswith("retry:"))
def handle_retry(call):
    retry_id = call.data.split(":")[1]
    with get_db() as conn:
        row = conn.execute("SELECT text FROM retry_cache WHERE msg_id = ?", (retry_id,)).fetchone()
    if row:
        original_text = row["text"]
        msg = call.message
        msg.text = original_text
        msg.from_user.id = call.from_user.id
        try: bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except: pass
        handle_message(msg)
    else:
        bot.answer_callback_query(call.id, "Erreur de recuperation.")

@bot.message_handler(content_types=["document", "photo"])
def handle_files(message: Message):
    if not is_allowed(message.from_user.id): return
    
    uid = message.from_user.id
    file_content = ""
    file_name = ""

    if message.document:
        if message.document.mime_type and ("text" in message.document.mime_type or "application/json" in message.document.mime_type or "python" in message.document.mime_type):
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_content = downloaded_file.decode('utf-8', errors='ignore')
            file_name = message.document.file_name
            prompt_text = f"Voici le contenu du fichier {file_name} :\n\n{file_content}\n\nQue dois-je faire avec ce fichier ?"
            message.text = prompt_text
            handle_message(message)
        else:
            bot.reply_to(message, "Desole, je ne peux lire que les fichiers texte ou de code pour le moment.")
    
    elif message.photo:
        bot.reply_to(message, "J'ai bien recu ton image, mais ma fonction d'analyse d'image est en cours de maintenance. Envoie-moi du texte ou un fichier .txt !")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: Message):
    if not is_allowed(message.from_user.id):
        bot.send_message(message.chat.id, "Acces refuse.")
        return
    
    uid, txt = message.from_user.id, message.text.strip()
    bot.send_chat_action(message.chat.id, 'typing')
    
    status_msg = bot.send_message(message.chat.id, "Reflexion")
    
    stop_anim = threading.Event()
    anim_thread = threading.Thread(target=animate_thinking, args=(message.chat.id, status_msg.message_id, stop_anim))
    anim_thread.start()
    
    mem = get_user_memory(uid)
    context = mem["last_messages"][-6:] + [{"role": "user", "content": txt}]
    sys_p = f"Tu es un assistant utile. Contexte : {mem['summary']}"
    
    try:
        reply = call_nvidia_api(sys_p, context)
        stop_anim.set()
        anim_thread.join()
        
        try:
            bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text=reply, parse_mode="Markdown")
        except:
            bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text=reply, parse_mode=None)
        
        context.append({"role": "assistant", "content": reply})
        save_user_memory(uid, mem["summary"], context, mem["exchange_count"] + 1)
            
    except Exception as e:
        stop_anim.set()
        anim_thread.join()
        log.error(f"Erreur : {e}")
        
        retry_id = str(int(time.time()))
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO retry_cache (msg_id, text) VALUES (?, ?)", (retry_id, txt))
            conn.commit()

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Reessayer", callback_query_data=f"retry:{retry_id}"))
        bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text="Delai depasse.", reply_markup=markup, parse_mode="Markdown")

if __name__ == "__main__":
    init_db()
    log.info(f"Demarrage : {MODEL_ID}")
    while True:
        try:
            bot.infinity_polling(timeout=30)
        except Exception as e:
            log.error(f"Polling error : {e}")
            time.sleep(5)
