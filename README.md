# Hermes

Agent autonome pilote depuis Telegram. Il **code**, **execute**, **lit des fichiers**
et **cherche sur le web** — et il tourne indifféremment sur n'importe quel modele,
d'un Ollama local sans aucun filtre jusqu'a Claude Opus 5.

```
Telegram  ──►  bot.py  ──►  agent.py  ──►  llm.py  ──►  14 fournisseurs
                              │
                              └──►  tools/  ──►  shell · python · fichiers · web
```

## Ce qu'il sait faire

| Outil | Effet |
|---|---|
| `shell` | Commande shell dans le workspace : tests, git, npm, compilation |
| `python` | Script Python execute et dont la sortie revient au modele |
| `read_file` / `write_file` / `edit_file` / `list_dir` | Manipulation de fichiers confinee au workspace |
| `web_search` | Recherche via Tavily, Brave, SearXNG ou DuckDuckGo (sans cle) |
| `web_fetch` | Telechargement d'une page et extraction du texte |

Les appels d'outils d'un meme tour partent **en parallele**. L'agent enchaine
jusqu'a 25 tours sans redemander la permission.

## Choisir son modele

`HERMES_PROVIDER` accepte 14 valeurs. Elles se rangent en trois familles.

**Local — aucun filtrage, aucune donnee qui sort**

| Fournisseur | Pour quoi |
|---|---|
| `ollama` | Le plus simple. `hermes4:70b`, `dolphin3:8b`, `qwen3.8:27b`, les builds *abliterated* |
| `vllm` | Le plus rapide pour servir des poids ouverts en production |
| `lmstudio` | Interface graphique, pratique sur poste de travail |

**Heberge, politique de contenu legere**

| Fournisseur | Pour quoi |
|---|---|
| `openrouter` | Une cle, des centaines de modeles, y compris les fine-tunes sans refus. **Le meilleur pour comparer** |
| `deepseek` | Tres peu de refus, excellent en code, prix plancher |
| `venice` | Oriente vie privee : pas de log de conversation, pas de filtre |
| `xai` | Grok, politique nettement plus laxiste que la moyenne |
| `groq`, `together`, `fireworks`, `mistral` | Poids ouverts a basse latence |

**Les plus capables en code**

| Fournisseur | Pour quoi |
|---|---|
| `anthropic` | `claude-opus-5`, reflexion adaptative activee, meilleur en agentique longue |
| `openai` | `gpt-5` |
| `custom` | N'importe quel endpoint compatible OpenAI, via `HERMES_CUSTOM_BASE_URL` |

> Un modele *abliterated* accepte tout mais raisonne un peu moins bien : retirer les
> refus coute de la qualite. Pour un agent qui code, **DeepSeek V3.x** et
> **Hermes 4** sont le bon compromis — permissifs *et* solides.

On bascule a chaud depuis Telegram, par conversation :

```
/model deepseek deepseek-chat
/model ollama hermes4:70b
/model anthropic claude-opus-5
```

## Installation

```bash
git clone https://github.com/MalikChekour/delta.git
cd delta
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

### 1. Le token Telegram

Il ne peut venir que de Telegram, personne d'autre ne peut le generer :

1. Ouvre Telegram, cherche **@BotFather**
2. `/newbot`, choisis un nom puis un identifiant finissant par `bot`
3. BotFather renvoie une ligne `123456789:AAE...` → c'est `TELEGRAM_BOT_TOKEN`

### 2. Ton identifiant utilisateur

Parle a **@userinfobot**, il renvoie ton numero → `HERMES_ALLOWED_USERS`.

C'est **obligatoire**. Hermes ouvre un shell sur la machine : sans liste blanche,
quiconque trouve le bot obtient ce shell.

### 3. La cle du modele

Renseigne uniquement celle du fournisseur choisi. Avec `ollama`, aucune cle :

```bash
ollama serve
ollama pull hermes4:70b
```

### 4. Lancer

```bash
python -m hermes
```

## Ou tourner Hermes

Le mode d'execution retenu est **subprocess local avec timeout** : le code que
l'agent ecrit tourne avec les droits du processus Hermes, `cwd` force sur le
workspace. Les garde-fous en place :

- chemins des outils fichiers confines au workspace (les `../` sont rejetes)
- timeout par commande, processus tue par groupe
- cles d'API retirees de l'environnement des sous-processus — un `env` lance par
  l'agent ne voit aucun secret
- sortie tronquee pour ne pas noyer le contexte

Ce ne sont pas des barreaux : `shell` peut ecrire hors du workspace. **Fais tourner
Hermes sur une VM ou un conteneur dedie**, pas sur ta machine de travail.

## Configuration

Tout est dans [`.env.example`](.env.example). Les reglages qui comptent :

| Variable | Defaut | Effet |
|---|---|---|
| `HERMES_MAX_TOOL_ITERATIONS` | `25` | Tours d'outils avant abandon |
| `HERMES_HISTORY_TURNS` | `40` | Messages gardes en memoire par conversation |
| `HERMES_EXEC_TIMEOUT` | `120` | Timeout par commande, en secondes |
| `HERMES_SEARCH_BACKEND` | `auto` | `tavily` / `brave` / `searxng` / `duckduckgo` |
| `HERMES_SYSTEM_PROMPT_FILE` | — | Remplace entierement le prompt systeme |
| `HERMES_THINKING` | `adaptive` | Reflexion adaptative cote Anthropic ; `off` pour couper |

## Commandes Telegram

| Commande | Effet |
|---|---|
| `/model` | Modele courant ; `/model <fournisseur> <modele>` pour changer |
| `/providers` | Les 14 fournisseurs et leurs modeles suggeres |
| `/tools` | Les outils disponibles |
| `/get <chemin>` | Renvoie un fichier du workspace en piece jointe |
| `/status` | Fournisseur, modele, taille de l'historique |
| `/reset` | Efface l'historique, garde le choix de modele |

## Ajouter un fournisseur

Une entree dans `hermes/providers.py`, rien d'autre :

```python
"mon-serveur": ProviderSpec(
    name="mon-serveur",
    kind="openai",                      # ou "anthropic"
    base_url="https://api.exemple.com/v1",
    api_key_env="MON_SERVEUR_API_KEY",
    default_model="mon-modele",
),
```

## Tests

```bash
pytest        # 33 tests
ruff check .
```

Couvrent le confinement des chemins, le retrait des secrets de l'environnement,
le timeout, la troncature d'historique sans casser les paires appel/resultat, la
traduction vers le protocole Anthropic et la boucle agentique complete.
