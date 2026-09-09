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
    """
    out: list[Message] = []
    for message in messages:
        role = message.get("role")

        if role == "tool":
            call_id = message.get("tool_call_id")
            # Le message precedent doit etre l'assistant qui a demande cet appel,
            # ou un autre resultat du meme lot.
            expected: list[str] = []
            for previous in reversed(out):
                if previous.get("role") == "assistant":
                    expected = _tool_call_ids(previous)
                    break
                if previous.get("role") != "tool":
                    break
            if not call_id or call_id not in expected:
                continue  # orphelin : le fournisseur le rejetterait
            if any(m.get("role") == "tool" and m.get("tool_call_id") == call_id for m in out):
                continue  # doublon
            out.append(message)
            continue

        # Nouveau message non-tool : le lot d'appels precedent doit etre complet.
        _close_pending(out)
        out.append(message)

    _close_pending(out)
    return out


def _close_pending(out: list[Message]) -> None:
    """Complete le dernier lot d'appels d'outils s'il est incomplet."""
    index = len(out) - 1
    while index >= 0 and out[index].get("role") == "tool":
        index -= 1
    if index < 0 or out[index].get("role") != "assistant":
        return
    expected = _tool_call_ids(out[index])
    if not expected:
        return
    answered = {m.get("tool_call_id") for m in out[index + 1 :]}
    for call_id in expected:
        if call_id not in answered:
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
