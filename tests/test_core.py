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
