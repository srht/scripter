"""LLM -> script -> run -> auto-debug loop. No GUI imports here."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PROVIDERS = {
    "Gemini": {"default_model": "gemini-2.5-flash", "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY")},
    "OpenRouter": {"default_model": "openrouter/free", "env": ("OPENROUTER_API_KEY",)},
}
DEFAULT_MODEL = PROVIDERS["Gemini"]["default_model"]
WORKSPACE = Path(os.environ.get("SCRIPTFORGE_HOME", Path.home() / "ScriptForge"))

SYSTEM_PROMPT = """You write single-file Python 3 scripts that do exactly what the user asks.
Rules:
- Reply with ONE fenced ```python code block and nothing else of substance.
- The script must run non-interactively (no input(), no GUI that blocks forever unless the user asked for one).
- Print meaningful results to stdout.
- Prefer the standard library. If third-party packages are required, put a first line
  `# requirements: pkg1, pkg2` (pip names) at the very top of the script.
- When given a failing script and its error output, return the complete corrected script, not a diff."""

CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)
REQ_RE = re.compile(r"^#\s*requirements:\s*(.+)$", re.M | re.I)


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass
class Outcome:
    code: str
    result: Optional[RunResult]
    attempts: int
    path: Optional[Path] = None
    cancelled: bool = False


def extract_code(text: str) -> str:
    blocks = CODE_RE.findall(text)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return text.strip() + "\n"  # model replied with bare code


def parse_requirements(code: str) -> list[str]:
    m = REQ_RE.search(code)
    if not m:
        return []
    pkgs = [p.strip() for p in m.group(1).split(",")]
    # only plain pip specs; refuse anything that looks like an option/URL
    return [p for p in pkgs if re.fullmatch(r"[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?([=<>!~]=?[A-Za-z0-9_.*]+)?", p)]


class LLM:
    """Gemini API (REST) client; conversation is kept so fixes have context."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model
        self.contents: list[dict] = []

    def ask(self, user_text: str) -> str:
        import json
        import urllib.error
        import urllib.request

        self.contents.append({"role": "user", "parts": [{"text": user_text}]})
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": self.contents,
            "generationConfig": {"maxOutputTokens": 8000},
        }).encode()
        req = urllib.request.Request(
            self.URL.format(model=self.model), data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            self.contents.pop()
            raise RuntimeError(f"Gemini API {e.code}: {e.read().decode(errors='replace')[:500]}") from None
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            self.contents.pop()
            raise RuntimeError(f"Gemini boş/engellenmiş yanıt döndü: {str(data)[:500]}") from None
        self.contents.append({"role": "model", "parts": [{"text": text}]})
        return text


class OpenRouterLLM:
    """OpenRouter (OpenAI-compatible chat completions); conversation is kept for fix rounds."""

    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, model: str = "openrouter/free"):
        self.api_key = api_key
        self.model = model
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def ask(self, user_text: str) -> str:
        import json
        import urllib.error
        import urllib.request

        self.messages.append({"role": "user", "content": user_text})
        req = urllib.request.Request(
            self.URL,
            data=json.dumps({"model": self.model, "messages": self.messages, "max_tokens": 8000}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "X-Title": "ScriptForge",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            self.messages.pop()
            raise RuntimeError(f"OpenRouter {e.code}: {e.read().decode(errors='replace')[:500]}") from None
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            self.messages.pop()
            raise RuntimeError(f"OpenRouter hatalı yanıt: {str(data)[:500]}") from None
        self.messages.append({"role": "assistant", "content": text})
        return text


def list_openrouter_models(free_only: bool = True, timeout: int = 30) -> list[str]:
    """Public endpoint, no key needed. Free = ':free' suffix or zero prompt+completion price."""
    import json
    import urllib.request

    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=timeout) as r:
        models = json.load(r)["data"]

    def is_free(m):
        pr = m.get("pricing") or {}
        return m["id"].endswith(":free") or (str(pr.get("prompt")) == "0" and str(pr.get("completion")) == "0")

    ids = sorted(m["id"] for m in models if not free_only or is_free(m))
    return ids


def make_llm(provider: str, api_key: str, model: str):
    if provider == "OpenRouter":
        return OpenRouterLLM(api_key, model)
    return LLM(api_key, model)


def env_key(provider: str) -> str:
    for name in PROVIDERS[provider]["env"]:
        if os.environ.get(name):
            return os.environ[name]
    return ""


def install_requirements(pkgs: list[str], libs: Path, log: Callable[[str], None]) -> None:
    if not pkgs:
        return
    libs.mkdir(parents=True, exist_ok=True)
    log(f"pip install -> {', '.join(pkgs)}")
    p = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(libs), *pkgs],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        log("pip failed:\n" + p.stderr[-1500:])


def run_script(path: Path, libs: Path, timeout: int, proc_holder: Optional[list] = None) -> RunResult:
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(libs), env.get("PYTHONPATH", "")]))
    proc = subprocess.Popen(
        [sys.executable, str(path)], cwd=path.parent, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc_holder is not None:
        proc_holder[:] = [proc]
    try:
        out, err = proc.communicate(timeout=timeout)
        return RunResult(proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return RunResult(-1, out or "", (err or "") + f"\n[timeout after {timeout}s]", timed_out=True)


def forge(
    request: str,
    llm,
    log: Callable[[str], None],
    on_code: Callable[[str], None] = lambda c: None,
    max_attempts: int = 4,
    timeout: int = 60,
    confirm: Optional[Callable[[str], bool]] = None,
    cancelled: Callable[[], bool] = lambda: False,
    proc_holder: Optional[list] = None,
    workspace: Path = WORKSPACE,
) -> Outcome:
    """Generate a script for `request`, run it, and ask the LLM to fix it on failure."""
    run_dir = workspace / "scripts" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    libs = workspace / "libs"
    path = run_dir / "script.py"

    log("LLM script yazıyor…")
    code = extract_code(llm.ask(f"Task: {request}"))
    result: Optional[RunResult] = None

    for attempt in range(1, max_attempts + 1):
        if cancelled():
            return Outcome(code, result, attempt - 1, path, cancelled=True)
        path.write_text(code, encoding="utf-8")
        on_code(code)
        if confirm is not None and not confirm(code):
            return Outcome(code, result, attempt - 1, path, cancelled=True)

        install_requirements(parse_requirements(code), libs, log)
        log(f"[{attempt}/{max_attempts}] çalıştırılıyor…")
        result = run_script(path, libs, timeout, proc_holder)
        if result.stdout:
            log("--- stdout ---\n" + result.stdout.rstrip())
        if result.ok:
            log("✔ Başarılı.")
            return Outcome(code, result, attempt, path)
        if cancelled():
            return Outcome(code, result, attempt, path, cancelled=True)
        log("--- stderr ---\n" + (result.stderr.rstrip() or "(boş)"))
        if attempt == max_attempts:
            break
        log("✘ Hata — LLM debug ediyor…")
        code = extract_code(llm.ask(
            "The script failed.\n\nstdout:\n" + result.stdout[-3000:] +
            "\n\nstderr:\n" + result.stderr[-4000:] +
            "\n\nReturn the complete fixed script."
        ))

    log("✘ Maksimum deneme sayısına ulaşıldı.")
    return Outcome(code, result, max_attempts, path)
