#!/usr/bin/env python3
"""
mp3_to_mp4_gui.py — 音频 → 纯黑屏 MP4 转换器（Tkinter）。

界面参考 Filee（KnifeLemon/Filee）：左侧导航栏 + 中间大拖放区 + 分页内容，留白大、文字少。
macOS 说明：Aqua 主题会接管 ttk 控件的 padding/背景/悬停态，style.configure() 基本无效，
所以导航、卡片、拖放区这些“自定义外观”的部分用原生 tk 控件（背景/前景可控），
按钮、输入框、进度条、列表仍用 ttk（本身就是 NSButton/NSTextField，跟着系统深浅色走）。
深浅色通过读系统的 AppleInterfaceStyle 判断，用两套苹果系统配色。
拖拽依赖 tkinterdnd2（可选，缺失时自动降级为按钮添加）。

依赖: Python 标准库 + 系统 ffmpeg（转换逻辑见 mp3_to_mp4.py）。
"""

import concurrent.futures
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
from tkinter import filedialog, messagebox, ttk

from mp3_to_mp4 import convert_one, default_output, find_ffmpeg, unique_output

APP_NAME = "ktrans"
APP_SUBTITLE = "音乐格式转换器"
VERSION = "2.02"

AUDIO_EXTS = (
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".ape", ".mka", ".wma", ".aif", ".aiff", ".mp2", ".wv", ".tta",
    ".ac3", ".ncm",
    # QQ 音乐 / Moo 音乐私有格式
    ".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8",
    ".qmcflac", ".qmcogg", ".tkm",
    ".mflac", ".mflac0", ".mflac1", ".mflaca", ".mflach", ".mflacl", ".mflacm",
    ".mgg", ".mgg0", ".mgg1", ".mgga", ".mggh", ".mggl", ".mggm",
    ".bkcmp3", ".bkcm4a", ".bkcflac", ".bkcwav", ".bkcape", ".bkcogg", ".bkcwma",
)

# 苹果系统配色（浅色 / 深色两套）
LIGHT = {
    "bg": "#ffffff", "sidebar": "#f2f2f7", "card": "#f5f5f7",
    "text": "#1d1d1f", "gray": "#86868b", "accent": "#007aff",
    "border": "#d2d2d7", "hover": "#e3e3e8",
    "accent_hover": "#3395ff", "card_hover": "#e8e8ed",
    "disabled": "#e3e3e8", "disabled_fg": "#a1a1a6",
}
DARK = {
    "bg": "#1e1e1e", "sidebar": "#262626", "card": "#2c2c2e",
    "text": "#f5f5f7", "gray": "#98989d", "accent": "#0a84ff",
    "border": "#3a3a3c", "hover": "#333336",
    "accent_hover": "#3b9bff", "card_hover": "#3a3a3c",
    "disabled": "#3a3a3c", "disabled_fg": "#6e6e73",
}


class FlatButton(tk.Canvas):
    """圆角按钮。

    macOS 上 tk.Button / ttk.Button 的背景由 Aqua 接管、无法自定义，在深色窗口里
    会渲染成浅色按钮，跟自绘的深色界面打架。这里用 Canvas 画圆角矩形，背景/圆角/
    悬浮/按下/禁用全部可控。
    """

    def __init__(self, master, text: str, command=None, pal: dict | None = None,
                 font=None, kind: str = "secondary", width: int = 96,
                 height: int = 32, radius: int = 7, bg: str | None = None) -> None:
        self.pal = pal or LIGHT
        self.kind = kind
        self.command = command
        self.label = text
        self.radius = radius
        self.font = font
        self.enabled = True
        self.hovered = False
        self.pressed = False
        super().__init__(
            master, width=width, height=height, bd=0, highlightthickness=0,
            bg=bg or self.pal["bg"], cursor="pointinghand",
        )
        self.bind("<Configure>", lambda e: self._draw(e.width, e.height))
        self.bind("<Enter>", lambda e: self._set_hovered(True))
        self.bind("<Leave>", lambda e: self._set_hovered(False))
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ---- 状态 ----
    def _fill(self) -> str:
        if not self.enabled:
            return self.pal["disabled"]
        if self.kind == "primary":
            return self.pal["accent_hover"] if (self.hovered or self.pressed) else self.pal["accent"]
        return self.pal["card_hover"] if (self.hovered or self.pressed) else self.pal["card"]

    def _fg(self) -> str:
        if not self.enabled:
            return self.pal["disabled_fg"]
        return "#ffffff" if self.kind == "primary" else self.pal["text"]

    def _set_hovered(self, v: bool) -> None:
        self.hovered = v
        self._redraw()

    def _on_press(self, event) -> None:
        if not self.enabled:
            return
        self.pressed = True
        self._redraw()

    def _on_release(self, event) -> None:
        if not self.enabled:
            return
        self.pressed = False
        self._redraw()
        if self.command:
            self.command()

    def set_enabled(self, v: bool) -> None:
        self.enabled = v
        self.config(cursor="pointinghand" if v else "arrow")
        self._redraw()

    def _redraw(self) -> None:
        self._draw(self.winfo_width(), self.winfo_height())

    # ---- 绘制 ----
    def _draw(self, w: int, h: int) -> None:
        self.delete("all")
        if w < 4 or h < 4:
            return
        c = self._fill()
        r = min(self.radius, h // 2, w // 2)
        # 中间两块矩形 + 四角扇形，拼出圆角矩形
        self.create_rectangle(r, 0, w - r, h, fill=c, outline=c)
        self.create_rectangle(0, r, w, h - r, fill=c, outline=c)
        for x, y, start in (
            (0, 0, 90), (w - 2 * r, 0, 0), (0, h - 2 * r, 180), (w - 2 * r, h - 2 * r, 270)
        ):
            self.create_arc(
                x, y, x + 2 * r, y + 2 * r, start=start, extent=90,
                fill=c, outline=c, style="pieslice",
            )
        self.create_text(w // 2, h // 2, text=self.label, fill=self._fg(), font=self.font)


def _is_dark() -> bool:
    """读 macOS 的界面样式（深色返回 'Dark'，浅色无此键会报错）。"""
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=3,
        )
        return out.stdout.strip().lower() == "dark"
    except Exception:
        return False


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class App:
    def __init__(self, root: tk.Tk, dnd_enabled: bool = False) -> None:
        self.root = root
        self.dnd_enabled = dnd_enabled
        self.pal = DARK if _is_dark() else LIGHT

        self.root.title(f"{APP_NAME} - {APP_SUBTITLE}")
        self.root.geometry("840x620")
        self.root.minsize(780, 540)
        self.root.configure(background=self.pal["bg"])

        self.files: list[str] = []
        self.recent: list[str] = []
        self.out_dir = tk.StringVar()
        self.resolution = tk.StringVar(value="1280x720")
        self.fps = tk.IntVar(value=1)
        self.crf = tk.IntVar(value=18)
        self.audio_codec = tk.StringVar(value="auto")
        self.count_text = tk.StringVar(value="尚未添加文件")
        self.status = tk.StringVar(value="准备就绪")

        self.log_q: queue.Queue = queue.Queue()
        self.running = False
        self.pages: dict[str, tk.Frame] = {}
        self.nav_items: dict[str, tk.Label] = {}
        self.page = "convert"

        self._init_fonts()
        self._build_shell()
        if dnd_enabled:
            self._enable_dnd()
        if sys.platform == "darwin":
            try:
                # 封装成 .app 后，把音频拖到 Dock 图标 / 访达“打开方式”会触发 odoc 事件
                self.root.createcommand("::tk::mac::OpenDocument", self._on_open_document)
            except Exception:
                pass
        self.root.after(100, self._poll_log)

    # ---------- 字体 ----------
    def _init_fonts(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        self.f_title = base.copy(); self.f_title.configure(size=15, weight="bold")
        self.f_nav = base.copy(); self.f_nav.configure(size=13)
        self.f_sub = base.copy(); self.f_sub.configure(size=11)
        self.f_drop = base.copy(); self.f_drop.configure(size=14)
        self.f_btn = base.copy(); self.f_btn.configure(size=12)

    # ---------- 外壳：侧边栏 + 内容区 ----------
    def _build_shell(self) -> None:
        side = tk.Frame(self.root, bg=self.pal["sidebar"], width=180)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        tk.Label(side, text=APP_NAME, bg=self.pal["sidebar"], fg=self.pal["text"],
                 font=self.f_title).pack(anchor="w", padx=18, pady=(20, 0))
        tk.Label(side, text=APP_SUBTITLE, bg=self.pal["sidebar"], fg=self.pal["gray"],
                 font=self.f_sub).pack(anchor="w", padx=18, pady=(0, 18))

        for key, text in (
            ("convert", "转换"),
            ("settings", "输出设置"),
            ("recent", "最近转换"),
            ("log", "日志"),
            ("about", "关于"),
        ):
            self._nav_item(side, text, key)

        self.content = tk.Frame(self.root, bg=self.pal["bg"])
        self.content.pack(side="left", fill="both", expand=True)

        self._build_convert()
        self._build_settings()
        self._build_recent()
        self._build_log()
        self._build_about()
        self.show_page("convert")

    def _nav_item(self, parent: tk.Frame, text: str, key: str) -> None:
        lbl = tk.Label(
            parent, text=text, bg=self.pal["sidebar"], fg=self.pal["text"],
            font=self.f_nav, anchor="w", padx=18, pady=10, cursor="pointinghand",
        )
        lbl.pack(fill="x")
        lbl.bind("<Button-1>", lambda e: self.show_page(key))
        lbl.bind("<Enter>", lambda e: self._nav_hover(lbl, key, True))
        lbl.bind("<Leave>", lambda e: self._nav_hover(lbl, key, False))
        self.nav_items[key] = lbl

    def _nav_hover(self, lbl: tk.Label, key: str, entering: bool) -> None:
        if self.page == key:
            return
        lbl.config(bg=self.pal["hover"] if entering else self.pal["sidebar"])

    def show_page(self, key: str) -> None:
        self.page = key
        for k, lbl in self.nav_items.items():
            if k == key:
                lbl.config(bg=self.pal["accent"], fg="#ffffff")
            else:
                lbl.config(bg=self.pal["sidebar"], fg=self.pal["text"])
        for k, frame in self.pages.items():
            if k == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    def _page(self, key: str) -> tk.Frame:
        f = tk.Frame(self.content, bg=self.pal["bg"])
        self.pages[key] = f
        return f

    # ---------- 转换页 ----------
    def _build_convert(self) -> None:
        p = self._page("convert")

        self.drop_zone = tk.Frame(
            p, bg=self.pal["card"], highlightthickness=1,
            highlightbackground=self.pal["border"], height=150,
        )
        self.drop_zone.pack(fill="x", padx=24, pady=(22, 14))
        self.drop_zone.pack_propagate(False)
        inner = tk.Frame(self.drop_zone, bg=self.pal["card"])
        inner.place(relx=0.5, rely=0.5, anchor="center")
        dz = "把音频拖到这里" if self.dnd_enabled else "添加音频开始"
        tk.Label(inner, text=dz, bg=self.pal["card"], fg=self.pal["text"],
                 font=self.f_drop).pack()
        tk.Label(inner, text="支持 mp3 / wav / flac / ncm / qmc / mflac 等",
                 bg=self.pal["card"], fg=self.pal["gray"],
                 font=self.f_sub).pack(pady=(6, 0))

        bar = tk.Frame(p, bg=self.pal["bg"])
        bar.pack(fill="x", padx=24)
        for txt, cmd, w in (
            ("添加文件", self._add_files, 88),
            ("添加文件夹", self._add_folder, 104),
            ("移除", self._remove_selected, 68),
            ("清空", self._clear, 68),
        ):
            FlatButton(bar, text=txt, command=cmd, pal=self.pal, font=self.f_btn,
                       width=w).pack(side="left", padx=(0, 8))
        tk.Label(bar, textvariable=self.count_text, bg=self.pal["bg"],
                 fg=self.pal["gray"], font=self.f_sub).pack(side="right")

        wrap = tk.Frame(p, bg=self.pal["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=(8, 12))
        self.tree = ttk.Treeview(
            wrap, columns=("name", "size"), show="headings", height=8
        )
        self.tree.heading("name", text="文件")
        self.tree.heading("size", text="大小")
        self.tree.column("name", width=460, anchor="w")
        self.tree.column("size", width=90, anchor="e")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bottom = tk.Frame(p, bg=self.pal["bg"])
        bottom.pack(fill="x", padx=24, pady=(0, 20))
        self.start_btn = FlatButton(
            bottom, text="开始转换", command=self._start, pal=self.pal,
            font=self.f_btn, kind="primary", width=240, height=40, radius=9,
        )
        self.start_btn.pack(fill="x")
        self.prog = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.prog.pack(fill="x", pady=(10, 6))
        tk.Label(bottom, textvariable=self.status, bg=self.pal["bg"],
                 fg=self.pal["gray"], font=self.f_sub).pack(anchor="w")

    # ---------- 设置页 ----------
    def _build_settings(self) -> None:
        p = self._page("settings")
        form = tk.Frame(p, bg=self.pal["bg"])
        form.pack(fill="x", padx=24, pady=24)

        def row(label: str, widget_builder, y: int) -> None:
            tk.Label(form, text=label, bg=self.pal["bg"], fg=self.pal["text"],
                     font=self.f_nav).grid(row=y, column=0, sticky="w", pady=10)
            widget_builder().grid(row=y, column=1, sticky="w", padx=14)

        f0 = tk.Frame(form, bg=self.pal["bg"])
        row("输出目录", lambda: f0, 0)
        ttk.Entry(f0, textvariable=self.out_dir, width=34).pack(side="left")
        FlatButton(f0, text="选择…", command=self._choose_out, pal=self.pal,
                   font=self.f_btn, width=76).pack(side="left", padx=8)

        f1 = tk.Frame(form, bg=self.pal["bg"])
        row("分辨率", lambda: f1, 1)
        ttk.Entry(f1, textvariable=self.resolution, width=12).pack(side="left")
        tk.Label(f1, text="帧率", bg=self.pal["bg"], fg=self.pal["text"],
                 font=self.f_nav).pack(side="left", padx=(16, 6))
        ttk.Spinbox(f1, from_=1, to=30, textvariable=self.fps, width=5).pack(side="left")
        tk.Label(f1, text="（静态黑屏用 1 即可）", bg=self.pal["bg"],
                 fg=self.pal["gray"], font=self.f_sub).pack(side="left", padx=10)

        f2 = tk.Frame(form, bg=self.pal["bg"])
        row("音频", lambda: f2, 2)
        ttk.Combobox(f2, textvariable=self.audio_codec, width=8,
                     values=["auto", "copy", "aac"], state="readonly").pack(side="left")
        tk.Label(f2, text="auto＝推荐上传：mp3/aac 原样，其余（flac/ncm 等）自动转 AAC",
                 bg=self.pal["bg"], fg=self.pal["gray"],
                 font=self.f_sub).pack(side="left", padx=10)

        f3 = tk.Frame(form, bg=self.pal["bg"])
        row("视频质量", lambda: f3, 3)
        ttk.Spinbox(f3, from_=0, to=51, textvariable=self.crf, width=5).pack(side="left")
        tk.Label(f3, text="CRF 0–51，越小越清晰越大（黑屏下 18 已近无损）",
                 bg=self.pal["bg"], fg=self.pal["gray"],
                 font=self.f_sub).pack(side="left", padx=10)

    # ---------- 最近转换页 ----------
    def _build_recent(self) -> None:
        p = self._page("recent")
        head = tk.Frame(p, bg=self.pal["bg"])
        head.pack(fill="x", padx=24, pady=(22, 10))
        tk.Label(head, text="最近转换", bg=self.pal["bg"], fg=self.pal["text"],
                 font=self.f_title).pack(side="left")
        FlatButton(head, text="在访达中显示", command=self._reveal, pal=self.pal,
                   font=self.f_btn, width=112).pack(side="right")
        FlatButton(head, text="清空记录", command=self._clear_recent, pal=self.pal,
                   font=self.f_btn, width=88).pack(side="right", padx=(0, 8))

        wrap = tk.Frame(p, bg=self.pal["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self.recent_box = tk.Listbox(
            wrap, bg=self.pal["card"], fg=self.pal["text"], relief="flat",
            borderwidth=0, highlightthickness=0, font=self.f_sub,
            selectbackground=self.pal["accent"],
        )
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.recent_box.yview)
        self.recent_box.configure(yscrollcommand=sb.set)
        self.recent_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------- 日志页 ----------
    def _build_log(self) -> None:
        p = self._page("log")
        head = tk.Frame(p, bg=self.pal["bg"])
        head.pack(fill="x", padx=24, pady=(22, 10))
        tk.Label(head, text="日志", bg=self.pal["bg"], fg=self.pal["text"],
                 font=self.f_title).pack(side="left")
        FlatButton(head, text="清空", pal=self.pal, font=self.f_btn, width=68,
                   command=lambda: self.log_text.delete("1.0", "end")).pack(side="right")

        wrap = tk.Frame(p, bg=self.pal["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self.log_text = tk.Text(
            wrap, bg=self.pal["card"], fg=self.pal["text"], relief="flat",
            borderwidth=0, highlightthickness=0, font=self.f_sub,
            insertbackground=self.pal["text"],
        )
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------- 关于页 ----------
    def _build_about(self) -> None:
        p = self._page("about")
        box = tk.Frame(p, bg=self.pal["bg"])
        box.pack(fill="both", expand=True, padx=24, pady=24)
        tk.Label(box, text=APP_NAME, bg=self.pal["bg"], fg=self.pal["text"],
                 font=self.f_title).pack(anchor="w")
        tk.Label(box, text=f"{APP_SUBTITLE} · 版本 {VERSION}", bg=self.pal["bg"],
                 fg=self.pal["gray"], font=self.f_nav).pack(anchor="w", pady=(2, 8))
        try:
            ffmpeg = find_ffmpeg(None)
        except RuntimeError as e:
            ffmpeg = str(e)
        for line in (
            "把音频转成可上传视频平台的 MP4，支持私有加密格式直转。",
            "",
            f"ffmpeg：{ffmpeg}",
            f"支持格式：{len(AUDIO_EXTS)} 种（含网易云 .ncm、QQ 音乐 .qmc*/.mflac*/.mgg*）",
            "拖拽支持：tkinterdnd2" if self.dnd_enabled else "拖拽支持：未安装 tkinterdnd2",
            "",
            "转换核心与解密模块均为纯标准库实现，文件全部在本机处理。",
        ):
            tk.Label(box, text=line, bg=self.pal["bg"],
                     fg=self.pal["gray"] if line else self.pal["bg"],
                     font=self.f_sub, justify="left").pack(anchor="w", pady=2)

    # ---------- 拖拽 ----------
    def _enable_dnd(self) -> None:
        from tkinterdnd2 import DND_FILES
        for w in (self.drop_zone, self.tree, self.root):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", self._on_drop)
            w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            w.dnd_bind("<<DropLeave>>", self._on_drop_leave)

    def _on_drop_enter(self, event) -> None:
        self.drop_zone.config(
            highlightbackground=self.pal["accent"], highlightthickness=2
        )

    def _on_drop_leave(self, event) -> None:
        self.drop_zone.config(
            highlightbackground=self.pal["border"], highlightthickness=1
        )

    def _on_drop(self, event) -> None:
        self._on_drop_leave(event)
        try:
            raw = self.root.tk.splitlist(event.data)
        except tk.TclError:
            raw = [event.data]

        paths: list[str] = []
        for item in raw:
            p = str(item).strip().strip("{}")
            if p.startswith("file://"):
                p = urllib.parse.unquote(p[7:])
            if not p:
                continue
            if os.path.isdir(p):
                for name in sorted(os.listdir(p)):
                    if name.lower().endswith(AUDIO_EXTS):
                        paths.append(os.path.join(p, name))
            elif os.path.isfile(p) and p.lower().endswith(AUDIO_EXTS):
                paths.append(p)

        if not paths:
            self._log("[拖拽] 没有识别到音频文件")
            return
        before = len(self.files)
        self._append_files(paths)
        self._log(f"[拖拽] 已加入 {len(self.files) - before} 个文件")

    # ---------- 输入操作 ----------
    def _on_open_document(self, paths) -> None:
        """macOS odoc 事件：拖到 Dock 图标 / 访达“打开方式”。paths 是路径元组。"""
        files = [
            str(p) for p in (paths or ())
            if os.path.isfile(str(p)) and str(p).lower().endswith(AUDIO_EXTS)
        ]
        if not files:
            return
        before = len(self.files)
        self._append_files(files)
        self.show_page("convert")
        self._log(f"[打开] 已加入 {len(self.files) - before} 个文件")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("音频", " ".join("*" + e for e in AUDIO_EXTS)),
                       ("所有文件", "*.*")],
        )
        if paths:
            self._append_files(list(paths))

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(title="选择包含音频的文件夹")
        if not d:
            return
        found = [
            os.path.join(d, n) for n in sorted(os.listdir(d))
            if n.lower().endswith(AUDIO_EXTS)
        ]
        if not found:
            messagebox.showinfo("提示", "该文件夹下未找到音频文件。")
            return
        self._append_files(found)

    def _append_files(self, paths: list[str]) -> None:
        for p in paths:
            if p in self.files:
                continue
            self.files.append(p)
            try:
                size = _human_size(os.path.getsize(p))
            except OSError:
                size = "-"
            self.tree.insert("", "end", values=(p, size))
        if self.files and not self.out_dir.get():
            self.out_dir.set(os.path.dirname(self.files[0]))
        self._refresh_count()

    def _refresh_count(self) -> None:
        n = len(self.files)
        self.count_text.set("尚未添加文件" if n == 0 else f"共 {n} 个文件")

    def _remove_selected(self) -> None:
        for iid in self.tree.selection():
            p = self.tree.set(iid, "name")
            if p in self.files:
                self.files.remove(p)
            self.tree.delete(iid)
        self._refresh_count()

    def _clear(self) -> None:
        self.files.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._refresh_count()

    def _choose_out(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_dir.set(d)

    def _reveal(self) -> None:
        sel = self.recent_box.curselection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一个文件。")
            return
        path = self.recent_box.get(sel[0])
        if os.path.exists(path):
            subprocess.run(["open", "-R", path])

    def _clear_recent(self) -> None:
        self.recent.clear()
        self.recent_box.delete(0, "end")

    def _log(self, msg: str) -> None:
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    # ---------- 转换 ----------
    def _start(self) -> None:
        if self.running:
            return
        if not self.files:
            messagebox.showwarning("提示", "请先添加音频文件。")
            return
        try:
            ffmpeg = find_ffmpeg(None)
        except RuntimeError as e:
            messagebox.showerror("缺少 ffmpeg", str(e))
            return
        try:
            w, h = (int(x) for x in self.resolution.get().lower().split("x"))
        except ValueError:
            messagebox.showerror("格式错误", "分辨率应为 WxH，例如 1280x720。")
            return

        out_dir = self.out_dir.get() or os.path.dirname(self.files[0])
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("无法创建输出目录", str(e))
            return

        self.running = True
        self.start_btn.set_enabled(False)
        self.prog["value"] = 0
        self.status.set(f"开始处理 {len(self.files)} 个文件")
        self._log(f"开始处理 {len(self.files)} 个文件，输出到 {out_dir}")

        threading.Thread(
            target=self._worker,
            args=(ffmpeg, out_dir, w, h, self.fps.get(),
                  self.crf.get(), self.audio_codec.get()),
            daemon=True,
        ).start()

    def _worker(self, ffmpeg, out_dir, w, h, fps, crf, codec) -> None:
        ok = total = 0
        n = len(self.files)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(4, os.cpu_count() or 2)
        ) as ex:
            futs = {}
            used: set[str] = set()
            for f in self.files:
                # 同名不同格式、或不同专辑里的同名曲目会撞成同一个输出，这里加序号区分
                out = unique_output(
                    os.path.join(out_dir, os.path.basename(default_output(f))), used
                )
                futs[ex.submit(
                    convert_one, ffmpeg, f, out, w, h, fps, crf, codec, True,
                    lambda m: self.log_q.put(m),
                )] = out
            for fut in concurrent.futures.as_completed(futs):
                if fut.result():
                    ok += 1
                    self.log_q.put(("__OUT__", futs[fut]))
                total += 1
                self.log_q.put(("__PROG__", total, n))
        self.log_q.put(("__DONE__", ok, total))

    def _poll_log(self) -> None:
        try:
            while True:
                item = self.log_q.get_nowait()
                if isinstance(item, tuple):
                    if item[0] == "__DONE__":
                        _, ok, total = item
                        self.prog["value"] = 100
                        self.status.set(f"完成：成功 {ok} / 共 {total}")
                        self._log(f"\n全部完成：成功 {ok}/{total}")
                        self.running = False
                        self.start_btn.set_enabled(True)
                    elif item[0] == "__PROG__":
                        _, done, n = item
                        self.prog["value"] = done / n * 100 if n else 0
                        self.status.set(f"正在转换 {done}/{n}")
                    elif item[0] == "__OUT__":
                        path = item[1]
                        self.recent.append(path)
                        self.recent_box.insert("end", path)
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)


def main() -> None:
    dnd = False
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        dnd = True
    except Exception as e:  # 没装 tkinterdnd2 或 tkdnd 加载失败 -> 降级，按钮照常可用
        root = tk.Tk()
        print(f"[提示] 拖拽不可用（{e}），已降级为按钮添加。")
    App(root, dnd)
    root.mainloop()


if __name__ == "__main__":
    main()
