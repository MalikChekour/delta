import pytest

from hermes.tools import ToolContext, build_registry


@pytest.fixture
def ctx(settings):
    return ToolContext(settings=settings, chat_id=1)


@pytest.fixture
def registry(settings):
    return build_registry(settings)


async def test_registry_exposes_expected_tools(registry):
    assert {"shell", "python", "read_file", "write_file", "edit_file", "list_dir",
            "web_search", "web_fetch"} <= set(registry.names())


async def test_write_then_read(registry, ctx):
    await registry.dispatch(ctx, "write_file", {"path": "a/b.txt", "content": "ligne1\nligne2"})
    out = await registry.dispatch(ctx, "read_file", {"path": "a/b.txt"})
    assert "ligne1" in out and "ligne2" in out
    assert "1 |" in out  # numerotation des lignes


async def test_edit_refuses_ambiguous_match(registry, ctx):
    await registry.dispatch(ctx, "write_file", {"path": "c.txt", "content": "x\nx\n"})
    out = await registry.dispatch(
        ctx, "edit_file", {"path": "c.txt", "old_string": "x", "new_string": "y"}
    )
    assert "2 fois" in out


async def test_edit_replace_all(registry, ctx):
    await registry.dispatch(ctx, "write_file", {"path": "d.txt", "content": "x\nx\n"})
    await registry.dispatch(
        ctx,
        "edit_file",
        {"path": "d.txt", "old_string": "x", "new_string": "y", "replace_all": True},
    )
    out = await registry.dispatch(ctx, "read_file", {"path": "d.txt"})
    assert "x" not in out.replace("d.txt", "")


async def test_path_escape_is_rejected(registry, ctx):
    out = await registry.dispatch(ctx, "read_file", {"path": "../../etc/passwd"})
    assert "ERREUR" in out and "workspace" in out


async def test_shell_runs_in_workspace(registry, ctx):
    await registry.dispatch(ctx, "write_file", {"path": "marker.txt", "content": "hi"})
    out = await registry.dispatch(ctx, "shell", {"command": "ls"})
    assert "marker.txt" in out


async def test_unknown_tool_is_reported_not_raised(registry, ctx):
    out = await registry.dispatch(ctx, "nope", {})
    assert "inconnu" in out


async def test_handler_exception_becomes_a_message(registry, ctx):
    out = await registry.dispatch(ctx, "read_file", {})  # KeyError 'path'
    assert out.startswith("ERREUR pendant read_file")
