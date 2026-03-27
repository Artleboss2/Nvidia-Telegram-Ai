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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # Récupéré depuis le .env
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
DB_PATH = os.getenv("DB_PATH", "/app/data/memory.db")

# Vérification des clés essentielles
if not all([TELEGRAM_TOKEN, NVIDIA_API_KEY, GEMINI_API_KEY]):
    log.error("Erreur : TELEGRAM_TOKEN, NVIDIA_API_KEY ou GEMINI_API_KEY manquant dans le .env")
    exit(1)

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

bot = telebot.TeleBot(TELEGRAM_TOKEN)
nvidia_client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL)

# --- UTILS API ---

def call_gemini_api(prompt, system_instruction=""):
    """Appel à Gemini 2.5 Flash pour une génération de code massive."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "tools": [{"google_search": {}}]
    }
    
    # Logique de tentative avec repli exponentiel
    for i in [1, 2, 4, 8, 16]:
        try:
            response = requests.post(url, json=payload, timeout=120)
            if response.status_code == 200:
                result = response.json()
                return result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', "")
        except Exception as e:
            log.warning(f"Tentative Gemini échouée : {e}")
        time.sleep(i)
    return "Erreur de génération Gemini après plusieurs tentatives."

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
    
    bot.edit_message_text("🔍 Phase 1: Exploration profonde du Repo (Gemini Search)...", chat_id, status_id)
    
    research_prompt = f"""Explore le dépôt {target_repo} et analyse la demande : {user_prompt}. 
    Prends ton temps pour lister TOUS les fichiers, la logique métier, et les dépendances. 
    Produis un rapport technique très détaillé sur la structure à adopter."""
    
    research_data = call_gemini_api(research_prompt, "Tu es un ingénieur de recherche senior spécialisé en analyse de code source.")
    
    bot.edit_message_text("💻 Phase 2: Codage intensif des modules (Gemini Core)...", chat_id, status_id)
    
    tasks = {
        "HTML": f"Basé sur cette recherche: {research_data}, génère un fichier HTML5 complet pour {user_prompt}. Minimum 200 lignes.",
        "CSS": f"Génère un design CSS moderne avec animations pour {user_prompt}. Minimum 300 lignes.",
        "JS": f"Développe toute la logique JavaScript pour {user_prompt}. Minimum 400 lignes."
    }
    
    results = {}
    for role, prompt in tasks.items():
        bot.edit_message_text(f"🛠️ Agent {role} en cours de rédaction massive...", chat_id, status_id)
        results[role] = call_gemini_api(prompt, f"Tu es l'expert {role}. Produis un code très détaillé. Ne sois pas paresseux.")

    bot.edit_message_text("🏗️ Phase 3: Assemblage & Certification (NVIDIA Ultra)...", chat_id, status_id)
    
    assembly_prompt = f"""
    Fusionne ces modules en un seul fichier .html fonctionnel. 
    HTML: {results.get('HTML')}
    CSS: {results.get('CSS')}
    JS: {results.get('JS')}
    
    RÈGLES : Pas de texte, pas de Markdown, juste le code assemblé prêt à l'emploi.
    """
    
    final_raw = call_nvidia_api("Tu es l'Assembleur Final. Tu ne réponds QUE par le code source pur.", [{"role": "user", "content": assembly_prompt}], MODELS["ultra"])
    
    # Nettoyage final des balises Markdown
    final_code = re.sub(r'^```[a-z]*\n', '', final_raw, flags=re.MULTILINE)
    final_code = re.sub(r'```$', '', final_code, flags=re.MULTILINE).strip()
    
    file_io = io.BytesIO(final_code.encode('utf-8'))
    file_io.name = "application_massive.html"
    bot.send_document(chat_id, file_io, caption=f"✅ Projet de {len(final_code)//1024}kb généré avec Gemini & NVIDIA.")
    bot.delete_message(chat_id, status_id)

# --- HANDLERS ---

def is_allowed(user_id: int):
    return user_id in ALLOWED_IDS if ALLOWED_IDS else True

@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    if not is_allowed(message.from_user.id): return
    bot.send_message(message.chat.id, "Arthur Pro prêt. Utilisez /model pour le mode Multi-Agent.")

@bot.message_handler(commands=["debug"])
def handle_debug(message: Message):
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    bot.send_message(message.chat.id, f"Moteur: {mem['model']}\nRésumé: {mem['summary'][:200]}...", parse_mode="Markdown")

@bot.message_handler(commands=["model"])
def handle_model_command(message: Message):
    if not is_allowed(message.from_user.id): return
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Léger", callback_data="setmod:flash"))
    markup.row(InlineKeyboardButton("Ultra", callback_data="setmod:ultra"))
    markup.row(InlineKeyboardButton("🚀 PIPELINE GEMINI (Massif)", callback_data="setmod:multi"))
    bot.send_message(message.chat.id, "Moteur de génération :", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmod:"))
def callback_set_model(call):
    key = call.data.split(":")[1]
    save_user_memory(call.from_user.id, "", [], 0, MODELS.get(key, "multi_agent_system" if key == "multi" else MODELS["flash"]))
    bot.answer_callback_query(call.id, "Modèle mis à jour !")

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
            bot.edit_message_text("Erreur API.", message.chat.id, status_msg.message_id)

if __name__ == "__main__":
    init_db()
    bot.infinity_polling()
