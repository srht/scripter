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
