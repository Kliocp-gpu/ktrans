<div align="center">

# 🎵 KTrans

**Drag. Drop. Converted.** MP3 / NCM / FLAC / WAV → pure-black MP4, in one step.

*拖进来，转出去。*

[![Version](https://img.shields.io/badge/version-1.00-blue)](../../releases)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)]()
[![Python](https://img.shields.io/badge/python-3.11+-yellow)]()

[中文](README.md) | [English](README_EN.md)

---

Turn audio into **truly pure-black** video files — built for platforms that only accept video uploads (TikTok / Douyin / Bilibili…).
And it decodes NetEase Cloud Music's `.ncm` private format along the way.

</div>

## ✨ Why KTrans

| | |
|:---:|:---|
| 🖤 **Truly black** | Not "looks black" — pixel value **Y=0 pure black**. Uses the `color_range` full-range flag to fix ffmpeg's default black source being Y=16 (visually black, but not actually black). |
| 🎁 **NCM support** | Built-in NetEase `.ncm` decryptor (pure Python, zero third-party dependencies). Drop an NCM file in, get an MP4 out. |
| 🎧 **Smart audio muxing** | Auto-detects the source codec: MP3 / AAC are remuxed losslessly, everything else (FLAC etc.) is transcoded to AAC — fixing Douyin's "extract audio failed" error. |
| 🪶 **Lightweight** | The conversion core has zero third-party dependencies — only system ffmpeg is required. |

## 📦 Install

### 1. Install ffmpeg

```bash
brew install ffmpeg
```

### 2. (Optional) GUI drag & drop support

```bash
pip install tkinterdnd2
```

### 3. Run

```bash
# GUI
python3 mp3_to_mp4_gui.py

# Command line
python3 mp3_to_mp4.py music.mp3
python3 mp3_to_mp4.py *.ncm *.flac --audio-codec auto
```

### 4. (Optional) Build a macOS app bundle

```bash
./build_app.sh
```

This produces `dist/ktrans.app` — double-click to run, or drag it into Applications.

> The script auto-detects a Python interpreter with a working tkinter, and bakes the `tkinterdnd2` (drag & drop) path into the launcher.

⚠️ This is a **local-use-only** bundle (shell + launcher script) that hardcodes your machine's Python paths — run `build_app.sh` again on any other machine. For a distributable build, wait for the py2app version (see Roadmap).

- Troubleshooting: check `/tmp/ktrans.log` if double-click does nothing
- Known limitation: v1 does not support "drop a file onto the Dock icon" (script-based `.app` bundles don't receive the system's odoc event) — drop files into the window instead

## 🖱️ Usage

### GUI

Drag files (or a whole folder) into the window. The sidebar shows conversion history, logs and settings.

### Command line

```bash
python3 mp3_to_mp4.py <files...> [-o output_dir] [--audio-codec auto|copy|aac] [--overwrite]

  --audio-codec auto   Default. MP3/AAC remuxed losslessly, others transcoded to AAC (recommended)
  --audio-codec copy   Always remux without transcoding (FLAC-in-MP4 may fail audio extraction on some platforms)
  --audio-codec aac    Always transcode to AAC 192k
  --overwrite          Overwrite existing outputs (default: auto-suffix " 2" to dedupe)
```

## 🎼 Supported formats

| Input | Notes |
|---|---|
| `.ncm` | NetEase private format, built-in decryption (output is whatever is inside, usually FLAC/MP3) |
| `.mp3` / `.aac` / `.m4a` / `.mp4` (audio-only) | Remuxed as-is, zero transcoding |
| `.flac` / `.wav` / `.ogg` / `.opus` / `.ape` / `.wma` / `.aiff` etc. | Anything ffmpeg understands; auto mode transcodes to AAC |

Output is always **`.mp4`** (H.264 black video + audio track), same name as the source, with auto-deduped suffixes on name collisions.

## ⚙️ How it works

1. **NCM decryption** (`ncm_decoder.py`): magic check → AES-128-ECB to recover the RC4 key → NCM-variant RC4 keystream XOR to restore audio bytes.
2. **Probe**: ffprobe reads the duration and audio codec.
3. **Mux**: `color=c=black` generates the black source; `-color_range 2` marks it full-range so decoders see Y=0 true black; the video track's duration is pinned explicitly to stay aligned with audio.

> 💡 When using `-shortest`, the 1fps black video encodes so fast that it overshoots past the audio's end, producing extra frames — so the color filter gets an explicit `d=<duration>` instead.

## ⚠️ Disclaimer

- The `.ncm` decryption is intended **only for converting audio files you legally own** to local formats. Do not use it to circumvent copyright protection or distribute copyrighted works. Support the artists 🎵
- This project is not affiliated with NetEase Cloud Music.

## 🗺️ Roadmap

- [ ] Standalone py2app bundle (no Python environment needed, distributable)
- [ ] Drop files onto the Dock icon (depends on the item above)
- [ ] Custom app icon
- [ ] Windows support

## 📄 License

[MIT](LICENSE) © [Kliocp-gpu](https://github.com/Kliocp-gpu)
