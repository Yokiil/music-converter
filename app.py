"""Windows 桌面界面；诊断、扫描和转码都不阻塞主线程。"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import convert, locate, platforms, probe, windows_drop
from core.default_profiles import merge_profile

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "转换输出"
DEFAULT_RATE = "原样 / 默认"
BG, INK, MUTED, BLUE = "#f0f4fa", "#182740", "#52637a", "#225cce"


class MusicConverter(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("音乐转换器 · 本地音频工作台")
        self.geometry("1060x700")
        self.minsize(920, 620)
        self.configure(bg=BG)
        self.profile = merge_profile(platforms.EMPTY_PROFILE)
        self.rows: dict[str, tuple[Path, int]] = {}
        self.infos: dict[str, probe.AudioInfo] = {}
        self.sequence = 0
        self.scans = 0
        self.events: queue.Queue = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.cancel = threading.Event()
        self.busy = False
        self.closed = False
        self.last_output: Path | None = None
        self.controls: list[tuple[ttk.Widget, str]] = []
        self._build_ui()
        self.update_idletasks()
        try:
            self._undo_drop = windows_drop.register(self, self._scan_selection)
        except (OSError, AttributeError, tk.TclError):
            self._undo_drop = lambda: None
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(60, self._drain)
        if not locate.ffmpeg_available() or not locate.probe_available():
            self._log("需要 FFmpeg 和 ffprobe，安装方法见 README。")

    def _build_ui(self) -> None:
        font = ("Microsoft YaHei UI", 10)
        self.option_add("*Font", font)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=(font[0], 19, "bold"))
        style.configure("TButton", padding=(11, 6))
        style.configure("Primary.TButton", padding=(15, 8), background=BLUE, foreground="white")
        style.map("Primary.TButton", background=[("disabled", "#9eb0cc"), ("active", "#194ba9")])
        style.configure("Treeview", rowheight=30, background="white", fieldbackground="white", foreground=INK)
        style.configure("Treeview.Heading", padding=(7, 7), background="#e1e9f6", foreground=INK)
        style.map("Treeview", background=[("selected", "#d4e4ff")], foreground=[("selected", INK)])
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#d9e3f1")
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)

        titlebar = ttk.Frame(outer)
        titlebar.pack(fill="x")
        ttk.Label(titlebar, text="音乐转换器", style="Title.TLabel").pack(side="left")
        ttk.Label(titlebar, text="本地导出 · 批量转码", style="Muted.TLabel").pack(side="left", padx=13)
        self.summary = ttk.Label(titlebar, text="未添加文件", style="Muted.TLabel")
        self.summary.pack(side="right")
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(12, 8))

        for label, command in (("添加文件", self.add_files), ("添加文件夹", self.add_folder),
                               ("移除选中", self._remove), ("清空", lambda: self._remove(True))):
            self._button(toolbar, label, command).pack(side="left", padx=(0, 6))
        self._button(toolbar, "恢复默认参数", lambda: self._set_profile(platforms.EMPTY_PROFILE)).pack(side="right")
        self._button(toolbar, "加载本地参数…", self._pick_profile).pack(side="right", padx=(0, 6))
        self.profile_text = ttk.Label(outer, text="已启用内置默认参数（网易云 / QQ / 酷狗 / 酷我）", style="Muted.TLabel")
        self.profile_text.pack(fill="x", pady=(0, 10))

        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True)
        cols = ("name", "format", "codec", "duration", "bitrate", "size", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="extended")
        for col, label, width in zip(cols, ("文件名", "识别格式", "编码", "时长", "码率", "体积", "诊断"),
                                     (265, 170, 90, 60, 88, 82, 112)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=52, stretch=col in ("name", "format"),
                             anchor="w" if col in ("name", "format") else "center")
        self.tree.tag_configure("wrapped", foreground="#2357ad")
        self.tree.tag_configure("warn", foreground="#8E531D")
        self.tree.tag_configure("hint", foreground=MUTED)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._detail)
        self.tree.bind("<Delete>", lambda _event: self._remove())
        self._placeholder()

        settings = ttk.Frame(outer)
        settings.pack(fill="x", pady=(14, 8))
        ttk.Label(settings, text="输出格式").pack(side="left")
        self.fmt_box = ttk.Combobox(settings, state="readonly", width=18,
                                    values=[target.label for target in convert.TARGETS.values()])
        self.fmt_box.current(0)
        self.fmt_box.pack(side="left", padx=(6, 12))
        self.fmt_box.bind("<<ComboboxSelected>>", self._on_format)
        self.controls.append((self.fmt_box, "readonly"))
        ttk.Label(settings, text="码率").pack(side="left")
        self.br_box = ttk.Combobox(settings, width=12)
        self.br_box.pack(side="left", padx=(6, 12))
        self._on_format()
        ttk.Label(settings, text="保存到").pack(side="left")
        self.out_var = tk.StringVar(value=str(DEFAULT_OUT))
        out = ttk.Entry(settings, textvariable=self.out_var)
        out.pack(side="left", fill="x", expand=True, padx=6)
        self.controls.append((out, "normal"))
        self._button(settings, "选择…", self._pick_output).pack(side="left")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(3, 9))
        self.start_btn = self._button(actions, "开始转换", self.start_convert, style="Primary.TButton")
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.folder_btn = ttk.Button(actions, text="打开输出", command=self._open_output, state="disabled")
        self.folder_btn.pack(side="left")
        self.progress = ttk.Progressbar(actions, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=(13, 8))
        self.status = ttk.Label(actions, text="就绪", width=22, anchor="e", style="Muted.TLabel")
        self.status.pack(side="right")
        self.log = tk.Text(outer, height=6, bg="white", fg=INK, relief="flat", padx=9, pady=5, wrap="word", state="disabled")
        self.log.pack(fill="x")

    def _button(self, parent, text, command, **kwargs):
        button = ttk.Button(parent, text=text, command=command, **kwargs)
        self.controls.append((button, "normal"))
        return button

    def _placeholder(self):
        if not self.rows and not self.tree.exists("hint"):
            self.tree.insert("", "end", iid="hint", values=("添加或拖入本地音频文件",), tags=("hint",))

    def add_files(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(probe.INPUT_EXTS))
        selected = filedialog.askopenfilenames(parent=self, title="选择本地文件",
                                               filetypes=[("音视频或平台封装", patterns), ("所有文件", "*")])
        self._add_paths(selected)

    def add_folder(self):
        folder = filedialog.askdirectory(parent=self, title="选择本地音乐文件夹")
        if folder:
            self._scan_selection([folder])

    def _scan_selection(self, paths):
        if self.busy or self.closed:
            return
        self.scans += 1
        skip = os.path.normcase(os.path.abspath(self.out_var.get() or DEFAULT_OUT))
        self.pool.submit(self._scan_job, paths, skip)
        self._refresh()

    def _scan_job(self, paths, skip):
        found = []
        try:
            for name in paths:
                path = Path(name)
                if path.is_file():
                    found.append(path)
                elif path.is_dir():
                    for root, dirs, names in os.walk(path):
                        dirs[:] = [d for d in dirs if not d.startswith(".music-")
                                   and os.path.normcase(os.path.join(root, d)) != skip]
                        found.extend(Path(root) / n for n in names if Path(n).suffix.lower() in probe.INPUT_EXTS)
        finally:
            self.events.put(("scan", found))

    def _add_paths(self, paths):
        if self.busy or self.closed:
            return
        for raw in paths:
            path = Path(raw).absolute()
            key = os.path.normcase(os.path.normpath(path))
            if key in self.rows:
                continue
            if self.tree.exists("hint"):
                self.tree.delete("hint")
            self.tree.insert("", "end", iid=key)
            self._enqueue(key, path)
        self._refresh()

    def _enqueue(self, key, path):
        self.sequence += 1
        self.rows[key] = (path, self.sequence)
        self.tree.item(key, values=(path.name, "", "", "", "", "", "读取中…"), tags=("hint",))
        self.pool.submit(self._diagnose_job, key, path, self.sequence, self.profile)

    def _diagnose_job(self, key, path, sequence, profile):
        try:
            info = probe.diagnose(path, profile)
        except Exception:
            info = probe.AudioInfo(path, verdict=probe.VERDICT_BROKEN, note="诊断失败，检查文件是否可读。")
        self.events.put(("info", (key, sequence, info)))

    def _render(self, key, sequence, info):
        if key not in self.rows or self.rows[key][1] != sequence:
            return
        self.infos[key] = info
        tag = "wrapped" if info.verdict == probe.VERDICT_WRAPPED else ("" if probe.is_convertible(info) else "warn")
        self.tree.item(key, values=(info.name, info.container, info.codec_text, info.duration_text,
                                    info.bitrate_text, info.size_text, info.verdict_text), tags=(tag,) if tag else ())
        self._refresh()

    def _refresh(self):
        pending = len(self.rows) - len(self.infos)
        ready = sum(probe.is_convertible(i) for i in self.infos.values())
        text = f"{len(self.rows)} 个文件 · {ready} 个可处理" if self.rows else "未添加文件"
        if pending:
            text += f" · {pending} 个读取中"
        if self.scans:
            text += " · 正在扫描目录"
        self.summary.configure(text=text)
        self.start_btn.configure(state="normal" if ready and not (pending or self.scans or self.busy) else "disabled")

    def _remove(self, all_rows=False):
        if self.busy:
            return
        for key in list(self.rows) if all_rows else self.tree.selection():
            if key in self.rows:
                self.rows.pop(key)
                self.infos.pop(key, None)
                self.tree.delete(key)
        self._placeholder()
        self._refresh()

    def _pick_profile(self):
        name = filedialog.askopenfilename(parent=self, title="选择本地 JSON 导出参数",
                                          filetypes=[("JSON", "*.json")])
        if name:
            try:
                profile = platforms.load_profile(name)
            except (OSError, ValueError) as exc:
                messagebox.showerror("无法加载参数", str(exc), parent=self)
                return
            self._set_profile(profile)

    def _set_profile(self, overrides):
        """Merge a local JSON on top of the built-in defaults, never replacing them wholesale."""
        self.profile = merge_profile(overrides)
        self.profile_text.configure(text=(
            f"内置默认参数 + {len(overrides)} 组本机覆盖 · 不上传、不保存" if overrides
            else "已启用内置默认参数（网易云 / QQ / 酷狗 / 酷我）"))
        self.infos.clear()
        for key, (path, _) in list(self.rows.items()):
            self._enqueue(key, path)
        self._refresh()

    def _pick_output(self):
        folder = filedialog.askdirectory(parent=self, title="选择输出文件夹")
        if folder:
            self.out_var.set(folder)

    def _open_output(self):
        if os.name == "nt" and self.last_output:
            try:
                os.startfile(self.last_output)
            except OSError as exc:
                messagebox.showerror("无法打开目录", str(exc), parent=self)

    def _detail(self, event):
        info = self.infos.get(self.tree.identify_row(event.y))
        if info:
            messagebox.showinfo(info.name, f"{info.verdict_text}：{info.note}\n\n{info.container} · {info.codec_text}\n"
                                f"{info.sample_rate or '—'} Hz · {info.channels or '—'} 声道 · {info.quality_text}\n\n{info.path}", parent=self)

    def _format(self):
        return tuple(convert.TARGETS)[max(0, self.fmt_box.current())]

    def _on_format(self, _event=None):
        rates = convert.TARGETS[self._format()].bitrates
        self.br_box.configure(values=(DEFAULT_RATE, *rates), state="readonly" if rates and not self.busy else "disabled")
        self.br_box.set(DEFAULT_RATE)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start_convert(self):
        if self.busy or self.scans or len(self.rows) != len(self.infos):
            return
        if not locate.ffmpeg_available() or not locate.probe_available():
            messagebox.showerror("缺少 FFmpeg", locate.missing_message(), parent=self)
            return
        items = [self.infos[k] for k in self.rows if probe.is_convertible(self.infos[k])]
        if not items:
            return
        fmt = self._format()
        bitrate = self.br_box.get() if self.br_box.get() != DEFAULT_RATE else None
        count = sum(convert.needs_lossy_warning(i, fmt, bitrate) for i in items)
        if count and not messagebox.askokcancel("确认转码", f"{count} 个文件可能重新压缩或降低位深。"
                                               "原文件不变，继续吗？", parent=self):
            return
        skipped = len(self.infos) - len(items)
        if skipped:
            self._log(f"跳过 {skipped} 个不可处理的文件，双击行可查看原因。")
        self.cancel.clear()
        self.busy = True
        for button, _ in self.controls:
            button.configure(state="disabled")
        self.br_box.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.folder_btn.configure(state="disabled")
        self.progress["value"] = 0
        out_dir = Path(self.out_var.get().strip() or DEFAULT_OUT).absolute()
        threading.Thread(target=self._batch, args=(items, out_dir, fmt, bitrate, self.profile)).start()

    def _batch(self, items, out_dir, fmt, bitrate, profile):
        ok = failed = 0
        total = len(items)
        try:
            for index, info in enumerate(items):
                if self.cancel.is_set():
                    break
                self.events.put(("status", f"处理中 {index + 1}/{total}"))
                def progress(fraction, idx=index):
                    self.events.put(("progress", 100 * (idx + fraction) / total))
                try:
                    result = convert.convert_to_directory(info, out_dir, fmt, bitrate, progress,
                                                          profile=profile, cancel=self.cancel)
                except Exception:
                    result = convert.ConvertResult(False, info.path, message="处理异常。")
                if result.cancelled:
                    break
                if result.ok:
                    ok += 1
                    self.events.put(("log", f"完成 · {info.name} → {result.dst.name} ({result.message})"))
                else:
                    failed += 1
                    self.events.put(("log", f"失败 · {info.name}：{result.message}"))
                progress(1)
        finally:
            status = ("已停止 · " if self.cancel.is_set() else "") + f"成功 {ok}/{total} · 失败 {failed}"
            self.events.put(("finished", (status, out_dir if ok else None)))

    def _stop(self):
        self.cancel.set()
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="正在停止…")

    def _finish(self, status, output):
        self.busy = False
        if output is not None:
            self.last_output = output
        for button, state in self.controls:
            button.configure(state=state)
        self.br_box.configure(state="readonly" if convert.TARGETS[self._format()].bitrates else "disabled")
        self.stop_btn.configure(state="disabled")
        self.folder_btn.configure(state="normal" if self.last_output and os.name == "nt" else "disabled")
        self.status.configure(text=status)
        self._log(status)
        self._refresh()

    def _drain(self):
        for _ in range(180):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "info":
                self._render(*payload)
            elif kind == "scan":
                self.scans -= 1
                self._add_paths(payload)
                if not payload:
                    self._log("没有在所选目录中找到已知的本地文件格式。")
            elif kind == "finished":
                self._finish(*payload)
            elif kind == "progress":
                self.progress["value"] = payload
            elif kind == "status" and not self.cancel.is_set():
                self.status.configure(text=payload)
            elif kind == "log":
                self._log(payload)
        if not self.closed:
            self.after(60, self._drain)

    def _close(self):
        self.closed = True
        self.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self._undo_drop()
        self.destroy()


def main() -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    MusicConverter().mainloop()


if __name__ == "__main__":
    main()
