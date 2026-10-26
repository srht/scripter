"""Tkinter front-end: describe a task, click once, get a generated + debugged + executed script."""
from __future__ import annotations

import os
import platform
import queue
import subprocess
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
        self.cur_out = None
        self.worker: threading.Thread | None = None
        self._build()
        self.after(100, self._drain)

    def _build(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        gen = ttk.Frame(nb)
        lib = ttk.Frame(nb)
        nb.add(gen, text="Üret")
        nb.add(lib, text="📚 Kitaplık")
        nb.bind("<<NotebookTabChanged>>", lambda e: self.lib_refresh() if nb.index("current") == 1 else None)
        self._build_library(lib)
        top = ttk.Frame(gen, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Sağlayıcı:").grid(row=0, column=0, sticky="w")
        self.provider = tk.StringVar(value="Gemini")
        pc = ttk.Combobox(top, textvariable=self.provider, values=list(core.PROVIDERS), state="readonly", width=11)
        pc.grid(row=0, column=1, padx=4)
        pc.bind("<<ComboboxSelected>>", lambda e: self._on_provider())
        ttk.Label(top, text="API Key:").grid(row=0, column=2, padx=(8, 0))
        self.keys = {p: core.env_key(p) for p in core.PROVIDERS}
        self.key = tk.StringVar(value=self.keys["Gemini"])
        ttk.Entry(top, textvariable=self.key, show="•", width=30).grid(row=0, column=3, padx=4)
        ttk.Label(top, text="Model:").grid(row=0, column=4, padx=(8, 0))
        self.model = tk.StringVar(value=core.DEFAULT_MODEL)
        self.model_box = ttk.Combobox(top, textvariable=self.model, width=38, values=[core.DEFAULT_MODEL])
        self.model_box.grid(row=0, column=5, padx=4)
        self.refresh_btn = ttk.Button(top, text="⟳ Ücretsiz modelleri getir", command=self.refresh_models)
        self.refresh_btn.grid(row=0, column=6, padx=4)
        self.refresh_btn.config(state="disabled")
        self._prov = "Gemini"
        self.free_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Sadece ücretsiz", variable=self.free_only).grid(row=0, column=7)
        top2 = ttk.Frame(gen, padding=(8, 0))
        top2.pack(fill="x")
        top = top2
        ttk.Label(top, text="Maks. deneme:").grid(row=0, column=0, padx=(12, 0))
        self.attempts = tk.IntVar(value=4)
        ttk.Spinbox(top, from_=1, to=10, textvariable=self.attempts, width=4).grid(row=0, column=1)
        ttk.Label(top, text="Zaman aşımı (sn, 0=sınırsız):").grid(row=0, column=2, padx=(12, 0))
        self.timeout = tk.IntVar(value=60)
        ttk.Spinbox(top, from_=0, to=3600, textvariable=self.timeout, width=6).grid(row=0, column=3)

        req = ttk.LabelFrame(gen, text="Ne yapmak istiyorsun?", padding=6)
        req.pack(fill="x", padx=8)
        self.request = scrolledtext.ScrolledText(req, height=4, wrap="word")
        self.request.pack(fill="x")
        self.request.bind("<Control-Return>", lambda e: (self.start(), "break")[1])

        bar = ttk.Frame(gen, padding=8)
        bar.pack(fill="x")
        self.btn = ttk.Button(bar, text="▶ Üret, Debug Et ve Çalıştır", command=self.start)
        self.btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="■ Durdur", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.review = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Çalıştırmadan önce kodu onayla", variable=self.review).pack(side="left", padx=12)
        self.status = ttk.Label(bar, text="Hazır")
        self.status.pack(side="right")

        pane = ttk.PanedWindow(gen, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.code = self._box(pane, "Üretilen script")
        self.log = self._box(pane, "Günlük / çıktı")
        self.log.tag_config("err", foreground="#c0392b")
        self._input_bar(gen)

    def _input_bar(self, parent):
        """Sends a line to the running script's stdin (answers input())."""
        f = ttk.Frame(parent, padding=(8, 0, 8, 8))
        f.pack(fill="x", side="bottom")
        ttk.Label(f, text="Script'e girdi:").pack(side="left")
        var = tk.StringVar()
        e = ttk.Entry(f, textvariable=var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        send = lambda *_: (self.send_input(var.get()), var.set(""))
        e.bind("<Return>", send)
        ttk.Button(f, text="Gönder", command=send).pack(side="left")
        ttk.Button(f, text="EOF", command=self.close_stdin).pack(side="left", padx=(4, 0))

    def send_input(self, line: str):
        p = self.proc[0] if self.proc else None
        if not p or p.poll() is not None or p.stdin is None:
            return
        try:
            p.stdin.write((line + "\n").encode())
            p.stdin.flush()
            self.q.put(("echo", line + "\n"))
        except (BrokenPipeError, OSError, ValueError):
            pass

    def close_stdin(self):
        p = self.proc[0] if self.proc else None
        if p and p.stdin:
            try:
                p.stdin.close()
            except OSError:
                pass

    def _emit(self, stream: str, text: str):
        self.q.put(("out", (stream, text)))

    def _box(self, pane, title):
        f = ttk.LabelFrame(pane, text=title, padding=4)
        t = scrolledtext.ScrolledText(f, wrap="none", font=("Courier", 10))
        t.pack(fill="both", expand=True)
        pane.add(f, weight=1)
        return t

    # -- library -------------------------------------------------------
    def _build_library(self, parent):
        bar = ttk.Frame(parent, padding=8)
        bar.pack(fill="x")
        self.lib_run_btn = ttk.Button(bar, text="▶ Çalıştır", command=self.lib_run)
        self.lib_run_btn.pack(side="left")
        ttk.Button(bar, text="■ Durdur", command=self.stop).pack(side="left", padx=4)
        ttk.Button(bar, text="Kopyala", command=self.lib_copy).pack(side="left", padx=4)
        ttk.Button(bar, text="Klasörü aç", command=self.lib_open).pack(side="left", padx=4)
        ttk.Button(bar, text="Sil", command=self.lib_delete).pack(side="left", padx=4)
        ttk.Button(bar, text="⟳ Yenile", command=self.lib_refresh).pack(side="left", padx=4)
        ttk.Label(bar, text="Ara:").pack(side="left", padx=(16, 2))
        self.lib_filter = tk.StringVar()
        self.lib_filter.trace_add("write", lambda *a: self.lib_refresh())
        ttk.Entry(bar, textvariable=self.lib_filter, width=24).pack(side="left")
        self.lib_status = ttk.Label(bar, text="")
        self.lib_status.pack(side="right")

        cols = ("created", "request", "state")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=9, selectmode="browse")
        for c, t, w in (("created", "Tarih", 140), ("request", "İstek", 620), ("state", "Durum", 80)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "request"))
        self.tree.pack(fill="x", padx=8)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._lib_show_code())
        self.tree.bind("<Double-1>", lambda e: self.lib_run())
        self.tree.bind("<Return>", lambda e: self.lib_run())

        pane = ttk.PanedWindow(parent, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=8)
        self.lib_code = self._box(pane, "Script")
        self.lib_out = self._box(pane, "Çıktı")
        self.lib_out.tag_config("err", foreground="#c0392b")
        self._input_bar(parent)
        self.entries: dict[str, core.LibraryEntry] = {}

    def lib_refresh(self):
        needle = self.lib_filter.get().lower().strip()
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.entries = {}
        for e in core.list_library():
            if needle and needle not in e.request.lower():
                continue
            state = {True: "✔", False: "✘", None: "?"}[e.ok]
            iid = self.tree.insert("", "end", values=(e.created, e.request.replace("\n", " ")[:200], state))
            self.entries[iid] = e
        self.lib_status.config(text=f"{len(self.entries)} script")
        if sel and sel[0] in self.tree.get_children():
            self.tree.selection_set(sel[0])

    def _lib_sel(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("ScriptForge", "Listeden bir script seç.")
            return None
        return self.entries.get(sel[0])

    def _lib_show_code(self):
        sel = self.tree.selection()
        e = self.entries.get(sel[0]) if sel else None
        if e:
            self.lib_code.delete("1.0", "end")
            try:
                self.lib_code.insert("1.0", e.code)
            except OSError as ex:
                self.lib_code.insert("1.0", f"Okunamadı: {ex}")

    def lib_run(self):
        e = self._lib_sel()
        if not e or (self.worker and self.worker.is_alive()):
            return
        self.cancel_flag.clear()
        self.cur_out = self.lib_out
        self.lib_out.delete("1.0", "end")
        self.lib_run_btn.config(state="disabled")
        self.lib_status.config(text="Çalışıyor…")

        def job():
            try:
                core.run_entry(e, lambda m: self.q.put(("lib_log", m)), self.timeout.get(), self.proc, on_output=self._emit)
            except Exception as ex:
                self.q.put(("lib_log", f"HATA: {ex}"))
            self.q.put(("lib_done", None))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def lib_copy(self):
        e = self._lib_sel()
        if e:
            self.clipboard_clear()
            self.clipboard_append(e.code)
            self.lib_status.config(text="Kod panoya kopyalandı")

    def lib_open(self):
        e = self._lib_sel()
        if not e:
            return
        if platform.system() == "Windows":
            os.startfile(e.dir)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if platform.system() == "Darwin" else "xdg-open", str(e.dir)])

    def lib_delete(self):
        e = self._lib_sel()
        if e and messagebox.askyesno("Sil", f"Bu script kalıcı olarak silinsin mi?\n\n{e.request[:120]}"):
            core.delete_entry(e)
            self.lib_refresh()

    # -- provider / models ---------------------------------------------
    def _on_provider(self):
        old = getattr(self, "_prov", "Gemini")
        self.keys[old] = self.key.get()
        new = self.provider.get()
        self._prov = new
        self.key.set(self.keys[new])
        self.model.set(core.PROVIDERS[new]["default_model"])
        self.model_box.config(values=[core.PROVIDERS[new]["default_model"]])
        self.refresh_btn.config(state="normal" if new == "OpenRouter" else "disabled")
        if new == "OpenRouter":
            self.refresh_models()

    def refresh_models(self):
        if self.provider.get() != "OpenRouter":
            return messagebox.showinfo("ScriptForge", "Model listesi sadece OpenRouter için getirilir.")
        self.status.config(text="Modeller getiriliyor…")
        free = self.free_only.get()

        def job():
            try:
                self.q.put(("models", core.list_openrouter_models(free)))
            except Exception as e:
                self.q.put(("log", f"Model listesi alınamadı: {e}"))
                self.q.put(("done", "Hata"))

        threading.Thread(target=job, daemon=True).start()

    # -- actions -------------------------------------------------------
    def start(self):
        if self.worker and self.worker.is_alive():
            return
        task = self.request.get("1.0", "end").strip()
        if not task:
            return messagebox.showinfo("ScriptForge", "Önce bir istek yaz.")
        if not self.key.get().strip():
            return messagebox.showerror("ScriptForge", f"{self.provider.get()} API key gerekli.")
        self.cancel_flag.clear()
        self.cur_out = self.log
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
            llm = core.make_llm(self.provider.get(), self.key.get().strip(), self.model.get().strip())
            out = core.forge(
                task, llm,
                log=lambda m: self.q.put(("log", m)),
                on_code=lambda c: self.q.put(("code", c)),
                max_attempts=self.attempts.get(),
                timeout=self.timeout.get(),
                confirm=self._confirm if self.review.get() else None,
                cancelled=self.cancel_flag.is_set,
                proc_holder=self.proc,
                on_output=self._emit,
            )
            ok = out.result is not None and out.result.ok
            self.q.put(("log", f"\nKaydedildi: {out.path}"))
            self.q.put(("lib_refresh", None))
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
                elif kind == "out":
                    stream, text = val
                    self.cur_out.insert("end", text, "err" if stream == "err" else ())
                    self.cur_out.see("end")
                elif kind == "echo":
                    self.cur_out.insert("end", val)
                    self.cur_out.see("end")
                elif kind == "lib_refresh":
                    self.lib_refresh()
                elif kind == "lib_log":
                    self.lib_out.insert("end", val + "\n")
                    self.lib_out.see("end")
                elif kind == "lib_done":
                    self.lib_run_btn.config(state="normal")
                    self.lib_status.config(text="Bitti")
                elif kind == "models":
                    self.model_box.config(values=["openrouter/free"] + [m for m in val if m != "openrouter/free"])
                    self.status.config(text=f"{len(val)} model bulundu")
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
