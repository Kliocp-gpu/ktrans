<div align="center">

<img src="assets/ktrans-icon-512.png" width="128" alt="KTrans icon">

# 🎵 KTrans

**Drag. Drop. Converted.** Encrypted music, MP3 / FLAC / WAV → black-screen video, cover video, or plain audio — one app.

*拖进来，转出去。*

[![Version](https://img.shields.io/badge/version-2.13-blue)](../../releases)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)]()
[![Python](https://img.shields.io/badge/python-3.11+-yellow)]()

[中文](README.md) | [English](README_EN.md)

---

Turn music into the video file platforms demand — built for places that only accept video uploads
(TikTok / Douyin / Bilibili…). Pick one of three pictures: **truly pure black** (smallest file, friendliest
to re-encoding), **album art + blurred backdrop + title** (looks like an actual release), or an animated
**audio waveform** over the blurred art. Not in the mood for video? Export mp3 / m4a / flac / wav instead.

NetEase `.ncm` and QQ Music `.qmc / .mflac / .mgg` are decrypted in-app — drop an encrypted file in and
get something usable out.

</div>

## 📸 Screenshots

The app (dark mode, v2.13):

![GUI](screenshots/gui-dark.png)

Output produced by cover mode (an `.ncm` with embedded art + tags, straight to video):

![Cover mode output](screenshots/cover-mode.jpg)

## ✨ Why KTrans

| | |
|:---:|:---|
| 🖤 **Truly black** | Not "looks black" — pixel value **Y=0 pure black**. Uses the `color_range` full-range flag to fix ffmpeg's default black source being Y=16 (visually black, but not actually black). |
| 🖼️ **Cover mode** | Pulls the embedded album art: blurred & scaled backdrop + centered cover + title/artist text, with automatic font shrinking and ellipsis for long titles. Files without cover art fall back to "black + centered title". |
| 📈 **Visualizer mode** | A live audio waveform (`showwaves`) laid over the blurred cover art, title/artist text as usual — the output is no longer a static picture. |
| 🎚️ **Loudness normalization** | One-click EBU R128 (target -16 LUFS): source libraries vary wildly in loudness, so normalized uploads stop pumping between tracks. |
| 🏷️ **Tag write-back** | Decrypted streams carry no tags; the converter writes title / artist / album and the embedded cover back into the output, so exported FLAC / MP3 no longer shows up as "Unknown Artist" in players. |
| 🎁 **NCM support** | Built-in NetEase `.ncm` decryptor (pure Python, zero third-party dependencies) — metadata and embedded cover art come out too. |
| 🎵 **All QQ Music formats** | `.qmc0/2/3/4/6/8`, `.qmcflac`, `.qmcogg`, `.mflac*`, `.mgg*`, `.tkm`, `.bkc*` — static table, map table and RC4 variant ciphers all reversed. |
| 🎧 **Smart audio muxing** | Auto-detects the source codec: MP3 / AAC are remuxed losslessly, everything else (FLAC etc.) is transcoded to AAC — fixing Douyin's "extract audio failed" error. |
| 🔄 **Not just MP4** | Audio-only output too: MP3 / M4A / FLAC / WAV — matching codecs are copied losslessly, embedded covers carried over. |
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
python3 mp3_to_mp4.py AlbumFolder/ --video visual --loudnorm   # recursive folder + waveform + loudnorm
```

### 4. Build a macOS app bundle

Two scripts — pick one:

```bash
./build_dist.sh    # recommended: full py2app bundle, standalone & distributable (~31 MB)
./build_app.sh     # lightweight: shell + launcher script, this machine only (seconds)
```

**`build_dist.sh` (standalone)**: bundles the interpreter, `tkinterdnd2` and the Tcl/Tk runtime
inside the app, so the other machine needs no Python at all — just double-click (ffmpeg still required).
Output: `dist/ktrans.app`.

```bash
ditto -c -k --keepParent dist/ktrans.app ktrans.zip   # zip it up to share
```

One-time build environment (already `.gitignore`d):

```bash
python3 -m venv .venv-pack
.venv-pack/bin/pip install py2app tkinterdnd2
```

> The script does three things py2app can't on this Python build: place the Tcl/Tk dylibs in
> `Resources/lib` so `_tkinter.so`'s built-in rpath resolves; copy the `tcl9.0` / `tk9.0` script
> libraries; and re-run an ad-hoc codesign after touching the binaries.

**`build_app.sh` (lightweight)**: shell + launcher script only, hardcoding your machine's Python paths.

- Troubleshooting: check `/tmp/ktrans.log` if double-click does nothing or it crashes
- Drop a file onto the Dock icon / Finder "Open With": **works while the app is already running**;
  a cold start that carries a file does not load it (macOS odoc events have no built-in support
  in Tk 9 — the event is dropped before Python starts)

## 🖱️ Usage

### GUI

Drag files (or a whole folder) into the window — when the list is empty, the list area itself is the drop zone.
The sidebar shows conversion history, logs and about.

With `mp4` output there's an extra "Picture" row: **black** (smallest file, friendliest to platform re-encoding) or **cover** (album art + blurred backdrop + title/artist).

### Command line

```bash
python3 mp3_to_mp4.py <files...> [-o output_dir] [--audio-codec auto|copy|aac] [--overwrite]

  --audio-codec auto   Default. MP3/AAC remuxed losslessly, others transcoded to AAC (recommended)
  --audio-codec copy   Always remux without transcoding (FLAC-in-MP4 may fail audio extraction on some platforms)
  --audio-codec aac    Always transcode to AAC 192k
  --video black|cover  Picture: black = pure black (default, smallest) / cover = album art
  --format mp4|mp3|m4a|flac|wav   Output format (default mp4)
  --overwrite          Overwrite existing outputs (default: auto-suffix " 2" to dedupe)
```

Cover mode example:

```bash
python3 mp3_to_mp4.py nocturne.mp3 --video cover -r 1920x1080
# [封面] 夜曲 Nocturne — 周杰伦 Jay Chou
# [完成] nocturne.mp4  (213.3 KB)
```

## 🎼 Supported formats

| Input | Notes |
|---|---|
| `.ncm` | NetEase private format, built-in decryption (output is whatever is inside, usually FLAC/MP3) |
| `.qmc*` `.mflac*` `.mgg*` `.tkm` `.bkc*` | QQ Music / Moo Music private formats, built-in decryption (algorithm ported from unlock-music, verified byte-for-byte) ⚠️ MusicEx-footer files not supported yet (key lives in the client database) |
| `.mp3` / `.aac` / `.m4a` / `.mp4` (audio-only) | Remuxed as-is, zero transcoding |
| `.flac` / `.wav` / `.ogg` / `.opus` / `.ape` / `.wma` / `.aiff` etc. | Anything ffmpeg understands; auto mode transcodes to AAC |

Output defaults to **`.mp4`** (H.264 video + audio track), same name as the source, with auto-deduped suffixes on name collisions.
Audio-only outputs (`--format mp3/m4a/flac/wav`) are supported as well.

## ⚙️ How it works

1. **NCM decryption** (`ncm_decoder.py`): magic check → AES-128-ECB to recover the RC4 key → NCM-variant RC4 keystream XOR to restore audio bytes. The metadata block and embedded cover are parsed along the way.
2. **QMC decryption** (`qmc_decoder.py`): footer key → TencentTEA derivation → pick static table / map table / RC4 variant by key length.
3. **Probe**: a single ffprobe call yields duration, audio codec, the `attached_pic` cover stream and ID3 tags (title/artist).
4. **Mux**: `color=c=black` generates the black source; `-color_range 2` marks it full-range so decoders see Y=0 true black; the video track's duration is pinned explicitly to stay aligned with audio.

> 💡 When using `-shortest`, the 1fps black video encodes so fast that it overshoots past the audio's end, producing extra frames — so the color filter gets an explicit `d=<duration>` instead.

5. **Cover mode**: the embedded cover is extracted to an image → `split` into two branches: one scaled up + cropped + gaussian-blurred + darkened as the backdrop, the other scaled to 56% of the frame height and centered on top → `drawtext` writes title/artist (text goes through `textfile=`, side-stepping filter escaping entirely). The frame is a `-loop 1` still image, cut to the audio duration with `-t`.

> 💡 When using `-shortest`, the 1fps black video encodes so fast that it overshoots past the audio's end, producing extra frames — so the color filter gets an explicit `d=<duration>` instead.

## ⚠️ Disclaimer

- The private-format decryption (`.ncm` / `.qmc*` / `.mflac*` / `.mgg*` etc.) is intended **only for converting audio files you legally own** to local formats. Do not use it to circumvent copyright protection or distribute copyrighted works. Support the artists 🎵
- The QMC decryption algorithm is ported from [unlock-music](https://git.unlock-music.dev/um/cli) (MIT License) — thanks to the original authors.
- UI layout inspired by [Filee](https://github.com/KnifeLemon/Filee) (MIT License).
- This project is not affiliated with NetEase Cloud Music or Tencent Music.

## 🙏 Acknowledgements

- [unlock-music](https://git.unlock-music.dev/um/cli) (MIT License) — the source of the QMC decryption algorithm, verified byte-for-byte against upstream test vectors.
- [ncmdump](https://github.com/taurusxin/ncmdump) (MIT License) — public reference for the NCM format.
- [Filee](https://github.com/KnifeLemon/Filee) (MIT License) — inspiration for the UI layout and visual style.

## 🗺️ Roadmap

- [x] Standalone py2app bundle (`build_dist.sh`, no Python environment needed, distributable)
- [x] Drop files onto the Dock icon (works while the app is running — see the note above)
- [ ] Custom app icon
- [ ] Windows support

## 📄 License

[MIT](LICENSE) © [Kliocp-gpu](https://github.com/Kliocp-gpu)
