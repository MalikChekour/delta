# Bancs de mesure

Deux instruments, pour que « l'agent est-il meilleur ? » se réponde par un chiffre.

| | Quoi | Durée |
|---|---|---|
| `python bench/banc.py` | 8 tâches — 4 de raisonnement, 4 de codage | ~6 min |
| `python bench/resistance.py` | 17 contrôles de robustesse, sans réseau ni LLM | ~30 s |

🚨 **Les tâches de codage sont jugées en EXÉCUTANT le code produit**, jamais en lisant la
réponse : un agent décrit très bien une solution juste et livre un fichier qui ne tourne pas.

🚨 **Lancer `banc.py` ne perturbe pas le service** : il passe par `hermes chat`, un REPL local
qui n'ouvre aucun poller Telegram — donc pas de conflit 409 avec la tâche planifiée.

⚠️ `banc.py` **vide `workspace/*.py`** à chaque tâche. N'y laissez rien à conserver.

## Comment s'en servir

Avant de toucher au prompt système ou à un réglage : lancer, noter. Changer **une** chose.
Relancer. Si le chiffre ne bouge pas, la modification n'en était pas une.

⚠️ **Ne taillez pas le prompt sur ce banc.** Huit tâches ne représentent pas le travail réel ;
à force d'ajuster pour elles, on optimise la mesure et non l'agent. Les règles ajoutées au
prompt doivent rester des principes généraux.

## Repères mesurés

| Date | Résultat |
|---|---|
| 11/09, avant le garde-boucle | 5/6, 407 s, une tâche à 20 étapes sans conclure |
| 11/09, après | 6/6, 222 s |
| 12/09, banc à 8 tâches | **8/8, 379 s** — raisonnement 3,5 étapes, codage 10,0 |

⚠️ **Une seule exécution ne prouve pas un taux.** Mesuré sur plusieurs passages, le codage
tourne autour de 4,4/5 : une tâche **différente** tombe à chaque fois. C'est de l'inconstance,
pas une lacune identifiable.
