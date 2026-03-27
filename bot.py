import os
import json
import sqlite3
import logging
import time
import threading
import io
import re
import random
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
import telebot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, Poll

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
DB_PATH = os.getenv("DB_PATH", "/app/data/memory.db")

NVIDIA_API_KEYS = []
for i in range(1, 11):
    key_name = f"NVIDIA_API_KEY_{i}"
    val = os.environ.get(key_name)
    if val and val.strip():
        NVIDIA_API_KEYS.append(val.strip())

if not NVIDIA_API_KEYS:
    old_key = os.environ.get("NVIDIA_API_KEY")
    if old_key:
        NVIDIA_API_KEYS = [k.strip() for k in old_key.split(",") if k.strip()]

if not TELEGRAM_TOKEN or not NVIDIA_API_KEYS:
    log.error("Erreur : TELEGRAM_TOKEN ou au moins une NVIDIA_API_KEY manquante.")
    exit(1)

NVIDIA_CLIENTS = [OpenAI(api_key=key, base_url=NVIDIA_BASE_URL) for key in NVIDIA_API_KEYS]

MODELS = {
    "flash": "meta/llama-3.1-8b-instruct",
    "pro": "meta/llama-3.1-70b-instruct",
    "ultra": "meta/llama-3.1-405b-instruct",
    "multi": "multi_agent_system"
}

STRINGS = {
    "fr": {
        "start": "Arthur Engineering\nMode Multi-API actif avec {} cles détectées.",
        "model_select": "Sélectionnez votre niveau d'expertise :",
        "lang_select": "Choisissez votre langue :",
        "pipe_config" : "CONFIGURER PIPELINE PREMIUM",
        "pipe_keys": "Allocation de puissance pour le pipeline Premium :",
        "pipe_set": "Pipeline Premium configuré avec {} cle(s).",
        "model_set": "Mode {} active.",
        "wait": "Reflexion en cours...",
        "phase1": "Phase 1: Architecture Premium...",
        "phase2": "Phase 2: Codage Haute Precision...",
        "success": "Code Premium généré avec Llama-3.1-405B.\nArchitecture validée",
        "system_companion": "Tu es une IA assistante utile et concise. Réponds naturellement aux questions de l'utilisateur.",
        "system_analyst": "Tu es un Architecte Logiciel Senior. Analyse la demande et conçois une structure technique parfaite (SOLID, DRY, Sécurité).",
        "system_developer": "Tu es un Développeur Full-Stack de classe mondiale. Écris un code source Premium, propre et commenté. Ne fournis QUE le code.",
        "lang_set": "Langue réglée sur le Français.",
        "export_done": "Voici votre fichier de mémoire au format JSON.",
        "reset_done": "Mémoire et préférences réinitialisées avec succès.",
        "custom_set": "Instructions de personnalisation mises à jour.",
        "custom_help": "Utilisation : /customize [vos instructions ici]",
        "poll_system": "Tu es un expert en analyse. On te présente un sondage. Réponds uniquement par l'index (0, 1, 2...) de l'option la plus logique.",
        "creator_text": (
            "OWNER : [Artleboss2](https://github.com/Artleboss2)\n\n"
            "GITHUB REPOS :\n"
            "• `Portfolio` : My dev portfolio\n"
            "[OPEN](https://artleboss2.vercel.app)\n\n"
            "• `Frython` : Python but in french\n"
            "[OPEN](https://github.com/Artleboss2/frython)\n\n"
            "• `Awesome AI` : List of all ai base knowledge\n"
            "[OPEN](https://github.com/Artleboss2/awesome-ai)\n\n"
            "• `Frython Scripts`: Scripts to use with frython\n"
            "[OPEN](https://github.com/Artleboss2/Frython-Scripts)\n\n"
            "• `Nvidia Telegram AI` : Multi-agent AI interface\n"
            "[OPEN](https://github.com/Artleboss2/Nvidia-Telegram-Ai/tree/main)\n\n"
            "FULL PROFILE : [github.com/Artleboss2](https://github.com/Artleboss2)"
        )
    },
    "en": {
        "start": "Arthur Engineering\nMulti-API mode active with {} keys detected.",
        "model_select": "Select your expertise level:",
        "lang_select": "Choose your language:",
        "pipe_config": "CONFIGURE PREMIUM PIPELINE",
        "pipe_keys": "Power allocation for the Premium pipeline:",
        "pipe_set": "Premium Pipeline configured with {} key(s).",
        "model_set": "{} mode activated.",
        "wait": "Thinking...",
        "phase1": "Phase 1: Premium Architecture...",
        "phase2": "Phase 2: High Precision Coding...",
        "success": "Premium Code generated with Llama-3.1-405B.\nArchitecture validated",
        "system_companion": "You are a helpful and concise AI assistant. Respond naturally to user queries.",
        "system_analyst": "You are a Senior Software Architect. Analyze the request and design a perfect technical structure (SOLID, DRY, Security).",
        "system_developer": "You are a world-class Full-Stack Developer. Write Premium, clean, and commented source code. Provide ONLY the code.",
        "lang_set": "Language set to English.",
        "export_done": "Here is your memory file in JSON format.",
        "reset_done": "Memory and preferences successfully reset.",
        "custom_set": "Custom instructions updated.",
        "custom_help": "Usage: /customize [your instructions here]",
        "poll_system": "You are an analysis expert. You are presented with a poll. Respond only with the index (0, 1, 2...) of the most logical option.",
        "creator_text": (
            "OWNER : [Artleboss2](https://github.com/Artleboss2)\n\n"
            "GITHUB REPOS :\n"
            "• `Portfolio` : My dev portfolio\n"
            "[OPEN](https://artleboss2.vercel.app)\n\n"
            "• `Frython` : Python but in french\n"
            "[OPEN](https://github.com/Artleboss2/frython)\n\n"
            "• `Awesome AI` : List of all ai base knowledge\n"
            "[OPEN](https://github.com/Artleboss2/awesome-ai)\n\n"
            "• `Frython Scripts`: Scripts to use with frython\n"
            "[OPEN](https://github.com/Artleboss2/Frython-Scripts)\n\n"
            "• `Nvidia Telegram AI` : Multi-agent AI interface\n"
            "[OPEN](https://github.com/Artleboss2/Nvidia-Telegram-Ai/tree/main)\n\n"
            "FULL PROFILE : [github.com/Artleboss2](https://github.com/Artleboss2)"
        )
    }
}

raw_ids = os.getenv("ADMIN_USER_ID", "")
ALLOWED_IDS = [int(i.strip()) for i in raw_ids.split(",") if i.strip()]

def ensure_data_dir():
    data_dir = os.path.dirname(DB_PATH)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)

ensure_data_dir()
bot = telebot.TeleBot(TELEGRAM_TOKEN)

def get_client(index=None):
    if index is not None and index < len(NVIDIA_CLIENTS):
        return NVIDIA_CLIENTS[index]
    return random.choice(NVIDIA_CLIENTS)

def call_nvidia_api(system_prompt, messages, model, client_index=None):
    client = get_client(client_index)
    payload = [{"role": "system", "content": system_prompt}] + messages
    try:
        res = client.chat.completions.create(
            model=model,
            messages=payload,
            temperature=0.7 if "companion" in system_prompt.lower() else 0.1,
            max_tokens=4096,
            timeout=300
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"Erreur NVIDIA API : {e}")
        return f"Error API ({str(e)})"

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
                current_model TEXT DEFAULT 'meta/llama-3.1-8b-instruct',
                pipeline_keys INTEGER DEFAULT 1,
                language TEXT DEFAULT 'fr',
                custom_instructions TEXT DEFAULT ''
            )
        """)
        cursor = conn.execute("PRAGMA table_info(memory)")
        columns = [row[1] for row in cursor.fetchall()]
        if "pipeline_keys" not in columns:
            conn.execute("ALTER TABLE memory ADD COLUMN pipeline_keys INTEGER DEFAULT 1")
        if "language" not in columns:
            conn.execute("ALTER TABLE memory ADD COLUMN language TEXT DEFAULT 'fr'")
        if "custom_instructions" not in columns:
            conn.execute("ALTER TABLE memory ADD COLUMN custom_instructions TEXT DEFAULT ''")
        conn.commit()

def get_user_memory(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memory WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"summary": "", "last_messages": [], "exchange_count": 0, "model": MODELS["flash"], "pipeline_keys": 1, "language": "fr", "custom_instructions": ""}
    res = dict(row)
    return {
        "summary": res.get("summary", ""),
        "last_messages": json.loads(res.get("last_messages_json", "[]")),
        "exchange_count": res.get("exchange_count", 0),
        "model": res.get("current_model", MODELS["flash"]),
        "pipeline_keys": res.get("pipeline_keys", 1),
        "language": res.get("language", "fr"),
        "custom_instructions": res.get("custom_instructions", "")
    }

def save_user_memory(user_id: int, **kwargs):
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM memory WHERE user_id = ?", (user_id,)).fetchone()
        if not exists:
            conn.execute("INSERT INTO memory (user_id) VALUES (?)", (user_id,))
        for key, value in kwargs.items():
            column = "last_messages_json" if key == "last_messages" else key
            val = json.dumps(value) if key == "last_messages" else value
            conn.execute(f"UPDATE memory SET {column} = ? WHERE user_id = ?", (val, user_id))
        conn.commit()

def run_multi_agent_pipeline(user_prompt, chat_id, status_id, num_keys, lang, custom_inst=""):
    target_repo = "https://github.com/Artleboss2"
    num_keys = min(num_keys, len(NVIDIA_CLIENTS))
    s = STRINGS.get(lang, STRINGS["fr"])
    bot.edit_message_text(s["phase1"], chat_id, status_id)
    
    base_analyst = s["system_analyst"]
    if custom_inst:
        base_analyst += f"\nCustom instructions: {custom_inst}"
        
    analysis_prompt = f"Target Repository: {target_repo}\nUser Request: {user_prompt}\nPlease analyze and create a premium coding plan."
    analysis_res = call_nvidia_api(base_analyst, [{"role": "user", "content": analysis_prompt}], MODELS["ultra"], client_index=0)
    
    if "Error" in analysis_res:
        bot.edit_message_text(analysis_res, chat_id, status_id)
        return
        
    bot.edit_message_text(s["phase2"], chat_id, status_id)
    
    base_dev = s["system_developer"]
    if custom_inst:
        base_dev += f"\nCustom instructions: {custom_inst}"

    final_res = ""
    if num_keys > 1:
        with ThreadPoolExecutor(max_workers=num_keys) as executor:
            tasks = [
                executor.submit(call_nvidia_api, base_dev, [{"role": "user", "content": f"Architecture: {analysis_res}\nRequest: {user_prompt}"}], MODELS["ultra"], 0),
                executor.submit(call_nvidia_api, "Technical Documentation Expert.", [{"role": "user", "content": f"Analysis: {analysis_res}"}], MODELS["pro"], 1 % num_keys)
            ]
            results = [t.result() for t in tasks]
            final_res = f"{results[0]}\n\n/* DOCUMENTATION */\n{results[1]}"
    else:
        final_res = call_nvidia_api(base_dev, [{"role": "user", "content": f"Architecture: {analysis_res}\nRequest: {user_prompt}"}], MODELS["ultra"], 0)
    
    final_code = re.sub(r'^```[a-z]*\n', '', final_res, flags=re.MULTILINE)
    final_code = re.sub(r'```$', '', final_code, flags=re.MULTILINE).strip()
    file_io = io.BytesIO(final_code.encode('utf-8'))
    file_io.name = "premium_solution.py" if "python" in user_prompt.lower() else "premium_index.html"
    bot.send_document(chat_id, file_io, caption=s["success"])
    bot.delete_message(chat_id, status_id)

def is_allowed(user_id: int):
    return user_id in ALLOWED_IDS if ALLOWED_IDS else True

@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    """Greets the user and displays the number of active API keys available for processing."""
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    bot.send_message(message.chat.id, STRINGS.get(mem["language"], STRINGS["fr"])["start"].format(len(NVIDIA_CLIENTS)), parse_mode="Markdown")

@bot.message_handler(commands=["creator"])
def handle_creator(message: Message):
    """Provides information about the developer and links to various GitHub repositories."""
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    bot.send_message(message.chat.id, s["creator_text"], parse_mode="Markdown", disable_web_page_preview=True)

@bot.message_handler(commands=["reset"])
def handle_reset(message: Message):
    """Clears all stored memory and preferences for the user, effectively resetting their profile."""
    if not is_allowed(message.from_user.id): return
    uid = message.from_user.id
    with get_db() as conn:
        conn.execute("DELETE FROM memory WHERE user_id = ?", (uid,))
        conn.commit()
    mem = get_user_memory(uid)
    bot.send_message(message.chat.id, STRINGS.get(mem["language"], STRINGS["fr"])["reset_done"])

@bot.message_handler(commands=["customize"])
def handle_customize(message: Message):
    """Processes the /customize command to save specific behavioral instructions for the AI."""
    if not is_allowed(message.from_user.id): return
    uid = message.from_user.id
    mem = get_user_memory(uid)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    
    cmd_parts = message.text.split(maxsplit=1)
    if len(cmd_parts) < 2:
        bot.send_message(message.chat.id, s["custom_help"])
        return
        
    instructions = cmd_parts[1].strip()
    save_user_memory(uid, custom_instructions=instructions)
    bot.send_message(message.chat.id, s["custom_set"])

@bot.message_handler(commands=["language"])
def handle_language(message: Message):
    """Displays an inline menu for the user to choose between French and English."""
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Français", callback_data="setlang:fr"),
               InlineKeyboardButton("English", callback_data="setlang:en"))
    bot.send_message(message.chat.id, STRINGS.get(mem["language"], STRINGS["fr"])["lang_select"], reply_markup=markup)

@bot.message_handler(commands=["model"])
def handle_model_command(message: Message):
    """Shows the selection menu for different Llama models and the premium pipeline configuration."""
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Léger (8B)", callback_data="setmod:flash"),
               InlineKeyboardButton("Pro (70B)", callback_data="setmod:pro"))
    markup.row(InlineKeyboardButton("Ultra (405B)", callback_data="setmod:ultra"))
    markup.row(InlineKeyboardButton(s["pipe_config"], callback_data="pipe_config"))
    bot.send_message(message.chat.id, s["model_select"], reply_markup=markup)

@bot.message_handler(commands=["export"])
def handle_export(message: Message):
    """Generates and sends a JSON file containing all the data stored in the user's memory."""
    if not is_allowed(message.from_user.id): return
    uid = message.from_user.id
    mem = get_user_memory(uid)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    json_data = json.dumps(mem, indent=4, ensure_ascii=False)
    file_io = io.BytesIO(json_data.encode('utf-8'))
    file_io.name = f"memory_user_{uid}.json"
    bot.send_document(message.chat.id, file_io, caption=s["export_done"])

@bot.callback_query_handler(func=lambda call: call.data.startswith("setlang:"))
def callback_set_lang(call):
    """Updates the user's preferred language based on their selection from the inline menu."""
    lang = call.data.split(":")[1]
    save_user_memory(call.from_user.id, language=lang)
    bot.answer_callback_query(call.id, STRINGS.get(lang, STRINGS["fr"])["lang_set"])
    bot.edit_message_text(STRINGS.get(lang, STRINGS["fr"])["lang_set"], call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "pipe_config")
def callback_pipe_config(call):
    """Presents the options for allocating a specific number of API keys to the premium pipeline."""
    mem = get_user_memory(call.from_user.id)
    markup = InlineKeyboardMarkup()
    for i in range(1, min(len(NVIDIA_CLIENTS) + 1, 11)):
        markup.insert(InlineKeyboardButton(f"{i} Clé(s)", callback_data=f"setpipe:{i}"))
    bot.edit_message_text(STRINGS.get(mem["language"], STRINGS["fr"])["pipe_keys"], call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setpipe:"))
def callback_set_pipe(call):
    """Activates the multi-agent system and sets the number of keys to be used for future requests."""
    num = int(call.data.split(":")[1])
    mem = get_user_memory(call.from_user.id)
    save_user_memory(call.from_user.id, current_model="multi_agent_system", pipeline_keys=num)
    bot.answer_callback_query(call.id, "OK")
    bot.edit_message_text(STRINGS.get(mem["language"], STRINGS["fr"])["pipe_set"].format(num), call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmod:"))
def callback_set_model(call):
    """Updates the user's current AI model preference based on their menu selection."""
    key = call.data.split(":")[1]
    mem = get_user_memory(call.from_user.id)
    new_model = MODELS.get(key, MODELS["flash"])
    save_user_memory(call.from_user.id, current_model=new_model)
    bot.answer_callback_query(call.id, "OK")
    bot.edit_message_text(STRINGS.get(mem["language"], STRINGS["fr"])["model_set"].format(key.upper()), call.message.chat.id, call.message.message_id)

@bot.message_handler(content_types=["poll"])
def handle_poll(message: Message):
    """Analyzes a poll and makes the bot vote for the most logical option using IA."""
    poll: Poll = message.poll
    # We use a default language or try to guess from context, here we use English for the prompt logic
    s = STRINGS["en"] 
    options_text = "\n".join([f"{i}. {opt.text}" for i, opt in enumerate(poll.options)])
    prompt = f"Poll Question: {poll.question}\nOptions:\n{options_text}\nPick the best option index."
    
    res = call_nvidia_api(s["poll_system"], [{"role": "user", "content": prompt}], MODELS["flash"])
    
    # Try to extract a digit from the response
    match = re.search(r'\d+', res)
    if match:
        index = int(match.group())
        if 0 <= index < len(poll.options):
            try:
                # Bots can only vote in non-anonymous polls or if they are the creator, 
                # but standard bots use stop_poll or we just log the 'choice'
                log.info(f"AI suggests option index {index} for poll: {poll.question}")
                # Note: Telegram Bot API doesn't allow bots to VOTE in polls they didn't create 
                # or in general anonymous polls. However, we can send a message with the recommendation.
                bot.send_message(message.chat.id, f"AI Recommendation for this poll: Option {index} ({poll.options[index].text})")
            except Exception as e:
                log.error(f"Error handling poll vote: {e}")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: Message):
    """The main entry point for processing text queries, routing them to either a single model or the multi-agent pipeline."""
    if not is_allowed(message.from_user.id): return
    uid, txt = message.from_user.id, message.text.strip()
    mem = get_user_memory(uid)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    status_msg = bot.send_message(message.chat.id, s["wait"])
    
    custom_inst = mem.get("custom_instructions", "")
    
    if mem["model"] == "multi_agent_system":
        threading.Thread(target=run_multi_agent_pipeline, 
                         args=(txt, message.chat.id, status_msg.message_id, mem["pipeline_keys"], mem["language"], custom_inst)).start()
    else:
        system_p = s["system_companion"]
        if custom_inst:
            system_p += f"\nCustom instructions: {custom_inst}"
            
        res = call_nvidia_api(system_p, [{"role": "user", "content": txt}], mem["model"])
        bot.edit_message_text(res, message.chat.id, status_msg.message_id)

if __name__ == "__main__":
    init_db()
    bot.infinity_polling()
