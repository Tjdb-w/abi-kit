"""Solidity ABI 值层编解码。

遵循 Solidity ABI 的 head/tail 布局：

- 静态类型直接占用一个或多个 32 字节字；
- 动态类型（string / 动态 bytes / 动态数组 / 含动态成员的定长数组
  或元组）在 head 中放 32 字节偏移，实体置于 tail；
- 动态数组实体以长度字开头，随后是与“等长定长数组”相同的块；
- 偏移均相对于所属块（数组元素块的起点在长度字之后）。

解码采用严格规范：块内动态成员必须按声明顺序紧密排列（偏移恰好指向前
一动态实体的终点），不允许空洞、重叠或倒序；所有填充字节必须为零。

值口径：
- uintM/intM -> int，bool -> bool，string -> str；
- bytes/bytesM -> bytes；
- address -> 小写 ``"0x" + 40 个十六进制字符``；
- fixedMxN/ufixedMxN -> decimal.Decimal（小数位数恰为 N）；
- function -> 恰好 24 字节的 bytes；
- 数组 -> list，元组 -> tuple。

所有值不匹配、非法编码与不适用的类型对象统一抛出
:class:`abi_kit.ABIValueError`；类型层错误仍属
:class:`abi_kit.ABITypeError`，本模块不改变其适用范围。
"""

from __future__ import annotations

from decimal import Decimal

from ._exceptions import ABIValueError
from ._types import (
    ABIType,
    ArrayType,
    ElementaryType,
    FixedPointType,
    FunctionType,
    TupleType,
)

_WORD = 32


# ---- 类型静态性质 --------------------------------------------------------


def _is_dynamic(abi_type: ABIType) -> bool:
    if isinstance(abi_type, ElementaryType):
        return abi_type.kind == "string" or (
            abi_type.kind == "bytes" and abi_type.byte_size is None
        )
    if isinstance(abi_type, (FixedPointType, FunctionType)):
        return False
    if isinstance(abi_type, ArrayType):
        return abi_type.length is None or _is_dynamic(abi_type.element_type)
    if isinstance(abi_type, TupleType):
        return any(_is_dynamic(c) for c in abi_type.components)
    raise ABIValueError(f"无法编解码非 ABIType 对象：{abi_type!r}")


def _static_size(abi_type: ABIType) -> int:
    """静态类型作为一个整体占用的字节数（仅对非动态类型调用）。"""
    if isinstance(abi_type, (ElementaryType, FixedPointType, FunctionType)):
        return _WORD
    if isinstance(abi_type, ArrayType):
        return abi_type.length * _static_size(abi_type.element_type)
    if isinstance(abi_type, TupleType):
        return sum(_static_size(c) for c in abi_type.components)
    raise ABIValueError(f"无法编解码非 ABIType 对象：{abi_type!r}")


def _head_slot(abi_type: ABIType) -> int:
    """该类型作为块成员时在 head 中占用的字节数。"""
    return _WORD if _is_dynamic(abi_type) else _static_size(abi_type)


# ---- 编码 ----------------------------------------------------------------


def _uint_word(value: int) -> bytes:
    return value.to_bytes(_WORD, "big", signed=False)


def _address_bytes(value) -> bytes:
    if not isinstance(value, str):
        raise ABIValueError(f"address 需要十六进制字符串，得到 {type(value).__name__}")
    text = value[2:] if value.startswith(("0x", "0X")) else value
    if len(text) != 40 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ABIValueError(
            f"address 必须是带或不带 0x 前缀的 40 位十六进制字符串：{value!r}"
        )
    return bytes.fromhex(text)


def _len_prefixed(payload: bytes) -> bytes:
    padding = (-len(payload)) % _WORD
    return _uint_word(len(payload)) + payload + b"\x00" * padding


def _encode_elementary(t: ElementaryType, value) -> bytes:
    kind = t.kind
    if kind == "uint":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ABIValueError(
                f"uint{t.bit_size} 需要 int，得到 {type(value).__name__}"
            )
        bits = t.bit_size
        if not 0 <= value < 1 << bits:
            raise ABIValueError(f"uint{bits} 超出范围 [0, {2**bits - 1}]：{value}")
        return _uint_word(value)
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ABIValueError(
                f"int{t.bit_size} 需要 int，得到 {type(value).__name__}"
            )
        bits = t.bit_size
        low = -(1 << (bits - 1))
        high = (1 << (bits - 1)) - 1
        if not low <= value <= high:
            raise ABIValueError(f"int{bits} 超出补码范围 [{low}, {high}]：{value}")
        return value.to_bytes(_WORD, "big", signed=True)
    if kind == "address":
        return b"\x00" * 12 + _address_bytes(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ABIValueError(f"bool 需要 Python bool，得到 {type(value).__name__}")
        return _uint_word(1 if value else 0)
    if kind == "string":
        if not isinstance(value, str):
            raise ABIValueError(f"string 需要 str，得到 {type(value).__name__}")
        try:
            payload = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ABIValueError(f"字符串无法编码为 UTF-8：{exc}") from exc
        return _len_prefixed(payload)
    # bytes（动态）或 bytesM
    if not isinstance(value, bytes):
        label = "bytes" if t.byte_size is None else f"bytes{t.byte_size}"
        raise ABIValueError(f"{label} 需要 bytes，得到 {type(value).__name__}")
    if t.byte_size is None:
        return _len_prefixed(value)
    if len(value) != t.byte_size:
        raise ABIValueError(
            f"bytes{t.byte_size} 需要恰好 {t.byte_size} 字节，得到 {len(value)} 字节"
        )
    return value + b"\x00" * (_WORD - t.byte_size)


def _fixed_label(t: FixedPointType) -> str:
    prefix = "fixed" if t.signed else "ufixed"
    return f"{prefix}{t.bit_size}x{t.scale}"


def _fixed_range(t: FixedPointType) -> tuple[int, int]:
    """缩放整数（值 × 10^N）的合法闭区间。"""
    if t.signed:
        return -(1 << (t.bit_size - 1)), (1 << (t.bit_size - 1)) - 1
    return 0, (1 << t.bit_size) - 1


def _encode_fixed_point(t: FixedPointType, value) -> bytes:
    label = _fixed_label(t)
    if not isinstance(value, Decimal):
        raise ABIValueError(
            f"{label} 需要 decimal.Decimal，得到 {type(value).__name__}"
        )
    if not value.is_finite():
        raise ABIValueError(f"{label} 需要有限小数，得到 {value}")
    low, high = _fixed_range(t)
    # 用 as_tuple 精确计算缩放整数 value × 10^N，不经过 decimal 上下文，
    # 避免默认精度对有效数字的舍入。
    sign, digits, exponent = value.as_tuple()
    coeff = 0
    for digit in digits:
        coeff = coeff * 10 + digit
    shift = exponent + t.scale
    if coeff == 0:
        scaled = 0
    elif shift >= 0:
        # 2^256 < 10^78：有效数字位数与正指数一旦放大到 78 位十进制，
        # 缩放整数必然越界，先拦截以避免构造天文数字。
        if len(digits) - 1 + shift >= 78:
            raise ABIValueError(
                f"{label} 缩放整数超出范围 [{low}, {high}]：{value}"
            )
        scaled = coeff * 10 ** shift
    else:
        if -shift >= len(digits):
            # 除数已大于系数本身，缩放后必非整数。
            raise ABIValueError(
                f"{label} 的有效小数位不能超过 {t.scale}：{value}"
            )
        scaled, remainder = divmod(coeff, 10 ** (-shift))
        if remainder:
            raise ABIValueError(
                f"{label} 的有效小数位不能超过 {t.scale}：{value}"
            )
    if sign:
        scaled = -scaled
    if not low <= scaled <= high:
        raise ABIValueError(
            f"{label} 缩放整数超出范围 [{low}, {high}]：{value}"
        )
    return scaled.to_bytes(_WORD, "big", signed=t.signed)


def _encode_function_value(t: FunctionType, value) -> bytes:
    if not isinstance(value, bytes):
        raise ABIValueError(f"function 需要 bytes，得到 {type(value).__name__}")
    if len(value) != t.byte_size:
        raise ABIValueError(
            f"function 需要恰好 {t.byte_size} 字节，得到 {len(value)} 字节"
        )
    return value + b"\x00" * (_WORD - t.byte_size)


def _encode_block(member_types: list[ABIType], values: list) -> bytes:
    """编码一个 head/tail 块（元组成员或一组同构数组元素）。

    动态成员在 head 中的偏移相对于本块起点，动态实体按声明顺序紧密
    拼接在整块 head 之后。
    """
    head_chunks: list[bytes] = []
    dynamic_entities: list[tuple[int, bytes]] = []
    head_length = 0
    for index, (member_type, value) in enumerate(zip(member_types, values)):
        if _is_dynamic(member_type):
            head_chunks.append(b"\x00" * _WORD)
            dynamic_entities.append((index, _encode(member_type, value)))
        else:
            head_chunks.append(_encode(member_type, value))
        head_length += _head_slot(member_type)

    tail = bytearray()
    for index, entity in dynamic_entities:
        head_chunks[index] = _uint_word(head_length + len(tail))
        tail += entity
    return b"".join(head_chunks) + bytes(tail)


def _encode(abi_type: ABIType, value) -> bytes:
    """把值编码为一个自洽实体（动态类型即其 tail 实体）。"""
    if isinstance(abi_type, ElementaryType):
        return _encode_elementary(abi_type, value)
    if isinstance(abi_type, FixedPointType):
        return _encode_fixed_point(abi_type, value)
    if isinstance(abi_type, FunctionType):
        return _encode_function_value(abi_type, value)
    if isinstance(abi_type, ArrayType):
        if not isinstance(value, list):
            raise ABIValueError(f"数组需要 list，得到 {type(value).__name__}")
        element_type = abi_type.element_type
        if abi_type.length is not None and len(value) != abi_type.length:
            raise ABIValueError(
                f"定长数组长度应为 {abi_type.length}，得到 {len(value)}"
            )
        block = _encode_block([element_type] * len(value), list(value))
        if abi_type.length is None:
            return _uint_word(len(value)) + block
        return block
    if isinstance(abi_type, TupleType):
        if not isinstance(value, tuple):
            raise ABIValueError(f"元组需要 tuple，得到 {type(value).__name__}")
        if len(value) != len(abi_type.components):
            raise ABIValueError(
                f"元组长度应为 {len(abi_type.components)}，得到 {len(value)}"
            )
        return _encode_block(list(abi_type.components), list(value))
    raise ABIValueError(f"无法编解码非 ABIType 对象：{abi_type!r}")


# ---- 解码 ----------------------------------------------------------------


def _read_word(data: bytes, pos: int, bound: int) -> bytes:
    if pos < 0 or pos + _WORD > bound:
        raise ABIValueError("编码数据长度不足：缺少完整的 32 字节字")
    return data[pos:pos + _WORD]


def _decode_elementary(t: ElementaryType, data: bytes, pos: int, bound: int):
    """解码基础类型实体，返回 ``(值, 终点位置)``。"""
    kind = t.kind
    if kind == "string" or (kind == "bytes" and t.byte_size is None):
        length = int.from_bytes(_read_word(data, pos, bound), "big", signed=False)
        payload_start = pos + _WORD
        padded_length = ((length + _WORD - 1) // _WORD) * _WORD
        end = payload_start + padded_length
        if end > bound:
            raise ABIValueError("动态数据长度超出编码边界")
        payload_end = payload_start + length
        if data[payload_end:end] != b"\x00" * (end - payload_end):
            raise ABIValueError("动态数据尾部填充非零，编码不规范")
        payload = data[payload_start:payload_end]
        if kind == "string":
            try:
                value = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ABIValueError(f"非法 UTF-8 字符串：{exc}") from exc
        else:
            value = payload
        return value, end

    word = _read_word(data, pos, bound)
    end = pos + _WORD
    if kind == "uint":
        value = int.from_bytes(word, "big", signed=False)
        if value >= 1 << t.bit_size:
            raise ABIValueError(f"uint{t.bit_size} 解码值越界：{value}")
        return value, end
    if kind == "int":
        value = int.from_bytes(word, "big", signed=True)
        bits = t.bit_size
        low = -(1 << (bits - 1))
        high = (1 << (bits - 1)) - 1
        if not low <= value <= high:
            raise ABIValueError(f"int{bits} 解码值超出补码范围 [{low}, {high}]：{value}")
        return value, end
    if kind == "address":
        if word[:12] != b"\x00" * 12:
            raise ABIValueError("address 高位填充非零，超出 20 字节范围")
        return "0x" + word[12:].hex(), end
    if kind == "bool":
        if word == b"\x00" * _WORD:
            return False, end
        if word == b"\x00" * (_WORD - 1) + b"\x01":
            return True, end
        raise ABIValueError("非法布尔字：既非全 0 也非末尾字节为 1")
    # bytesM
    if word[t.byte_size:] != b"\x00" * (_WORD - t.byte_size):
        raise ABIValueError(f"bytes{t.byte_size} 尾部填充非零，编码不规范")
    return word[:t.byte_size], end


def _decode_fixed_point(t: FixedPointType, data: bytes, pos: int, bound: int):
    """解码固定小数实体，返回 ``(Decimal 值, 终点位置)``。"""
    word = _read_word(data, pos, bound)
    end = pos + _WORD
    scaled = int.from_bytes(word, "big", signed=t.signed)
    low, high = _fixed_range(t)
    if not low <= scaled <= high:
        raise ABIValueError(
            f"{_fixed_label(t)} 解码缩放整数超出范围 [{low}, {high}]：{scaled}"
        )
    # 直接按 (符号, 系数数字, 指数 -N) 构造 Decimal，小数位数恰为 N，
    # 不经过 decimal 上下文，任何位宽都不会被舍入。
    digits = tuple(int(c) for c in str(abs(scaled)))
    value = Decimal((1 if scaled < 0 else 0, digits, -t.scale))
    return value, end


def _decode_function_value(t: FunctionType, data: bytes, pos: int, bound: int):
    """解码 function 实体，返回 ``(24 字节值, 终点位置)``。"""
    word = _read_word(data, pos, bound)
    end = pos + _WORD
    if word[t.byte_size:] != b"\x00" * (_WORD - t.byte_size):
        raise ABIValueError("function 尾部填充非零，编码不规范")
    return word[:t.byte_size], end


def _decode_block(
    member_types: list[ABIType], data: bytes, base: int, bound: int
):
    """解码一个 head/tail 块，返回 ``(按声明顺序的值列表, 块终点)``。

    动态成员的偏移必须恰好指向前一动态实体的终点（紧密排列），否则
    视为非法编码。
    """
    head_length = 0
    for member_type in member_types:
        head_length += _head_slot(member_type)
    head_end = base + head_length
    if head_end > bound:
        raise ABIValueError("编码数据长度不足：块 head 越界")

    values = [None] * len(member_types)
    cursor = head_end
    head_pos = base
    for index, member_type in enumerate(member_types):
        if _is_dynamic(member_type):
            offset = int.from_bytes(
                _read_word(data, head_pos, bound), "big", signed=False
            )
            target = base + offset
            if target != cursor:
                raise ABIValueError(
                    f"动态偏移非法：成员 {index} 应指向 {cursor}，实际指向 {target}"
                )
            values[index], cursor = _decode(member_type, data, target, bound)
        else:
            values[index], end = _decode(member_type, data, head_pos, bound)
            if end != head_pos + _static_size(member_type):
                raise ABIValueError("静态成员尺寸与声明不一致")
        head_pos += _head_slot(member_type)
    return values, cursor


def _decode(abi_type: ABIType, data: bytes, pos: int, bound: int):
    """从 ``pos`` 解码一个实体，返回 ``(值, 终点位置)``，不越过 bound。"""
    if isinstance(abi_type, ElementaryType):
        return _decode_elementary(abi_type, data, pos, bound)
    if isinstance(abi_type, FixedPointType):
        return _decode_fixed_point(abi_type, data, pos, bound)
    if isinstance(abi_type, FunctionType):
        return _decode_function_value(abi_type, data, pos, bound)
    if isinstance(abi_type, ArrayType):
        element_type = abi_type.element_type
        if abi_type.length is None:
            count = int.from_bytes(_read_word(data, pos, bound), "big", signed=False)
            base = pos + _WORD
            if base > bound:
                raise ABIValueError("编码数据长度不足：动态数组缺少元素块")
        else:
            count = abi_type.length
            base = pos
        # 先按 head 尺寸拦截伪造的超大长度，避免成员列表内存放大。
        slot = _head_slot(element_type)
        remaining = bound - base
        if slot:
            if count > remaining // slot:
                raise ABIValueError("数组元素块越界：长度与数据不符")
            member_types = [element_type] * count
        elif count:
            # 零尺寸静态元素（如 ()）：实体不占字节，无法用剩余数据约束
            # 长度；仅需防止离谱的乘法分配。
            try:
                member_types = [element_type] * count
            except (MemoryError, OverflowError) as exc:
                raise ABIValueError("数组长度过大，超出可解码范围") from exc
        else:
            member_types = []
        values, end = _decode_block(member_types, data, base, bound)
        return values, end
    if isinstance(abi_type, TupleType):
        values, end = _decode_block(list(abi_type.components), data, pos, bound)
        return tuple(values), end
    raise ABIValueError(f"无法编解码非 ABIType 对象：{abi_type!r}")


# ---- 公开入口 ------------------------------------------------------------


def encode_abi_value(abi_type: ABIType, value) -> bytes:
    """按 ``abi_type`` 把 Python 值编码为完整 ABI 编码 bytes。

    值与类型不匹配或类型对象不适用时抛出 :class:`abi_kit.ABIValueError`。
    """
    if not isinstance(abi_type, ABIType):
        raise ABIValueError(f"无法编解码非 ABIType 对象：{abi_type!r}")
    return _encode(abi_type, value)


def decode_abi_value(abi_type: ABIType, data: bytes):
    """把完整 ABI 编码 bytes 解码回 Python 值。

    要求 ``data`` 恰好容纳一个值：长度不足、偏移越界、尾部残留、
    定长数组长度不符、非法布尔字、非法 UTF-8 或越界数值均抛出
    :class:`abi_kit.ABIValueError`。
    """
    if not isinstance(abi_type, ABIType):
        raise ABIValueError(f"无法编解码非 ABIType 对象：{abi_type!r}")
    if not isinstance(data, bytes):
        raise ABIValueError(f"编码数据必须是 bytes，得到 {type(data).__name__}")

    bound = len(data)
    value, end = _decode(abi_type, data, 0, bound)
    if end != bound:
        raise ABIValueError(
            f"尾部残留 {bound - end} 字节：编码未被完整消费"
        )
    return value
