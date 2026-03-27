import os
import json
import sqlite3
import logging
import time
import threading
import io
import base64
import re
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
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
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "[https://integrate.api.nvidia.com/v1](https://integrate.api.nvidia.com/v1)")
VISION_MODEL_ID = os.getenv("VISION_MODEL_ID", "meta/llama-3.2-11b-vision-instruct")
DB_PATH = os.getenv("DB_PATH", "/app/data/memory.db")

MODELS = {
    "flash": "meta/llama-3.1-8b-instruct",
    "pro": "meta/llama-3.1-70b-instruct",
    "ultra": "meta/llama-3.1-405b-instruct",
    "multi": "multi_agent_system"
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
        temperature=0.3, # Encore plus bas pour éviter les "fantaisies" de formatage
        max_tokens=4096,
        timeout=240      # 4 minutes max pour les gros projets
    )
    return res.choices[0].message.content.strip()

def agent_worker(role_name, prompt, model_id, context_info=""):
    full_prompt = f"Expertise: {role_name}\nInfos de recherche: {context_info}\n\nMission: {prompt}"
    try:
        return call_nvidia_api(f"Tu es l'expert {role_name}. Produis un code robuste et complet.", [{"role": "user", "content": full_prompt}], model_id)
    except Exception as e:
        return f"Erreur {role_name}: {str(e)}"

def clean_markdown(text):
    """Supprime les balises ```html et ``` au début et à la fin."""
    text = re.sub(r'^```[a-z]*\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    return text.strip()

def run_multi_agent_pipeline(user_prompt, chat_id, status_id):
    bot.edit_message_text("🔍 Phase 1: Recherche & Stratégie...", chat_id, status_id)
    
    search_prompt = f"Analyse cette demande: {user_prompt}. Liste les fonctions JS indispensables et le style CSS attendu."
    research_data = agent_worker("Analyse & Recherche", search_prompt, MODELS["flash"])
    
    bot.edit_message_text("💻 Phase 2: Génération des couches...", chat_id, status_id)
    
    tasks = {
        "HTML": (f"Structure HTML5 propre pour: {user_prompt}", MODELS["pro"]),
        "CSS": (f"CSS moderne (Tailwind ou CSS pur) pour: {user_prompt}", MODELS["pro"]),
        "JS": (f"Logique JS complète pour: {user_prompt}", MODELS["pro"])
    }
    
    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_role = {executor.submit(agent_worker, role, t[0], t[1], research_data): role for role, t in tasks.items()}
        for future in future_to_role:
            role = future_to_role[future]
            results[role] = future.result()
            bot.edit_message_text(f"✅ Agent {role} a fini son travail...", chat_id, status_id)

    bot.edit_message_text("🏗️ Phase 3: Fusion & Finalisation...", chat_id, status_id)
    
    assembly_prompt = f"""
    Assemble les composants suivants en un FICHIER UNIQUE HTML.
    
    HTML: {results.get('HTML')}
    CSS: {results.get('CSS')}
    JS: {results.get('JS')}
    
    RÈGLES CRITIQUES :
    1. Ne mets JAMAIS de balises Markdown (```html).
    2. Insère le CSS dans <style>...</style>.
    3. Insère le JS dans <script>...</script>.
    4. Le fichier doit être 100% prêt à l'emploi.
    """
    
    final_raw = call_nvidia_api("Tu es l'Assembleur Final. Tu ne réponds QUE par le code brut sans aucun texte autour ni balise Markdown.", [{"role": "user", "content": assembly_prompt}], MODELS["ultra"])
    
    # Sécurité supplémentaire au cas où il ignore la consigne du Markdown
    final_code = clean_markdown(final_raw)
    
    file_io = io.BytesIO(final_code.encode('utf-8'))
    file_io.name = "projet_final.html"
    bot.send_document(chat_id, file_io, caption="✅ Voici votre projet assemblé par l'agent Ultra.")
    bot.delete_message(chat_id, status_id)

def animate_thinking(chat_id, message_id, stop_event):
    frames = ["Réflexion", "Réflexion.", "Réflexion..", "Réflexion..."]
    idx = 0
    while not stop_event.is_set():
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=frames[idx % 4])
            idx += 1
            time.sleep(1.2)
        except: break

@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    if not is_allowed(message.from_user.id): return
    bot.send_message(message.chat.id, "Assistant Arthur prêt.\n/model pour changer de mode.\n/debug pour voir la mémoire.")

@bot.message_handler(commands=["debug"])
def handle_debug(message: Message):
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    debug_text = (
        f"⚙️ **DEBUG SYSTÈME**\n\n"
        f"👤 **Utilisateur:** `{message.from_user.id}`\n"
        f"🤖 **Moteur actif:** `{mem['model']}`\n"
        f"📊 **Échanges:** `{mem['exchange_count']}`\n"
        f"📝 **Mémoire Résumée:**\n{mem['summary'] or '_Vide_'}"
    )
    bot.send_message(message.chat.id, debug_text, parse_mode="Markdown")

@bot.message_handler(commands=["model"])
def handle_model_command(message: Message):
    if not is_allowed(message.from_user.id): return
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Léger (8B)", callback_data="setmod:flash"))
    markup.row(InlineKeyboardButton("Équilibré (70B)", callback_data="setmod:pro"))
    markup.row(InlineKeyboardButton("Ultra (405B)", callback_data="setmod:ultra"))
    markup.row(InlineKeyboardButton("🚀 TRAVAIL MULTIPLE (Pipeline)", callback_data="setmod:multi"))
    bot.send_message(message.chat.id, "Choisis ton moteur :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmod:"))
def callback_set_model(call):
    key = call.data.split(":")[1]
    new_model = MODELS.get(key)
    if new_model:
        with get_db() as conn:
            conn.execute("INSERT INTO memory (user_id, current_model) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET current_model=excluded.current_model", (call.from_user.id, new_model))
            conn.commit()
        bot.answer_callback_query(call.id, f"Mode {key.upper()} activé !")
        bot.edit_message_text(f"Mode actif : {key.upper()}", chat_id=call.message.chat.id, message_id=call.message.message_id)

@bot.message_handler(commands=["reset"])
def handle_reset(message: Message):
    if not is_allowed(message.from_user.id): return
    with get_db() as conn:
        conn.execute("UPDATE memory SET summary='', last_messages_json='[]', exchange_count=0 WHERE user_id=?", (message.from_user.id,))
        conn.commit()
    bot.send_message(message.chat.id, "Mémoire réinitialisée.")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: Message):
    if not is_allowed(message.from_user.id): return
    uid, txt = message.from_user.id, message.text.strip()
    mem = get_user_memory(uid)
    
    status_msg = bot.send_message(message.chat.id, "Analyse...")
    
    if mem["model"] == "multi_agent_system":
        threading.Thread(target=run_multi_agent_pipeline, args=(txt, message.chat.id, status_msg.message_id)).start()
        return

    stop_event = threading.Event()
    anim = threading.Thread(target=animate_thinking, args=(message.chat.id, status_msg.message_id, stop_event))
    anim.start()
    
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
        log.error(f"Erreur: {str(e)}")
        bot.edit_message_text(f"Désolé, problème technique. Modèle: {mem['model']}", message.chat.id, status_msg.message_id)

if __name__ == "__main__":
    init_db()
    bot.infinity_polling()
