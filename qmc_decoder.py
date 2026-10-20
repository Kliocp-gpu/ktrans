#!/usr/bin/env python3
"""
qmc_decoder.py — QQ 音乐 QMC / MFLAC / MGG 私有格式解密（纯 Python，零第三方依赖）。

算法移植自 unlock-music（MIT License）开源实现：
    https://git.unlock-music.dev/um/cli （algo/qmc/*.go）
移植时用上游 Go 代码生成的确定性测试向量逐字节比对验证（真实 ekey 与真实加密文件都跑过）。

覆盖：
- QMCv1 静态密钥：尾标既不是 QTag/STag/MusicEx 也不是裸密钥长度时的默认算法
  （qmc0/qmc2/qmc3/qmcflac/qmcogg/tkm 等老格式常见）
- QMCv2 密钥派生：文件尾部内嵌 base64 ekey（QTag 尾标或裸密钥尾标），
  经 TencentTEA 派生出实际密钥，再按长度选 map / RC4 变体加密
- mflac / mgg：同一套派生与加密体系

不支持：MusicEx 尾标（"cex\\x00"）的文件 —— 它们的密钥存在 QQ 音乐桌面客户端的
MMKV 数据库里而不在文件中，需要客户端环境才能解，v2 暂不支持（会给出明确报错）。

免责声明：仅供用户转换自己合法获取的音频文件，请勿用于绕过版权保护传播作品。
"""

import base64
import math
import os
import struct
import tempfile

# ---------------------------------------------------------------------------
# TEA 块加密（Needham & Wheeler, 1994；腾讯用的是 32 轮变体）
# ---------------------------------------------------------------------------

_DELTA = 0x9E3779B9
_MASK32 = 0xFFFFFFFF


def _tea_block(block: bytes, key: bytes, rounds: int, encrypt: bool) -> bytes:
    """TEA 单块（8 字节）加解密。key 16 字节，大端。"""
    if len(block) != 8 or len(key) != 16:
        raise ValueError("TEA 需要 8 字节块与 16 字节密钥")
    v0, v1 = struct.unpack(">II", block)
    k0, k1, k2, k3 = struct.unpack(">IIII", key)
    if encrypt:
        s = 0
        for _ in range(rounds // 2):
            s = (s + _DELTA) & _MASK32
            v0 = (v0 + ((((v1 << 4) & _MASK32) + k0) ^ ((v1 + s) & _MASK32) ^ ((v1 >> 5) + k1))) & _MASK32
            v1 = (v1 + ((((v0 << 4) & _MASK32) + k2) ^ ((v0 + s) & _MASK32) ^ ((v0 >> 5) + k3))) & _MASK32
    else:
        s = (_DELTA * (rounds // 2)) & _MASK32
        for _ in range(rounds // 2):
            v1 = (v1 - ((((v0 << 4) & _MASK32) + k2) ^ ((v0 + s) & _MASK32) ^ ((v0 >> 5) + k3))) & _MASK32
            v0 = (v0 - ((((v1 << 4) & _MASK32) + k0) ^ ((v1 + s) & _MASK32) ^ ((v1 >> 5) + k1))) & _MASK32
            s = (s - _DELTA) & _MASK32
    return struct.pack(">II", v0, v1)


def _tencent_tea_decrypt(data: bytes, key: bytes) -> bytes:
    """腾讯私有 TEA 包装（带 1 字节 padLen + 2 字节盐 + 7 字节零校验，CBC 链接）。

    逐行对照 unlock-music 的 decryptTencentTea。
    """
    SALT_LEN, ZERO_LEN = 2, 7
    if len(data) % 8 != 0:
        raise ValueError("TEA 输入长度必须是 8 的倍数")
    if len(data) < 16:
        raise ValueError("TEA 输入太短")

    dest = bytearray(_tea_block(data[:8], key, 32, encrypt=False))
    pad_len = dest[0] & 0x7
    out_len = len(data) - 1 - pad_len - SALT_LEN - ZERO_LEN
    if out_len < 0:
        raise ValueError("TEA padLen 异常（密钥不匹配）")
    out = bytearray(out_len)

    iv_prev = bytes(8)
    iv_cur = data[:8]
    pos = 8
    dest_idx = 1 + pad_len

    def crypt_block():
        nonlocal iv_prev, iv_cur, pos, dest_idx, dest
        iv_prev = iv_cur
        iv_cur = data[pos:pos + 8]
        dest = bytearray(a ^ b for a, b in zip(dest, iv_cur))
        dest = bytearray(_tea_block(bytes(dest), key, 32, encrypt=False))
        pos += 8
        dest_idx = 0

    i = 1
    while i <= SALT_LEN:
        if dest_idx < 8:
            dest_idx += 1
            i += 1
        elif dest_idx == 8:
            crypt_block()

    out_pos = 0
    while out_pos < out_len:
        if dest_idx < 8:
            out[out_pos] = dest[dest_idx] ^ iv_prev[dest_idx]
            dest_idx += 1
            out_pos += 1
        elif dest_idx == 8:
            crypt_block()

    # 注意：上游零校验循环里 destIdx 不自增（7 次都查同一位置），
    # 这里必须原样照抄以保持与上游一致的接受/拒绝行为
    for _ in range(ZERO_LEN):
        if dest_idx >= 8:
            raise ValueError("TEA 零校验越界")
        if dest[dest_idx] != iv_prev[dest_idx]:
            raise ValueError("TEA 零校验失败（密钥不匹配）")

    return bytes(out)


# ---------------------------------------------------------------------------
# 密钥派生（ekey -> 实际加密密钥）
# ---------------------------------------------------------------------------

def _simple_make_key(salt: int, length: int) -> bytes:
    """上游用 tan() 生成；已验证 Python libm 结果与上游测试向量一致。"""
    return bytes(int(abs(math.tan(salt + i * 0.1)) * 100.0) & 0xFF for i in range(length))


_DERIVE_V2_KEY1 = bytes([
    0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40,
    0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28,
])
_DERIVE_V2_KEY2 = bytes([
    0x2A, 0x2A, 0x23, 0x21, 0x28, 0x23, 0x24, 0x25,
    0x26, 0x5E, 0x61, 0x31, 0x63, 0x5A, 0x2C, 0x54,
])
_RAW_KEY_PREFIX_V2 = b"QQMusic EncV2,Key:"


def derive_key(raw_key: bytes) -> bytes:
    """ekey（base64 文本）-> 实际密钥。对照 unlock-music deriveKey。"""
    raw = base64.b64decode(raw_key, validate=True)
    if raw.startswith(_RAW_KEY_PREFIX_V2):
        v2 = raw[len(_RAW_KEY_PREFIX_V2):]
        v2 = _tencent_tea_decrypt(v2, _DERIVE_V2_KEY1)
        v2 = _tencent_tea_decrypt(v2, _DERIVE_V2_KEY2)
        raw = base64.b64decode(v2, validate=True)
    return _derive_key_v1(raw)


def _derive_key_v1(raw: bytes) -> bytes:
    if len(raw) < 16:
        raise ValueError("ekey 太短")
    simple = _simple_make_key(106, 8)
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i * 2] = simple[i]
        tea_key[i * 2 + 1] = raw[i]
    rs = _tencent_tea_decrypt(raw[8:], bytes(tea_key))
    return raw[:8] + rs


# ---------------------------------------------------------------------------
# 三种内容加密
# ---------------------------------------------------------------------------

# QMCv1 静态密钥盒（256 字节，上游 cipher_static.go）
_STATIC_BOX = bytes([
    0x77, 0x48, 0x32, 0x73, 0xDE, 0xF2, 0xC0, 0xC8,
    0x95, 0xEC, 0x30, 0xB2, 0x51, 0xC3, 0xE1, 0xA0,
    0x9E, 0xE6, 0x9D, 0xCF, 0xFA, 0x7F, 0x14, 0xD1,
    0xCE, 0xB8, 0xDC, 0xC3, 0x4A, 0x67, 0x93, 0xD6,
    0x28, 0xC2, 0x91, 0x70, 0xCA, 0x8D, 0xA2, 0xA4,
    0xF0, 0x08, 0x61, 0x90, 0x7E, 0x6F, 0xA2, 0xE0,
    0xEB, 0xAE, 0x3E, 0xB6, 0x67, 0xC7, 0x92, 0xF4,
    0x91, 0xB5, 0xF6, 0x6C, 0x5E, 0x84, 0x40, 0xF7,
    0xF3, 0x1B, 0x02, 0x7F, 0xD5, 0xAB, 0x41, 0x89,
    0x28, 0xF4, 0x25, 0xCC, 0x52, 0x11, 0xAD, 0x43,
    0x68, 0xA6, 0x41, 0x8B, 0x84, 0xB5, 0xFF, 0x2C,
    0x92, 0x4A, 0x26, 0xD8, 0x47, 0x6A, 0x7C, 0x95,
    0x61, 0xCC, 0xE6, 0xCB, 0xBB, 0x3F, 0x47, 0x58,
    0x89, 0x75, 0xC3, 0x75, 0xA1, 0xD9, 0xAF, 0xCC,
    0x08, 0x73, 0x17, 0xDC, 0xAA, 0x9A, 0xA2, 0x16,
    0x41, 0xD8, 0xA2, 0x06, 0xC6, 0x8B, 0xFC, 0x66,
    0x34, 0x9F, 0xCF, 0x18, 0x23, 0xA0, 0x0A, 0x74,
    0xE7, 0x2B, 0x27, 0x70, 0x92, 0xE9, 0xAF, 0x37,
    0xE6, 0x8C, 0xA7, 0xBC, 0x62, 0x65, 0x9C, 0xC2,
    0x08, 0xC9, 0x88, 0xB3, 0xF3, 0x43, 0xAC, 0x74,
    0x2C, 0x0F, 0xD4, 0xAF, 0xA1, 0xC3, 0x01, 0x64,
    0x95, 0x4E, 0x48, 0x9F, 0xF4, 0x35, 0x78, 0x95,
    0x7A, 0x39, 0xD6, 0x6A, 0xA0, 0x6D, 0x40, 0xE8,
    0x4F, 0xA8, 0xEF, 0x11, 0x1D, 0xF3, 0x1B, 0x3F,
    0x3F, 0x07, 0xDD, 0x6F, 0x5B, 0x19, 0x30, 0x19,
    0xFB, 0xEF, 0x0E, 0x37, 0xF0, 0x0E, 0xCD, 0x16,
    0x49, 0xFE, 0x53, 0x47, 0x13, 0x1A, 0xBD, 0xA4,
    0xF1, 0x40, 0x19, 0x60, 0x0E, 0xED, 0x68, 0x09,
    0x06, 0x5F, 0x4D, 0xCF, 0x3D, 0x1A, 0xFE, 0x20,
    0x77, 0xE4, 0xD9, 0xDA, 0xF9, 0xA4, 0x2B, 0x76,
    0x1C, 0x71, 0xDB, 0x00, 0xBC, 0xFD, 0x0C, 0x6C,
    0xA5, 0x47, 0xF7, 0xF6, 0x00, 0x79, 0x4A, 0x11,
])

_WRAP = 0x7FFF  # 上游 getMask 的回绕点：offset > 0x7FFF 时对 0x7FFF 取模


def _xor(data: bytes, mask: bytes) -> bytes:
    """等长批量异或（big-int 方式，C 级速度）。"""
    return (int.from_bytes(data, "big") ^ int.from_bytes(mask, "big")).to_bytes(len(data), "big")


def _build_mask(g: bytes, offset: int, length: int) -> bytes:
    """按上游 getMask 规则铺出 [offset, offset+length) 的掩码。

    g[i] 是 offset=i 处的掩码（i ∈ [0, 0x8000]）。回绕规则：
    x <= 0x7FFF 时取 g[x]；x > 0x7FFF 时取 g[(x - 0x7FFF) % 0x7FFF]
    （与上游 `offset %= 0x7FFF` 等价，已用向量验证）。
    """
    out = bytearray(length)
    pos = 0
    while pos < length:
        x = offset + pos
        if x <= _WRAP:
            idx = x
            run = min(length - pos, _WRAP + 1 - idx)
        else:
            idx = (x - _WRAP) % _WRAP
            run = min(length - pos, _WRAP - idx)
        out[pos:pos + run] = g[idx:idx + run]
        pos += run
    return bytes(out)


def _static_table() -> bytes:
    g = bytearray(_WRAP + 1)
    for i in range(_WRAP + 1):
        g[i] = _STATIC_BOX[(i * i + 27) & 0xFF]
    return bytes(g)


def _map_table(key: bytes) -> bytes:
    size = len(key)
    g = bytearray(_WRAP + 1)
    for i in range(_WRAP + 1):
        idx = (i * i + 71214) % size
        r = ((idx & 0x7) + 4) % 8
        v = key[idx]
        g[i] = ((v << r) | (v >> r)) & 0xFF
    return bytes(g)


def decrypt_static(data: bytes, offset: int = 0) -> bytes:
    return _xor(data, _build_mask(_static_table(), offset, len(data)))


def decrypt_map(data: bytes, key: bytes, offset: int = 0) -> bytes:
    if not key:
        raise ValueError("map 密钥为空")
    return _xor(data, _build_mask(_map_table(key), offset, len(data)))


class _RC4Variant:
    """QMC 的 RC4 变体（key > 300 字节时使用）：分段重置 + 段内跳过。对照上游 cipher_rc4.go。"""

    SEG = 5120
    FIRST = 128

    def __init__(self, key: bytes):
        if not key:
            raise ValueError("rc4 密钥为空")
        self.n = len(key)
        self.key = key
        # 注意：上游 box 是 []byte，box[i] = byte(i)，n>256 时初值按 256 回绕
        box = [i & 0xFF for i in range(self.n)]
        j = 0
        for i in range(self.n):
            j = (j + box[i] + key[i % self.n]) % self.n
            box[i], box[j] = box[j], box[i]
        self.box = box
        # hash base：Go 里是 uint32 乘法，溢出会回绕，必须掩码
        h = 1
        for v in key:
            if v == 0:
                continue
            nh = (h * v) & _MASK32
            if nh == 0 or nh <= h:
                break
            h = nh
        self.hash = h

    def _segment_skip(self, seg_id: int) -> int:
        seed = self.key[seg_id % self.n]
        if seed == 0:
            # 上游此处 float64 除零得 +Inf，int64 转换行为依赖平台；
            # 真实密钥派生自可见字符，理论上不会遇到，防御性处理
            return 0
        return int(float(self.hash) / float((seg_id + 1) * seed) * 100.0) % self.n

    def keystream(self, length: int, offset: int = 0) -> bytes:
        """生成 [offset, offset+length) 的密钥流。逐段处理，对照上游 Decrypt。"""
        out = bytearray(length)
        done = 0
        to_process = length
        off = offset

        if off < self.FIRST:
            bs = min(to_process, self.FIRST - off)
            for i in range(bs):
                out[i] = self.key[self._segment_skip(off + i)]
            off += bs
            done += bs
            to_process -= bs
            if to_process == 0:
                return bytes(out)

        while to_process > 0:
            if off % self.SEG != 0:
                bs = min(to_process, self.SEG - off % self.SEG)
            elif to_process > self.SEG:
                bs = self.SEG
            else:
                bs = to_process
            self._keystream_segment(out, done, bs, off)
            off += bs
            done += bs
            to_process -= bs
        return bytes(out)

    def _keystream_segment(self, out: bytearray, start: int, bs: int, offset: int) -> None:
        box = self.box.copy()
        n = self.n
        j = k = 0
        skip = (offset % self.SEG) + self._segment_skip(offset // self.SEG)
        i = -skip
        end = bs
        while i < end:
            j = (j + 1) % n
            k = (box[j] + k) % n
            box[j], box[k] = box[k], box[j]
            if i >= 0:
                out[start + i] = box[(box[j] + box[k]) % n]
            i += 1


def decrypt_rc4(data: bytes, key: bytes, offset: int = 0) -> bytes:
    c = _RC4Variant(key)
    return _xor(data, c.keystream(len(data), offset))


# ---------------------------------------------------------------------------
# 格式嗅探 + 尾标解析 + 入口
# ---------------------------------------------------------------------------

def _detect_ext(buf: bytes) -> str | None:
    if buf[:4] == b"fLaC":
        return ".flac"
    if buf[:4] == b"OggS":
        return ".ogg"
    if buf[:3] == b"ID3" or (len(buf) >= 2 and buf[0] == 0xFF and (buf[1] & 0xE0) == 0xE0):
        return ".mp3"
    if buf[4:8] == b"ftyp":
        return ".m4a"
    if buf[:4] == b"RIFF" and buf[8:12] == b"WAVE":
        return ".wav"
    return None


def decode_qmc(path: str) -> str:
    """解码 QMC/MFLAC/MGG 文件，返回解密后音频的临时文件路径（调用方负责删除）。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 16:
        raise ValueError("文件太小，不是有效的 QMC 格式")

    size = len(data)
    last4 = data[-4:]

    if last4 == b"QTag":
        # QTag 尾标：最后 8 字节 = [metaLen(4, 大端)]["QTag"]，meta 为逗号分隔的
        # "ekey,songID,extra"
        meta_len = struct.unpack(">I", data[-8:-4])[0]
        meta_raw = data[size - 8 - meta_len:size - 8]
        items = meta_raw.split(b",")
        if len(items) != 3:
            raise ValueError("QTag 元数据格式异常")
        audio = data[:size - 8 - meta_len]
        key = derive_key(items[0])
    elif last4 == b"STag":
        raise ValueError("STag 尾标文件不含密钥，暂不支持")
    elif last4 == b"cex\x00":
        # MusicEx 尾标：密钥在 QQ 音乐桌面客户端的 MMKV 数据库里，不在文件中
        raise ValueError("该文件密钥存于 QQ 音乐客户端数据库（MusicEx），暂不支持；"
                         "请尝试用 QQ 音乐旧版下载的 QMC 格式")
    else:
        n = struct.unpack("<I", last4)[0]
        if 0 < n <= 0xFFFF:
            # 裸密钥尾标：最后 4 字节是密钥长度，密钥在其前面（base64 文本）
            raw_key = data[size - 4 - n:size - 4].rstrip(b"\x00")
            audio = data[:size - 4 - n]
            key = derive_key(raw_key)
        else:
            # 无密钥：QMCv1 静态加密
            audio = data
            key = b""

    if len(key) > 300:
        decrypted = decrypt_rc4(audio, key)
    elif len(key) > 0:
        decrypted = decrypt_map(audio, key)
    else:
        decrypted = decrypt_static(audio)

    ext = _detect_ext(decrypted)
    if ext is None:
        raise ValueError("解密后无法识别音频格式（密钥派生或算法可能已失效）")

    fd, tmp = tempfile.mkstemp(suffix=ext, prefix="qmc_")
    with os.fdopen(fd, "wb") as out:
        out.write(decrypted)
    return tmp


if __name__ == "__main__":
    # 自测：TEA 标准参考向量（零密钥、零明文 -> 41EA3A0A94BAA940）
    ct = _tea_block(bytes(8), bytes(16), 64, encrypt=True)
    assert ct == bytes.fromhex("41ea3a0a94baa940"), "TEA 加密参考向量不符"
    assert _tea_block(ct, bytes(16), 64, encrypt=False) == bytes(8), "TEA 解密往返失败"
    # 上游内联测试向量：simpleMakeKey(106, 8)
    assert _simple_make_key(106, 8) == bytes.fromhex("695646382b20150b"), "simpleMakeKey 向量不符"
    print("qmc_decoder 自测通过")
