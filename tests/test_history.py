"""L'hygiene de l'historique est ce qui separe un bot stable d'un bot qui
tombe en panne au bout de quelques tours : ces cas sont exactement ceux que
les fournisseurs rejettent par un HTTP 400."""

from __future__ import annotations

from hermes.history import MISSING_RESULT, sanitize, trim


def call(id_: str, name: str = "shell") -> dict:
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_appel_sans_reponse_est_complete():
    messages = [
        {"role": "user", "content": "salut"},
        {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
    ]
    out = sanitize(messages)
    assert [m.get("tool_call_id") for m in out[2:]] == ["a", "b"]
    assert out[-1]["content"] == MISSING_RESULT


def test_resultat_orphelin_est_supprime():
    messages = [
        {"role": "user", "content": "salut"},
        {"role": "tool", "tool_call_id": "zombie", "content": "ok"},
        {"role": "assistant", "content": "bonjour"},
    ]
    assert sanitize(messages) == [messages[0], messages[2]]


def test_resultat_en_double_est_supprime():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [call("a")]},
        {"role": "tool", "tool_call_id": "a", "content": "un"},
        {"role": "tool", "tool_call_id": "a", "content": "deux"},
    ]
    out = sanitize(messages)
    assert sum(1 for m in out if m["role"] == "tool") == 1


def test_lot_interrompu_puis_nouveau_tour():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [call("a")]},
        {"role": "user", "content": "en fait non"},
    ]
    out = sanitize(messages)
    assert out[1]["role"] == "tool" and out[1]["tool_call_id"] == "a"
    assert out[2]["role"] == "user"


def test_sanitize_ne_modifie_pas_l_entree():
    messages = [{"role": "assistant", "content": "", "tool_calls": [call("a")]}]
    copie = [dict(m) for m in messages]
    sanitize(messages)
    assert messages == copie


def test_trim_recule_jusqu_a_un_message_utilisateur():
    messages = [
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "", "tool_calls": [call("a")]},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
        {"role": "assistant", "content": "fini"},
        {"role": "user", "content": "2"},
        {"role": "assistant", "content": "ok"},
    ]
    out = trim(messages, 3)
    assert out[0]["role"] == "user" and out[0]["content"] == "2"


def test_trim_garde_tout_si_la_fenetre_suffit():
    messages = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    assert trim(messages, 10) == messages


def test_trim_sans_message_utilisateur_recent():
    messages = [{"role": "assistant", "content": f"m{i}"} for i in range(5)]
    assert trim(messages, 2) == []


def test_identifiants_reutilises_d_un_tour_a_l_autre():
    """vLLM, Ollama et llama.cpp numerotent les appels a partir de zero dans
    chaque message : `call_0` reapparait a chaque tour. Un dedoublonnage
    portant sur tout l'historique remplacait alors le resultat legitime du
    second tour par un resultat de substitution — l'agent perdait ses propres
    observations sans que rien ne le signale."""
    messages = [
        {"role": "user", "content": "tour 1"},
        {"role": "assistant", "content": "", "tool_calls": [call("call_0")]},
        {"role": "tool", "tool_call_id": "call_0", "content": "RESULTAT 1"},
        {"role": "assistant", "content": "fini 1"},
        {"role": "user", "content": "tour 2"},
        {"role": "assistant", "content": "", "tool_calls": [call("call_0")]},
        {"role": "tool", "tool_call_id": "call_0", "content": "RESULTAT 2"},
    ]
    out = sanitize(messages)
    contenus = [m["content"] for m in out if m["role"] == "tool"]
    assert contenus == ["RESULTAT 1", "RESULTAT 2"]
    assert MISSING_RESULT not in contenus


def test_sanitize_est_idempotent():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [call("c0"), call("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
        {"role": "user", "content": "suite"},
        {"role": "tool", "tool_call_id": "c0", "content": "orphelin"},
    ]
    une = sanitize(messages)
    assert sanitize(une) == une


def test_ordre_des_resultats_preserve():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b"), call("c")]},
        {"role": "tool", "tool_call_id": "c", "content": "trois"},
        {"role": "tool", "tool_call_id": "a", "content": "un"},
    ]
    out = sanitize(messages)
    assert [m["content"] for m in out[1:]] == ["trois", "un", MISSING_RESULT]
