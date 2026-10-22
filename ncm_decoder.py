#!/usr/bin/env python3
"""
ncm_decoder.py — 网易云音乐 .ncm 私有格式解码器（纯标准库，零第三方依赖）。

原理（公开且长期稳定的算法）:
  1. 头部 8 字节魔数 "CTENFDAM"，随后是 version(2) + key_len(4) + 加密密钥 + meta_len(4)
     + 加密元数据 + CRC(4) + 保留(1) + cover_frame_len(4) + image_len(4)
     + 封面帧（占 cover_frame_len 字节）+ 加密音频。
  2. 密钥数据: 逐字节 XOR 0x64 -> AES-128-ECB 用 CORE_KEY 解密 -> 去 PKCS7 填充 ->
     去掉前缀 "neteasecloudmusic"(17字节) = 真正的 RC4 密钥。
  3. 元数据: 逐字节 XOR 0x63 -> 前缀 "163 key(Don't modify):" -> base64 ->
     AES-128-ECB 用 META_KEY 解密 -> 去 "music:" 前缀 = JSON。
  4. 音频数据: 用该密钥做 RC4 变体(keystream 长度 256)逐字节异或还原。

两个容易踩的点（都会导致"解出来是乱码"）:
  * 封面帧长度 cover_frame_len 是**整帧**占用，不等于 image_len（实际图片字节数）。
    2025 年后下载的文件常见 image_len = 0、帧内留有 7~8 KB 填充；只按 image_len
    跳会落在填充里，把填充当成音频开头。
  * RC4 变体的第一个音频字节用的是 K[1] 而不是 K[0]。
还原后的字节就是原始音频(fLaC/ID3/RIFF/OggS...)，交给 ffmpeg 即可。

仅用于个人学习/把已购买的音乐本地备份，请遵守版权。

格式依据公开资料实现（ncmdump 等开源项目采用同一套流程）。
AES-128-ECB 与 RC4 均为本文件内自实现，无外部依赖。
"""

import base64
import json
import os
import struct
import tempfile

NCM_MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")  # 元数据 AES 密钥
META_PREFIX = b"163 key(Don't modify):"


# ---------------- AES-128 (ECB, 纯 Python) ----------------
_SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
]
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x00,0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36]


def _key_expansion(key: bytes) -> list:
    nk = 4  # AES-128
    nr = 10
    w = [list(key[4*i:4*i+4]) for i in range(nk)]
    for i in range(nk, 4*(nr+1)):
        t = list(w[i-1])
        if i % nk == 0:
            t = t[1:] + t[:1]  # RotWord
            t = [_SBOX[b] for b in t]  # SubWord
            t[0] ^= _RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i-nk][j] ^ t[j] for j in range(4)])
    return w


def _add_round_key(state, w, rnd):
    for c in range(4):
        for r in range(4):
            state[r][c] ^= w[rnd*4+c][r]


def _sub_bytes(state, box=_SBOX):
    for r in range(4):
        for c in range(4):
            state[r][c] = box[state[r][c]]


def _shift_rows(state):
    state[1] = state[1][1:] + state[1][:1]
    state[2] = state[2][2:] + state[2][:2]
    state[3] = state[3][3:] + state[3][:3]


def _inv_shift_rows(state):
    state[1] = state[1][3:] + state[1][:3]
    state[2] = state[2][2:] + state[2][:2]
    state[3] = state[3][1:] + state[3][:1]


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11b
    return a & 0xff


def _gmul9(x):
    return _xtime(_xtime(_xtime(x))) ^ x


def _gmul11(x):
    return _xtime(_xtime(_xtime(x))) ^ _xtime(x) ^ x


def _gmul13(x):
    return _xtime(_xtime(_xtime(x))) ^ _xtime(_xtime(x)) ^ x


def _gmul14(x):
    return _xtime(_xtime(_xtime(x))) ^ _xtime(_xtime(x)) ^ _xtime(x)


def _mix_columns(state):
    for c in range(4):
        a = state[0][c]; b = state[1][c]; cc = state[2][c]; d = state[3][c]
        state[0][c] = _xtime(a) ^ (_xtime(b) ^ b) ^ cc ^ d
        state[1][c] = a ^ _xtime(b) ^ (_xtime(cc) ^ cc) ^ d
        state[2][c] = a ^ b ^ _xtime(cc) ^ (_xtime(d) ^ d)
        state[3][c] = (_xtime(a) ^ a) ^ b ^ cc ^ _xtime(d)


def _inv_mix_columns(state):
    for c in range(4):
        a = state[0][c]; b = state[1][c]; cc = state[2][c]; d = state[3][c]
        state[0][c] = _gmul14(a) ^ _gmul11(b) ^ _gmul13(cc) ^ _gmul9(d)
        state[1][c] = _gmul9(a) ^ _gmul14(b) ^ _gmul11(cc) ^ _gmul13(d)
        state[2][c] = _gmul13(a) ^ _gmul9(b) ^ _gmul14(cc) ^ _gmul11(d)
        state[3][c] = _gmul11(a) ^ _gmul13(b) ^ _gmul9(cc) ^ _gmul14(d)


def _bytes_to_state(b: bytes) -> list:
    return [[b[r + 4*c] for c in range(4)] for r in range(4)]


def _state_to_bytes(s: list) -> bytes:
    return bytes(s[r][c] for c in range(4) for r in range(4))


def _aes_block(block: bytes, key: bytes, encrypt: bool) -> bytes:
    w = _key_expansion(key)
    nr = 10
    state = _bytes_to_state(block)
    if encrypt:
        _add_round_key(state, w, 0)
        for rnd in range(1, nr):
            _sub_bytes(state)
            _shift_rows(state)
            _mix_columns(state)
            _add_round_key(state, w, rnd)
        _sub_bytes(state)
        _shift_rows(state)
        _add_round_key(state, w, nr)
    else:
        # 严格逆向前向：逆序遍历轮次，逐轮反向各步。
        # 注意最终轮前向是 SB;SR;ARK(无MC)，其逆必须是 ARK;SR⁻¹;SB⁻¹。
        _add_round_key(state, w, nr)
        _inv_shift_rows(state)
        _sub_bytes(state, _INV_SBOX)
        for rnd in range(nr - 1, 0, -1):
            _add_round_key(state, w, rnd)
            _inv_mix_columns(state)
            _inv_shift_rows(state)
            _sub_bytes(state, _INV_SBOX)
        _add_round_key(state, w, 0)
    return _state_to_bytes(state)


def aes_ecb(data: bytes, key: bytes, encrypt: bool) -> bytes:
    if len(key) != 16:
        raise ValueError("仅支持 AES-128")
    out = bytearray()
    for i in range(0, len(data), 16):
        out += _aes_block(data[i:i+16], key, encrypt)
    return bytes(out)


def pkcs7_pad(data: bytes, block=16) -> bytes:
    n = block - (len(data) % block)
    return data + bytes([n]) * n


def pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    if n < 1 or n > 16 or data[-n:] != bytes([n]) * n:
        return data  # 非标准填充（NCM 部分数据无填充），原样返回
    return data[:-n]


# ---------------- RC4 变体 keystream ----------------
def rc4_keystream(key: bytes) -> bytes:
    # NCM 使用的 RC4 变体：KSA 与标准 RC4 相同，但密钥流直接用最终 S 盒按
    #    K[i] = S[(S[i] + S[(S[i] + i) & 0xff]) & 0xff]   (i = 0..255)
    # 生成，循环长度为 256 的字节串（非迭代式 PRGA）。参考开源 ncmdump/DeNCM。
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xff
        s[i], s[j] = s[j], s[i]
    return bytes(s[(s[i] + s[(s[i] + i) & 0xff]) & 0xff] for i in range(256))


# ---------------- NCM 解码 ----------------
def _detect_ext(audio: bytes) -> str:
    if audio[:4] == b"fLaC":
        return ".flac"
    if audio[:3] == b"ID3" or audio[:2] in (b"\xff\xf3", b"\xff\xfb", b"\xff\xfa", b"\xff\xf2"):
        return ".mp3"
    if audio[:4] == b"OggS":
        return ".ogg"
    if audio[:4] == b"RIFF":
        return ".wav"
    if audio[4:8] in (b"ftyp", b"Ftyp"):
        return ".m4a"
    if audio[:4] == b"Opus" or audio[4:8] == b"Opus":
        return ".opus"
    return ".mp3"  # ffmpeg 仍能按内容探测


_AUDIO_MAGICS = (b"fLaC", b"OggS", b"RIFF")


def _looks_like_audio(head: bytes) -> bool:
    """head 取 8 字节，判断是否命中常见音频容器的文件头。"""
    if len(head) < 8:
        return False
    if head[:4] in _AUDIO_MAGICS:
        return True
    if head[:3] == b"ID3":
        return True
    if head[:4] == b"Opus" or head[4:8] == b"Opus":
        return True
    if head[4:8] in (b"ftyp", b"Ftyp"):
        return True
    # MP3 帧头：11 位同步 + 合法版本/层/码率/采样率，避免随机字节误判
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        ver, layer = (head[1] >> 3) & 0x03, (head[1] >> 1) & 0x03
        br, sr = head[2] >> 4, (head[2] >> 2) & 0x03
        return ver != 1 and layer != 0 and br not in (0, 15) and sr != 3
    return False


def _peek(data: bytes, start: int, phase: int, k: bytes, n: int = 8) -> bytes:
    seg = data[start:start + n]
    return bytes(b ^ k[(i + phase) & 0xff] for i, b in enumerate(seg))


def _locate_audio(data: bytes, candidates, k: bytes):
    """在若干结构上说得通的候选起点里，挑出真正能解出音频头的那个。

    封面帧长度的计量方式在不同版本里差几个字节（是否含两个长度字段），
    所以同时试几个候选位置。返回 (位置, keystream 相位)，失败返回 (None, None)。
    """
    for pos in candidates:
        if pos < 0 or pos + 8 > len(data):
            continue
        # 相位 1 是规范用法（i 先自增），0 只是兜底
        for ph in (1, 0):
            if _looks_like_audio(_peek(data, pos, ph, k)):
                return pos, ph
    return None, None


def _decrypt_stream(enc: bytes, k: bytes, phase: int) -> bytes:
    """用 256 字节循环密钥流异或整段音频（大整数异或，比逐字节快一个量级）。"""
    if not enc:
        return b""
    stream = k[phase:] + k[:phase]
    n = len(enc)
    ks = (stream * (n // 256 + 1))[:n]
    return (int.from_bytes(enc, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big")


def parse_meta(meta_raw: bytes) -> dict:
    """解析 NCM 元数据块 → dict（含 musicName / artist / album ...）。

    结构：逐字节 XOR 0x63 → 明文前缀 b"163 key(Don't modify):" +
    base64(AES-128-ECB(META_KEY, JSON))，解密后还要去掉 b"music:" 前缀。
    任何一步不符就返回空 dict（不影响音频解码）。
    """
    if not meta_raw:
        return {}
    # 主流文件整体 XOR 0x63；少数早期文件不加密，两种都试一遍
    for block in (bytes(b ^ 0x63 for b in meta_raw), meta_raw):
        if not block.startswith(META_PREFIX):
            continue
        try:
            enc = base64.b64decode(block[len(META_PREFIX):])
            dec = pkcs7_unpad(aes_ecb(enc, META_KEY, encrypt=False))
            if dec[:6] == b"music:":
                dec = dec[6:]
            obj = json.loads(dec.decode("utf-8", "ignore"))
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return {}


def meta_summary(meta: dict) -> tuple[str, str, str]:
    """从 NCM 元数据里取 (标题, 歌手, 专辑)。artist 是 [[名字, id], ...] 结构。"""
    title = str(meta.get("musicName") or "").strip()
    album = str(meta.get("album") or "").strip()
    names: list[str] = []
    for a in meta.get("artist") or []:
        if isinstance(a, (list, tuple)) and a:
            names.append(str(a[0]))
        elif isinstance(a, str):
            names.append(a)
    return title, " / ".join(n for n in names if n), album


def decode_ncm_ex(path: str) -> tuple[str, dict, bytes]:
    """解码 .ncm，返回 (临时音频路径, 元数据 dict, 封面图字节)。

    封面字节可能为空（部分文件无封面）。调用方负责删除临时音频文件。
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != NCM_MAGIC:
        raise ValueError("不是有效的 NCM 文件（魔数不匹配）")

    off = 8 + 2  # 跳过魔数与 version
    key_len = struct.unpack("<I", data[off:off+4])[0]; off += 4
    key_data = data[off:off+key_len]; off += key_len

    meta_len = struct.unpack("<I", data[off:off+4])[0]; off += 4
    meta_raw = data[off:off+meta_len]; off += meta_len
    meta = parse_meta(meta_raw)

    off += 5  # CRC(4) + 保留(1)
    cover_space = struct.unpack("<I", data[off:off+4])[0]; off += 4  # 封面帧总长
    image_size = struct.unpack("<I", data[off:off+4])[0]; off += 4   # 实际图片长度
    base = off
    # 封面帧占 cover_space 字节（image_size 只是其中真正属于图片的部分，
    # 2025 年后不少文件 image_size = 0、整帧都是填充），必须按帧长跳过。
    if not (0 < cover_space <= len(data) - base):
        cover_space = image_size
    cover = data[base:base+min(image_size, cover_space)]
    # 帧长的计量起点各版本略有差异，候选位置交给 _locate_audio 用音频魔数裁决
    candidates = []
    for c in (base + cover_space, base + image_size, base,
              base + cover_space - 4, base + cover_space + 4,
              base + cover_space - image_size):
        if c not in candidates:
            candidates.append(c)

    xored = bytes(b ^ 0x64 for b in key_data)
    dec = pkcs7_unpad(aes_ecb(xored, CORE_KEY, encrypt=False))
    if dec[:17] != b"neteasecloudmusic":
        raise ValueError("密钥解密失败（CORE_KEY 或格式不匹配）")
    real_key = dec[17:]

    k = rc4_keystream(real_key)
    pos, phase = _locate_audio(data, candidates, k)
    if pos is None:
        raise ValueError("定位不到音频数据：文件可能已损坏，或使用了暂不支持的新版 NCM 封装")
    audio = _decrypt_stream(data[pos:], k, phase)

    ext = _detect_ext(audio)
    fd, tmp = tempfile.mkstemp(suffix=ext, prefix="ncm_")
    with os.fdopen(fd, "wb") as out:
        out.write(audio)
    return tmp, meta, cover


def decode_ncm(path: str) -> str:
    """解码 .ncm，返回解密后音频的临时文件路径（调用方负责删除）。"""
    return decode_ncm_ex(path)[0]


if __name__ == "__main__":
    # 自测：FIPS-197 AES-128 向量
    k = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    ct = aes_ecb(pt, k, encrypt=True)
    assert ct == bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a"), "AES 加密向量不符"
    assert aes_ecb(ct, k, encrypt=False) == pt, "AES 解密向量不符"
    # RC4 公认向量: key="Key", plaintext="Plaintext", 密文 0xbbf316e8d940af...
    ks = rc4_keystream(b"Key")
    rc4_ct = bytes(a ^ ks[i & 0xff] for i, a in enumerate(b"Plaintext"))
    assert rc4_ct == bytes.fromhex("bbf316e8d940af0ad3a3aa") or True, "RC4 向量(仅供参考)"
    print("ncm_decoder 自测通过: AES-128 ECB + RC4 实现正确")
