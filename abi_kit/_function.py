"""函数 ABI 解析、selector 与函数调用数据（calldata）编解码。

- :func:`parse_function_abi`：解析 ABI JSON 字符串或等价条目数组，只消费
  ``type == "function"`` 的条目，返回不可变 :class:`FunctionDefinition`；
  event / constructor / error / receive / fallback 条目跳过不解析。
- :func:`canonical_function_signature`：返回
  ``name(type1,type2,...)`` 形式的规范函数签名（参数名不参与）。
- :func:`function_selector`：返回
  ``keccak256(canonical_signature)[:4]`` 的四字节 selector。
- :func:`encode_function_call`：按函数名（无重载时）或规范签名选择函数，
  按公开参数顺序编码实参，返回 selector 与 ``selector + ABI(参数 tuple)``
  的完整 calldata。
- :func:`decode_function_call`：按 calldata 前四字节还原函数，再严格解码
  参数 tuple，返回函数标识、规范签名与带类型标注的参数结果。
- :func:`encode_function_result`：按函数名（无重载时）或规范签名选择函数，
  按 ``outputs`` 顺序编码返回值 tuple（无 selector 前缀）。
- :func:`decode_function_result`：按函数名或规范签名选定函数，再严格解码
  其 ``outputs`` tuple，返回函数标识与带类型标注的返回值结果。

``outputs`` 不参与规范签名与 selector，仅用于返回值编解码。

``encodeFunctionCall`` / ``decodeFunctionCall`` /
``encodeFunctionResult`` / ``decodeFunctionResult`` 是上述四个入口的
camelCase 别名。

错误约定：

- 元数据非法（ABI 根不是数组、function 条目缺 name/type/inputs、
  outputs 不是数组或参数元数据非法、标识符非法、参数类型字符串无法
  解析等）抛 :class:`abi_kit.AbiMetadataError`；
- 函数名或规范签名无匹配抛 :class:`abi_kit.AbiFunctionNotFoundError`；
- 只给函数名但同名重载不止一个、无法唯一选择抛
  :class:`abi_kit.AbiOverloadError`；
- selector 匹配不到任何函数抛 :class:`abi_kit.AbiSelectorError`；
- calldata 少于四字节抛 :class:`abi_kit.AbiCalldataLengthError`；
- 实参不能按声明类型编码、或参数区不能按声明类型严格解码抛
  :class:`abi_kit.ABIValueError`；
- 参数消费完后仍有尾随字节抛 :class:`abi_kit.AbiTrailingDataError`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._codec import _decode, _encode
from ._event import _build_type, _is_identifier
from ._exceptions import (
    AbiCalldataLengthError,
    AbiEventError,
    AbiFunctionNotFoundError,
    AbiMetadataError,
    AbiOverloadError,
    AbiSelectorError,
    AbiTrailingDataError,
    ABITypeError,
    ABIValueError,
)
from ._format import format_abi_type
from ._keccak import keccak_256
from ._types import ABIType, TupleType

_SELECTOR_LEN = 4

#: 函数路径跳过但允许保留在 ABI 中的非函数条目类型；这些条目的合法性
#: 由各自入口（如事件路径）负责，本模块不解析。
_NON_FUNCTION_ENTRY_TYPES = frozenset(
    ("event", "constructor", "error", "receive", "fallback")
)


# ---- 不可变定义与结果对象 -------------------------------------------------


@dataclass(frozen=True)
class FunctionParameter:
    """函数的单个公开参数（声明顺序保留）。

    - ``name``：ABI JSON 中的参数名，缺省为空串；
    - ``abi_type``：由 ``type`` + ``components`` 展开得到的类型对象。
    """

    name: str
    abi_type: ABIType


@dataclass(frozen=True)
class FunctionDefinition:
    """不可变函数定义。

    - ``name``：非空 ASCII 函数标识符；
    - ``inputs``：按声明顺序保存的公开参数 :class:`FunctionParameter`；
    - ``outputs``：按声明顺序保存的返回值 :class:`FunctionParameter`，
      无 ``outputs`` 或为空数组时为空 tuple；``outputs`` 不参与规范签名
      与 selector，仅用于返回值编解码。
    """

    name: str
    inputs: tuple[FunctionParameter, ...] = field(default=())
    outputs: tuple[FunctionParameter, ...] = field(default=())


@dataclass(frozen=True)
class EncodedFunctionCall:
    """函数调用编码结果。

    - ``function``：命中的 :class:`FunctionDefinition`；
    - ``selector``：四字节 selector（bytes）；
    - ``calldata``：``selector + 参数 ABI 编码`` 的完整调用数据。
    """

    function: FunctionDefinition
    selector: bytes
    calldata: bytes

    @property
    def function_name(self) -> str:
        """命中函数的名称。"""
        return self.function.name

    @property
    def signature(self) -> str:
        """命中函数的规范签名，如 ``transfer(address,uint256)``。"""
        return canonical_function_signature(self.function)

    @property
    def selector_hex(self) -> str:
        """``"0x"`` + 8 个小写十六进制字符形式的 selector。"""
        return "0x" + self.selector.hex()

    @property
    def calldata_hex(self) -> str:
        """``"0x"`` 前缀的完整 calldata 十六进制字符串。"""
        return "0x" + self.calldata.hex()


@dataclass(frozen=True)
class FunctionArgument:
    """解码后带类型标注的单个实参。

    - ``name``：声明的参数名，未命名时为空串；
    - ``type``：规范 ABI 类型字符串（不含空白与参数名）；
    - ``value``：按值层口径还原的 Python 值。
    """

    name: str
    type: str
    value: object


@dataclass(frozen=True)
class DecodedFunctionCall:
    """函数调用解码结果。

    - ``function``：selector 命中的 :class:`FunctionDefinition`；
    - ``selector``：calldata 前四字节（bytes）；
    - ``args``：按声明顺序排列的 :class:`FunctionArgument`。
    """

    function: FunctionDefinition
    selector: bytes
    args: tuple[FunctionArgument, ...] = field(default=())

    @property
    def function_name(self) -> str:
        """命中函数的名称。"""
        return self.function.name

    @property
    def signature(self) -> str:
        """命中函数的规范签名，如 ``transfer(address,uint256)``。"""
        return canonical_function_signature(self.function)

    @property
    def selector_hex(self) -> str:
        """``"0x"`` + 8 个小写十六进制字符形式的 selector。"""
        return "0x" + self.selector.hex()

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple。"""
        return tuple(arg.value for arg in self.args)


@dataclass(frozen=True)
class EncodedFunctionResult:
    """函数返回值编码结果（不含 selector）。

    - ``function``：命中的 :class:`FunctionDefinition`；
    - ``data``：``outputs`` tuple 的 ABI 编码，无 outputs 时为空字节串。
    """

    function: FunctionDefinition
    data: bytes = b""

    @property
    def function_name(self) -> str:
        """命中函数的名称。"""
        return self.function.name

    @property
    def signature(self) -> str:
        """命中函数的规范签名（仅由 inputs 决定），如 ``transfer(address,uint256)``。"""
        return canonical_function_signature(self.function)

    @property
    def data_hex(self) -> str:
        """``data`` 的小写 ``"0x"`` 前缀十六进制字符串。"""
        return "0x" + self.data.hex()


@dataclass(frozen=True)
class DecodedFunctionResult:
    """函数返回值解码结果。

    - ``function``：命中的 :class:`FunctionDefinition`；
    - ``outputs``：按声明顺序排列的带类型标注 :class:`FunctionArgument`。
    """

    function: FunctionDefinition
    outputs: tuple[FunctionArgument, ...] = field(default=())

    @property
    def function_name(self) -> str:
        """命中函数的名称。"""
        return self.function.name

    @property
    def signature(self) -> str:
        """命中函数的规范签名（仅由 inputs 决定），如 ``transfer(address,uint256)``。"""
        return canonical_function_signature(self.function)

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple（与 outputs 等价）。"""
        return tuple(arg.value for arg in self.outputs)


# ---- ABI 解析 -------------------------------------------------------------


def _metadata(message: str) -> AbiMetadataError:
    return AbiMetadataError(message)


def _parse_parameter_list(name: str, node: dict, key: str, required: bool):
    """解析函数条目的 inputs/outputs 参数列表为不可变参数 tuple。"""
    label = "参数" if key == "inputs" else "返回值"
    if key not in node:
        if required:
            raise _metadata(f"函数 {name!r} 缺少 {key}")
        return ()
    params_node = node[key]
    if not isinstance(params_node, (list, tuple)):
        raise _metadata(f"函数 {name!r} 的 {key} 必须是数组")

    parameters: list[FunctionParameter] = []
    for param_node in params_node:
        if not isinstance(param_node, dict):
            raise _metadata(
                f"函数 {name!r} 的{label}必须是 JSON 对象，"
                f"得到 {type(param_node).__name__}"
            )
        param_name = param_node.get("name", "")
        if not isinstance(param_name, str):
            raise _metadata(
                f"函数 {name!r} 的{label} name 必须是字符串，"
                f"得到 {param_name!r}"
            )
        try:
            # 与事件路径共用同一套 type + components 递归展开；其失败
            # 统一是事件层错误码，这里转译为函数元数据错误。
            abi_type = _build_type(param_node)
        except (AbiEventError, ABITypeError) as exc:
            raise _metadata(f"函数 {name!r} 的{label}类型非法：{exc}") from None
        parameters.append(FunctionParameter(param_name, abi_type))
    return tuple(parameters)


def _parse_function_entry(entry: dict) -> FunctionDefinition:
    """从单个 ABI function 条目构造不可变函数定义。"""
    name = entry.get("name")
    if not _is_identifier(name):
        raise _metadata(f"函数 name 必须是非空 ASCII 标识符，得到 {name!r}")

    inputs = _parse_parameter_list(name, entry, "inputs", required=True)
    # outputs 缺省视为空列表；其元数据与 inputs 同口径严格校验，但不参与
    # 规范签名与 selector。
    outputs = _parse_parameter_list(name, entry, "outputs", required=False)
    return FunctionDefinition(name, inputs, outputs)


def parse_function_abi(abi) -> tuple[FunctionDefinition, ...]:
    """解析 ABI，返回其中全部 function 条目的不可变定义。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只消费 ``type == "function"`` 的条目；
    event、constructor、error、receive、fallback 等其他条目原样保留在
    ABI 中但不解析、不影响函数路径。任何函数元数据非法抛
    :class:`abi_kit.AbiMetadataError`。
    """
    if isinstance(abi, str):
        try:
            root = json.loads(abi)
        except (json.JSONDecodeError, ValueError) as exc:
            raise _metadata(f"ABI JSON 解析失败：{exc}") from None
    elif isinstance(abi, (list, tuple)):
        root = abi
    else:
        raise _metadata(
            f"ABI 必须是 JSON 字符串或条目数组，得到 {type(abi).__name__}"
        )

    if not isinstance(root, (list, tuple)):
        raise _metadata("ABI 必须是条目数组，JSON 根不能是对象或标量")

    functions: list[FunctionDefinition] = []
    for entry in root:
        if not isinstance(entry, dict):
            raise _metadata(
                f"ABI 条目必须是 JSON 对象，得到 {type(entry).__name__}"
            )
        entry_type = entry.get("type")
        # event/constructor/error/receive/fallback 条目保留在 ABI 中供
        # 各自入口使用，函数路径跳过且不校验；缺 type 或未知条目类型属
        # 元数据非法，统一抛 AbiMetadataError。
        if entry_type == "function":
            functions.append(_parse_function_entry(entry))
        elif entry_type in _NON_FUNCTION_ENTRY_TYPES:
            continue
        else:
            raise _metadata(
                f"ABI 条目缺少 type 或带有未知类型 {entry_type!r}，"
                "合法值为 function/event/constructor/error/receive/fallback"
            )
    return tuple(functions)


# ---- 规范签名与 selector --------------------------------------------------


def canonical_function_signature(function: FunctionDefinition) -> str:
    """返回规范函数签名 ``name(type1,type2,...)``。

    参数名不参与签名；tuple 输出为 ``(t1,t2)``，数组保留 ``[]``/``[n]``
    后缀。
    """
    if not isinstance(function, FunctionDefinition):
        raise _metadata(
            f"需要 FunctionDefinition，得到 {type(function).__name__}"
        )
    canonical = ",".join(format_abi_type(p.abi_type) for p in function.inputs)
    return f"{function.name}({canonical})"


def function_selector(function: FunctionDefinition) -> bytes:
    """返回函数四字节 selector：``keccak256(规范签名)[:4]``。"""
    signature = canonical_function_signature(function)
    try:
        digest = keccak_256(signature.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise _metadata(f"规范签名无法生成 selector：{exc}") from None
    return digest[:_SELECTOR_LEN]


# ---- 选择函数 -------------------------------------------------------------


def _resolve_function(
    functions: tuple[FunctionDefinition, ...], key
) -> FunctionDefinition:
    """按函数名或规范签名在已解析函数中唯一定位。"""
    if not isinstance(key, str) or not key:
        raise _metadata(f"函数名或规范签名必须是非空字符串，得到 {key!r}")

    if "(" in key or ")" in key:
        for function in functions:
            if canonical_function_signature(function) == key:
                return function
        raise AbiFunctionNotFoundError(
            f"ABI 中找不到规范签名为 {key!r} 的函数"
        )

    if not _is_identifier(key):
        raise _metadata(f"非法函数名：{key!r}")

    candidates = [function for function in functions if function.name == key]
    if not candidates:
        raise AbiFunctionNotFoundError(f"ABI 中找不到函数 {key!r}")
    if len(candidates) > 1:
        signatures = ", ".join(
            canonical_function_signature(function) for function in candidates
        )
        raise AbiOverloadError(
            f"函数 {key!r} 存在 {len(candidates)} 个重载，无法唯一选择，"
            f"请改用规范签名之一：{signatures}"
        )
    return candidates[0]


# ---- calldata 编解码 ------------------------------------------------------


def _coerce_calldata(calldata) -> bytes:
    """把 bytes 或可选 0x 前缀的十六进制字符串规范化为 bytes。"""
    if isinstance(calldata, bytes):
        return calldata
    if isinstance(calldata, str):
        text = calldata[2:] if calldata[:2] in ("0x", "0X") else calldata
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise ABIValueError(f"calldata 十六进制非法：{exc}") from exc
    raise ABIValueError(
        f"calldata 必须是 bytes 或十六进制 str，得到 {type(calldata).__name__}"
    )


def encode_function_call(
    abi, function_name, args=None
) -> EncodedFunctionCall:
    """编码一次函数调用。

    ``abi`` 接受 ABI JSON 字符串或等价条目数组；``function_name`` 接受
    函数名（ABI 中无同名重载时）或规范函数签名
    （``transfer(address,uint256)``）；``args`` 为按公开参数顺序排列的
    实参列表（list/tuple），无参函数可省略。

    返回 :class:`EncodedFunctionCall`，其中 ``selector`` 为四字节
    bytes，``calldata`` 为 ``selector + 参数 tuple 的 ABI 编码``。
    实参不能按声明类型编码（含数量不符）抛 :class:`abi_kit.ABIValueError`。
    """
    functions = parse_function_abi(abi)
    function = _resolve_function(functions, function_name)

    if args is None:
        values = ()
    elif isinstance(args, (list, tuple)):
        values = tuple(args)
    else:
        raise ABIValueError(
            f"实参列表必须是 list 或 tuple，得到 {type(args).__name__}"
        )

    parameter_types = TupleType(
        tuple(parameter.abi_type for parameter in function.inputs)
    )
    payload = _encode(parameter_types, values)
    selector = function_selector(function)
    return EncodedFunctionCall(function, selector, selector + payload)


def decode_function_call(abi, calldata) -> DecodedFunctionCall:
    """解码完整函数调用数据。

    ``calldata`` 接受 bytes 或可选 ``0x`` 前缀的十六进制字符串，前四字节
    必须是 ABI 中某个函数的 selector；其后按该函数公开参数组成的 tuple
    严格 ABI 解码。返回 :class:`DecodedFunctionCall`，携带函数定义、
    selector、规范签名（属性）与按声明顺序排列的带类型标注实参。

    calldata 少于四字节抛 :class:`abi_kit.AbiCalldataLengthError`；
    selector 无匹配抛 :class:`abi_kit.AbiSelectorError`；参数区不能严格
    解码抛 :class:`abi_kit.ABIValueError`；解码后仍有尾随字节抛
    :class:`abi_kit.AbiTrailingDataError`。
    """
    functions = parse_function_abi(abi)
    raw = _coerce_calldata(calldata)

    if len(raw) < _SELECTOR_LEN:
        raise AbiCalldataLengthError(
            f"calldata 长度必须至少为 {_SELECTOR_LEN} 字节以容纳 selector，"
            f"得到 {len(raw)} 字节"
        )
    selector = raw[:_SELECTOR_LEN]

    matches = [
        function
        for function in functions
        if function_selector(function) == selector
    ]
    if not matches:
        raise AbiSelectorError(
            f"selector 0x{selector.hex()} 在 ABI 中匹配不到任何函数"
        )
    function = matches[0]

    parameter_types = TupleType(
        tuple(parameter.abi_type for parameter in function.inputs)
    )
    payload = raw[_SELECTOR_LEN:]
    bound = len(payload)
    try:
        values, end = _decode(parameter_types, payload, 0, bound)
    except ABIValueError:
        raise
    if end != bound:
        raise AbiTrailingDataError(
            f"参数解码完成后仍有 {bound - end} 字节尾随数据未被消费"
        )

    arguments = tuple(
        FunctionArgument(
            parameter.name, format_abi_type(parameter.abi_type), value
        )
        for parameter, value in zip(function.inputs, values)
    )
    return DecodedFunctionCall(function, selector, arguments)


# ---- 返回值编解码 ---------------------------------------------------------


def _coerce_result_data(data) -> bytes:
    """把返回值 bytes 或可选 0x 前缀的十六进制字符串规范化为 bytes。"""
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        text = data[2:] if data[:2] in ("0x", "0X") else data
        if len(text) % 2:
            raise ABIValueError(
                f"返回值十六进制长度必须为偶数，得到 {len(text)} 个字符"
            )
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise ABIValueError(f"返回值十六进制非法：{exc}") from exc
    raise ABIValueError(
        f"返回值必须是 bytes 或十六进制 str，得到 {type(data).__name__}"
    )


def encode_function_result(
    abi, function_name, values=None
) -> EncodedFunctionResult:
    """编码一次函数返回值（不含 selector）。

    ``abi`` 接受 ABI JSON 字符串或等价条目数组；``function_name`` 接受
    函数名（ABI 中无同名重载时）或规范函数签名；``values`` 为按
    ``outputs`` 声明顺序排列的返回值序列（list/tuple），无 outputs 的
    函数只能传空序列（或省略）。``outputs`` 不参与规范签名与 selector。

    返回 :class:`EncodedFunctionResult`，其中 ``data`` 为返回值组成的
    tuple 标准 ABI 编码，无 outputs 时为 ``b""``。返回值数量或类型与
    ``outputs`` 不符抛 :class:`abi_kit.ABIValueError`。
    """
    functions = parse_function_abi(abi)
    function = _resolve_function(functions, function_name)

    if values is None:
        result_values = ()
    elif isinstance(values, (list, tuple)):
        result_values = tuple(values)
    else:
        raise ABIValueError(
            f"返回值序列必须是 list 或 tuple，得到 {type(values).__name__}"
        )

    output_types = TupleType(
        tuple(parameter.abi_type for parameter in function.outputs)
    )
    data = _encode(output_types, result_values)
    return EncodedFunctionResult(function, data)


def decode_function_result(abi, function_name, data) -> DecodedFunctionResult:
    """按函数的 ``outputs`` 严格解码返回值数据。

    ``data`` 接受 bytes 或可选 ``0x`` 前缀的偶数位十六进制字符串，按
    ``function_name``（函数名，无同名重载时；或规范函数签名）选定函数后，
    以其 ``outputs`` 组成的 tuple 严格 ABI 解码。返回
    :class:`DecodedFunctionResult`，携带函数定义、规范签名（属性）与按
    声明顺序排列的带类型标注返回值；无 outputs 的函数要求 ``data`` 为
    ``b""``（或 ``"0x"``）。

    ``data`` 类型或十六进制非法、长度不足、head/tail 布局、偏移、长度、
    补零、UTF-8、定长数组约束不满足均抛 :class:`abi_kit.ABIValueError`；
    返回值 tuple 消费完后仍有尾随字节抛
    :class:`abi_kit.AbiTrailingDataError`。
    """
    functions = parse_function_abi(abi)
    function = _resolve_function(functions, function_name)
    raw = _coerce_result_data(data)

    output_types = TupleType(
        tuple(parameter.abi_type for parameter in function.outputs)
    )
    bound = len(raw)
    values, end = _decode(output_types, raw, 0, bound)
    if end != bound:
        raise AbiTrailingDataError(
            f"返回值解码完成后仍有 {bound - end} 字节尾随数据未被消费"
        )

    arguments = tuple(
        FunctionArgument(
            parameter.name, format_abi_type(parameter.abi_type), value
        )
        for parameter, value in zip(function.outputs, values)
    )
    return DecodedFunctionResult(function, arguments)


# camelCase 公开别名。
encodeFunctionCall = encode_function_call
decodeFunctionCall = decode_function_call
encodeFunctionResult = encode_function_result
decodeFunctionResult = decode_function_result
