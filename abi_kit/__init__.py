"""ABI Kit: Solidity ABI tooling.

提供：
- ABI 类型字符串的解析与规范格式化（parse_abi_type / format_abi_type）；
- 值层完整编解码（encode_abi_value / decode_abi_value）；
- 嵌套值的路径化读取与定点替换
  （get_abi_value_at_path / replace_abi_value_at_path）；
- 事件 ABI 解析、签名 topic0、日志还原校验与日志编码
  （parse_event_abi / event_topic0 / decode_event_log /
  encode_event_log）；
- 合约级事件注册表与单条/批量日志分派还原
  （parse_event_registry / decode_contract_event_log /
  decode_contract_event_logs）；
- 函数 ABI 解析、selector 与函数调用 calldata 编解码
  （parse_function_abi / function_selector / canonical_function_signature /
  encode_function_call / decode_function_call）；
- 函数返回值（outputs）编解码
  （encode_function_result / decode_function_result）。

类型层错误抛出 ABITypeError，值层错误抛出 ABIValueError（别名
AbiValueError），路径层五类失败抛出带错误码的 AbiPathError，事件层
六类失败抛出带错误码的 AbiEventError；合约事件注册表与日志分派的
六类失败抛出带错误码的 AbiLogDispatchError；函数调用路径的元数据、
查找、selector、calldata 长度与尾随数据错误分别抛出 AbiMetadataError、
AbiFunctionNotFoundError、AbiOverloadError、AbiSelectorError、
AbiCalldataLengthError、AbiTrailingDataError。
"""

from ._codec import decode_abi_value, encode_abi_value
from ._event import (
    EncodedEventLog,
    EventDefinition,
    decode_event_log,
    encode_event_log,
    event_topic0,
    parse_event_abi,
)
from ._exceptions import (
    AbiCalldataLengthError,
    AbiEventError,
    AbiFunctionNotFoundError,
    AbiLogDispatchError,
    AbiMetadataError,
    AbiOverloadError,
    AbiPathError,
    AbiSelectorError,
    AbiTrailingDataError,
    AbiValueError,
    ABITypeError,
    ABIValueError,
)
from ._format import format_abi_type
from ._function import (
    DecodedFunctionCall,
    DecodedFunctionResult,
    EncodedFunctionCall,
    EncodedFunctionResult,
    FunctionArgument,
    FunctionDefinition,
    FunctionParameter,
    canonical_function_signature,
    decode_function_call,
    decode_function_result,
    decodeFunctionCall,
    decodeFunctionResult,
    encode_function_call,
    encode_function_result,
    encodeFunctionCall,
    encodeFunctionResult,
    function_selector,
    parse_function_abi,
)
from ._parser import parse_abi_type
from ._path import get_abi_value_at_path, replace_abi_value_at_path
from ._registry import (
    DecodedContractEventLog,
    EventRegistry,
    decode_contract_event_log,
    decode_contract_event_logs,
    parse_event_registry,
)
from ._types import ABIType, ArrayType, ElementaryType, TupleType

__all__ = [
    "ABITypeError",
    "ABIValueError",
    "AbiValueError",
    "AbiPathError",
    "AbiEventError",
    "AbiLogDispatchError",
    "AbiMetadataError",
    "AbiFunctionNotFoundError",
    "AbiOverloadError",
    "AbiSelectorError",
    "AbiCalldataLengthError",
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
    "encode_event_log",
    "EncodedEventLog",
    "EventRegistry",
    "DecodedContractEventLog",
    "parse_event_registry",
    "decode_contract_event_log",
    "decode_contract_event_logs",
    "FunctionDefinition",
    "FunctionParameter",
    "FunctionArgument",
    "EncodedFunctionCall",
    "DecodedFunctionCall",
    "EncodedFunctionResult",
    "DecodedFunctionResult",
    "parse_function_abi",
    "canonical_function_signature",
    "function_selector",
    "encode_function_call",
    "decode_function_call",
    "encodeFunctionCall",
    "decodeFunctionCall",
    "encode_function_result",
    "decode_function_result",
    "encodeFunctionResult",
    "decodeFunctionResult",
]
