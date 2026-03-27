import os
import json
import sqlite3
import logging
import time
import threading
import io
import re
import requests
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
import telebot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

# Configuration du logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Récupération des variables d'environnement
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
HF_TOKEN = os.environ.get("HF_TOKEN") # Clé Hugging Face
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
DB_PATH = os.getenv("DB_PATH", "/app/data/memory.db")

# Vérification des clés essentielles
if not all([TELEGRAM_TOKEN, NVIDIA_API_KEY, HF_TOKEN]):
    log.error("Erreur : TELEGRAM_TOKEN, NVIDIA_API_KEY ou HF_TOKEN manquant dans le .env")
    exit(1)

MODELS = {
    "flash": "meta/llama-3.1-8b-instruct",
    "pro": "meta/llama-3.1-70b-instruct",
    "ultra": "meta/llama-3.1-405b-instruct",
    "multi": "multi_agent_system"
}

# Modèle Hugging Face par défaut pour le codage lourd (ex: Llama 3.3 70B ou Qwen 2.5 72B)
HF_MODEL_ID = "meta-llama/Llama-3.3-70B-Instruct"

raw_ids = os.getenv("ADMIN_USER_ID", "")
ALLOWED_IDS = [int(i.strip()) for i in raw_ids.split(",") if i.strip()]

def ensure_data_dir():
    data_dir = os.path.dirname(DB_PATH)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)

ensure_data_dir()

bot = telebot.TeleBot(TELEGRAM_TOKEN)
nvidia_client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL)

# --- UTILS API HUGGING FACE ---

def call_huggingface_api(prompt, system_instruction=""):
    """Appel à l'API Hugging Face pour la génération de code."""
    url = f"https://api-inference.huggingface.co/models/{HF_MODEL_ID}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    # Formatage du prompt pour Llama 3 (Chat Template)
    formatted_prompt = f"<|system|>\n{system_instruction}<|end|>\n<|user|>\n{prompt}<|end|>\n<|assistant|>"
    
    payload = {
        "inputs": formatted_prompt,
        "parameters": {
            "max_new_tokens": 2048,
            "temperature": 0.3,
            "return_full_text": False
        }
    }
    
    for i in [1, 2, 4, 8]:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            if response.status_code == 200:
                result = response.json()
                # Selon le modèle, la structure de réponse peut varier légèrement
                if isinstance(result, list):
                    return result[0].get('generated_text', "").strip()
                return result.get('generated_text', "").strip()
            elif response.status_code == 503: # Modèle en cours de chargement
                log.info("Hugging Face : Modèle en cours de chargement...")
                time.sleep(20)
                continue
        except Exception as e:
            log.warning(f"Tentative Hugging Face échouée : {e}")
        time.sleep(i)
    return "Erreur de génération Hugging Face après plusieurs tentatives."

def call_nvidia_api(system_prompt, messages, model):
    payload = [{"role": "system", "content": system_prompt}] + messages
    res = nvidia_client.chat.completions.create(
        model=model,
        messages=payload,
        temperature=0.2,
        max_tokens=4096,
        timeout=240
    )
    return res.choices[0].message.content.strip()

# --- DATABASE ---

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
        conn.commit()

def get_user_memory(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memory WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"summary": "", "last_messages": [], "exchange_count": 0, "model": MODELS["flash"]}
    return {
        "summary": row["summary"] or "",
        "last_messages": json.loads(row["last_messages_json"] or "[]"),
        "exchange_count": row["exchange_count"] or 0,
        "model": row["current_model"] or MODELS["flash"]
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
        """, (user_id, summary, json.dumps(last_messages), exchange_count))
        conn.commit()

# --- PIPELINE MULTI-AGENT ---

def run_multi_agent_pipeline(user_prompt, chat_id, status_id):
    target_repo = "https://github.com/ArthurPujat/TelegramAI"
    
    bot.edit_message_text("🔍 Phase 1: Analyse stratégique (Hugging Face)...", chat_id, status_id)
    
    research_prompt = f"Analyse le dépôt {target_repo} et la demande : {user_prompt}. Liste les besoins techniques."
    research_data = call_huggingface_api(research_prompt, "Tu es un ingénieur de recherche spécialisé en code.")
    
    bot.edit_message_text("💻 Phase 2: Codage des modules (Hugging Face)...", chat_id, status_id)
    
    tasks = {
        "HTML": f"Génère un fichier HTML5 complet pour {user_prompt}. Sois très précis. Minimum 150 lignes.",
        "CSS": f"Génère un design CSS moderne pour {user_prompt}. Sois créatif. Minimum 200 lignes.",
        "JS": f"Développe toute la logique JavaScript pour {user_prompt}. Minimum 250 lignes."
    }
    
    results = {}
    for role, prompt in tasks.items():
        bot.edit_message_text(f"🛠️ Agent {role} en cours de génération...", chat_id, status_id)
        results[role] = call_huggingface_api(prompt, f"Tu es l'expert {role}. Produis un code robuste.")

    bot.edit_message_text("🏗️ Phase 3: Assemblage final (NVIDIA Ultra)...", chat_id, status_id)
    
    assembly_prompt = f"Fusionne ces modules en un seul fichier .html.\nHTML: {results.get('HTML')}\nCSS: {results.get('CSS')}\nJS: {results.get('JS')}"
    final_raw = call_nvidia_api("Tu es l'Assembleur Final. Réponds uniquement par le code source pur.", [{"role": "user", "content": assembly_prompt}], MODELS["ultra"])
    
    # Nettoyage Markdown
    final_code = re.sub(r'^```[a-z]*\n', '', final_raw, flags=re.MULTILINE)
    final_code = re.sub(r'```$', '', final_code, flags=re.MULTILINE).strip()
    
    file_io = io.BytesIO(final_code.encode('utf-8'))
    file_io.name = "projet_hf.html"
    bot.send_document(chat_id, file_io, caption=f"✅ Généré via Hugging Face & NVIDIA ({len(final_code)//1024}kb).")
    bot.delete_message(chat_id, status_id)

# --- HANDLERS ---

def is_allowed(user_id: int):
    return user_id in ALLOWED_IDS if ALLOWED_IDS else True

@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    if not is_allowed(message.from_user.id): return
    bot.send_message(message.chat.id, "Arthur opérationnel avec Hugging Face.\nUtilisez /model pour tester le pipeline.")

@bot.message_handler(commands=["debug"])
def handle_debug(message: Message):
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    card = (
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃    🆔 **CARTE ARTHUR HF** ┃\n"
        "┠──────────────────────────┨\n"
        f"┃ 👤 **USER:** `{message.from_user.first_name}`\n"
        f"┃ 🔢 **ID:** `{message.from_user.id}`\n"
        f"┃ 🤖 **MODÈLE:** `{mem['model'].split('/')[-1]}`\n"
        f"┃ 🛠️ **ENGINE:** `Hugging Face` \n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
    )
    bot.send_message(message.chat.id, card, parse_mode="Markdown")

@bot.message_handler(commands=["model"])
def handle_model_command(message: Message):
    if not is_allowed(message.from_user.id): return
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🚀 PIPELINE HF (Massif)", callback_data="setmod:multi"))
    markup.row(InlineKeyboardButton("Léger (NVIDIA)", callback_data="setmod:flash"))
    markup.row(InlineKeyboardButton("Ultra (NVIDIA)", callback_data="setmod:ultra"))
    bot.send_message(message.chat.id, "Moteur de génération :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmod:"))
def callback_set_model(call):
    key = call.data.split(":")[1]
    new_model = MODELS.get(key, MODELS["flash"]) if key != "multi" else "multi_agent_system"
    save_user_memory(call.from_user.id, "", [], 0, new_model)
    bot.answer_callback_query(call.id, f"Moteur {key.upper()} activé !")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: Message):
    if not is_allowed(message.from_user.id): return
    uid, txt = message.from_user.id, message.text.strip()
    mem = get_user_memory(uid)
    
    status_msg = bot.send_message(message.chat.id, "Traitement...")
    
    if mem["model"] == "multi_agent_system":
        threading.Thread(target=run_multi_agent_pipeline, args=(txt, message.chat.id, status_msg.message_id)).start()
    else:
        try:
            res = call_nvidia_api("Tu es Arthur, assistant IA.", [{"role": "user", "content": txt}], mem["model"])
            bot.edit_message_text(res, message.chat.id, status_msg.message_id)
        except:
            bot.edit_message_text("Erreur API NVIDIA.", message.chat.id, status_msg.message_id)

if __name__ == "__main__":
    init_db()
    bot.infinity_polling()
