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
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

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
        "start": "Arthur Engineering\nMode Multi-API actif avec {} cles detectees.",
        "model_select": "Selectionnez votre niveau d'expertise :",
        "lang_select": "Choisissez votre langue :",
        "pipe_config": "CONFIGURER PIPELINE PREMIUM",
        "pipe_keys": "Allocation de puissance pour le pipeline Premium :",
        "pipe_set": "Pipeline Premium configure avec {} cle(s).",
        "model_set": "Mode {} active.",
        "wait": "Reflexion en cours...",
        "phase1": "Phase 1: Architecture Premium...",
        "phase2": "Phase 2: Codage Haute Precision...",
        "success": "Code Premium genere avec Llama-3.1-405B.\nArchitecture validee",
        "system_companion": "Tu es une IA assistante utile et concise. Reponds naturellement aux questions de l'utilisateur.",
        "system_analyst": "Tu es un Architecte Logiciel Senior. Analyse la demande et concois une structure technique parfaite (SOLID, DRY, Securite).",
        "system_developer": "Tu es un Developpeur Full-Stack de classe mondiale. Ecris un code source Premium, propre et commente. Ne fournis QUE le code.",
        "lang_set": "Langue reglee sur le Francais.",
        "export_done": "Voici votre fichier de memoire au format JSON.",
        "creator_text": (
            "Proprietaire: Artleboss2\n\n"
            "Depots GitHub principaux :\n"
            "1. TelegramAI : Interface IA multi-agents pour Telegram\n"
            "2. MyProjects : Collection de scripts et d'outils\n"
            "3. Portfolio : Site vitrine personnel\n\n"
            "Profil complet : https://github.com/Artleboss2"
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
        "creator_text": (
            "Owner: Artleboss2\n\n"
            "Main GitHub Repositories:\n"
            "1. TelegramAI: Multi-agent AI interface for Telegram\n"
            "2. MyProjects: Collection of scripts and tools\n"
            "3. Portfolio: Personal showcase website\n\n"
            "Full profile: https://github.com/Artleboss2"
        )
    },
    "zh": {
        "start": "Arthur Engineering\n多API模式已激活，检测到 {} 个密钥。",
        "model_select": "选择您的专业水平：",
        "lang_select": "选择您的语言：",
        "pipe_config": "配置高级流水线",
        "pipe_keys": "高级流水线功率分配：",
        "pipe_set": "高级流水线已配置 {} 个密钥。",
        "model_set": "{} 模式已激活。",
        "wait": "思考中...",
        "phase1": "阶段 1：高级架构设计...",
        "phase2": "阶段 2：高精度编码...",
        "success": "使用 Llama-3.1-405B 生成的高级代码。\n架构已验证",
        "system_companion": "你是一个乐于助人且简洁的AI助手。请自然地回答用户的问题。",
        "system_analyst": "你是一位资深软件架构师。分析需求并设计完美的技术结构（SOLID, DRY, 安全）。",
        "system_developer": "你是一位世界级的全栈开发人员。编写高级、整洁且有注释的源代码。只提供代码。",
        "lang_set": "语言已设置为中文。",
        "export_done": "这是您的 JSON 格式记忆文件。",
        "creator_text": (
            "所有者: Artleboss2\n\n"
            "主要 GitHub 仓库：\n"
            "1. TelegramAI: 电报多代理 AI 界面\n"
            "2. MyProjects: 脚本和工具集合\n"
            "3. Portfolio: 个人展示网站\n\n"
            "完整资料: https://github.com/Artleboss2"
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
        return f"Error/Erreur API ({str(e)})"

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
                language TEXT DEFAULT 'fr'
            )
        """)
        cursor = conn.execute("PRAGMA table_info(memory)")
        columns = [row[1] for row in cursor.fetchall()]
        if "pipeline_keys" not in columns:
            conn.execute("ALTER TABLE memory ADD COLUMN pipeline_keys INTEGER DEFAULT 1")
        if "language" not in columns:
            conn.execute("ALTER TABLE memory ADD COLUMN language TEXT DEFAULT 'fr'")
        conn.commit()

def get_user_memory(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memory WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"summary": "", "last_messages": [], "exchange_count": 0, "model": MODELS["flash"], "pipeline_keys": 1, "language": "fr"}
    res = dict(row)
    return {
        "summary": res.get("summary", ""),
        "last_messages": json.loads(res.get("last_messages_json", "[]")),
        "exchange_count": res.get("exchange_count", 0),
        "model": res.get("current_model", MODELS["flash"]),
        "pipeline_keys": res.get("pipeline_keys", 1),
        "language": res.get("language", "fr")
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

def run_multi_agent_pipeline(user_prompt, chat_id, status_id, num_keys, lang):
    target_repo = "https://github.com/Artleboss2"
    num_keys = min(num_keys, len(NVIDIA_CLIENTS))
    s = STRINGS.get(lang, STRINGS["fr"])
    bot.edit_message_text(s["phase1"], chat_id, status_id)
    analysis_prompt = f"Target Repository: {target_repo}\nUser Request: {user_prompt}\nPlease analyze and create a premium coding plan."
    analysis_res = call_nvidia_api(s["system_analyst"], [{"role": "user", "content": analysis_prompt}], MODELS["ultra"], client_index=0)
    if "Error" in analysis_res:
        bot.edit_message_text(analysis_res, chat_id, status_id)
        return
    bot.edit_message_text(s["phase2"], chat_id, status_id)
    final_res = ""
    if num_keys > 1:
        with ThreadPoolExecutor(max_workers=num_keys) as executor:
            tasks = [
                executor.submit(call_nvidia_api, s["system_developer"], [{"role": "user", "content": f"Architecture: {analysis_res}\nRequest: {user_prompt}"}], MODELS["ultra"], 0),
                executor.submit(call_nvidia_api, "Technical Documentation Expert.", [{"role": "user", "content": f"Analysis: {analysis_res}"}], MODELS["pro"], 1 % num_keys)
            ]
            results = [t.result() for t in tasks]
            final_res = f"{results[0]}\n\n/* DOCUMENTATION */\n{results[1]}"
    else:
        final_res = call_nvidia_api(s["system_developer"], [{"role": "user", "content": f"Architecture: {analysis_res}\nRequest: {user_prompt}"}], MODELS["ultra"], 0)
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
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    bot.send_message(message.chat.id, STRINGS.get(mem["language"], STRINGS["fr"])["start"].format(len(NVIDIA_CLIENTS)))

@bot.message_handler(commands=["creator"])
def handle_creator(message: Message):
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    bot.send_message(message.chat.id, s["creator_text"], disable_web_page_preview=True)

@bot.message_handler(commands=["language"])
def handle_language(message: Message):
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Francais", callback_data="setlang:fr"),
               InlineKeyboardButton("English", callback_data="setlang:en"))
    markup.row(InlineKeyboardButton("Mandarin", callback_data="setlang:zh"))
    bot.send_message(message.chat.id, STRINGS.get(mem["language"], STRINGS["fr"])["lang_select"], reply_markup=markup)

@bot.message_handler(commands=["model"])
def handle_model_command(message: Message):
    if not is_allowed(message.from_user.id): return
    mem = get_user_memory(message.from_user.id)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Leger (8B)", callback_data="setmod:flash"),
               InlineKeyboardButton("Pro (70B)", callback_data="setmod:pro"))
    markup.row(InlineKeyboardButton("Ultra (405B)", callback_data="setmod:ultra"))
    markup.row(InlineKeyboardButton(s["pipe_config"], callback_data="pipe_config"))
    bot.send_message(message.chat.id, s["model_select"], reply_markup=markup)

@bot.message_handler(commands=["export"])
def handle_export(message: Message):
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
    lang = call.data.split(":")[1]
    save_user_memory(call.from_user.id, language=lang)
    bot.answer_callback_query(call.id, STRINGS.get(lang, STRINGS["fr"])["lang_set"])
    bot.edit_message_text(STRINGS.get(lang, STRINGS["fr"])["lang_set"], call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "pipe_config")
def callback_pipe_config(call):
    mem = get_user_memory(call.from_user.id)
    markup = InlineKeyboardMarkup()
    for i in range(1, min(len(NVIDIA_CLIENTS) + 1, 11)):
        markup.insert(InlineKeyboardButton(f"{i} Cle(s)", callback_data=f"setpipe:{i}"))
    bot.edit_message_text(STRINGS.get(mem["language"], STRINGS["fr"])["pipe_keys"], call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setpipe:"))
def callback_set_pipe(call):
    num = int(call.data.split(":")[1])
    mem = get_user_memory(call.from_user.id)
    save_user_memory(call.from_user.id, current_model="multi_agent_system", pipeline_keys=num)
    bot.answer_callback_query(call.id, "OK")
    bot.edit_message_text(STRINGS.get(mem["language"], STRINGS["fr"])["pipe_set"].format(num), call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmod:"))
def callback_set_model(call):
    key = call.data.split(":")[1]
    mem = get_user_memory(call.from_user.id)
    new_model = MODELS.get(key, MODELS["flash"])
    save_user_memory(call.from_user.id, current_model=new_model)
    bot.answer_callback_query(call.id, "OK")
    bot.edit_message_text(STRINGS.get(mem["language"], STRINGS["fr"])["model_set"].format(key.upper()), call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: Message):
    if not is_allowed(message.from_user.id): return
    uid, txt = message.from_user.id, message.text.strip()
    mem = get_user_memory(uid)
    s = STRINGS.get(mem["language"], STRINGS["fr"])
    status_msg = bot.send_message(message.chat.id, s["wait"])
    if mem["model"] == "multi_agent_system":
        threading.Thread(target=run_multi_agent_pipeline, 
                         args=(txt, message.chat.id, status_msg.message_id, mem["pipeline_keys"], mem["language"])).start()
    else:
        res = call_nvidia_api(s["system_companion"], [{"role": "user", "content": txt}], mem["model"])
        bot.edit_message_text(res, message.chat.id, status_msg.message_id)

if __name__ == "__main__":
    init_db()
    bot.infinity_polling()
