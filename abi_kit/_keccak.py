"""纯 Python 的 Keccak-256（Ethereum 口径）。

注意与 NIST SHA3-256 不同：Keccak 的多率填充域后缀为 ``0x01``
（SHA3-256 使用 ``0x06``），因此不能直接使用 :mod:`hashlib` 的
``sha3_256``。本实现仅覆盖事件签名所需的 Keccak-256：

- 位率 r = 1088 bit（136 字节），容量 c = 512 bit；
- 输出 256 bit（32 字节）。

实现遵循 Keccak 团队参考实现的直接写法，不追求吞吐。
"""

from __future__ import annotations

_MASK64 = (1 << 64) - 1

#: 24 轮轮常数。
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

#: 循环左移偏移量 ROTATION_OFFSETS[x][y]。
_ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rol(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK64 if shift else value


def _keccak_f(lanes: list[list[int]]) -> None:
    """就地执行 24 轮 Keccak-f[1600] 置换；lanes 为 5x5（索引 [x][y]）。"""
    for rc in _RC:
        # θ
        c = [
            lanes[x][0] ^ lanes[x][1] ^ lanes[x][2] ^ lanes[x][3] ^ lanes[x][4]
            for x in range(5)
        ]
        d = [c[(x + 4) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                lanes[x][y] ^= d[x]

        # ρ 与 π：lane (x, y) 移至 (y, 2x+3y mod 5)。
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(lanes[x][y], _ROTATION[x][y])

        # χ
        for x in range(5):
            for y in range(5):
                lanes[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])

        # ι
        lanes[0][0] ^= rc


_RATE = 136  # 字节
_OUTPUT = 32  # 字节


def keccak_256(data: bytes) -> bytes:
    """返回 ``data`` 的 Keccak-256 摘要（32 字节）。"""
    if not isinstance(data, bytes):
        raise TypeError(f"keccak_256 仅接受 bytes，得到 {type(data).__name__}")

    padded = bytearray(data)
    # 多率填充 pad10*1 并叠加域后缀 0x01（Keccak 口径）。
    padded.append(0x01)
    padded.extend(b"\x00" * ((_RATE - len(padded)) % _RATE))
    padded[-1] ^= 0x80

    lanes = [[0] * 5 for _ in range(5)]
    for start in range(0, len(padded), _RATE):
        block = padded[start:start + _RATE]
        for i in range(_RATE // 8):
            lanes[i % 5][i // 5] ^= int.from_bytes(block[i * 8:i * 8 + 8], "little")
        _keccak_f(lanes)

    out = bytearray()
    while len(out) < _OUTPUT:
        for y in range(5):
            for x in range(5):
                out.extend(lanes[x][y].to_bytes(8, "little"))
    return bytes(out[:_OUTPUT])
