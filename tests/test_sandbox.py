from pathlib import Path

import pytest

from hermes import sandbox


async def test_run_shell_captures_output(tmp_path: Path):
    result = await sandbox.run_shell("echo bonjour", cwd=tmp_path, timeout=10)
    assert result.returncode == 0
    assert "bonjour" in result.stdout
    assert not result.timed_out


async def test_run_shell_reports_failure(tmp_path: Path):
    result = await sandbox.run_shell("exit 3", cwd=tmp_path, timeout=10)
    assert result.returncode == 3


async def test_timeout_kills_process(tmp_path: Path):
    result = await sandbox.run_shell("sleep 30", cwd=tmp_path, timeout=1)
    assert result.timed_out
    assert "TIMEOUT" in result.render(1000)


async def test_python_runs_in_workspace(tmp_path: Path):
    result = await sandbox.run_python("import os; print(os.getcwd())", cwd=tmp_path, timeout=10)
    assert str(tmp_path.resolve()) in result.stdout


async def test_secrets_are_stripped_from_child_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:abcd")
    monkeypatch.setenv("HARMLESS_VAR", "visible")
    result = await sandbox.run_shell("env", cwd=tmp_path, timeout=10)
    assert "sk-secret" not in result.stdout
    assert "1234:abcd" not in result.stdout
    assert "visible" in result.stdout


def test_resolve_in_blocks_escapes(tmp_path: Path):
    (tmp_path / "inside.txt").write_text("ok")
    assert sandbox.resolve_in(tmp_path, "inside.txt").name == "inside.txt"
    for evil in ("../outside.txt", "/etc/passwd", "a/../../../etc/shadow"):
        with pytest.raises(ValueError):
            sandbox.resolve_in(tmp_path, evil)


def test_clip_keeps_head_and_tail():
    text = "A" * 500 + "B" * 500
    clipped = sandbox._clip(text, 100)
    assert clipped.startswith("A")
    assert clipped.rstrip().endswith("B")
    assert "coupes" in clipped
