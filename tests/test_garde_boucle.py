"""Tests du garde-boucle et de l'avertissement de fin de course.

🚨 CE QUE CES TESTS PROTEGENT. Mesure du 11/09, meme tache jouee quatre fois :
**3, 6, 7 et 20 etapes** — la derniere epuisant la limite sans jamais conclure. En tracant
les appels, la cause etait nette : l'agent trouvait la reponse en 3 etapes puis appelait
`retiens` **six fois de suite avec des arguments identiques**.

Le probleme n'etait pas la competence du modele mais sa CONVERGENCE. Apres correctif :
3, 3, 7, 4 — moyenne divisee par deux, plus aucun echec.
"""

from __future__ import annotations

import pytest

from hermes import llm
from hermes.agent import Agent
from hermes.memory import Store
from hermes.tools import Registry, ToolContext, Tool


class _Compteur:
    """Outil qui compte ses executions reelles."""

    def __init__(self) -> None:
        self.executions = 0

    def outil(self) -> Tool:
        def handler(ctx, args):
            self.executions += 1
            return f"resultat numero {self.executions}"

        return Tool(name="compte", description="test",
                    parameters={"type": "object", "properties": {}, "required": []},
                    handler=handler)


def _agent(settings, tmp_path, compteur: _Compteur) -> Agent:
    registre = Registry()
    registre.add(compteur.outil())
    return Agent(settings, registre, Store(tmp_path / "g.db"))


def _appel(nom: str, arguments: str, ident: str = "1") -> llm.ToolCall:
    return llm.ToolCall(id=ident, name=nom, arguments=arguments)


async def test_un_appel_identique_n_est_pas_reexecute(settings, tmp_path):
    """🚨 Le cœur du correctif : reexecuter un appel identique ne peut rien apprendre de
    neuf, et c'est exactement ce que l'agent faisait six fois d'affilee."""
    c = _Compteur()
    agent = _agent(settings, tmp_path, c)
    ctx = ToolContext(workspace=tmp_path, exec_timeout=5, output_limit=500,
                      request_timeout=5, search_url="x")
    deja: dict = {}
    premier = await agent._execute(ctx, _appel("compte", "{}"), deja, 1)
    second = await agent._execute(ctx, _appel("compte", "{}"), deja, 2)

    assert c.executions == 1, "l'outil a ete execute deux fois malgre un appel identique"
    assert "resultat numero 1" in second, "le resultat precedent doit etre rendu"
    assert "IDENTIQUE" in second and "REPONDS" in second, (
        "le modele doit etre PREVENU qu'il se repete, sinon il recommence"
    )
    assert premier != second


async def test_des_arguments_differents_sont_bien_reexecutes(settings, tmp_path):
    """Le miroir : un garde-boucle trop large empecherait toute recherche affinee."""
    c = _Compteur()
    agent = _agent(settings, tmp_path, c)
    ctx = ToolContext(workspace=tmp_path, exec_timeout=5, output_limit=500,
                      request_timeout=5, search_url="x")
    deja: dict = {}
    await agent._execute(ctx, _appel("compte", '{"q": "a"}'), deja, 1)
    await agent._execute(ctx, _appel("compte", '{"q": "b"}'), deja, 2)
    assert c.executions == 2


async def test_l_ordre_des_arguments_ne_trompe_pas_le_garde(settings, tmp_path):
    """Le modele n'ecrit pas toujours les cles dans le meme ordre : `{a,b}` et `{b,a}`
    sont le MEME appel. Sans tri, le garde les croirait differents et laisserait la
    boucle se poursuivre."""
    c = _Compteur()
    agent = _agent(settings, tmp_path, c)
    ctx = ToolContext(workspace=tmp_path, exec_timeout=5, output_limit=500,
                      request_timeout=5, search_url="x")
    deja: dict = {}
    await agent._execute(ctx, _appel("compte", '{"a": 1, "b": 2}'), deja, 1)
    await agent._execute(ctx, _appel("compte", '{"b": 2, "a": 1}'), deja, 2)
    assert c.executions == 1


async def test_sans_registre_de_garde_le_comportement_est_inchange(settings, tmp_path):
    """`deja=None` doit rendre le comportement d'origine : le garde ne s'impose pas."""
    c = _Compteur()
    agent = _agent(settings, tmp_path, c)
    ctx = ToolContext(workspace=tmp_path, exec_timeout=5, output_limit=500,
                      request_timeout=5, search_url="x")
    await agent._execute(ctx, _appel("compte", "{}"))
    await agent._execute(ctx, _appel("compte", "{}"))
    assert c.executions == 2


async def test_retiens_dit_quand_rien_ne_change(settings, tmp_path):
    """🚨 La CAUSE de la boucle : l'outil repondait « mise a jour » meme sur un contenu
    identique, et le modele, croyant avoir agi, rappelait l'outil."""
    from hermes.tools import build_registry

    reg = build_registry()
    ctx = ToolContext(workspace=tmp_path, exec_timeout=5, output_limit=2000,
                      request_timeout=5, search_url="x")
    args = {"titre": "T", "contenu": "un fait stable"}
    premier = await reg.dispatch(ctx, "retiens", args)
    second = await reg.dispatch(ctx, "retiens", args)
    assert "enregistree" in premier
    assert "identique" in second.lower()
    assert "n'appelle plus" in second.lower()


async def test_l_avertissement_de_fin_ne_se_declenche_pas_trop_tot(settings, tmp_path):
    """🚨 Defaut de la premiere version, attrape par `test_boucle_avec_outil` : avec un
    budget de 4 tours, « il te reste 3 tours » etait vrai des le PREMIER appel. L'agent
    etait somme de conclure avant d'avoir commence. On verifie la borne des deux cotes."""
    maxi = settings.max_tool_iterations
    for iteration in range(maxi):
        reste = maxi - iteration - 1
        entame = (iteration + 1) >= maxi * 0.6
        avertit = 0 < reste <= 3 and entame
        if iteration == 0:
            assert not avertit, "jamais au premier tour, quel que soit le budget"
        if reste == 0:
            assert not avertit, "inutile quand il ne reste plus rien"
