# ══════════════════════════════════════════════════════════════════════
#  TELEGRAM AI BOT — Guide de déploiement Docker
#  Prérequis : Docker >= 20.10, Linux/macOS/WSL2
# ══════════════════════════════════════════════════════════════════════


## ── 1. STRUCTURE DU PROJET ───────────────────────────────────────────

```
telegram-ai-bot/
├── bot.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml   ← optionnel, recommandé
└── data/                ← créé automatiquement, contient memory.db
```

Crée le dossier et place-toi dedans :

```bash
mkdir telegram-ai-bot && cd telegram-ai-bot
```


## ── 2. VARIABLES D'ENVIRONNEMENT ─────────────────────────────────────

Crée un fichier `.env` (ne pas committer dans git !) :

```bash
cat > .env << 'EOF'
TELEGRAM_TOKEN=7xxxxxxxxx:AAF...ton_token_ici
NVIDIA_API_KEY=nvapi-...ta_clé_ici
# Optionnel — valeurs par défaut :
MODEL_ID=meta/llama-3.1-405b-instruct
MEMORY_THRESHOLD=10
EOF
```

Assure-toi que `.env` est dans ton `.gitignore` :

```bash
echo ".env" >> .gitignore
echo "data/" >> .gitignore
```


## ── 3A. DÉPLOIEMENT AVEC DOCKER COMPOSE (recommandé) ─────────────────

Crée `docker-compose.yml` :

```bash
cat > docker-compose.yml << 'EOF'
version: "3.9"

services:
  telegram-bot:
    build: .
    container_name: telegram-ai-bot
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
    networks:
      - bot-net
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

networks:
  bot-net:
    driver: bridge
EOF
```

Build et lancement :

```bash
docker compose up -d --build
```

Logs en temps réel :

```bash
docker compose logs -f
```

Arrêt propre :

```bash
docker compose down
```


## ── 3B. DÉPLOIEMENT SANS DOCKER COMPOSE (commandes brutes) ───────────

Build de l'image :

```bash
docker build -t telegram-ai-bot:latest .
```

Créer le dossier de données persistantes :

```bash
mkdir -p ./data
```

Lancer le container :

```bash
docker run -d \
  --name telegram-ai-bot \
  --restart unless-stopped \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  --network bridge \
  telegram-ai-bot:latest
```

Logs en temps réel :

```bash
docker logs -f telegram-ai-bot
```


## ── 4. COMMANDES DE MAINTENANCE ──────────────────────────────────────

Vérifier l'état du container :
```bash
docker ps -a --filter name=telegram-ai-bot
```

Redémarrer après un changement de code :
```bash
docker compose up -d --build   # avec compose
# ou :
docker stop telegram-ai-bot && docker rm telegram-ai-bot
docker build -t telegram-ai-bot:latest . && docker run -d ...  # sans compose
```

Inspecter la base SQLite directement :
```bash
docker exec -it telegram-ai-bot sqlite3 /app/data/memory.db \
  "SELECT user_id, exchange_count, length(summary) as summary_len FROM memory;"
```

Sauvegarder la base de données :
```bash
cp ./data/memory.db ./data/memory.db.bak
```

Supprimer le container ET les données (reset complet) :
```bash
docker compose down
rm -rf ./data
```


## ── 5. VARIABLES D'ENVIRONNEMENT DISPONIBLES ─────────────────────────

| Variable           | Défaut                                  | Description                        |
|--------------------|------------------------------------------|------------------------------------|
| TELEGRAM_TOKEN     | *obligatoire*                            | Token BotFather                    |
| NVIDIA_API_KEY     | *obligatoire*                            | Clé API NVIDIA NIM                 |
| MODEL_ID           | meta/llama-3.1-405b-instruct             | Modèle NVIDIA NIM à utiliser       |
| NVIDIA_BASE_URL    | https://integrate.api.nvidia.com/v1      | Endpoint API                       |
| DB_PATH            | /app/data/memory.db                      | Chemin SQLite dans le container    |
| MEMORY_THRESHOLD   | 10                                       | Échanges avant compression mémoire |


## ── 6. OBTENIR LES TOKENS ────────────────────────────────────────────

**Telegram Token** :
1. Ouvrir Telegram → chercher @BotFather
2. `/newbot` → suivre les instructions
3. Copier le token fourni

**NVIDIA API Key** :
1. https://build.nvidia.com/
2. S'inscrire → "Get API Key"
3. Copier `nvapi-...`
