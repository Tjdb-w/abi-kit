"""Keccak-256（Ethereum 使用的原始 Keccak 变体）。

Python 标准库的 :func:`hashlib.sha3_256` 是 FIPS 202 SHA3-256，填充域字节
为 0x06；Ethereum 事件签名使用填充域字节 0x01 的原始 Keccak-256，两者
输出不同。为保持“仅依赖标准库”，此处以 Keccak-f[1600] 置换直接实现：

- rate = 1088 bit（136 字节），capacity = 512 bit，输出 256 bit；
- 填充为 Keccak 的 pad10*1 与域字节 0x01 异或。
"""

from __future__ import annotations

_RATE = 136
_OUTPUT = 32

# Keccak-f[1600] 轮常数。
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# 旋转偏移：ROTATION[x][y]（以 lane 索引 5*x+y 表示时常见表格略有转置，
# 这里按 Keccak 规范步函数 rho 的 (x, y) 坐标给出）。
_ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

_MASK64 = (1 << 64) - 1


def _rotl64(value: int, amount: int) -> int:
    if amount == 0:
        return value
    return ((value << amount) | (value >> (64 - amount))) & _MASK64


def _keccak_f(state: list[int]) -> None:
    """对 25 个 64 位 lane（索引 5*x+y）原地执行 24 轮置换。"""
    for round_index in range(24):
        # θ
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
             for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            dx = d[x]
            for y in range(5):
                state[x + 5 * y] ^= dx

        # ρ 与 π：lane 由 (x, y) 重排到 (y, 2x+3y mod 5) 并旋转。
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(
                    state[x + 5 * y], _ROTATION[x][y]
                )

        # χ
        for y in range(5):
            row = [b[x + 5 * y] for x in range(5)]
            for x in range(5):
                state[x + 5 * y] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])

        # ι
        state[0] ^= _RC[round_index]


def keccak256(data: bytes) -> bytes:
    """返回 ``data`` 的 Keccak-256 摘要（32 字节）。"""
    if not isinstance(data, bytes):
        raise TypeError(f"keccak256 输入必须是 bytes，得到 {type(data).__name__}")

    state = [0] * 25

    # Keccak 填充：pad10*1 后首字节与域分隔 0x01 异或。
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\x00" * ((-len(data) - 1) % _RATE))
    padded[-1] ^= 0x80

    for start in range(0, len(padded), _RATE):
        block = padded[start:start + _RATE]
        for lane_index in range(_RATE // 8):
            offset = lane_index * 8
            state[lane_index] ^= int.from_bytes(
                block[offset:offset + 8], "little", signed=False
            )
        _keccak_f(state)

    #  squeezing：rate 足以容纳 32 字节输出，取前 4 个 lane 即可。
    return b"".join(state[i].to_bytes(8, "little", signed=False) for i in range(4))
