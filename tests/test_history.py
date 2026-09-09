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
