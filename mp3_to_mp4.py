#!/usr/bin/env python3
"""
mp3_to_mp4.py — 把音频（默认 mp3）封装成 mp4，视频轨为纯黑屏。

单文件、零第三方依赖，仅调用系统已安装的 ffmpeg。
核心函数（find_ffmpeg / build_command / convert_one）同时被 CLI 与 GUI 复用。

用法:
    python mp3_to_mp4.py song.mp3
    python mp3_to_mp4.py song.mp3 -o out.mp4
    python mp3_to_mp4.py song.mp3 --resolution 1920x1080 --fps 1
    python mp3_to_mp4.py a.mp3 b.mp3 c.mp3          # 批量，各自生成同名 mp4

依赖: ffmpeg（需在 PATH 中，或用 --ffmpeg 指定路径）
"""

import argparse
import os
import shutil
import subprocess
import sys

# QQ 音乐 / Moo 音乐私有加密格式（同体系，统一走 qmc_decoder）
QMC_EXTS = (
    ".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8",
    ".qmcflac", ".qmcogg", ".tkm",
    ".mflac", ".mflac0", ".mflac1", ".mflaca", ".mflach", ".mflacl", ".mflacm",
    ".mgg", ".mgg0", ".mgg1", ".mgga", ".mggh", ".mggl", ".mggm",
    ".bkcmp3", ".bkcm4a", ".bkcflac", ".bkcwav", ".bkcape", ".bkcogg", ".bkcwma",
)

# 输出格式：mp4 = 黑屏视频（上传平台）；其余 = 纯音频输出（解锁/转码）
OUTPUT_FORMATS = ("mp4", "mp3", "m4a", "flac", "wav")


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
) -> bool:
    """转换单个文件。log 为可调用对象（CLI 用 print，GUI 用队列回调）。返回是否成功。"""
    if not os.path.isfile(audio_path):
        log(f"[跳过] 输入文件不存在: {audio_path}")
        return False

    # NCM(网易云私有格式)需先解码成临时音频文件，再交给 ffmpeg
    tmp = None
    src = audio_path
    ext = os.path.splitext(audio_path)[1].lower()
    if ext == ".ncm":
        try:
            from ncm_decoder import decode_ncm
        except Exception as e:
            log(f"[跳过] NCM 解码不可用（缺 ncm_decoder）: {e}")
            return False
        try:
            src = decode_ncm(audio_path)
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

    try:
        if output_format == "mp4":
            dur = _probe_duration(ffmpeg, src)
            effective = audio_codec
            if audio_codec == "auto":
                # 抖音等平台的“提取音频”认不准 FLAC-in-MP4 这类非标准组合：
                # mp3/aac 原样封装，其余（flac/ape/wav/ogg/opus...）自动转 AAC。
                codec = _probe_codec(ffmpeg, src)
                if codec in ("mp3", "aac"):
                    effective = "copy"
                    log(f"[音频] 源编码 {codec}，原样封装")
                else:
                    effective = "aac"
                    log(f"[音频] 源编码 {codec or '未知'}，自动转 AAC（保证平台可提取音频）")
            cmd = build_command(
                ffmpeg, src, output_path, width, height, fps, crf, effective, overwrite,
                video_duration=dur,
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
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    size = os.path.getsize(output_path)
    log(f"[完成] {output_path}  ({size / 1024:.1f} KB)")
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
                        help="输出格式：mp4=黑屏视频（默认，上传平台用）；"
                             "mp3/m4a/flac/wav=纯音频输出（解锁/转码）")
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
                       args.audio_codec, args.overwrite, output_format=args.format):
            ok += 1
        else:
            fail += 1

    print(f"\n汇总: 成功 {ok} 个，失败/跳过 {fail} 个。")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
