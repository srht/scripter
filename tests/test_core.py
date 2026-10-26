import json
import tempfile
from pathlib import Path

from scriptforge import core


class FakeLLM:
    """First answer is buggy, second answer is the fix."""

    def __init__(self):
        self.calls = 0

    def ask(self, text):
        self.calls += 1
        if self.calls == 1:
            return "```python\nprint(undefined_name)\n```"
        return "Fixed:\n```python\nprint('hello 42')\n```"


def test_extract_and_requirements():
    code = core.extract_code("x\n```python\n# requirements: requests, rich>=13\nprint(1)\n```\ny")
    assert core.parse_requirements(code) == ["requests", "rich>=13"]
    assert core.parse_requirements("# requirements: --index-url http://evil") == []


def test_debug_loop_fixes_script():
    with tempfile.TemporaryDirectory() as d:
        logs = []
        llm = FakeLLM()
        out = core.forge("print hello", llm, logs.append, workspace=Path(d), timeout=10)
        assert out.result.ok and "hello 42" in out.result.stdout
        assert out.attempts == 2 and llm.calls == 2
        assert out.path.exists()


def test_gives_up_after_max_attempts():
    class Bad:
        def ask(self, t):
            return "```python\nraise SystemExit(3)\n```"

    with tempfile.TemporaryDirectory() as d:
        out = core.forge("x", Bad(), lambda m: None, workspace=Path(d), max_attempts=2, timeout=10)
        assert not out.result.ok and out.attempts == 2


def test_gemini_client_parses_and_keeps_history(monkeypatch):
    import io, json, urllib.request

    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["key"] = req.full_url, req.headers.get("X-goog-api-key")
        seen["body"] = json.loads(req.data)
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "```python\nprint(1)\n```"}]}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    llm = core.LLM("KEY", "gemini-x")
    assert "print(1)" in llm.ask("hi")
    assert "gemini-x:generateContent" in seen["url"] and seen["key"] == "KEY"
    assert [c["role"] for c in llm.contents] == ["user", "model"]


def test_openrouter_client_and_free_filter(monkeypatch):
    import io, json, urllib.request

    def fake_urlopen(req, timeout=0):
        if isinstance(req, str):
            return io.BytesIO(json.dumps({"data": [
                {"id": "a/b:free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "c/d", "pricing": {"prompt": "0.001", "completion": "0.002"}},
                {"id": "e/f", "pricing": {"prompt": "0", "completion": "0"}},
            ]}).encode())
        assert req.headers["Authorization"] == "Bearer K"
        body = json.loads(req.data)
        assert body["model"] == "a/b:free" and body["messages"][0]["role"] == "system"
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "```python\nprint(2)\n```"}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert core.list_openrouter_models(True) == ["a/b:free", "e/f"]
    assert len(core.list_openrouter_models(False)) == 3
    llm = core.make_llm("OpenRouter", "K", "a/b:free")
    assert "print(2)" in llm.ask("x") and llm.messages[-1]["role"] == "assistant"


def test_library_lists_runs_and_deletes(tmp_path):
    out = core.forge("print hello", FakeLLM(), lambda m: None, workspace=tmp_path, timeout=10)
    # legacy dir without meta.json must still be listed
    legacy = tmp_path / "scripts" / "19990101-000000"
    legacy.mkdir()
    (legacy / "script.py").write_text("print('legacy')\n")

    entries = core.list_library(tmp_path)
    assert len(entries) == 2
    mine = next(e for e in entries if e.dir == out.path.parent)
    assert mine.request == "print hello" and mine.ok is True and mine.attempts == 2

    logs = []
    res = core.run_entry(mine, logs.append, timeout=10, workspace=tmp_path)
    assert res.ok and "hello 42" in "\n".join(logs)

    core.delete_entry(mine, tmp_path)
    assert [e.dir for e in core.list_library(tmp_path)] == [legacy]


def test_delete_refuses_outside_library(tmp_path):
    import pytest

    (tmp_path / "scripts").mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    e = core.LibraryEntry(victim, "x", "2020", None)
    with pytest.raises(ValueError):
        core.delete_entry(e, tmp_path)
    assert victim.exists()


def _script(tmp_path, body):
    p = tmp_path / "s.py"
    p.write_text(body)
    return p


def test_input_fails_fast_when_not_interactive(tmp_path):
    p = _script(tmp_path, "print(input('adın? '))\n")
    r = core.run_script(p, tmp_path / "libs", timeout=10)
    assert not r.ok and "EOFError" in r.stderr


def test_interactive_input_and_live_output(tmp_path):
    import threading, time

    p = _script(tmp_path, "name = input('adın? ')\nprint('merhaba', name)\n")
    holder, chunks = [], []

    def feeder():
        while not holder:
            time.sleep(0.02)
        while "adın?" not in "".join(t for _, t in chunks):  # wait for the prompt (no newline!)
            time.sleep(0.02)
        holder[0].stdin.write(b"Ali\n")
        holder[0].stdin.flush()

    threading.Thread(target=feeder, daemon=True).start()
    r = core.run_script(p, tmp_path / "libs", 10, holder, lambda s, t: chunks.append((s, t)), interactive=True)
    assert r.ok and "merhaba Ali" in r.stdout
    assert "adın?" in "".join(t for _, t in chunks)


def test_timeout_still_kills(tmp_path):
    p = _script(tmp_path, "import time\ntime.sleep(30)\n")
    r = core.run_script(p, tmp_path / "libs", timeout=1)
    assert r.timed_out and not r.ok


def test_manual_edit_history_and_restore(tmp_path):
    out = core.forge("print hello", FakeLLM(), lambda m: None, workspace=tmp_path, timeout=10)
    e = core.list_library(tmp_path)[0]
    core.save_code(e, "print('v2')")
    assert e.code == "print('v2')\n"
    assert len(list((e.dir / "history").glob("*.py"))) == 1
    core.save_code(e, "print('v2')\n")  # unchanged -> no new backup
    assert len(list((e.dir / "history").glob("*.py"))) == 1
    assert core.restore_previous(e) and "hello 42" in e.code
    assert core.restore_previous(e) and "v2" in e.code  # toggles back


class EditLLM:
    def __init__(self):
        self.prompts = []

    def ask(self, text):
        self.prompts.append(text)
        return "```python\nprint('edited')\n```"


def test_llm_edit_in_place_keeps_original_request(tmp_path):
    core.forge("print hello", FakeLLM(), lambda m: None, workspace=tmp_path, timeout=10)
    e = core.list_library(tmp_path)[0]
    llm = EditLLM()
    out = core.forge("make it say edited", llm, lambda m: None, workspace=tmp_path, timeout=10, base=e)
    assert out.result.ok and out.path == e.script
    assert "hello 42" in llm.prompts[0] and "make it say edited" in llm.prompts[0]  # current code sent to LLM
    assert "edited" in e.code
    assert len(core.list_library(tmp_path)) == 1  # edited in place, no new entry
    again = core.list_library(tmp_path)[0]
    assert again.request == "print hello" and again.ok is True
    meta = json.loads((e.dir / "meta.json").read_text())
    assert meta["edits"] == ["make it say edited"]
    assert any("hello 42" in f.read_text() for f in (e.dir / "history").glob("*.py"))
