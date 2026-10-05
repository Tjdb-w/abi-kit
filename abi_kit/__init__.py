"""ABI Kit: Solidity ABI tooling.

提供：
- ABI 类型字符串的解析与规范格式化（parse_abi_type / format_abi_type）；
- 值层完整编解码（encode_abi_value / decode_abi_value）；
- 嵌套值的路径化读取与定点替换
  （get_abi_value_at_path / replace_abi_value_at_path）；
- 事件 ABI 解析、签名 topic0、日志还原校验与日志编码
  （parse_event_abi / event_topic0 / decode_event_log /
  encode_event_log）；
- 合约级事件注册表与日志分派还原
  （parse_event_registry / decode_contract_event_log /
  decode_contract_event_logs）；
- 函数 ABI 解析、selector 与函数调用 calldata 编解码
  （parse_function_abi / function_selector / canonical_function_signature /
  encode_function_call / decode_function_call）；
- 函数返回值（outputs）编解码
  （encode_function_result / decode_function_result）；
- Solidity error ABI 解析、selector 与 revert data 编解码
  （parse_error_abi / canonical_error_signature / error_selector /
  encode_error_data / decode_error_data）；
- 合约部署 constructor ABI 解析与 deployment data 编解码
  （parse_constructor_abi / encode_constructor_data /
  decode_constructor_data）；
- 合约调用分派，统一处理 function / receive / fallback
  （parse_contract_call_registry / encode_contract_call /
  decode_contract_call）。

类型层错误抛出 ABITypeError，值层错误抛出 ABIValueError（别名
AbiValueError），路径层五类失败抛出带错误码的 AbiPathError，事件层
六类失败抛出带错误码的 AbiEventError；合约级事件注册表构建与日志
分派失败抛出带错误码的 AbiLogDispatchError；函数调用路径的元数据、
查找、selector、calldata 长度与尾随数据错误分别抛出 AbiMetadataError、
AbiFunctionNotFoundError、AbiOverloadError、AbiSelectorError、
AbiCalldataLengthError、AbiTrailingDataError；error 路径的查找、重载、
selector、数据长度与尾随数据错误分别抛出 AbiErrorNotFoundError、
AbiErrorOverloadError、AbiErrorSelectorError、AbiErrorDataLengthError、
AbiErrorTrailingDataError（元数据错误与函数路径共用 AbiMetadataError）；
部署路径的 creation bytecode / deployment data 类型、十六进制、长度与
前缀不一致错误抛 AbiDeploymentDataError，参数尾随数据与函数路径共用
AbiTrailingDataError（元数据错误同样共用 AbiMetadataError）；合约调用
分派（function / receive / fallback）的注册表构建、目标选择与数据非法
失败抛带错误码的 AbiContractCallError，值层失败仍抛 ABIValueError、
尾随数据仍抛 AbiTrailingDataError。
"""

from ._codec import decode_abi_value, encode_abi_value
from ._constructor import (
    ConstructorArgument,
    ConstructorDefinition,
    ConstructorParameter,
    DecodedDeploymentData,
    EncodedDeploymentData,
    decode_constructor_data,
    encode_constructor_data,
    parse_constructor_abi,
)
from ._contract import (
    EventRegistry,
    decode_contract_event_log,
    decode_contract_event_logs,
    parse_event_registry,
)
from ._contract_call import (
    ContractCallRegistry,
    DecodedContractCall,
    decode_contract_call,
    encode_contract_call,
    parse_contract_call_registry,
)
from ._error import (
    DecodedErrorData,
    EncodedErrorData,
    ErrorArgument,
    ErrorDefinition,
    canonical_error_signature,
    decode_error_data,
    encode_error_data,
    error_selector,
    parse_error_abi,
)
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
    AbiContractCallError,
    AbiErrorDataLengthError,
    AbiErrorNotFoundError,
    AbiErrorOverloadError,
    AbiErrorSelectorError,
    AbiErrorTrailingDataError,
    AbiDeploymentDataError,
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
    "AbiErrorNotFoundError",
    "AbiErrorOverloadError",
    "AbiErrorSelectorError",
    "AbiErrorDataLengthError",
    "AbiErrorTrailingDataError",
    "AbiDeploymentDataError",
    "AbiContractCallError",
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
    "ErrorDefinition",
    "ErrorArgument",
    "EncodedErrorData",
    "DecodedErrorData",
    "parse_error_abi",
    "canonical_error_signature",
    "error_selector",
    "encode_error_data",
    "decode_error_data",
    "ConstructorDefinition",
    "ConstructorParameter",
    "ConstructorArgument",
    "EncodedDeploymentData",
    "DecodedDeploymentData",
    "parse_constructor_abi",
    "encode_constructor_data",
    "decode_constructor_data",
    "ContractCallRegistry",
    "DecodedContractCall",
    "parse_contract_call_registry",
    "encode_contract_call",
    "decode_contract_call",
]
