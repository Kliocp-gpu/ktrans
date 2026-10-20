<div align="center">

# 🎵 KTrans

**拖进来，转出去。MP3 / NCM / FLAC / WAV → 黑屏 MP4，一步到位。**

*Drag. Drop. Converted.*

[![Version](https://img.shields.io/badge/version-1.00-blue)](../../releases)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)]()
[![Python](https://img.shields.io/badge/python-3.11+-yellow)]()

[中文](README.md) | [English](README_EN.md)

---

把音频变成**真·纯黑屏**的视频文件 —— 专为那些"只收视频"的上传平台而生（抖音 / B站 / 视频号…）。
顺带把网易云的 `.ncm` 私有格式也解了。

</div>

## 📸 截图

![GUI 界面（深色模式）](screenshots/gui-dark.png)

## ✨ 为什么是 KTrans

| | |
|:---:|:---|
| 🖤 **真黑屏** | 不是"看着黑"，是像素值 Y=0 的**纯黑**。利用 `color_range` 全范围标记，解决 ffmpeg 默认黑屏源 Y=16（肉眼黑但不是黑）的问题。 |
| 🖼️ **封面模式** | 自动抠出内嵌专辑图：模糊放大铺底 + 居中封面 + 歌名/歌手文字，长标题自动缩字号加省略号。无封面的文件自动退回"黑底 + 居中歌名"。 |
| 🎁 **NCM 全支持** | 内置网易云 `.ncm` 解密器（纯 Python 实现，无需第三方库），连元数据与内嵌封面一起解出来直接用。 |
| 🎵 **QQ 音乐全系** | `.qmc0/2/3/4/6/8`、`.qmcflac`、`.qmcogg`、`.mflac*`、`.mgg*`、`.tkm`、`.bkc*` 全部支持，静态表 / 映射表 / RC4 变体三种加密逐一还原。 |
| 🎧 **音频智能封装** | 自动探测源编码：MP3 / AAC 原样封装保证无损，FLAC 等其他编码自动转 AAC —— 解决抖音"提取音乐失败"。 |
| 🔄 **不只出 MP4** | 也能只出音频：MP3 / M4A / FLAC / WAV，同源编码直接 copy 无损、内嵌封面一起带走。 |
| 🪶 **轻量** | 核心转换零第三方依赖，只依赖系统 ffmpeg。 |

## 📦 安装

### 1. 安装 ffmpeg

```bash
brew install ffmpeg
```

### 2. （可选）GUI 拖拽支持

```bash
pip install tkinterdnd2
```

### 3. 启动

```bash
# GUI
python3 mp3_to_mp4_gui.py
# 或直接双击 启动GUI.command

# 命令行
python3 mp3_to_mp4.py 音乐.mp3
python3 mp3_to_mp4.py *.ncm *.flac --audio-codec auto
```

### 4.（可选）打包成 macOS App

```bash
./build_app.sh
```

产出 `dist/ktrans.app`，双击即可运行，也可拖进「应用程序」文件夹。

> 脚本会自动探测你机器上带可用 tkinter 的 Python 解释器，并把拖拽库 `tkinterdnd2` 的路径写进启动器。

⚠️ 这是「外壳 + 启动脚本」结构的**本机自用版**，启动器里记录的是你本机的 Python 路径，换台机器要重新跑一次 `build_app.sh`。想发给别人直接用，请等 py2app 全量打包版本（见 Roadmap）。

- 排障：双击没反应时看日志 `/tmp/ktrans.log`
- 已知限制：v1 不支持"把文件拖到 Dock 图标上打开"（脚本型 `.app` 收不到系统的 odoc 事件），请在窗口内拖放

## 🖱️ 使用

### GUI

把文件（或整个文件夹）拖进窗口就行——没添加文件时，列表区域本身就是拖放区。
左侧边栏可查看转换记录、日志、关于。

输出选 `mp4` 时多出一行「画面」：**纯黑**（体积最小，平台压制最友好）或 **封面**（专辑图 + 模糊背景 + 歌名歌手）。

### 命令行

```bash
python3 mp3_to_mp4.py <文件...> [-o 输出目录] [--audio-codec auto|copy|aac] [--overwrite]

  --audio-codec auto   默认。MP3/AAC 原样封装，其他自动转 AAC 192k（推荐）
  --audio-codec copy   无条件原样封装（FLAC 转出的 MP4 部分平台无法提取音频）
  --audio-codec aac    无条件转 AAC 192k
  --video black|cover  画面：black 纯黑屏（默认，体积最小）/ cover 封面模式
  --format mp4|mp3|m4a|flac|wav   输出格式（默认 mp4）
  --overwrite          覆盖已存在的输出文件（默认自动加 " 2" 后缀去重）
```

封面模式示例：

```bash
python3 mp3_to_mp4.py 夜曲.mp3 --video cover -r 1920x1080
# [封面] 夜曲 Nocturne — 周杰伦 Jay Chou
# [完成] 夜曲.mp4  (213.3 KB)
```

## 🎼 支持格式

| 输入 | 说明 |
|---|---|
| `.ncm` | 网易云私有格式，内置解密（解出什么就是什么，通常是 FLAC/MP3） |
| `.qmc*` `.mflac*` `.mgg*` `.tkm` `.bkc*` | QQ 音乐 / Moo 音乐私有格式，内置解密（算法移植自 unlock-music，已逐字节对齐验证）⚠️ MusicEx 尾标的文件暂不支持（密钥在客户端数据库） |
| `.mp3` / `.aac` / `.m4a` / `.mp4`（纯音频） | 原样封装，零转码 |
| `.flac` / `.wav` / `.ogg` / `.opus` / `.ape` / `.wma` / `.aiff` 等 | 只要 ffmpeg 认识就行，auto 模式下自动转 AAC |

输出默认 **`.mp4`**（H.264 视频 + 音频轨），同名输出，撞名自动加后缀；
也可以只出音频（`--format mp3/m4a/flac/wav`）。

## ⚙️ 工作原理

1. **NCM 解密**（`ncm_decoder.py`）：魔数校验 → AES-128-ECB 解出 RC4 密钥 → NCM 变体 RC4 流 XOR 还原音频字节；元数据块与内嵌封面一并解析。
2. **QMC 解密**（`qmc_decoder.py`）：尾部密钥 → TencentTEA 派生 → 按密钥长度选静态表 / 映射表 / RC4 变体还原。
3. **探测**：ffprobe 一次拿到时长、音频编码、`attached_pic` 封面流和 ID3 标签（歌名/歌手）。
4. **合成**：`color=c=black` 生成黑帧源，`-color_range 2` 标记全范围让解码端得到 Y=0 真黑；黑帧视频轨用显式时长与音频精确对齐。

> 💡 之前用 `-shortest` 对齐时，1fps 黑屏视频编码太快，会在音频结束前"冲过头"产生多余帧——所以改为给 color 滤镜显式 `d=<时长>`。

5. **封面模式**：内嵌封面导出成图片 → `split` 成两路：一路放大裁切 + 高斯模糊 + 压暗当背景，一路按比例缩到画面高度的 56% 居中叠上去 → `drawtext` 写歌名/歌手（文字走 `textfile=`，彻底绕开滤镜转义问题）。画面用 `-loop 1` 循环静态图，再用 `-t` 卡到音频时长。

## ⚠️ 声明

- 私有格式解密功能（`.ncm` / `.qmc*` / `.mflac*` / `.mgg*` 等）仅用于**转换你自己合法获取的音频文件**的本地格式，请勿用于绕过版权保护传播作品。支持下正版 🎵
- QMC 解密算法移植自 [unlock-music](https://git.unlock-music.dev/um/cli)（MIT License），感谢原作者。
- 本项目与网易云音乐、腾讯音乐官方无关。

## 🗺️ Roadmap

- [ ] py2app 独立打包（免 Python 环境，可直接分发给别人）
- [ ] 拖文件到 Dock 图标即可打开（依赖上面那项）
- [ ] 自定义 App 图标
- [ ] Windows 版

## 📄 License

[MIT](LICENSE) © [Kliocp-gpu](https://github.com/Kliocp-gpu)
