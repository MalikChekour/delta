"""Hygiene de l'historique de conversation.

Le protocole ``/chat/completions`` impose deux invariants que tout le monde
oublie, et dont la violation produit un HTTP 400 opaque qui casse une
conversation *definitivement* (l'historique fautif est relu a chaque tour) :

1. un message ``assistant`` porteur de ``tool_calls`` doit etre suivi d'un
   message ``tool`` pour *chacun* des identifiants annonces ;
2. un message ``tool`` doit repondre a un ``tool_calls`` qui le precede.

Une troncature naive, une annulation en plein tour ou une erreur reseau entre
l'appel et le resultat suffisent a les rompre. ``sanitize`` retablit les deux
avant toute ecriture en base et avant tout envoi au fournisseur, ce qui rend la
faute impossible a persister.
"""

from __future__ import annotations

from typing import Any

Message = dict[str, Any]

#: Contenu injecte pour un appel d'outil reste sans reponse.
MISSING_RESULT = "[resultat manquant : le tour a ete interrompu]"


def _tool_call_ids(message: Message) -> list[str]:
    calls = message.get("tool_calls") or []
    return [c["id"] for c in calls if isinstance(c, dict) and c.get("id")]


def sanitize(messages: list[Message]) -> list[Message]:
    """Renvoie un historique conforme aux deux invariants ci-dessus.

    Ne modifie jamais la liste d'entree. Les messages ``tool`` orphelins sont
    supprimes ; les appels sans reponse recoivent un resultat de substitution,
    de sorte que la conversation reste exploitable au lieu d'etre perdue.

    Le raisonnement se fait *par lot* — un message assistant et les resultats
    qui le suivent. C'est essentiel : beaucoup de fournisseurs (vLLM, Ollama,
    llama.cpp) numerotent les appels a partir de zero dans chaque message, si
    bien que ``call_0`` reapparait a chaque tour. Chercher un doublon dans tout
    l'historique ferait passer pour un doublon le resultat legitime du tour
    suivant, qui serait alors remplace par un resultat de substitution :
    l'agent perdrait ses propres observations sans que rien ne le signale.
    """
    out: list[Message] = []
    attendus: list[str] = []  # ids annonces par le message assistant courant
    obtenus: set[str] = set()  # ids deja repondus dans ce lot

    for message in messages:
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not call_id or call_id not in attendus or call_id in obtenus:
                continue  # orphelin ou doublon : le fournisseur le rejetterait
            obtenus.add(call_id)
            out.append(message)
            continue

        # Nouveau message non-tool : le lot precedent doit d'abord etre complet.
        _close(out, attendus, obtenus)
        out.append(message)
        attendus = _tool_call_ids(message) if message.get("role") == "assistant" else []
        obtenus = set()

    _close(out, attendus, obtenus)
    return out


def _close(out: list[Message], attendus: list[str], obtenus: set[str]) -> None:
    """Complete un lot d'appels d'outils reste incomplet."""
    for call_id in attendus:
        if call_id not in obtenus:
            out.append({"role": "tool", "tool_call_id": call_id, "content": MISSING_RESULT})


def trim(messages: list[Message], keep: int) -> list[Message]:
    """Ne garde que les ``keep`` derniers messages, en coupant proprement.

    La coupe recule jusqu'au premier message ``user`` : commencer un historique
    par une reponse d'outil ou par un tour d'assistant desoriente les modeles et
    fait echouer certains fournisseurs.
    """
    if keep <= 0 or len(messages) <= keep:
        return sanitize(messages)

    cut = len(messages) - keep
    while cut < len(messages) and messages[cut].get("role") != "user":
        cut += 1
    if cut >= len(messages):
        # Aucun point de reprise propre dans la fenetre : on repart du dernier
        # message utilisateur connu, ou de rien.
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return sanitize(messages[index:])
        return []
    return sanitize(messages[cut:])
