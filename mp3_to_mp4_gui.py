#!/usr/bin/env python3
"""
mp3_to_mp4_gui.py — KTrans 图形界面（Tkinter）。

左侧导航（转换 / 最近转换 / 日志 / 关于）+ 顶部设置卡 + 文件列表，
列表里没有文件时，整块区域本身就是拖放区。转换逻辑全部来自 mp3_to_mp4.py：
纯黑屏、封面模式、纯音频三种产出，以及 NCM / QMC 解密都在那边。

界面参考 Filee（KnifeLemon/Filee）：留白大、文字少、胶囊导航。
macOS 说明：Aqua 主题会接管 ttk 控件的 padding/背景/悬停态，style.configure() 基本无效，
所以导航、卡片、按钮这些"自定义外观"的部分用原生 tk 控件 / Canvas 自绘（背景前景可控），
输入框、进度条、列表仍用 ttk（本身就是 NSTextField 一类，跟着系统深浅色走）。
深浅色通过读系统的 AppleInterfaceStyle 判断，用两套苹果系统配色。
拖拽依赖 tkinterdnd2（可选，缺失时自动降级为按钮添加）；
Dock 拖放/访达打开方式由 OdocWatcher 用 Carbon AppleEvent 实现（Tk 9 没有内置支持）。

依赖: Python 标准库 + 系统 ffmpeg（转换逻辑见 mp3_to_mp4.py）。
"""

import concurrent.futures
import glob
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
from tkinter import filedialog, messagebox, ttk

from mp3_to_mp4 import convert_one, default_output, find_ffmpeg, unique_output

APP_NAME = "ktrans"
APP_SUBTITLE = "音乐格式转换器"
VERSION = "2.03"

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


class Card(tk.Canvas):
    """圆角卡片容器。

    Aqua 下 tk.Frame 没法圆角，这里用 Canvas 自绘圆角矩形当背景，
    内容 Frame 内缩放置，四个角由卡片底色露出（与 FlatButton 同一画法）。
    """

    def __init__(self, master, pal: dict, radius: int = 12,
                 fill: str | None = None, height: int | None = None,
                 auto_height: bool = True) -> None:
        super().__init__(master, bg=pal["bg"], bd=0, highlightthickness=0,
                         height=height or 1)
        self.pal = pal
        self.radius = radius
        self.fill_color = fill or pal["card"]
        self.inner = tk.Frame(self, bg=self.fill_color)
        self._win = self.create_window(0, 0, window=self.inner, anchor="nw")
        self.bind("<Configure>", lambda e: self._layout())
        if height is None and auto_height:
            self.inner.bind("<Configure>", lambda e: self._auto_height())

    def _layout(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("cardbg")
        r = min(self.radius, w // 2, h // 2)
        c = self.fill_color
        self.create_rectangle(r, 0, w - r, h, fill=c, outline=c, tags="cardbg")
        self.create_rectangle(0, r, w, h - r, fill=c, outline=c, tags="cardbg")
        for x, y, start in (
            (0, 0, 90), (w - 2 * r, 0, 0), (0, h - 2 * r, 180), (w - 2 * r, h - 2 * r, 270)
        ):
            self.create_arc(x, y, x + 2 * r, y + 2 * r, start=start, extent=90,
                            fill=c, outline=c, style="pieslice", tags="cardbg")
        self.tag_lower("cardbg")
        inset = r // 2 + 3
        self.coords(self._win, inset, inset)
        self.itemconfigure(self._win, width=w - 2 * inset, height=h - 2 * inset)

    def _auto_height(self) -> None:
        """内容驱动高度（用于固定内容卡片；expand 型卡片不触发明显抖动）。"""
        req = self.inner.winfo_reqheight() + self.radius + 6
        if abs(req - self.winfo_height()) > 2:
            self.configure(height=req)

    def set_ring(self, color: str | None) -> None:
        """拖拽悬停反馈：画/擦一圈圆角描边。"""
        self.delete("ring")
        if not color:
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self.radius, w // 2, h // 2) - 1
        self.create_line(r, 1, w - r, 1, fill=color, width=2, tags="ring")
        self.create_line(r, h - 1, w - r, h - 1, fill=color, width=2, tags="ring")
        self.create_line(1, r, 1, h - r, fill=color, width=2, tags="ring")
        self.create_line(w - 1, r, w - 1, h - r, fill=color, width=2, tags="ring")
        for x, y, start in (
            (1, 1, 90), (w - 2 * r - 1, 1, 0), (1, h - 2 * r - 1, 180),
            (w - 2 * r - 1, h - 2 * r - 1, 270)
        ):
            self.create_arc(x, y, x + 2 * r, y + 2 * r, start=start, extent=90,
                            outline=color, width=2, style="arc", tags="ring")


class NavItem(tk.Canvas):
    """侧边栏导航项：单色图标 + 文字，选中/悬停画圆角胶囊底（macOS 侧栏风格）。"""

    def __init__(self, master, icon: str, text: str, command,
                 pal: dict, font, height: int = 34) -> None:
        super().__init__(master, height=height, bg=pal["sidebar"],
                         bd=0, highlightthickness=0, cursor="pointinghand")
        self.pal = pal
        self.icon = icon
        self.text = text
        self.command = command
        self.font = font
        self.selected = False
        self.hovered = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.command())

    def _set_hover(self, v: bool) -> None:
        self.hovered = v
        self._draw()

    def set_selected(self, v: bool) -> None:
        self.selected = v
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        if self.selected or self.hovered:
            c = self.pal["accent"] if self.selected else self.pal["hover"]
            r = 7
            x0, y0, x1, y1 = 8, 2, w - 8, h - 2
            self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=c, outline=c)
            self.create_rectangle(x0, y0 + r, x1, y1 - r, fill=c, outline=c)
            for x, y, start in (
                (x0, y0, 90), (x1 - 2 * r, y0, 0), (x0, y1 - 2 * r, 180),
                (x1 - 2 * r, y1 - 2 * r, 270)
            ):
                self.create_arc(x, y, x + 2 * r, y + 2 * r, start=start,
                                extent=90, fill=c, outline=c, style="pieslice")
        fg = "#ffffff" if self.selected else self.pal["text"]
        if not self.selected and self.hovered:
            fg = self.pal["text"]
        self.create_text(20, h // 2, text=self.icon, fill=fg,
                         font=self.font, anchor="w")
        self.create_text(40, h // 2, text=self.text, fill=fg,
                         font=self.font, anchor="w")


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


class OdocWatcher:
    """接管 macOS 的「打开文档」AppleEvent（odoc），用于 Dock 拖放 / 访达「打开方式」。

    为什么不用 Tk 的 `::tk::mac::OpenDocuments`：**Tk 9.0 这个构建里根本没有该命令**
    （`info commands ::tk::mac::*` 只有 DoScriptFile / macOSVersion 等几个），
    Tk 8.6 时代的那套 odoc 集成在这版 Tk 里不存在。所以直接用 ctypes 调 Carbon 的
    AppleEvent API，自己收事件——思路与 py2app 的 argv_emulation 相同，
    区别是这里常驻，App 已经在跑时再拖文件也能收到。

    用法：install() 挂处理器 → Tk 里每 200ms 调一次 pump() 处理积压事件。
    """

    kCoreEventClass = 0x61657674         # 'aevt'
    kAEOpenDocuments = 0x6F646F63        # 'odoc'
    keyDirectObject = 0x2D2D2D2D         # '----'
    typeAEList = 0x6C697374              # 'list'
    typeFSRef = 0x66737266               # 'fsrf'
    typeChar = 0x54455854                # 'TEXT'
    kEventClassAppleEvent = 0x65707063   # 'eppc'
    kEventAppleEvent = 1

    def __init__(self, callback) -> None:
        self.callback = callback
        self.ok = False
        self._carbon = None
        self._handler = None

    # ---- 安装 ----
    def install(self) -> bool:
        if sys.platform != "darwin" or self.ok:
            return self.ok
        try:
            import ctypes

            class AEDesc(ctypes.Structure):
                _fields_ = [("descKey", ctypes.c_int),
                            ("descContent", ctypes.c_void_p)]

            class EventTypeSpec(ctypes.Structure):
                _fields_ = [("eventClass", ctypes.c_int),
                            ("eventKind", ctypes.c_uint)]

            self.ctypes = ctypes
            self.AEDesc = AEDesc
            self.EventTypeSpec = EventTypeSpec

            carbon = ctypes.CDLL(
                "/System/Library/Frameworks/Carbon.framework/Carbon")
            self._callback_t = ctypes.CFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
            carbon.AEInstallEventHandler.argtypes = [
                ctypes.c_int, ctypes.c_int, self._callback_t,
                ctypes.c_void_p, ctypes.c_char]
            carbon.AEProcessEvent.restype = ctypes.c_int
            carbon.AEProcessEvent.argtypes = [ctypes.c_void_p]
            carbon.ReceiveNextEvent.restype = ctypes.c_int
            carbon.ReceiveNextEvent.argtypes = [
                ctypes.c_long, ctypes.POINTER(EventTypeSpec), ctypes.c_double,
                ctypes.c_char, ctypes.POINTER(ctypes.c_void_p)]
            carbon.AEGetParamDesc.restype = ctypes.c_int
            carbon.AEGetParamDesc.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(AEDesc)]
            carbon.AECountItems.restype = ctypes.c_int
            carbon.AECountItems.argtypes = [
                ctypes.POINTER(AEDesc), ctypes.POINTER(ctypes.c_long)]
            carbon.AEGetNthDesc.restype = ctypes.c_int
            carbon.AEGetNthDesc.argtypes = [
                ctypes.c_void_p, ctypes.c_long, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_void_p]
            carbon.AEGetDescDataSize.restype = ctypes.c_int
            carbon.AEGetDescDataSize.argtypes = [ctypes.POINTER(AEDesc)]
            carbon.AEGetDescData.restype = ctypes.c_int
            carbon.AEGetDescData.argtypes = [
                ctypes.POINTER(AEDesc), ctypes.c_void_p, ctypes.c_int]
            carbon.FSRefMakePath.restype = ctypes.c_int
            carbon.FSRefMakePath.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]

            self._carbon = carbon
            self._handler = self._callback_t(self._on_ae)
            carbon.AEInstallEventHandler(
                self.kCoreEventClass, self.kAEOpenDocuments,
                self._handler, 0, b"\0")
            self.ok = True
        except Exception:
            self.ok = False
        return self.ok

    # ---- AE 回调 ----
    def _on_ae(self, message, reply, refcon):
        ctypes = self.ctypes
        paths: list[str] = []
        try:
            desc = self.AEDesc()
            if self._carbon.AEGetParamDesc(
                    message, self.keyDirectObject, self.typeAEList,
                    ctypes.byref(desc)) == 0:
                count = ctypes.c_long()
                if self._carbon.AECountItems(ctypes.byref(desc),
                                             ctypes.byref(count)) == 0:
                    for i in range(count.value):
                        item = self.AEDesc()
                        # 先按 FSRef 取，失败再按 TEXT 取（不同来源类型不同）
                        for typ in (self.typeFSRef, self.typeChar):
                            if self._carbon.AEGetNthDesc(
                                    ctypes.byref(desc), i + 1, typ, 0,
                                    ctypes.byref(item)) != 0:
                                continue
                            size = self._carbon.AEGetDescDataSize(
                                ctypes.byref(item))
                            if size <= 0:
                                continue
                            buf = ctypes.create_string_buffer(size)
                            if self._carbon.AEGetDescData(
                                    ctypes.byref(item), buf, size) != 0:
                                continue
                            if typ == self.typeFSRef:
                                pbuf = ctypes.create_string_buffer(1024)
                                if self._carbon.FSRefMakePath(
                                        ctypes.byref(buf), pbuf, 1023) == 0:
                                    paths.append(pbuf.value.decode("utf-8"))
                            else:
                                paths.append(buf.value.decode("utf-8"))
                            break
        except Exception:
            pass
        if paths:
            try:
                self.callback(paths)
            except Exception:
                pass
        return 0

    # ---- 事件泵 ----
    def pump(self) -> None:
        if not self.ok:
            return
        ctypes = self.ctypes
        spec = self.EventTypeSpec(self.kEventClassAppleEvent,
                                  self.kEventAppleEvent)
        while True:
            evt = ctypes.c_void_p()
            st = self._carbon.ReceiveNextEvent(
                1, ctypes.byref(spec), 0.0, b"\1", ctypes.byref(evt))
            if st != 0:      # 没事件了（-9875 超时）或出错
                break
            if self._carbon.AEProcessEvent(evt) != 0:
                break


class App:
    def __init__(self, root: tk.Tk, dnd_enabled: bool = False,
                 odoc: "OdocWatcher | None" = None,
                 pending: list[str] | None = None) -> None:
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
        self.out_format = tk.StringVar(value="mp4")
        self.video_mode = tk.StringVar(value="纯黑")
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
            # 封装成 .app 后，把音频拖到 Dock 图标 / 访达“打开方式”会触发 odoc 事件。
            # Tk 9 里没有 ::tk::mac::OpenDocuments（Tk 8.6 才有），改用自己挂的
            # Carbon AppleEvent 处理器，详见 OdocWatcher 的注释。
            # odoc 可能在 main() 里更早就装好了（抢冷启动事件），这里接管回调即可。
            self.odoc = odoc or OdocWatcher(self._on_open_document)
            self.odoc.callback = self._on_open_document
            self.odoc.install()
            if pending:
                self._on_open_document(pending)
            self.odoc.pump()
            extra = [a for a in sys.argv[1:]
                     if a.lower().endswith(AUDIO_EXTS) and os.path.isfile(a)]
            if extra:
                self._on_open_document(extra)
            self.root.after(300, self._pump_odoc)
        self.root.after(100, self._poll_log)

    # ---------- 字体 ----------
    def _init_fonts(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        self.f_title = base.copy(); self.f_title.configure(size=15, weight="bold")
        self.f_nav = base.copy(); self.f_nav.configure(size=13)
        self.f_sub = base.copy(); self.f_sub.configure(size=11)
        self.f_drop = base.copy(); self.f_drop.configure(size=14)
        self.f_empty = base.copy(); self.f_empty.configure(size=16)
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

        for key, icon, text in (
            ("convert", "⇄", "转换"),
            ("recent", "◷", "最近转换"),
            ("log", "≡", "日志"),
            ("about", "ⓘ", "关于"),
        ):
            item = NavItem(side, icon, text, lambda k=key: self.show_page(k),
                           self.pal, self.f_nav)
            item.pack(fill="x", padx=2, pady=1)
            self.nav_items[key] = item

        self.content = tk.Frame(self.root, bg=self.pal["bg"])
        self.content.pack(side="left", fill="both", expand=True)

        self._build_convert()
        self._build_recent()
        self._build_log()
        self._build_about()
        self.show_page("convert")

    def show_page(self, key: str) -> None:
        self.page = key
        for k, item in self.nav_items.items():
            item.set_selected(k == key)
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

        # 顶部：设置卡片（原“输出设置”页并入主页）
        self._build_settings_card(p)

        # 中部：文件列表卡片（空状态与列表共用同一块区域，拖放目标就是它）
        self.list_card = Card(p, self.pal, radius=12, auto_height=False)
        self.list_card.pack(fill="both", expand=True, padx=20, pady=(4, 10))
        inner = self.list_card.inner

        bar = tk.Frame(inner, bg=self.pal["card"])
        bar.pack(fill="x", padx=8, pady=(8, 4))
        for txt, cmd, w in (
            ("添加文件", self._add_files, 88),
            ("添加文件夹", self._add_folder, 104),
            ("移除", self._remove_selected, 68),
            ("清空", self._clear, 68),
        ):
            FlatButton(bar, text=txt, command=cmd, pal=self.pal, font=self.f_btn,
                       width=w, height=28, bg=self.pal["card"]).pack(side="left", padx=(0, 8))
        tk.Label(bar, textvariable=self.count_text, bg=self.pal["card"],
                 fg=self.pal["gray"], font=self.f_sub).pack(side="right", padx=4)

        # 卡片主体：列表 与 空状态 二选一占位
        self.list_body = tk.Frame(inner, bg=self.pal["card"])
        self.list_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # —— 有文件时：列表 ——
        self.tree_wrap = tk.Frame(self.list_body, bg=self.pal["card"])
        self.tree = ttk.Treeview(
            self.tree_wrap, columns=("name", "size"), show="headings", height=8
        )
        self.tree.heading("name", text="文件")
        self.tree.heading("size", text="大小")
        self.tree.column("name", width=460, anchor="w")
        self.tree.column("size", width=90, anchor="e")
        sb = ttk.Scrollbar(self.tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # —— 没有文件时：空状态（本身就是拖放区） ——
        self._build_empty_state()
        self._sync_empty_state()

        # 底部：开始按钮（固定宽度居中，不再整行铺满）+ 进度 + 状态
        bottom = tk.Frame(p, bg=self.pal["bg"])
        bottom.pack(fill="x", padx=20, pady=(0, 16))
        self.start_btn = FlatButton(
            bottom, text="开始转换", command=self._start, pal=self.pal,
            font=self.f_btn, kind="primary", width=220, height=36, radius=9,
        )
        self.start_btn.pack()
        self.prog = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.prog.pack(fill="x", pady=(10, 4))
        tk.Label(bottom, textvariable=self.status, bg=self.pal["bg"],
                 fg=self.pal["gray"], font=self.f_sub).pack(anchor="w")

    # ---------- 空状态（列表区域的占位形态） ----------
    def _build_empty_state(self) -> None:
        """列表为空时，整个列表区域变成一个大拖放区：图标 + 主文案 + 副文案。"""
        self.empty = tk.Frame(self.list_body, bg=self.pal["card"])
        box = tk.Frame(self.empty, bg=self.pal["card"])
        box.place(relx=0.5, rely=0.5, anchor="center")

        # 圆环 + 下箭头（Canvas 自绘，避免依赖图片资源）
        self.empty_icon = tk.Canvas(box, width=64, height=64, bd=0,
                                    highlightthickness=0, bg=self.pal["card"])
        self.empty_icon.pack()
        c = self.pal["accent"]
        self.empty_icon.create_oval(7, 7, 57, 57, outline=c, width=2)
        self.empty_icon.create_line(32, 21, 32, 43, fill=c, width=2)
        self.empty_icon.create_line(22, 34, 32, 44, 42, 34, fill=c, width=2,
                                    capstyle="round", joinstyle="round")

        self.empty_title = tk.Label(
            box, text="把音频拖到这里" if self.dnd_enabled else "添加音频开始",
            bg=self.pal["card"], fg=self.pal["text"], font=self.f_empty,
        )
        self.empty_title.pack(pady=(6, 0))
        self.empty_hint = tk.Label(
            box, text="或点击上方「添加文件」· 支持 mp3 / wav / flac / ncm / qmc / mflac 等",
            bg=self.pal["card"], fg=self.pal["gray"], font=self.f_sub,
        )
        self.empty_hint.pack(pady=(6, 0))

    def _sync_empty_state(self) -> None:
        """列表区在「空状态」与「文件列表」之间切换，同一时刻只显示一个。"""
        want_empty = not self.files
        if getattr(self, "_empty_shown", None) == want_empty:
            return
        self._empty_shown = want_empty
        if want_empty:
            self.tree_wrap.pack_forget()
            self.empty.pack(fill="both", expand=True)
        else:
            self.empty.pack_forget()
            self.tree_wrap.pack(fill="both", expand=True)

    # ---------- 设置卡片（并入转换页顶部） ----------
    def _ctl_label(self, parent, text: str, pad=(0, 6)) -> None:
        tk.Label(parent, text=text, bg=self.pal["card"], fg=self.pal["gray"],
                 font=self.f_sub).pack(side="left", padx=pad)

    def _build_settings_card(self, parent) -> None:
        card = Card(parent, self.pal, radius=12)
        card.pack(fill="x", padx=20, pady=(14, 4))
        form = card.inner

        r1 = tk.Frame(form, bg=self.pal["card"])
        r1.pack(fill="x", padx=10, pady=(10, 2))
        self._ctl_label(r1, "输出格式")
        ttk.Combobox(r1, textvariable=self.out_format, width=6,
                     values=["mp4", "mp3", "m4a", "flac", "wav"],
                     state="readonly").pack(side="left")
        self._ctl_label(r1, "音频", pad=(14, 6))
        ttk.Combobox(r1, textvariable=self.audio_codec, width=6,
                     values=["auto", "copy", "aac"], state="readonly").pack(side="left")
        self._ctl_label(r1, "输出目录", pad=(14, 6))
        ttk.Entry(r1, textvariable=self.out_dir, width=18).pack(side="left")
        FlatButton(r1, text="选择…", command=self._choose_out, pal=self.pal,
                   font=self.f_btn, width=64, height=26,
                   bg=self.pal["card"]).pack(side="left", padx=8)

        # 高级行：仅视频（mp4）输出时可见
        self.adv_row = tk.Frame(form, bg=self.pal["card"])
        self._ctl_label(self.adv_row, "画面")
        ttk.Combobox(self.adv_row, textvariable=self.video_mode, width=5,
                     values=["纯黑", "封面"], state="readonly").pack(side="left")
        self._ctl_label(self.adv_row, "分辨率", pad=(12, 6))
        ttk.Entry(self.adv_row, textvariable=self.resolution,
                  width=9).pack(side="left")
        self._ctl_label(self.adv_row, "帧率", pad=(12, 6))
        ttk.Spinbox(self.adv_row, from_=1, to=30, textvariable=self.fps,
                    width=3).pack(side="left")
        self._ctl_label(self.adv_row, "质量", pad=(12, 6))
        ttk.Spinbox(self.adv_row, from_=0, to=51, textvariable=self.crf,
                    width=3).pack(side="left")
        tk.Label(self.adv_row, text="近无损", bg=self.pal["card"],
                 fg=self.pal["gray"], font=self.f_sub).pack(side="left", padx=(12, 0))

        self.out_format.trace_add("write", lambda *_: self._toggle_adv())
        self._toggle_adv()

    def _toggle_adv(self) -> None:
        if self.out_format.get() == "mp4":
            self.adv_row.pack(fill="x", padx=10, pady=(2, 10))
        else:
            self.adv_row.pack_forget()

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
        # 空状态里的子控件覆盖在卡片上，也要注册，否则没文件时拖不进来
        targets = [
            self.list_card, self.list_card.inner, self.list_body,
            self.tree_wrap, self.tree,
            self.empty, self.empty_icon, self.empty_title, self.empty_hint,
            self.root,
        ]
        for w in targets:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
                w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                w.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            except Exception:
                continue

    def _on_drop_enter(self, event) -> None:
        self.list_card.set_ring(self.pal["accent"])

    def _on_drop_leave(self, event) -> None:
        self.list_card.set_ring(None)

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
        # 从 Dock 图标打开时窗口可能在后面，主动提到前台，否则用户以为没反应
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        self._log(f"[打开] 已加入 {len(self.files) - before} 个文件")

    def _pump_odoc(self) -> None:
        """周期性处理 AppleEvent 队列：odoc 事件在这一步被取出并变成加文件。"""
        try:
            self.odoc.pump()
        except Exception:
            pass
        self.root.after(300, self._pump_odoc)

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
        self._sync_empty_state()

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
                  self.crf.get(), self.audio_codec.get(), self.out_format.get(),
                  "cover" if self.video_mode.get() == "封面" else "black"),
            daemon=True,
        ).start()

    def _worker(self, ffmpeg, out_dir, w, h, fps, crf, codec, out_fmt="mp4",
                video_mode="black") -> None:
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
                    os.path.join(out_dir, os.path.basename(default_output(f, out_fmt))), used
                )
                futs[ex.submit(
                    convert_one, ffmpeg, f, out, w, h, fps, crf, codec, True,
                    lambda m: self.log_q.put(m), out_fmt, video_mode,
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


def _setup_frozen() -> None:
    """py2app 独立包里的启动准备：定位 Tcl/Tk 脚本库 + 把输出重定向到日志。

    独立包没有终端，出错时窗口直接闪退、什么都看不到；统一写 /tmp/ktrans.log 便于排查。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        f = open("/tmp/ktrans.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = f
        print(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 ktrans（独立包）====")
    except Exception:
        pass

    # Tcl/Tk 的 .tcl 脚本库在 Contents/Resources/tcl9.0、tk9.0 下，
    # 不告诉 Tk 位置它会报 "Cannot find a usable init.tcl"。
    contents = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    res = os.path.join(contents, "Resources")
    for env, prefix in (("TCL_LIBRARY", "tcl"), ("TK_LIBRARY", "tk")):
        for cand in sorted(glob.glob(os.path.join(res, f"{prefix}[0-9]*"))):
            os.environ.setdefault(env, cand)
            break


def main() -> None:
    _setup_frozen()
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
