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

DEFAULT_MODEL = "claude-sonnet-5-5"
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
    """Thin wrapper over the Anthropic Messages API (conversation kept so fixes have context)."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.messages: list[dict] = []

    def ask(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        resp = self.client.messages.create(
            model=self.model, max_tokens=8000, system=SYSTEM_PROMPT, messages=self.messages
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        self.messages.append({"role": "assistant", "content": text})
        return text


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
