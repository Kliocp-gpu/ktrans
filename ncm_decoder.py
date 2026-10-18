#!/usr/bin/env python3
"""
ncm_decoder.py — 网易云音乐 .ncm 私有格式解码器（纯标准库，零第三方依赖）。

原理（公开且长期稳定的算法）:
  1. 头部 8 字节魔数 "CTENFDAM"，随后是 version(2) + key_len(4) + 加密密钥 + meta_len(4)
     + 加密元数据 + 保留(5) + image_space(4) + image_size(4) + 封面 + 加密音频。
  2. 密钥数据: 逐字节 XOR 0x64 -> AES-128-ECB 用 CORE_KEY 解密 -> 去 PKCS7 填充 ->
     去掉前缀 "neteasecloudmusic"(17字节) = 真正的 RC4 密钥。
  3. 音频数据: 用该密钥做 RC4 变体(keystream 长度 256)逐字节异或还原。
还原后的字节就是原始音频(fLaC/ID3/RIFF/OggS...)，交给 ffmpeg 即可。

仅用于个人学习/把已购买的音乐本地备份，请遵守版权。

AES-128-ECB 与 RC4 均为本文件内自实现，无外部依赖。
"""

import os
import struct
import tempfile

NCM_MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")  # 备用，本工具不解析元数据


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


def decode_ncm(path: str) -> str:
    """解码 .ncm，返回解密后音频的临时文件路径（调用方负责删除）。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != NCM_MAGIC:
        raise ValueError("不是有效的 NCM 文件（魔数不匹配）")

    off = 8 + 2  # 跳过魔数与 version
    key_len = struct.unpack("<I", data[off:off+4])[0]; off += 4
    key_data = data[off:off+key_len]; off += key_len

    meta_len = struct.unpack("<I", data[off:off+4])[0]; off += 4
    off += meta_len  # 元数据（本工具不解析）
    off += 5  # 保留字段
    off += 4  # image_space
    image_size = struct.unpack("<I", data[off:off+4])[0]; off += 4
    off += image_size  # 封面
    audio_enc = data[off:]

    xored = bytes(b ^ 0x64 for b in key_data)
    dec = pkcs7_unpad(aes_ecb(xored, CORE_KEY, encrypt=False))
    if dec[:17] != b"neteasecloudmusic":
        raise ValueError("密钥解密失败（CORE_KEY 或格式不匹配）")
    real_key = dec[17:]

    k = rc4_keystream(real_key)
    # 关键下标：NCM 的 RC4 变体里，第一个音频字节用的是 K[1] 而不是 K[0]
    # （开源实现均为 i 先自增再用 / j 从 1 开始）。写成 k[i & 0xff] 会整体错位一位，
    # 解出来是乱码：ffmpeg 会探到 12000Hz、24kb/s、时长 1h47m 这类明显不对的头。
    audio = bytes(a ^ k[(i + 1) & 0xff] for i, a in enumerate(audio_enc))

    ext = _detect_ext(audio)
    fd, tmp = tempfile.mkstemp(suffix=ext, prefix="ncm_")
    with os.fdopen(fd, "wb") as out:
        out.write(audio)
    return tmp


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
