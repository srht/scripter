"""Tkinter front-end: describe a task, click once, get a generated + debugged + executed script."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from . import core


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ScriptForge — LLM ile Python script üret & çalıştır")
        self.geometry("1000x720")
        self.q: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.proc: list = []
        self.worker: threading.Thread | None = None
        self._build()
        self.after(100, self._drain)

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="API Key:").grid(row=0, column=0, sticky="w")
        self.key = tk.StringVar(value=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", ""))
        ttk.Entry(top, textvariable=self.key, show="•", width=40).grid(row=0, column=1, padx=4)
        ttk.Label(top, text="Model:").grid(row=0, column=2, padx=(12, 0))
        self.model = tk.StringVar(value=core.DEFAULT_MODEL)
        ttk.Entry(top, textvariable=self.model, width=22).grid(row=0, column=3, padx=4)
        ttk.Label(top, text="Maks. deneme:").grid(row=0, column=4, padx=(12, 0))
        self.attempts = tk.IntVar(value=4)
        ttk.Spinbox(top, from_=1, to=10, textvariable=self.attempts, width=4).grid(row=0, column=5)
        ttk.Label(top, text="Zaman aşımı (sn):").grid(row=0, column=6, padx=(12, 0))
        self.timeout = tk.IntVar(value=60)
        ttk.Spinbox(top, from_=5, to=3600, textvariable=self.timeout, width=6).grid(row=0, column=7)

        req = ttk.LabelFrame(self, text="Ne yapmak istiyorsun?", padding=6)
        req.pack(fill="x", padx=8)
        self.request = scrolledtext.ScrolledText(req, height=4, wrap="word")
        self.request.pack(fill="x")
        self.request.bind("<Control-Return>", lambda e: (self.start(), "break")[1])

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        self.btn = ttk.Button(bar, text="▶ Üret, Debug Et ve Çalıştır", command=self.start)
        self.btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="■ Durdur", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.review = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Çalıştırmadan önce kodu onayla", variable=self.review).pack(side="left", padx=12)
        self.status = ttk.Label(bar, text="Hazır")
        self.status.pack(side="right")

        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.code = self._box(pane, "Üretilen script")
        self.log = self._box(pane, "Günlük / çıktı")

    def _box(self, pane, title):
        f = ttk.LabelFrame(pane, text=title, padding=4)
        t = scrolledtext.ScrolledText(f, wrap="none", font=("Courier", 10))
        t.pack(fill="both", expand=True)
        pane.add(f, weight=1)
        return t

    # -- actions -------------------------------------------------------
    def start(self):
        if self.worker and self.worker.is_alive():
            return
        task = self.request.get("1.0", "end").strip()
        if not task:
            return messagebox.showinfo("ScriptForge", "Önce bir istek yaz.")
        if not self.key.get().strip():
            return messagebox.showerror("ScriptForge", "Gemini API key gerekli (GEMINI_API_KEY).")
        self.cancel_flag.clear()
        self.log.delete("1.0", "end")
        self.btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status.config(text="Çalışıyor…")
        self.worker = threading.Thread(target=self._work, args=(task,), daemon=True)
        self.worker.start()

    def stop(self):
        self.cancel_flag.set()
        for p in self.proc:
            try:
                p.kill()
            except Exception:
                pass

    def _confirm(self, code: str) -> bool:
        # called from worker thread; ask the UI thread and wait
        ev, box = threading.Event(), []
        self.q.put(("ask", (ev, box)))
        ev.wait()
        return bool(box and box[0])

    def _work(self, task):
        try:
            llm = core.LLM(self.key.get().strip(), self.model.get().strip())
            out = core.forge(
                task, llm,
                log=lambda m: self.q.put(("log", m)),
                on_code=lambda c: self.q.put(("code", c)),
                max_attempts=self.attempts.get(),
                timeout=self.timeout.get(),
                confirm=self._confirm if self.review.get() else None,
                cancelled=self.cancel_flag.is_set,
                proc_holder=self.proc,
            )
            ok = out.result is not None and out.result.ok
            self.q.put(("log", f"\nKaydedildi: {out.path}"))
            self.q.put(("done", "Başarılı" if ok else ("İptal" if out.cancelled else "Başarısız")))
        except Exception as e:  # network/auth errors etc.
            self.q.put(("log", f"HATA: {e}"))
            self.q.put(("done", "Hata"))

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", val + "\n")
                    self.log.see("end")
                elif kind == "code":
                    self.code.delete("1.0", "end")
                    self.code.insert("1.0", val)
                elif kind == "ask":
                    ev, box = val
                    box.append(messagebox.askyesno("Onay", "Üretilen kod çalıştırılsın mı?\n(Sağ panelde görebilirsin)"))
                    ev.set()
                elif kind == "done":
                    self.status.config(text=val)
                    self.btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
