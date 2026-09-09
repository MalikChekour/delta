from __future__ import annotations

import pytest

from hermes.errors import ToolError
from hermes.sandbox import clip, resolve_in
from hermes.tools import Registry, Tool, ToolContext, build_registry

# -- confinement -----------------------------------------------------------


def test_chemin_relatif_accepte(tmp_path):
    assert resolve_in(tmp_path, "a/b.txt") == (tmp_path / "a/b.txt").resolve()


@pytest.mark.parametrize("evasion", ["../secret", "a/../../secret", "/etc/passwd", "~/.ssh/id_rsa"])
def test_evasion_refusee(tmp_path, evasion):
    (tmp_path.parent / "secret").write_text("x")
    with pytest.raises(ToolError):
        resolve_in(tmp_path, evasion)


def test_lien_symbolique_ne_permet_pas_de_sortir(tmp_path):
    dehors = tmp_path.parent / "dehors"
    dehors.mkdir(exist_ok=True)
    (tmp_path / "lien").symlink_to(dehors)
    with pytest.raises(ToolError):
        resolve_in(tmp_path, "lien/cible.txt")


def test_clip_conserve_debut_et_fin():
    out = clip("A" * 100 + "B" * 100, 40)
    assert out.startswith("A") and out.rstrip().endswith("B") and "omis" in out


# -- registre --------------------------------------------------------------


async def test_outil_inconnu(context):
    registry = Registry()
    assert "outil inconnu" in await registry.dispatch(context, "fantome", {})


async def test_argument_manquant(context):
    registry = Registry()
    registry.add(
        Tool(
            "t",
            "Test.",
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            lambda ctx, args: "jamais",
        )
    )
    assert "manquant" in await registry.dispatch(context, "t", {})


async def test_exception_convertie_en_texte(context):
    registry = Registry()

    def casse(ctx, args):
        raise RuntimeError("boum")

    registry.add(Tool("t", "Test.", {"type": "object", "properties": {}}, casse))
    assert "RuntimeError: boum" in await registry.dispatch(context, "t", {})


async def test_handler_synchrone_et_asynchrone(context):
    registry = Registry()

    async def asynchrone(ctx, args):
        return "async"

    registry.add(Tool("a", "A.", {"type": "object", "properties": {}}, asynchrone))
    registry.add(Tool("s", "S.", {"type": "object", "properties": {}}, lambda c, a: "sync"))
    assert await registry.dispatch(context, "a", {}) == "async"
    assert await registry.dispatch(context, "s", {}) == "sync"


def test_registre_refuse_les_doublons():
    registry = Registry()
    outil = Tool("t", "T.", {"type": "object", "properties": {}}, lambda c, a: "")
    registry.add(outil)
    with pytest.raises(ValueError):
        registry.add(outil)


def test_composition_du_registre():
    complet = build_registry()
    assert {"shell", "python", "web_search", "fetch_url", "read_file"} <= set(complet.tools)
    restreint = build_registry(enable_shell=False, enable_web=False)
    assert "shell" not in restreint and "read_file" in restreint


# -- outils fichiers -------------------------------------------------------


async def test_ecriture_lecture_edition(context):
    registry = build_registry(enable_web=False)
    out = await registry.dispatch(context, "write_file", {"path": "a/b.py", "content": "x = 1\n"})
    assert "Ecrit" in out
    assert (context.workspace / "a/b.py").read_text() == "x = 1\n"

    out = await registry.dispatch(context, "read_file", {"path": "a/b.py"})
    assert "x = 1" in out

    out = await registry.dispatch(context, "edit_file", {"path": "a/b.py", "old": "1", "new": "2"})
    assert (context.workspace / "a/b.py").read_text() == "x = 2\n"


async def test_edition_ambigue_refusee(context):
    registry = build_registry(enable_web=False)
    (context.workspace / "d.txt").write_text("aa")
    out = await registry.dispatch(context, "edit_file", {"path": "d.txt", "old": "a", "new": "b"})
    assert "2 fois" in out


async def test_lecture_hors_workspace(context):
    registry = build_registry(enable_web=False)
    out = await registry.dispatch(context, "read_file", {"path": "../../etc/passwd"})
    assert "hors du workspace" in out


async def test_liste_de_fichiers(context):
    (context.workspace / "sous").mkdir()
    (context.workspace / "sous/f.txt").write_text("x")
    registry = build_registry(enable_web=False)
    out = await registry.dispatch(context, "list_files", {"path": "."})
    assert "sous/f.txt" in out


# -- execution -------------------------------------------------------------


async def test_shell(context):
    registry = build_registry(enable_web=False)
    assert "bonjour" in await registry.dispatch(context, "shell", {"command": "echo bonjour"})


async def test_shell_code_de_retour(context):
    registry = build_registry(enable_web=False)
    out = await registry.dispatch(context, "shell", {"command": "exit 3"})
    assert "code de retour 3" in out


async def test_shell_timeout(context):
    registry = build_registry(enable_web=False)
    out = await registry.dispatch(context, "shell", {"command": "sleep 30", "timeout": 1})
    assert "TIMEOUT" in out


async def test_python(context):
    registry = build_registry(enable_web=False)
    out = await registry.dispatch(context, "python", {"code": "print(6 * 7)"})
    assert "42" in out


async def test_sortie_tronquee(context):
    context = ToolContext(**{**context.__dict__, "output_limit": 200})
    registry = build_registry(enable_web=False)
    out = await registry.dispatch(context, "shell", {"command": "seq 1 10000"})
    assert len(out) < 400 and "omis" in out


# -- defauts trouves a la relecture ---------------------------------------


async def test_appels_python_simultanes_ne_se_melangent_pas(context):
    """Le modele emet souvent plusieurs appels dans un meme tour, executes en
    parallele. Avec un fichier temporaire de nom fixe, l'un executait le code
    de l'autre en silence."""
    registry = build_registry(enable_web=False)
    import asyncio

    lent = "import time; time.sleep(0.3); print('AAA')"
    premier, second = await asyncio.gather(
        registry.dispatch(context, "python", {"code": lent}),
        registry.dispatch(context, "python", {"code": "print('BBB')"}),
    )
    assert "AAA" in premier and "BBB" not in premier
    assert "BBB" in second and "AAA" not in second


async def test_le_script_temporaire_est_efface(context):
    registry = build_registry(enable_web=False)
    await registry.dispatch(context, "python", {"code": "print(1)"})
    assert not list(context.workspace.glob(".hermes_*.py"))


async def test_outil_synchrone_ne_bloque_pas_la_boucle(context):
    """Un outil synchrone execute dans la boucle d'evenements gelerait tout le
    bot — les autres conversations comme le polling Telegram."""
    import asyncio

    registry = Registry()

    def lent(ctx, args):
        import time

        time.sleep(0.2)
        return "fini"

    registry.add(Tool("lent", "Lent.", {"type": "object", "properties": {}}, lent))

    battements = 0

    async def coeur():
        nonlocal battements
        while True:
            battements += 1
            await asyncio.sleep(0.005)

    tache = asyncio.create_task(coeur())
    await asyncio.sleep(0.01)
    battements = 0
    assert await registry.dispatch(context, "lent", {}) == "fini"
    tache.cancel()
    assert battements > 3, "la boucle d'evenements est restee bloquee"


async def test_liste_de_fichiers_s_arrete_a_la_limite(context):
    for index in range(700):
        (context.workspace / f"f{index}.txt").write_text("x")
    registry = build_registry(enable_web=False)
    sortie = await registry.dispatch(context, "list_files", {"path": "."})
    assert "tronquee" in sortie
    assert len(sortie.splitlines()) <= 501


async def test_liste_de_fichiers_ignore_les_dossiers_caches(context):
    (context.workspace / ".git").mkdir()
    (context.workspace / ".git/objet").write_text("x")
    (context.workspace / "visible.txt").write_text("x")
    registry = build_registry(enable_web=False)
    sortie = await registry.dispatch(context, "list_files", {"path": "."})
    assert "visible.txt" in sortie and ".git" not in sortie
