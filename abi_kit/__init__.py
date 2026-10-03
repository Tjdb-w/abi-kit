"""ABI Kit: Solidity ABI tooling.

提供：
- ABI 类型字符串的解析与规范格式化（parse_abi_type / format_abi_type）；
- 值层完整编解码（encode_abi_value / decode_abi_value）；
- 嵌套值的路径化读取与定点替换
  （get_abi_value_at_path / replace_abi_value_at_path）；
- 事件 ABI 解析、签名 topic0 与日志还原校验
  （parse_event_abi / event_topic0 / decode_event_log）。

类型层错误抛出 ABITypeError，值层错误抛出 ABIValueError，路径层五类
失败抛出带错误码的 AbiPathError，事件层五类失败抛出带错误码的
AbiEventError。
"""

from ._codec import decode_abi_value, encode_abi_value
from ._event import (
    EventDefinition,
    decode_event_log,
    event_topic0,
    parse_event_abi,
)
from ._exceptions import AbiEventError, AbiPathError, ABITypeError, ABIValueError
from ._format import format_abi_type
from ._parser import parse_abi_type
from ._path import get_abi_value_at_path, replace_abi_value_at_path
from ._types import ABIType, ArrayType, ElementaryType, TupleType

__all__ = [
    "ABITypeError",
    "ABIValueError",
    "AbiPathError",
    "AbiEventError",
    "ABIType",
    "ElementaryType",
    "ArrayType",
    "TupleType",
    "parse_abi_type",
    "format_abi_type",
    "encode_abi_value",
    "decode_abi_value",
    "get_abi_value_at_path",
    "replace_abi_value_at_path",
    "EventDefinition",
    "parse_event_abi",
    "event_topic0",
    "decode_event_log",
]
