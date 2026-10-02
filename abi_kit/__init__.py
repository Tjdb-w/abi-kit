"""ABI Kit：合约 ABI 编解码套件。

本轮仅提供类型解析与规范化：

- :func:`parse_abi_type`：ABI 类型字符串 → 不可变类型对象。
- :func:`format_abi_type`：类型对象 → 规范类型字符串。
- :class:`ABITypeError`：所有无效输入的唯一异常。

类型对象通过 ``kind`` 区分 ``"elementary"`` / ``"array"`` / ``"tuple"``，
保留位宽、字节宽度、定长长度与元组组件顺序，供后续嵌套结构编解码与
事件日志处理复用。
"""

from .types import (
    ABIType,
    ABITypeError,
    ArrayType,
    ElementaryType,
    TupleType,
)
from .parser import format_abi_type, parse_abi_type

__all__ = [
    "ABIType",
    "ABITypeError",
    "ArrayType",
    "ElementaryType",
    "TupleType",
    "format_abi_type",
    "parse_abi_type",
]
