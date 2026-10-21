#!/usr/bin/env python3
"""make_icon.py — 生成 ktrans 的应用图标（assets/ktrans.icns）。

图标设计：爱乐之城 Seb's 爵士酒吧招牌的视觉语言 —— 深夜蓝底 + 暖金色霓虹灯管音符。
只用 Pillow / numpy 画，不额外装东西；产物经 iconset + iconutil 打成 .icns。

用法：
    python tools/make_icon.py            # 生成 icns + 各尺寸 png
    python tools/make_icon.py --preview  # 另外输出一张预览图到 /tmp（方便肉眼看小尺寸）
"""

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ICONSET = ROOT / "build" / "ktrans.iconset"
OUT_ICNS = ROOT / "assets" / "ktrans.icns"

# 画布基准：所有几何坐标都写在这个 1024 空间里，最后统一缩放
BASE = 1024
SS = 2  # 超采样倍数，画完再缩回 1024，边缘更干净

# 配色（深蓝底 + 暖金，取自电影那组青蓝 / 琥珀对比）
BG_TOP = (14, 62, 104)
BG_BOTTOM = (4, 30, 56)
GOLD = (240, 162, 51)
GLOW = (247, 190, 90)

# 八分音符几何（1024 空间）
HEAD = dict(cx=406.0, cy=665.0, rx=134.0, ry=96.0, rot=-20.0)
STEM = dict(x=512.0, y1=652.0, y2=255.0, w=58.0)
# 符尾：外缘 + 内缘两条贝塞尔围成月牙（外宽内窄，才是真的符尾）
FLAG_OUT = dict(p0=(512.0, 255.0), c1=(652.0, 258.0), c2=(752.0, 346.0),
                p3=(730.0, 466.0))
FLAG_IN = dict(p0=(730.0, 466.0), c1=(697.0, 391.0), c2=(627.0, 331.0),
               p3=(512.0, 326.0))
# 招牌边框
FRAME = dict(x0=96.0, y0=96.0, x1=928.0, y1=928.0, r=185.0, w=13.0, alpha=0.8)


def bezier(p0, c1, c2, p3, steps=96):
    t = np.linspace(0.0, 1.0, steps)
    mt = 1.0 - t
    pts = np.stack([mt ** 3, 3 * mt ** 2 * t, 3 * mt * t ** 2, t ** 3], axis=1)
    ctrl = np.array([p0, c1, c2, p3], dtype=np.float64)
    return pts @ ctrl


def squircle_coverage(size, n=4.6, ss=4):
    """超椭圆的像素覆盖率，直接得到抗锯齿的 alpha。"""
    a = size / 2.0
    cov = np.zeros((size, size), dtype=np.float32)
    idx = np.arange(size, dtype=np.float32)
    offsets = [(i + 0.5) / ss for i in range(ss)]
    for oy in offsets:
        ay = np.abs((idx + oy - a) / a) ** n
        for ox in offsets:
            ax = np.abs((idx + ox - a) / a) ** n
            cov += (ay[:, None] + ax[None, :] <= 1.0)
    return Image.fromarray((cov / (ss * ss) * 255.0 + 0.5).astype(np.uint8), "L")


def draw_note(size, scale, stroke_w, head_alpha_layer):
    """把八分音符画进一张 L 模式遮罩（255 = 实心）。"""
    k = size / BASE
    cx, cy = 512.0, 512.0

    def X(v):
        return (cx + (v - cx) * scale) * k

    def Y(v):
        return (cy + (v - cy) * scale) * k

    d = ImageDraw.Draw(head_alpha_layer)

    # 符头：先画正椭圆，再整体旋转（旋转 L 遮罩可避免透明像素混色产生黑边）
    rx, ry = HEAD["rx"] * scale * k, HEAD["ry"] * scale * k
    hx, hy = X(HEAD["cx"]), Y(HEAD["cy"])
    head = Image.new("L", (size, size), 0)
    ImageDraw.Draw(head).ellipse([hx - rx, hy - ry, hx + rx, hy + ry], fill=255)
    head = head.rotate(HEAD["rot"], resample=Image.BICUBIC, center=(hx, hy), fillcolor=0)
    head_alpha_layer.paste(head, (0, 0), head)

    w = max(2, int(round(stroke_w * k)))

    # 符干
    sx, y1, y2 = X(STEM["x"]), Y(STEM["y1"]), Y(STEM["y2"])
    d.line([(sx, y1), (sx, y2)], fill=255, width=w, joint="curve")
    r = w / 2.0
    for py in (y1, y2):
        d.ellipse([sx - r, py - r, sx + r, py + r], fill=255)

    # 符尾：外缘 + 内缘两条贝塞尔闭合成月牙多边形
    outer = bezier(FLAG_OUT["p0"], FLAG_OUT["c1"], FLAG_OUT["c2"], FLAG_OUT["p3"])
    inner = bezier(FLAG_IN["p0"], FLAG_IN["c1"], FLAG_IN["c2"], FLAG_IN["p3"])
    poly = [(X(x), Y(y)) for x, y in outer] + [(X(x), Y(y)) for x, y in inner]
    d.polygon(poly, fill=255)

    return head_alpha_layer


def draw_frame(size):
    k = size / BASE
    layer = Image.new("L", (size, size), 0)
    ImageDraw.Draw(layer).rounded_rectangle(
        [FRAME["x0"] * k, FRAME["y0"] * k, FRAME["x1"] * k, FRAME["y1"] * k],
        radius=FRAME["r"] * k, outline=255, width=max(1, int(round(FRAME["w"] * k))),
    )
    return layer.point(lambda v: int(v * FRAME["alpha"]))


def render(size, *, frame=True, note_scale=1.0, stroke_w=STEM["w"], glow_radius=46):
    """渲染一张 size×size 的成品图（RGBA，圆角方形外透明）。"""
    work = size * SS
    # 背景渐变
    t = np.linspace(0.0, 1.0, work, dtype=np.float32)[:, None, None]
    top = np.array(BG_TOP, dtype=np.float32)
    bot = np.array(BG_BOTTOM, dtype=np.float32)
    grad = top + (bot - top) * t
    img = Image.fromarray(
        np.repeat(grad.astype(np.uint8), work, axis=1), "RGB").convert("RGBA")

    note = draw_note(work, note_scale, stroke_w, Image.new("L", (work, work), 0))
    frame_layer = draw_frame(work) if frame else Image.new("L", (work, work), 0)
    glyph = ImageChops.lighter(note, frame_layer)

    # 霓虹光晕：把图形模糊后垫在下面
    glow = glyph.filter(ImageFilter.GaussianBlur(glow_radius * SS / 2.0))
    img.paste(GLOW, (0, 0), glow.point(lambda v: int(v * 0.5)))
    img.paste(GOLD, (0, 0), frame_layer)
    img.paste(GOLD, (0, 0), note)

    img = img.resize((size, size), Image.LANCZOS)
    img.putalpha(squircle_coverage(size))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="另外输出预览图")
    args = ap.parse_args()

    OUT_ICNS.parent.mkdir(parents=True, exist_ok=True)
    ICONSET.mkdir(parents=True, exist_ok=True)

    # 小尺寸丢掉招牌边框、加粗笔画，16px 才不至于糊成一团
    small = dict(frame=False, note_scale=1.15, stroke_w=104, glow_radius=26)
    full = dict(frame=True, note_scale=1.0, stroke_w=STEM["w"], glow_radius=46)

    variants = {
        16: small, 32: small, 64: small,
        128: full, 256: full, 512: full, 1024: full,
    }
    images = {}
    for size, kw in variants.items():
        images[size] = render(size, **kw)
        print(f"  渲染 {size}px")

    names = {
        16: ["icon_16x16.png"],
        32: ["icon_16x16@2x.png", "icon_32x32.png"],
        64: ["icon_32x32@2x.png"],
        128: ["icon_128x128.png"],
        256: ["icon_128x128@2x.png", "icon_256x256.png"],
        512: ["icon_256x256@2x.png", "icon_512x512.png"],
        1024: ["icon_512x512@2x.png"],
    }
    for size, files in names.items():
        for name in files:
            images[size].save(ICONSET / name)

    # 顺手留下 png 原件，README / 文档里可以直接引用
    for size in (128, 512, 1024):
        images[size].save(OUT_ICNS.parent / f"ktrans-icon-{size}.png")

    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(OUT_ICNS)],
                   check=True)
    print(f"  生成 {OUT_ICNS.relative_to(ROOT)}")

    if args.preview:
        sheet = Image.new("RGB", (1600, 620), (142, 142, 147))
        sheet.paste(images[512], (24, 24), images[512])
        x = 580
        for size in (128, 64, 32, 16):
            up = images[size].resize((240, 240), Image.NEAREST)
            sheet.paste(up, (x, 80), up)
            x += 252
        sheet.save("/tmp/ktrans-icon-preview.png")
        print("  预览图 /tmp/ktrans-icon-preview.png")


if __name__ == "__main__":
    sys.exit(main())
