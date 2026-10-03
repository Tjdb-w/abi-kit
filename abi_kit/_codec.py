"""Solidity ABI 值层编解码（head/tail 规则）。

公开入口：
- encode_abi_value(abi_type, value) -> bytes
- decode_abi_value(abi_type, data) -> Python 值

编码布局遵循 Solidity ABI 规范：单个值的编码等同于只含该值的
一元元组编码——静态类型直接是其字编码，动态类型则先放一个
偏移字，尾部跟随实际内容。

值口径：整数为 int，bool 为 bool，string 为 str，bytes/bytesM 为
bytes，address 为小写 ``0x`` 加 40 位十六进制字符串，数组为 list，
元组为 tuple。所有值/编码/类型问题统一抛出 ABIValueError。
"""

from __future__ import annotations

from ._exceptions import ABIValueError
from ._types import ABIType, ArrayType, ElementaryType, TupleType

_WORD = 32
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

#: 元素编码尺寸为 0 的数组（如 ``()[]``）允许解码的最大长度，
#: 防止畸形长度字导致解码循环无法终止。
_ZERO_SIZE_ELEMENT_LIMIT = 1 << 20


# ---- 类型结构辅助 -------------------------------------------------------


def _is_dynamic(abi_type: ABIType) -> bool:
    if isinstance(abi_type, ElementaryType):
        return abi_type.kind == "string" or (
            abi_type.kind == "bytes" and abi_type.byte_size is None
        )
    if isinstance(abi_type, ArrayType):
        return abi_type.length is None or _is_dynamic(abi_type.element_type)
    if isinstance(abi_type, TupleType):
        return any(_is_dynamic(c) for c in abi_type.components)
    raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


def _static_size(abi_type: ABIType) -> int:
    """静态类型在 head 区域占用的字节数；仅对静态类型调用。"""
    if isinstance(abi_type, ElementaryType):
        return _WORD
    if isinstance(abi_type, ArrayType):
        return abi_type.length * _static_size(abi_type.element_type)
    if isinstance(abi_type, TupleType):
        return sum(_static_size(c) for c in abi_type.components)
    raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


def _check_type(abi_type: ABIType) -> None:
    if not isinstance(abi_type, ABIType):
        raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


def _word(number: int) -> bytes:
    return number.to_bytes(_WORD, "big")


# ---- 编码 ---------------------------------------------------------------


def _check_int(value: object, type_desc: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ABIValueError(
            f"{type_desc} 的值必须是 int，得到 {type(value).__name__}"
        )
    return value


def _check_bytes(value: object, type_desc: str) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    raise ABIValueError(
        f"{type_desc} 的值必须是 bytes 类型，得到 {type(value).__name__}"
    )


def _check_sequence(value: object, type_desc: str) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ABIValueError(
        f"{type_desc} 的值必须是 list 或 tuple，得到 {type(value).__name__}"
    )


def _encode_address(value: object) -> bytes:
    if not isinstance(value, str):
        raise ABIValueError(
            f"address 的值必须是十六进制字符串，得到 {type(value).__name__}"
        )
    text = value
    if text[:2] in ("0x", "0X"):
        text = text[2:]
    if len(text) != 40 or any(ch not in _HEX_DIGITS for ch in text):
        raise ABIValueError(f"address 的值必须是 40 位十六进制：{value!r}")
    return _word(int(text, 16))


def _encode_dynamic_bytes(data: bytes) -> bytes:
    padding = -len(data) % _WORD
    return _word(len(data)) + data + b"\x00" * padding


def _encode_elementary(abi_type: ElementaryType, value: object) -> bytes:
    kind = abi_type.kind
    if kind == "uint":
        number = _check_int(value, "uint")
        if not 0 <= number < (1 << abi_type.bit_size):
            raise ABIValueError(
                f"uint{abi_type.bit_size} 的值越界：{number}"
            )
        return _word(number)
    if kind == "int":
        number = _check_int(value, "int")
        bound = 1 << (abi_type.bit_size - 1)
        if not -bound <= number < bound:
            raise ABIValueError(
                f"int{abi_type.bit_size} 的值越界：{number}"
            )
        return _word(number % (1 << 256))
    if kind == "address":
        return _encode_address(value)
    if kind == "bool":
        if type(value) is not bool:
            raise ABIValueError(
                f"bool 的值必须是 Python bool，得到 {type(value).__name__}"
            )
        return _word(1 if value else 0)
    if kind == "string":
        if not isinstance(value, str):
            raise ABIValueError(
                f"string 的值必须是 str，得到 {type(value).__name__}"
            )
        return _encode_dynamic_bytes(value.encode("utf-8"))
    if kind == "bytes":
        if abi_type.byte_size is None:
            return _encode_dynamic_bytes(_check_bytes(value, "bytes"))
        data = _check_bytes(value, f"bytes{abi_type.byte_size}")
        if len(data) != abi_type.byte_size:
            raise ABIValueError(
                f"bytes{abi_type.byte_size} 的长度必须是 {abi_type.byte_size}，"
                f"得到 {len(data)}"
            )
        return data + b"\x00" * (_WORD - abi_type.byte_size)
    raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


def _encode_tuple_body(types: list, values: list) -> bytes:
    """按 head/tail 规则编码一组按声明顺序排列的 (类型, 值)。"""
    heads = []
    tails = []
    offset = sum(
        _WORD if _is_dynamic(t) else _static_size(t) for t in types
    )
    for abi_type, value in zip(types, values):
        if _is_dynamic(abi_type):
            tail = _encode(abi_type, value)
            heads.append(_word(offset))
            tails.append(tail)
            offset += len(tail)
        else:
            heads.append(_encode(abi_type, value))
    return b"".join(heads + tails)


def _encode(abi_type: ABIType, value: object) -> bytes:
    if isinstance(abi_type, ElementaryType):
        return _encode_elementary(abi_type, value)
    if isinstance(abi_type, ArrayType):
        items = _check_sequence(value, "数组")
        if abi_type.length is None:
            body = _encode_tuple_body(
                [abi_type.element_type] * len(items), items
            )
            return _word(len(items)) + body
        if len(items) != abi_type.length:
            raise ABIValueError(
                f"定长数组长度必须是 {abi_type.length}，得到 {len(items)}"
            )
        return _encode_tuple_body(
            [abi_type.element_type] * abi_type.length, items
        )
    if isinstance(abi_type, TupleType):
        items = _check_sequence(value, "元组")
        if len(items) != len(abi_type.components):
            raise ABIValueError(
                f"元组需要 {len(abi_type.components)} 个组成部分，"
                f"得到 {len(items)}"
            )
        return _encode_tuple_body(list(abi_type.components), items)
    raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


# ---- 解码 ---------------------------------------------------------------


class _Decoder:
    """只读视图；记录读取到达的最远位置以检测尾部残留。"""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.max_end = 0

    def read(self, offset: int, size: int) -> bytes:
        end = offset + size
        if offset < 0 or end > len(self._data):
            raise ABIValueError(
                f"数据长度不足或偏移越界：读取 [{offset}, {end})，"
                f"数据共 {len(self._data)} 字节"
            )
        if end > self.max_end:
            self.max_end = end
        return self._data[offset:end]

    def read_word(self, offset: int) -> int:
        return int.from_bytes(self.read(offset, _WORD), "big")


def _decode_dynamic_bytes(decoder: _Decoder, offset: int) -> bytes:
    length = decoder.read_word(offset)
    data = decoder.read(offset + _WORD, length)
    padding = -length % _WORD
    if padding and any(decoder.read(offset + _WORD + length, padding)):
        raise ABIValueError("动态字节数据的尾部填充不是零")
    return data


def _decode_elementary(decoder: _Decoder, abi_type: ElementaryType, offset: int):
    kind = abi_type.kind
    if kind == "uint":
        number = decoder.read_word(offset)
        if number >= (1 << abi_type.bit_size):
            raise ABIValueError(
                f"uint{abi_type.bit_size} 的编码数值越界：{number}"
            )
        return number
    if kind == "int":
        number = decoder.read_word(offset)
        bound = 1 << (abi_type.bit_size - 1)
        if number < bound:
            return number
        if number >= (1 << 256) - bound:
            return number - (1 << 256)
        raise ABIValueError(
            f"int{abi_type.bit_size} 的编码不是合法的符号扩展：{number}"
        )
    if kind == "address":
        number = decoder.read_word(offset)
        if number >= (1 << 160):
            raise ABIValueError(f"address 的编码数值越界：{number}")
        return "0x" + number.to_bytes(20, "big").hex()
    if kind == "bool":
        number = decoder.read_word(offset)
        if number == 0:
            return False
        if number == 1:
            return True
        raise ABIValueError(f"非法的布尔字：{number}")
    if kind == "string":
        data = _decode_dynamic_bytes(decoder, offset)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ABIValueError(f"string 的编码不是合法 UTF-8：{exc}") from exc
    if kind == "bytes":
        if abi_type.byte_size is None:
            return _decode_dynamic_bytes(decoder, offset)
        word = decoder.read(offset, _WORD)
        if any(word[abi_type.byte_size:]):
            raise ABIValueError(
                f"bytes{abi_type.byte_size} 的尾部填充不是零"
            )
        return word[: abi_type.byte_size]
    raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


def _decode_tuple_body(decoder: _Decoder, types: list, base: int) -> list:
    """解码位于 base 处的一组 head/tail 成员，偏移相对 base。"""
    values = []
    cursor = base
    for abi_type in types:
        if _is_dynamic(abi_type):
            tail_offset = decoder.read_word(cursor)
            values.append(_decode(decoder, abi_type, base + tail_offset))
            cursor += _WORD
        else:
            values.append(_decode(decoder, abi_type, cursor))
            cursor += _static_size(abi_type)
    return values


def _decode(decoder: _Decoder, abi_type: ABIType, offset: int):
    if isinstance(abi_type, ElementaryType):
        return _decode_elementary(decoder, abi_type, offset)
    if isinstance(abi_type, ArrayType):
        element = abi_type.element_type
        if abi_type.length is None:
            length = decoder.read_word(offset)
            base = offset + _WORD
        else:
            length = abi_type.length
            base = offset
        if (
            length > _ZERO_SIZE_ELEMENT_LIMIT
            and not _is_dynamic(element)
            and _static_size(element) == 0
        ):
            raise ABIValueError(f"数组长度异常：{length}")
        items = _decode_tuple_body(decoder, [element] * length, base)
        return list(items)
    if isinstance(abi_type, TupleType):
        return tuple(_decode_tuple_body(decoder, list(abi_type.components), offset))
    raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


# ---- 公开入口 -----------------------------------------------------------


def encode_abi_value(abi_type: ABIType, value: object) -> bytes:
    """把 Python 值按 ABI 类型编码为 bytes。

    值与类型不匹配或类型对象不适用时抛出 :class:`abi_kit.ABIValueError`。
    """
    _check_type(abi_type)
    return _encode_tuple_body([abi_type], [value])


def decode_abi_value(abi_type: ABIType, data: bytes):
    """把完整的 ABI 编码 bytes 按类型还原为 Python 值。

    长度不足、偏移越界、尾部残留、非法布尔字、非法 UTF-8、越界数值
    或类型对象不适用时抛出 :class:`abi_kit.ABIValueError`；
    输入 data 不会被修改。
    """
    _check_type(abi_type)
    if isinstance(data, (bytes, bytearray, memoryview)):
        blob = bytes(data)
    else:
        raise ABIValueError(
            f"编码数据必须是 bytes 类型，得到 {type(data).__name__}"
        )
    decoder = _Decoder(blob)
    values = _decode_tuple_body(decoder, [abi_type], 0)
    if decoder.max_end != len(blob):
        raise ABIValueError(
            f"编码数据存在未消费的尾部：共 {len(blob)} 字节，"
            f"消费到 {decoder.max_end}"
        )
    return values[0]
