"""类型对象到规范类型字符串的格式化。"""

from __future__ import annotations

from ._exceptions import ABITypeError
from ._types import ABIType, ArrayType, ElementaryType, TupleType


def format_abi_type(abi_type: ABIType) -> str:
    """返回类型对象的规范类型字符串（不含任何空白）。

    对任意合法对象，``format_abi_type(parse_abi_type(s))`` 与
    再次解析/格式化的结果保持一致。
    """
    if isinstance(abi_type, ElementaryType):
        if abi_type.kind in ("uint", "int"):
            return f"{abi_type.kind}{abi_type.bit_size}"
        if abi_type.kind == "bytes" and abi_type.byte_size is not None:
            return f"bytes{abi_type.byte_size}"
        return abi_type.kind
    if isinstance(abi_type, ArrayType):
        suffix = "[]" if abi_type.length is None else f"[{abi_type.length}]"
        return format_abi_type(abi_type.element_type) + suffix
    if isinstance(abi_type, TupleType):
        return "(" + ",".join(format_abi_type(c) for c in abi_type.components) + ")"
    raise ABITypeError(f"无法格式化非 ABIType 对象：{abi_type!r}")
