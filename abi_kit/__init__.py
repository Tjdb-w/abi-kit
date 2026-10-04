"""ABI Kit: Solidity ABI tooling.

提供：
- ABI 类型字符串的解析与规范格式化（parse_abi_type / format_abi_type）；
- 值层完整编解码（encode_abi_value / decode_abi_value）；
- 嵌套值的路径化读取与定点替换
  （get_abi_value_at_path / replace_abi_value_at_path）；
- 事件 ABI 解析、签名 topic0 与日志还原校验
  （parse_event_abi / event_topic0 / decode_event_log）；
- 函数 ABI 解析、四字节 selector 与函数调用编解码
  （parse_function_abi / function_selector /
  encode_function_call / decode_function_call）。

类型层错误抛出 ABITypeError，值层错误抛出 ABIValueError，路径层五类
失败抛出带错误码的 AbiPathError，事件层五类失败抛出带错误码的
AbiEventError；函数调用路径的失败按情形分别抛出 AbiMetadataError、
AbiFunctionNotFoundError、AbiOverloadError、AbiSelectorError、
AbiCalldataLengthError、AbiValueError、AbiTrailingDataError。
"""

from ._codec import decode_abi_value, encode_abi_value
from ._event import (
    EventDefinition,
    decode_event_log,
    event_topic0,
    parse_event_abi,
)
from ._exceptions import (
    ABITypeError,
    AbiCalldataLengthError,
    AbiEventError,
    AbiFunctionNotFoundError,
    AbiMetadataError,
    AbiOverloadError,
    AbiPathError,
    AbiSelectorError,
    AbiTrailingDataError,
    AbiValueError,
    ABIValueError,
)
from ._format import format_abi_type
from ._function import (
    FunctionCallEncoding,
    FunctionCallResult,
    FunctionDefinition,
    FunctionParameter,
    decode_function_call,
    encode_function_call,
    function_selector,
    parse_function_abi,
    parse_functions,
)
from ._parser import parse_abi_type
from ._path import get_abi_value_at_path, replace_abi_value_at_path
from ._types import ABIType, ArrayType, ElementaryType, TupleType

#: 函数调用入口的 camelCase 公开别名。
encodeFunctionCall = encode_function_call
decodeFunctionCall = decode_function_call

__all__ = [
    "ABITypeError",
    "ABIValueError",
    "AbiPathError",
    "AbiEventError",
    "AbiMetadataError",
    "AbiFunctionNotFoundError",
    "AbiOverloadError",
    "AbiSelectorError",
    "AbiCalldataLengthError",
    "AbiValueError",
    "AbiTrailingDataError",
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
    "FunctionDefinition",
    "FunctionParameter",
    "FunctionCallEncoding",
    "FunctionCallResult",
    "parse_function_abi",
    "parse_functions",
    "function_selector",
    "encode_function_call",
    "decode_function_call",
    "encodeFunctionCall",
    "decodeFunctionCall",
]
