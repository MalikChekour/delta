# Hermes

Agent autonome pilote depuis Telegram. Modele principal **GLM-4.7-Heretic**,
repli **Venice**. Il execute reellement du shell et du Python, lit et ecrit des
fichiers, cherche sur le web — dans un workspace confine.

```
vous   > Recupere le cours de l'or, trace-le sur 30 jours et envoie-moi le PNG.
hermes > ⚙️ web_search → fetch_url → write_file → python
hermes > Voici le graphique. Le cours a pris 4,2 % sur la periode. [graphique.png]
```

## Demarrage

```bash
git clone <ce depot> hermes && cd hermes
make dev                  # environnement virtuel + dependances
cp .env.example .env      # puis renseigner le fichier (voir plus bas)
make doctor               # verifie token, cles et routes de modeles
make run                  # demarre le bot
```

Le minimum a renseigner dans `.env` :

```ini
TELEGRAM_BOT_TOKEN=...        # donne par @BotFather
HERMES_ALLOWED_USERS=123456   # ton identifiant numerique, via @userinfobot
VENICE_API_KEY=...            # ou OPENROUTER_API_KEY, CHUTES_API_KEY, ZAI_API_KEY
```

Si tu ne connais pas ton identifiant numerique, mets `HERMES_CLAIM_OWNER=1` :
le **premier** a envoyer `/start` devient proprietaire, et lui seul. Fais-le
immediatement apres le demarrage, puis recopie l'identifiant affiche dans
`HERMES_ALLOWED_USERS`.

Sans Telegram, la meme boucle d'agent tourne dans le terminal :

```bash
.venv/bin/hermes chat
```

## Modeles

Un modele d'Hermes est un **alias** resolu vers une liste ordonnee de routes
`fournisseur:identifiant`. Hermes prend la premiere route servable — cle
renseignee, ou serveur local qui repond — et bascule sur la suivante si elle
tombe. Une route en echec est ecartee cinq minutes plutot que ressayee a chaque
message.

| Alias | Routes, dans l'ordre |
| --- | --- |
| `glm-4.7-heretic` *(defaut)* | vLLM local → Ollama local → `venice:olafangensan-glm-4.7-flash-heretic` → Chutes → OpenRouter → `venice:zai-org-glm-4.7` |
| `venice` *(repli)* | `venice:zai-org-glm-4.7` → `venice:venice-uncensored-1-2` |
| `venice-uncensored` | `venice:venice-uncensored-1-2` |
| `glm-4.7` | Venice → Z.ai → OpenRouter |
| `deepseek` | DeepSeek → Venice → OpenRouter |

```bash
hermes models        # catalogue et etat de chaque route
```

Dans Telegram : `/model venice`, `/model glm-4.7`, `/model venice:un-modele`,
ou `/model auto` pour revenir a la chaine par defaut. Le choix est retenu par
conversation.

Servir les poids en local (rien ne sort de la machine) :

```bash
vllm serve p-e-w/GLM-4.7-Heretic --port 8000
```

Hermes sonde le port : des que le serveur repond, la route locale passe devant
les routes hebergees, sans rien changer a la configuration.

**Choix du modele.** `glm-4.7-flash-heretic` est rapide, sans refus, et appelle
correctement les outils, mais sa redaction finale part parfois en digression.
Si tu preferes des reponses plus tenues au prix de quelques refus,
`/model glm-4.7` bascule sur le GLM d'origine, meme famille.

## Commandes Telegram

| Commande | Effet |
| --- | --- |
| *(texte libre)* | confie une tache a l'agent |
| *(fichier joint)* | depose le fichier dans le workspace |
| `/model` | voir ou changer de modele |
| `/models` | catalogue et etat des routes |
| `/tools` | outils disponibles |
| `/status` | modele, historique, workspace, tache en cours |
| `/stop` | interrompre la tache en cours |
| `/get <chemin>` | recuperer un fichier du workspace |
| `/reset` | effacer l'historique |

## Outils

`shell`, `python`, `read_file`, `write_file`, `edit_file`, `list_files`,
`web_search`, `fetch_url`. Ils s'executent dans le workspace, avec delai
maximal et sortie tronquee ; `HERMES_ENABLE_SHELL=0` et `HERMES_ENABLE_WEB=0`
en retirent des familles entieres.

## Ce qui a ete fait pour que ca ne casse pas

Les pannes d'un bot agentique sont toujours les memes. Elles sont traitees ici
une par une, et chacune a son test de non-regression.

- **Historique invalide.** Un appel d'outil sans resultat, ou un resultat
  orphelin, produit un HTTP 400 qui casse la conversation *definitivement*,
  puisque l'historique fautif est relu a chaque tour. `hermes/history.py`
  retablit les invariants du protocole avant chaque envoi et avant chaque
  ecriture en base ; la sauvegarde a lieu dans un `finally`, y compris quand le
  tour echoue ou est annule.
- **Identifiants d'appels reutilises.** vLLM, Ollama et llama.cpp numerotent les
  appels a partir de zero dans *chaque* message : `call_0` reapparait a chaque
  tour. Le rapprochement appel/resultat se fait donc par lot, jamais sur
  l'historique entier — sans quoi tout resultat d'outil a partir du deuxieme
  tour passerait pour un doublon et serait remplace par un resultat de
  substitution : l'agent perdrait ses propres observations en silence.
- **Balisage refuse par Telegram.** Envoyer le Markdown d'un modele tel quel
  echoue des qu'une asterisque traine ou qu'un bloc de code n'est pas ferme.
  Hermes convertit lui-meme en HTML et decoupe *ligne par ligne*, en refermant
  les balises a chaque frontiere. Un balisage entremele (`~~a *b~~ c*`)
  produirait des balises croisees, que Telegram refuse : chaque ligne rendue est
  verifiee, et repliee en texte nu si elle sort mal formee.
- **Appels d'outils simultanes.** Le modele emet souvent plusieurs appels dans un
  meme tour, executes en parallele. L'outil `python` ecrit donc son script sous
  un nom unique : avec un nom fixe, un appel executait le code d'un autre sans
  que rien ne le signale.
- **Boucle d'evenements bloquee.** Un outil synchrone qui lit un fichier ou
  parcourt un dossier gelerait tout le bot — les autres conversations comme le
  polling Telegram. Ils sont executes hors de la boucle, et le parcours de
  dossiers s'arrete des la limite atteinte au lieu de traverser l'arbre entier.
- **Messages simultanes.** Deux messages envoyes coup sur coup liraient le meme
  historique et le dernier ecraserait l'autre. Un verrou par `chat_id`
  serialise les tours d'une conversation sans bloquer les autres — et `/stop`
  interrompt toutes les taches du chat, y compris celles qui attendent leur tour.
- **Fournisseur en panne.** Une cle expiree, un modele retire du catalogue ou un
  serveur local eteint ne coupent plus le service : le routeur bascule sur la
  route suivante et met la route morte en quarantaine, plus longtemps si la
  panne est structurelle (cle refusee) que passagere (debit depasse).
- **Outil qui echoue.** L'erreur est rendue au modele sous forme de texte plutot
  que remontee en exception : il corrige au tour suivant au lieu de faire tomber
  la boucle.
- **Reponse vide.** Un modele renvoie parfois ni texte ni appel d'outil. Le cas
  est nomme explicitement plutot que de laisser un message blanc.
- **Chemins.** Un chemin absolu ou une evasion par lien symbolique est refuse
  explicitement, jamais replie en silence dans le workspace.
- **Reessais.** Dans python-telegram-bot, `BadRequest` derive de `NetworkError` ;
  rejouer aveuglement les erreurs reseau retardait donc de plusieurs secondes le
  repli sur un envoi definitivement refuse. A l'inverse, un envoi qui echoue
  jusqu'au bout leve, au lieu de perdre le message en silence.

Les deux modules dont une faille casse une conversation de facon definitive —
le respect des invariants du protocole et le rendu Telegram — sont verifies par
tirage aleatoire en plus des tests unitaires.

```bash
make test    # 126 tests, sans acces reseau
make lint
```

## Securite

Hermes execute des commandes arbitraires sur la machine qui l'heberge, au nom
de quiconque figure dans `HERMES_ALLOWED_USERS`. Deux consequences :

1. **La liste blanche n'est pas optionnelle.** Sans elle, le bot refuse de
   demarrer. Un bot Telegram est trouvable par son nom d'utilisateur.
2. **Faites-le tourner dans un conteneur** si la machine sert a autre chose :
   `docker compose up -d --build` monte un volume dedie et rien d'autre.

Le fichier `.env` contient des secrets et n'est jamais versionne. Un token ou
une cle qui a circule en clair — capture d'ecran, message, journal — doit etre
revoque : `/revoke` aupres de @BotFather pour le bot, et rotation de la cle chez
le fournisseur.

## Deploiement

```bash
docker compose up -d --build     # conteneur, volume /data, redemarrage auto
```

ou, en service systeme : `deploy/hermes.service` (durci, redemarrage
automatique, ecriture limitee au workspace).

## Architecture

```
hermes/
  config.py       configuration, validee au demarrage
  providers.py    catalogue fournisseurs + alias de modeles, sonde locale
  llm.py          client OpenAI-compatible, routeur avec bascule
  agent.py        boucle : modele → outils → modele
  history.py      invariants du protocole (le nerf de la stabilite)
  memory.py       SQLite, verrou par chat, proprietaires
  tools/          shell, python, fichiers, web
  formatting.py   Markdown → HTML Telegram, decoupage sur
  bot.py          interface Telegram
  doctor.py       diagnostic
```

Tous les fournisseurs parlent le protocole OpenAI `/chat/completions` : un seul
dialecte, un seul adaptateur, donc une seule source de bugs. Ajouter un
fournisseur, c'est ajouter une entree dans `providers.PROVIDERS`.
