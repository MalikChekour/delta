# -*- coding: utf-8 -*-
"""Banc de RESISTANCE : ce qui se passe quand les choses vont mal.

🚨 Un agent qui repond bien aux bonnes questions n'est pas un agent solide. On injecte donc
des pannes et on verifie qu'aucune ne fait tomber la boucle, ne fait fuir un secret, ni ne
laisse l'historique dans un etat incoherent.

Chaque controle porte sur une panne DEJA RENCONTREE ou deja tentee, pas sur une hypothese.
Usage : python bench/resistance.py
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import hermes.config as C                      # noqa: E402
from hermes.agent import Agent                 # noqa: E402
from hermes.memory import Store                # noqa: E402
from hermes.tools import (Registry, Tool, ToolContext,  # noqa: E402
                          build_registry, caviarde)

REUSSIS: list[tuple[str, bool, str]] = []


def note(nom: str, ok: bool, detail: str = "") -> None:
    REUSSIS.append((nom, ok, detail))
    print("  %s %-34s %s" % ("[OK]" if ok else "[KO]", nom, detail[:64]), flush=True)


async def principal() -> int:
    s = C.load(require_telegram=False)
    reg = build_registry()
    atelier = Path(tempfile.mkdtemp(prefix="resist_"))
    ctx = ToolContext(workspace=atelier, exec_timeout=30, output_limit=4000,
                      request_timeout=20, search_url="https://html.duckduckgo.com/html/")

    print("\n═══ 1. PANNES D'OUTILS ═══")

    def boum(_c, _a):
        raise RuntimeError("panne simulee")

    r = Registry()
    r.add(Tool("boum", "t", {"type": "object", "properties": {}, "required": []}, boum))
    note("un outil qui leve", "ERREUR" in await r.dispatch(ctx, "boum", {}))
    note("un outil inconnu", "inconnu" in await r.dispatch(ctx, "inexistant", {}))
    sortie = await reg.dispatch(ctx, "read_file", {"path": "../../../windows/win.ini"})
    note("evasion du workspace refusee", "hors du workspace" in sortie)
    note("regex invalide", "ERREUR" in await reg.dispatch(ctx, "code_search",
                                                          {"pattern": "[[[bancal"})
         or "regex" in (await reg.dispatch(ctx, "code_search", {"pattern": "[[[bancal"})).lower())
    note("diff bidon refuse", "diff unifie" in await reg.dispatch(ctx, "apply_patch",
                                                                 {"diff": "pas un diff"}))
    note("URL morte", "ERREUR" in await reg.dispatch(
        ctx, "fetch_url", {"url": "https://ceci-nexiste-pas-42.invalid"}))
    note("code de retour non nul rapporte",
         "retour" in await reg.dispatch(ctx, "shell", {"command": "exit 3"}))
    note("accents dans un script",
         "éàü" in await reg.dispatch(ctx, "python", {"code": "print('éàü ✅')"}))

    print("\n═══ 2. SECRETS ═══")
    faux = "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"
    os.environ["BANC_RESISTANCE_API_KEY"] = faux
    from hermes.tools import oublie_les_secrets
    oublie_les_secrets()
    note("valeur entiere caviardee", faux not in caviarde(f"cle={faux}"))
    note("fragment caviarde", faux[:20] not in caviarde(f"debut={faux[:20]}"))
    note("base64 caviarde",
         "SECRET RETIRE" in caviarde(base64.b64encode(faux.encode()).decode()))
    note("texte anodin intact", caviarde("133 mg de vitamine C") == "133 mg de vitamine C")
    (atelier / "f.txt").write_text(f"K={faux}\n", encoding="utf-8")
    note("sortie d'outil caviardee",
         faux not in await reg.dispatch(ctx, "read_file", {"path": "f.txt"}))
    note("meme par le shell, non confine",
         faux not in await reg.dispatch(ctx, "python", {"code": f"print({faux!r})"}))
    del os.environ["BANC_RESISTANCE_API_KEY"]
    oublie_les_secrets()

    print("\n═══ 3. BOUCLE DE L'AGENT ═══")
    compte = {"n": 0}

    def compteur(_c, _a):
        compte["n"] += 1
        return "resultat"

    r2 = Registry()
    r2.add(Tool("compte", "t", {"type": "object", "properties": {}, "required": []}, compteur))
    a = Agent(s, r2, Store(atelier / "b.db"))
    from hermes import llm
    deja: dict = {}
    appel = llm.ToolCall(id="1", name="compte", arguments="{}")
    await a._execute(ctx, appel, deja, 1)
    second = await a._execute(ctx, appel, deja, 2)
    note("appel identique non reexecute", compte["n"] == 1 and "IDENTIQUE" in second)

    a2 = Agent(s, build_registry(), Store(atelier / "c.db"))
    r1, r2b = await asyncio.gather(a2.respond(991, "Dis: UN"), a2.respond(991, "Dis: DEUX"))
    session = await a2.store.load(991)
    roles = [m.get("role") for m in session.messages]
    note("deux messages simultanes", bool(r1.text) and bool(r2b.text)
         and all(x in ("user", "assistant", "tool", "system") for x in roles),
         "%d messages, roles valides" % len(roles))

    print("\n═══ 4. MODELES ═══")
    a3 = Agent(replace(s, model_chain=("glm-4.7-heretic",)), build_registry(),
               Store(atelier / "d.db"))
    rep = await a3.respond(992, "Dis simplement : bonjour")
    note("bascule sur route joignable", bool(rep.text) and bool(rep.route), rep.route[:44])

    ok = sum(1 for _, c, _ in REUSSIS if c)
    print("\n=== RESISTANCE : %d/%d ===" % (ok, len(REUSSIS)))
    return 0 if ok == len(REUSSIS) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
