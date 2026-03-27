# Nvidia Telegram AI Multi-Agent

## Ce projet est une interface de bot Telegram avancée permettant d'interagir avec les modèles de langage Llama 3.1 (8B, 70B et 405B) hébergés par NVIDIA. Il intègre un système de gestion de mémoire SQLite et un pipeline multi-agent pour la génération de code de haute précision.

Fonctionnalités Principales

Multi-API : Supporte jusqu'à 10 clés API NVIDIA simultanées pour augmenter les quotas et la puissance de calcul.

Gestion de Modèles : Choix entre les versions Flash (8B), Pro (70B) et Ultra (405B).

Système Multi-Agent : Pipeline spécialisé qui sépare l'analyse architecturale (Analyste Senior) du codage (Développeur Full-Stack).

Mémoire Persistante : Stockage des préférences utilisateurs, des instructions personnalisées et de l'historique dans une base de données SQLite.

Bilingue : Support complet du Français et de l'Anglais.

Analyse de Sondages : Capacité à analyser les questions de type Poll et à suggérer l'option la plus logique via l'IA.

Personnalisation : Commande `/customize` dédiée pour définir un comportement système spécifique.

## Installation

### Cloner le dépôt sur votre machine locale ou serveur.

#### Installer les dépendances Python :
#### `pip install -r requirements.txt`

### Configurer les variables d'environnement dans un fichier `.env` ou dans votre système.

## Configuration (Variables d'environnement)

`TELEGRAM_TOKEN` : Votre jeton obtenu via `@BotFather`.

`NVIDIA_API_KEY_1` à NVIDIA_API_KEY10 : Vos clés API NVIDIA (au moins une est requise).

`ADMIN_USER_ID` : (Optionnel) IDs Telegram autorisés à utiliser le bot, séparés par des virgules.

`DB_PATH` : Chemin vers le fichier de base de données (par défaut : `/app/data/memory.db`).

#### Commandes du Bot

`/start` : Initialisation et vérification des clés API.

`/model` : Menu de sélection du modèle ou activation du pipeline multi-agent.

`/customize` : Définition d'instructions personnalisées pour l'IA.

`/language` : Changement de la langue d'interface.

`/export` : Exportation des données de mémoire au format JSON.

`/reset` : Réinitialisation totale du profil utilisateur.

`/creator` : Informations sur l'auteur et liens vers les dépôts GitHub.

Architecture Technique

Le bot utilise la bibliothèque `pyTelegramBotAPI` pour la gestion des événements Telegram et le client `OpenAI` pour les appels vers l'infrastructure NVIDIA. Les appels complexes (multi-agents) sont exécutés dans des threads séparés via `ThreadPoolExecutor` pour ne pas bloquer les autres utilisateurs pendant les phases de réflexion prolongées.

### Sécurité et Limitations

- Le bot vérifie l'ID de l'utilisateur par rapport à la liste blanche `ADMIN_USER_ID` si celle-ci est configurée.

- Les fichiers générés par le pipeline multi-agent sont envoyés directement en tant que documents pour préserver le formatage du code.
