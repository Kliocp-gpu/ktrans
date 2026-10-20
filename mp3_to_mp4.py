#!/usr/bin/env python3
"""
mp3_to_mp4.py — KTrans 转换核心：音频 → 视频 / 纯音频。

三种出片方式（`--video` / `--format` 控制）：
  black  纯黑屏 mp4（默认）。像素值 Y=0 的真黑，体积最小，专治"只收视频"的平台
  cover  封面模式。内嵌专辑图导出 → 模糊放大铺底 + 居中封面 + 歌名/歌手文字；
         没有封面的文件自动退成"黑底 + 居中歌名"
  其余   纯音频输出 mp3 / m4a / flac / wav，同源编码直接 copy 无损

加密格式先经 ncm_decoder（网易云 .ncm）/ qmc_decoder（QQ 音乐 .qmc/.mflac/.mgg）解密，
再交给 ffmpeg。零第三方依赖，只调用系统 ffmpeg；核心函数同时被 CLI 与 GUI 复用。

用法:
    python mp3_to_mp4.py song.mp3
    python mp3_to_mp4.py song.ncm --video cover -r 1920x1080
    python mp3_to_mp4.py song.flac --format flac
    python mp3_to_mp4.py a.mp3 b.mp3 c.mp3          # 批量，各自生成同名输出

依赖: ffmpeg（需在 PATH 中，或用 --ffmpeg 指定路径）
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata

# QQ 音乐 / Moo 音乐私有加密格式（同体系，统一走 qmc_decoder）
QMC_EXTS = (
    ".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8",
    ".qmcflac", ".qmcogg", ".tkm",
    ".mflac", ".mflac0", ".mflac1", ".mflaca", ".mflach", ".mflacl", ".mflacm",
    ".mgg", ".mgg0", ".mgg1", ".mgga", ".mggh", ".mggl", ".mggm",
    ".bkcmp3", ".bkcm4a", ".bkcflac", ".bkcwav", ".bkcape", ".bkcogg", ".bkcwma",
)

# 输出格式：mp4 = 视频（上传平台）；其余 = 纯音频输出（解锁/转码）
OUTPUT_FORMATS = ("mp4", "mp3", "m4a", "flac", "wav")

# mp4 的画面模式：black = 纯黑屏；cover = 专辑封面 + 模糊背景 + 歌名文字
VIDEO_MODES = ("black", "cover")

# drawtext 需要字体文件；优先挑带中文字形的（macOS / Windows / Linux 常见位置）
FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)


def find_font(explicit: str | None = None) -> str | None:
    """返回一个可用的字体文件路径（供 drawtext 用）；找不到返回 None（则不加文字）。"""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for p in FONT_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def find_ffmpeg(explicit: str | None) -> str:
    """返回可用的 ffmpeg 路径；找不到则抛 RuntimeError（不在此退出，交给调用方处理）。"""
    if explicit:
        if not (shutil.which(explicit) or os.path.isfile(explicit)):
            raise RuntimeError(f"指定的 ffmpeg 不存在: {explicit}")
        return explicit
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 兜底：扫描常见安装路径（部分系统 ffmpeg 不在 PATH 中）
    candidates = [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    if os.name == "nt":  # Windows 常见解压位置
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.append(os.path.join(local, "ffmpeg", "bin", "ffmpeg.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise RuntimeError(
        "未找到 ffmpeg。请先安装 ffmpeg 并确保其在 PATH 中，\n"
        "       或用 --ffmpeg /path/to/ffmpeg 显式指定。"
    )


def default_output(input_path: str, fmt: str = "mp4") -> str:
    """输入 a/b/song.mp3 -> a/b/song.<fmt>（默认 mp4）。"""
    base, _ = os.path.splitext(input_path)
    return base + "." + fmt


def unique_output(path: str, used: set[str] | None = None) -> str:
    """输出路径去重：同一批次里撞名时按 Finder 风格追加序号（song.mp4 -> song 2.mp4）。

    不加这个的话，s5.wav 与 s5.flac 的 default_output 都是 s5.mp4，
    后一个会静默覆盖前一个，日志还照样报“成功”。
    """
    if used is None:
        used = set()
    key = os.path.normcase(os.path.abspath(path))
    if key not in used:
        used.add(key)
        return path
    d, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    i = 2
    while True:
        cand = os.path.join(d, f"{stem} {i}{ext}")
        k = os.path.normcase(os.path.abspath(cand))
        if k not in used:
            used.add(k)
            return cand
        i += 1


def _ffprobe_path(ffmpeg: str) -> str | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        cand = os.path.join(
            os.path.dirname(ffmpeg),
            "ffprobe.exe" if os.name == "nt" else "ffprobe",
        )
        if os.path.isfile(cand):
            ffprobe = cand
    return ffprobe


def _probe_duration(ffmpeg: str, media: str) -> float | None:
    """用 ffprobe 取媒体时长（秒）。取不到返回 None（调用方退回不限制黑屏时长）。"""
    ffprobe = _ffprobe_path(ffmpeg)
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", media],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _probe_codec(ffmpeg: str, media: str) -> str | None:
    """取第一路音频流的编码名（mp3/aac/flac/...）。取不到返回 None。"""
    ffprobe = _ffprobe_path(ffmpeg)
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=nw=1:nk=1", media],
            capture_output=True, text=True, timeout=30,
        )
        lines = out.stdout.strip().splitlines()
        return lines[0].strip() if lines and lines[0].strip() else None
    except Exception:
        return None


def probe_media(ffmpeg: str, media: str) -> dict:
    """一次 ffprobe 拿到封面流索引、标签（歌名/歌手）和时长。

    返回 {'duration','audio_codec','cover_index','cover_codec','title','artist','album'}，
    取不到的字段为 None / 空串。封面靠 disposition.attached_pic == 1 识别。
    """
    info = {
        "duration": None, "audio_duration": None, "audio_codec": None,
        "cover_index": None, "cover_codec": None,
        "title": "", "artist": "", "album": "",
    }
    ffprobe = _ffprobe_path(ffmpeg)
    if not ffprobe:
        return info
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", media],
            capture_output=True, text=True, timeout=30,
        )
        js = json.loads(out.stdout or "{}")
    except Exception:
        return info

    fmt = js.get("format") or {}
    try:
        info["duration"] = float(fmt.get("duration") or 0) or None
    except (TypeError, ValueError):
        pass
    # ffprobe 的标签键大小写不稳定（title / TITLE / Title 都见过）
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    info["title"] = (tags.get("title") or "").strip()
    info["artist"] = (tags.get("artist") or tags.get("album_artist") or "").strip()
    info["album"] = (tags.get("album") or "").strip()

    for s in js.get("streams") or []:
        if s.get("codec_type") == "audio" and info["audio_codec"] is None:
            info["audio_codec"] = s.get("codec_name")
            try:
                info["audio_duration"] = float(s.get("duration") or 0) or None
            except (TypeError, ValueError):
                pass
        disp = s.get("disposition") or {}
        if disp.get("attached_pic") == 1 and info["cover_index"] is None:
            info["cover_index"] = s.get("index")
            info["cover_codec"] = s.get("codec_name")
    return info


_COVER_EXT = {"mjpeg": ".jpg", "jpeg": ".jpg", "png": ".png",
              "bmp": ".bmp", "webp": ".webp", "gif": ".gif"}


def extract_cover(ffmpeg: str, media: str, info: dict, out_dir: str) -> str | None:
    """把内嵌封面导出成图片文件，返回路径；没有封面或导出失败返回 None。"""
    idx = info.get("cover_index")
    if idx is None:
        return None
    ext = _COVER_EXT.get(str(info.get("cover_codec") or "").lower(), "")
    # 先试无损拷贝（mjpeg → .jpg），失败再解码成 png
    attempts = []
    if ext:
        attempts.append((os.path.join(out_dir, "cover" + ext), ["-c:v", "copy"]))
    attempts.append((os.path.join(out_dir, "cover.png"), ["-c:v", "png"]))
    for dst, codec_args in attempts:
        cmd = [ffmpeg, "-y", "-v", "error", "-i", media, "-map", f"0:{idx}",
               "-frames:v", "1", *codec_args, dst]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=60)
        except Exception:
            continue
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return dst
    return None


def _filter_quote(path: str) -> str:
    """滤镜图里的路径引号：单引号包裹，内部单引号按 ffmpeg 规则断开转义。"""
    return "'" + path.replace("'", r"'\''") + "'"


def _text_units(text: str) -> float:
    """粗估文字宽度（以字号为 1 个单位）：全角 1.0，半角 0.58。"""
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.58
               for ch in text)


def _truncate(text: str, limit: int = 56) -> str:
    """过长的歌名截断加省略号（配合缩字号，保证不出画面）。"""
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _fit_fontsize(text: str, base: int, max_px: int) -> int:
    """歌名太长就按比例缩字号，保证不出画面。"""
    units = _text_units(text)
    if units <= 0:
        return base
    est = units * base
    if est <= max_px:
        return base
    return max(12, int(base * max_px / est))


def _write_text(work_dir: str, name: str, text: str) -> str:
    """把文字写进文件，用 textfile= 传给 drawtext。

    这样完全绕开 drawtext 的转义地狱（冒号、引号、逗号、反斜杠、% 都要特殊处理）。
    """
    p = os.path.join(work_dir, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text.replace("\n", " ").replace("\r", " ").strip())
    return p


def build_drawtext(title: str, artist: str, width: int, height: int,
                   work_dir: str | None, font: str | None,
                   center: bool = False) -> str:
    """拼 drawtext 滤镜串（不含前后逗号）；没有字体/没有文字时返回空串。"""
    if not font or not work_dir:
        return ""
    parts: list[str] = []
    t_size = max(12, int(height * 0.061))
    a_size = max(10, int(height * 0.036))
    max_w = int(width * 0.82)

    if center:
        block = t_size + max(6, int(height * 0.026)) + a_size
        ty = max(0, int((height - block) / 2))
        ay = ty + t_size + max(6, int(height * 0.026))
    else:
        ty = int(height * 0.754)
        ay = ty + int(height * 0.086)

    if title:
        title = _truncate(title)
        p = _write_text(work_dir, "title.txt", title)
        s = _fit_fontsize(title, t_size, max_w)
        parts.append(
            f"drawtext=fontfile={_filter_quote(font)}:textfile={_filter_quote(p)}"
            f":fontcolor=white:fontsize={s}:x=(w-text_w)/2:y={ty}"
        )
    if artist:
        artist = _truncate(artist, 48)
        p = _write_text(work_dir, "artist.txt", artist)
        s = _fit_fontsize(artist, a_size, max_w)
        parts.append(
            f"drawtext=fontfile={_filter_quote(font)}:textfile={_filter_quote(p)}"
            f":fontcolor=white@0.72:fontsize={s}:x=(w-text_w)/2:y={ay}"
        )
    return ",".join(parts)


def build_cover_command(
    ffmpeg: str,
    audio_path: str,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    crf: int,
    audio_codec: str,
    overwrite: bool,
    duration: float | None,
    cover_path: str | None = None,
    title: str = "",
    artist: str = "",
    work_dir: str | None = None,
    font: str | None = None,
) -> list[str]:
    """封面模式命令：模糊放大的封面铺底 + 居中封面 + 底部歌名/歌手。

    cover_path 为 None 时退化成「黑底 + 居中文字」（原文件没有内嵌封面）。
    画面用 -loop 1 循环单张图，再用 -t 卡到音频时长，避免 1fps 下 -shortest 不准。
    """
    art = build_drawtext(title, artist, width, height, work_dir, font,
                         center=not cover_path)
    cmd = [ffmpeg, "-y" if overwrite else "-n"]

    if cover_path:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", cover_path]
        cmd += ["-i", audio_path]
        amap = "1:a"
        cover_h = max(2, int(round(height * 0.560)) // 2 * 2)
        blur = max(2, int(round(height * 0.055)))
        dy = int(round(height * -0.069))
        graph = (
            "[0:v]split=2[b][f];"
            f"[b]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma={blur},"
            "eq=brightness=-0.24:saturation=1.2[bg];"
            f"[f]scale=-2:{cover_h}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2{dy:+d}"
        )
        if art:
            graph += "," + art
        graph += "[v]"
    else:
        color = f"color=c=black:s={width}x{height}:r={fps}"
        if duration:
            color += f":d={duration:.3f}"
        cmd += ["-i", audio_path, "-f", "lavfi", "-i", color]
        amap = "0:a"
        graph = "[1:v]"
        if art:
            graph += art
        graph += "[v]"

    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", amap,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p"]
    if not cover_path:
        # 纯黑底仍要全范围（见 build_command 注释）；封面图走正常范围转换
        cmd += ["-color_range", "2"]
    cmd += ["-c:a", audio_codec]
    if audio_codec == "aac":
        cmd += ["-b:a", "192k"]
    if duration:
        # 黑屏分支靠 color 的 d= 收尾，但 1fps 下 d=5.042 会补到第 6 帧（6s），
        # 徒手多出 1 秒；统一用 -t 卡死输出时长。
        # 这里刻意不加 -shortest：实测它会让音轨提前 50ms 收尾（比 -t 的 26ms 还多），
        # 而且它会和 1fps 的帧边界打架。真正的残损文件另有长度校验兜底。
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-movflags", "+faststart", output_path]
    return cmd


def build_command(
    ffmpeg: str,
    audio_path: str,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    crf: int,
    audio_codec: str,
    overwrite: bool,
    video_duration: float | None = None,
) -> list[str]:
    # 必须给黑屏源显式时长(d=)：1fps 黑帧编码极快(约 55 倍速)，视频编码器会在
    # “音频读到 EOF”这个信号传回来之前冲过头，导致视频轨比音频长十几到几十秒
    # （尾部多出静音黑屏），且帧数每次都不一样。给了 d= 后视频输入自己会 EOF。
    color = f"color=c=black:s={width}x{height}:r={fps}"
    if video_duration:
        color += f":d={video_duration:.3f}"

    cmd = [ffmpeg]
    cmd.append("-y" if overwrite else "-n")
    cmd += [
        "-i", audio_path,
        "-f", "lavfi",
        # color 源默认有限范围(TV)，“黑”=Y16；-color_range 2 让 ffmpeg 转成
        # 全范围并写入 VUI，得到 Y=0 的纯黑（各播放器都显示为真黑）。
        "-i", color,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-color_range", "2",
        "-c:a", audio_codec,
    ]
    if audio_codec == "aac":
        cmd += ["-b:a", "192k"]
    cmd += ["-shortest", "-movflags", "+faststart", output_path]
    return cmd


def build_audio_command(
    ffmpeg: str,
    audio_path: str,
    output_path: str,
    fmt: str,
    overwrite: bool,
    log=print,
) -> list[str]:
    """纯音频输出命令：同源编码则 copy 无损，否则转码；尽量保留内嵌封面。

    wav 容器不支持封面图，直接丢弃视频流；mp3/m4a/flac 用 `-map 0:v?`
    可选映射带过去（disposition 随流自动保留，无需显式设置）。
    """
    codec = _probe_codec(ffmpeg, audio_path)
    cmd = [ffmpeg, "-y" if overwrite else "-n", "-i", audio_path]
    if fmt == "wav":
        cmd += ["-map", "0:a", "-vn", "-c:a", "pcm_s16le"]
        if codec != "pcm_s16le":
            log(f"[音频] 源编码 {codec or '未知'}，转 PCM/WAV")
        return cmd + [output_path]

    cmd += ["-map", "0:a", "-map", "0:v?", "-c:v", "copy"]
    if fmt == "mp3":
        if codec == "mp3":
            cmd += ["-c:a", "copy"]
            log("[音频] 源编码 mp3，原样输出")
        else:
            cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
            log(f"[音频] 源编码 {codec or '未知'}，转 MP3 V2")
    elif fmt == "m4a":
        if codec == "aac":
            cmd += ["-c:a", "copy"]
            log("[音频] 源编码 aac，原样输出")
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
            log(f"[音频] 源编码 {codec or '未知'}，转 AAC 192k")
    elif fmt == "flac":
        if codec == "flac":
            cmd += ["-c:a", "copy"]
            log("[音频] 源编码 flac，原样输出")
        else:
            cmd += ["-c:a", "flac"]
            log(f"[音频] 源编码 {codec or '未知'}，转 FLAC（无损）")
    else:
        raise ValueError(f"不支持的音频输出格式: {fmt}")
    return cmd + [output_path]


def convert_one(
    ffmpeg: str,
    audio_path: str,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    crf: int,
    audio_codec: str,
    overwrite: bool,
    log=print,
    output_format: str = "mp4",
    video_mode: str = "black",
    font: str | None = None,
) -> bool:
    """转换单个文件。log 为可调用对象（CLI 用 print，GUI 用队列回调）。返回是否成功。

    video_mode 只在 output_format == "mp4" 时生效：
      black = 纯黑屏（最省体积，平台压制最友好）
      cover = 专辑封面 + 模糊背景 + 歌名/歌手文字
    """
    if not os.path.isfile(audio_path):
        log(f"[跳过] 输入文件不存在: {audio_path}")
        return False

    # NCM(网易云私有格式)需先解码成临时音频文件，再交给 ffmpeg
    tmp = None
    src = audio_path
    ncm_meta: dict = {}
    ncm_cover: bytes = b""
    ext = os.path.splitext(audio_path)[1].lower()
    if ext == ".ncm":
        try:
            from ncm_decoder import decode_ncm_ex
        except Exception as e:
            log(f"[跳过] NCM 解码不可用（缺 ncm_decoder）: {e}")
            return False
        try:
            src, ncm_meta, ncm_cover = decode_ncm_ex(audio_path)
            tmp = src
            log(f"[NCM] 已解密: {audio_path}  ->  {src}")
        except Exception as e:
            log(f"[失败] NCM 解密出错 {audio_path}: {e}")
            return False
    elif ext in QMC_EXTS:
        # QMC/MFLAC/MGG（QQ音乐私有格式，Moo 音乐的 bkc* 同体系）
        try:
            from qmc_decoder import decode_qmc
        except Exception as e:
            log(f"[跳过] QMC 解码不可用（缺 qmc_decoder）: {e}")
            return False
        try:
            src = decode_qmc(audio_path)
            tmp = src
            log(f"[QMC] 已解密: {audio_path}  ->  {src}")
        except Exception as e:
            log(f"[失败] QMC 解密出错 {audio_path}: {e}")
            return False

    work: str | None = None
    try:
        if output_format == "mp4":
            info = probe_media(ffmpeg, src)
            dur = info["duration"]
            effective = audio_codec
            if audio_codec == "auto":
                # 抖音等平台的“提取音频”认不准 FLAC-in-MP4 这类非标准组合：
                # mp3/aac 原样封装，其余（flac/ape/wav/ogg/opus...）自动转 AAC。
                codec = info["audio_codec"]
                if codec in ("mp3", "aac"):
                    effective = "copy"
                    log(f"[音频] 源编码 {codec}，原样封装")
                else:
                    effective = "aac"
                    log(f"[音频] 源编码 {codec or '未知'}，自动转 AAC（保证平台可提取音频）")

            mode = video_mode
            if mode == "cover" and not dur:
                # 封面模式靠 -t 卡时长；探不到时长就退回黑屏，避免无限循环出片
                log("[封面] 取不到音频时长，改用纯黑屏")
                mode = "black"

            if mode == "cover":
                work = tempfile.mkdtemp(prefix="ktrans_cov_")
                cover = None
                if ncm_cover:
                    # NCM 的封面是独立字节块，直接落盘
                    cext = ".jpg" if ncm_cover[:3] == b"\xff\xd8\xff" else ".png"
                    cover = os.path.join(work, "cover" + cext)
                    with open(cover, "wb") as f:
                        f.write(ncm_cover)
                else:
                    cover = extract_cover(ffmpeg, src, info, work)
                if ncm_meta:
                    from ncm_decoder import meta_summary
                    n_title, n_artist, _ = meta_summary(ncm_meta)
                else:
                    n_title, n_artist = "", ""
                title = n_title or info["title"] or \
                    os.path.splitext(os.path.basename(audio_path))[0]
                artist = n_artist or info["artist"]
                font = find_font(font)
                if cover is None:
                    log("[封面] 源文件没有内嵌封面，改用黑底 + 文字")
                elif not font:
                    log("[封面] 没找到中文字体，跳过文字只放封面")
                log(f"[封面] {title}" + (f" — {artist}" if artist else ""))
                cmd = build_cover_command(
                    ffmpeg, src, output_path, width, height, fps, crf,
                    effective, overwrite, duration=dur, cover_path=cover,
                    title=title, artist=artist, work_dir=work, font=font,
                )
            else:
                cmd = build_command(
                    ffmpeg, src, output_path, width, height, fps, crf,
                    effective, overwrite, video_duration=dur,
                )
        else:
            cmd = build_audio_command(ffmpeg, src, output_path, output_format, overwrite, log)
        log(f"[转换] {src}  ->  {output_path}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", "ignore") if exc.stderr else ""
        log(f"[失败] {audio_path}\n{err[-2000:]}")
        return False
    finally:
        for p in (tmp, work):
            if p and os.path.exists(p):
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except OSError:
                    pass

    size = os.path.getsize(output_path)
    log(f"[完成] {output_path}  ({size / 1024:.1f} KB)")
    if output_format == "mp4":
        # 兜底：视频比音频长出很多，说明源文件的时长头不实（截断/残损），
        # 此时成片会是几分钟的静音封面——提醒一句，别让人以为是转换坏了。
        oi = probe_media(ffmpeg, output_path)
        vdur, adur = oi.get("duration"), oi.get("audio_duration")
        if vdur and adur and vdur - adur > 5:
            log(f"[提示] 视频比音频长 {vdur - adur:.1f}s，源文件可能被截断或时长信息不实")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把音频封装成纯黑屏 mp4（音频原样保留）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", help="输入音频文件（可多个，如 mp3/wav/m4a）")
    parser.add_argument("-o", "--output", help="输出 mp4 路径（仅单个输入时生效）")
    parser.add_argument("-r", "--resolution", default="1280x720",
                        help="视频分辨率，默认 1280x720（格式 WxH）")
    parser.add_argument("--fps", type=int, default=1,
                        help="黑屏帧率，默认 1（静态黑屏足够，文件最小）")
    parser.add_argument("--crf", type=int, default=18,
                        help="视频质量 0-51，默认 18（黑屏下几乎无损且极小）")
    parser.add_argument("--audio-codec", default="auto", choices=["auto", "copy", "aac"],
                        help="音频处理：auto=mp3/aac 原样、其余自动转 AAC（默认，推荐上传平台）；"
                             "copy=一律原样不重编码；aac=一律转 AAC")
    parser.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径（默认从 PATH 查找）")
    parser.add_argument("--format", default="mp4", choices=list(OUTPUT_FORMATS),
                        help="输出格式：mp4=视频（默认，上传平台用）；"
                             "mp3/m4a/flac/wav=纯音频输出（解锁/转码）")
    parser.add_argument("--video", default="black", choices=list(VIDEO_MODES),
                        help="mp4 的画面：black=纯黑屏（默认，体积最小）；"
                             "cover=专辑封面+模糊背景+歌名/歌手文字")
    parser.add_argument("--font", help="drawtext 用的字体文件（封面模式，默认自动探测中文字体）")
    parser.add_argument("-y", "--overwrite", action="store_true",
                        help="覆盖已存在的输出文件（默认遇到同名会跳过报错）")
    args = parser.parse_args()

    try:
        width, height = (int(x) for x in args.resolution.lower().split("x"))
    except ValueError:
        sys.exit(f"[错误] 分辨率格式应为 WxH，例如 1280x720，收到: {args.resolution}")

    try:
        ffmpeg = find_ffmpeg(args.ffmpeg)
    except RuntimeError as e:
        sys.exit(f"[错误] {e}")

    if len(args.inputs) > 1 and args.output:
        print("[提示] 传入多个输入时 -o/--output 被忽略，按同名生成。")

    ok = fail = 0
    used: set[str] = set()
    for audio in args.inputs:
        out = args.output if (len(args.inputs) == 1 and args.output) \
            else default_output(audio, args.format)
        out = unique_output(out, used)
        if convert_one(ffmpeg, audio, out, width, height, args.fps, args.crf,
                       args.audio_codec, args.overwrite, output_format=args.format,
                       video_mode=args.video, font=args.font):
            ok += 1
        else:
            fail += 1

    print(f"\n汇总: 成功 {ok} 个，失败/跳过 {fail} 个。")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
