"""py2app 打包配置：把 ktrans 打成一个脱离本机 Python 环境的独立 .app。

用法（在项目根目录）：
    .venv-pack/bin/python setup.py py2app            # 生成 dist/ktrans.app（独立，可分发）
    .venv-pack/bin/python setup.py py2app -A         # 别名模式，只在自己的机器上跑（秒级）

产物可直接拖到「应用程序」，也可以压缩后发给别人——对方不需要装 Python。
仍然需要对方机器上有 ffmpeg（转换核心依赖它）。
"""

import os
import plistlib

from setuptools import setup

APP_NAME = "ktrans"
VERSION = "2.12"

# 让 Finder / 拖到 Dock 图标 / “打开方式” 都能把音频交给 App（odoc 事件）
DOC_TYPES = [
    {
        "CFBundleTypeName": "Audio File",
        "CFBundleTypeRole": "Viewer",
        "LSHandlerRank": "Alternate",
        "LSItemContentTypes": [
            "public.audio", "public.mp3", "public.aiff-audio",
            "com.microsoft.waveform-audio", "org.xiph.flac",
            "public.mpeg-4-audio",
        ],
    }
]

OPTIONS = {
    # 不要打开 argv_emulation：它的引导期 AppleEvent 处理器在这个组合
    # （python-build-standalone 3.13 + Tk 9）下会让进程 abort()——实测双击带文件
    # 启动直接崩。冷启动带文件的支持改为在 App 初始化后立刻手动收一次事件队列，
    # 见 mp3_to_mp4_gui.App.__init__ 里的 odoc.pump()。
    "argv_emulation": False,
    "packages": ["tkinterdnd2"],
    "includes": ["tkinter", "mp3_to_mp4", "ncm_decoder", "qmc_decoder"],
    "excludes": [
        "numpy", "scipy", "PIL", "matplotlib", "pandas",
        "pytest", "setuptools", "pkg_resources", "pip",
    ],
    "iconfile": "assets/ktrans.icns" if os.path.exists("assets/ktrans.icns") else None,
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.kliocp.ktrans",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleGetInfoString": f"{APP_NAME} {VERSION} — 音乐格式转换器",
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "CFBundleDocumentTypes": DOC_TYPES,
    },
}
OPTIONS = {k: v for k, v in OPTIONS.items() if v is not None}

setup(
    name=APP_NAME,
    version=VERSION,
    description="音频 → 黑屏视频 / 封面视频，支持 NCM / QMC 解密",
    app=["mp3_to_mp4_gui.py"],
    data_files=[],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
