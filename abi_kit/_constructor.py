"""合约部署阶段的 constructor 参数编解码。

- :func:`parse_constructor_abi`：解析 ABI JSON 字符串或等价条目数组，
  只消费 ``type == "constructor"`` 的条目，返回不可变
  :class:`ConstructorDefinition`；无 constructor 条目表示零输入构造
  函数，多个 constructor 条目抛 :class:`abi_kit.AbiMetadataError`，
  function/event/error/receive/fallback 等其他条目跳过不解析。
- :func:`encode_constructor_data`：按 constructor inputs 声明把实参
  编码为 ABI tuple，接在规范化后的 creation bytecode 之后，返回
  :class:`EncodedDeploymentData`；零参数时 ``data`` 就是 bytecode 本身。
- :func:`decode_constructor_data`：用同一 ABI 与 creation bytecode 从
  完整 deployment data 还原 constructor 实参——先逐字节核对开头的
  creation bytecode，再严格解码剩余完整 tuple，返回
  :class:`DecodedDeploymentData`。

错误约定：

- ABI 根非法、constructor 条目元数据非法（缺 inputs、参数描述或类型
  字符串非法等）或存在多个 constructor 条目抛
  :class:`abi_kit.AbiMetadataError`；
- creation_bytecode / deployment_data 不是 bytes 或可选 ``0x`` 前缀的
  偶数位十六进制字符串、deployment data 短于 creation bytecode、或其
  开头与 creation bytecode 不一致，抛
  :class:`abi_kit.AbiDeploymentDataError`；
- 实参数量或类型与 inputs 声明不符、或参数区不能按声明类型严格解码
  抛 :class:`abi_kit.ABIValueError`；
- 参数消费完后仍有尾随字节抛 :class:`abi_kit.AbiTrailingDataError`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._codec import _decode, _encode
from ._event import _build_type
from ._exceptions import (
    AbiDeploymentDataError,
    AbiEventError,
    AbiMetadataError,
    AbiTrailingDataError,
    ABITypeError,
    ABIValueError,
)
from ._format import format_abi_type
from ._types import ABIType, TupleType

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


# ---- 不可变定义与结果对象 -------------------------------------------------


@dataclass(frozen=True)
class ConstructorParameter:
    """constructor 的单个公开参数（声明顺序保留）。

    - ``name``：ABI JSON 中的参数名，缺省为空串；
    - ``abi_type``：由 ``type`` + ``components`` 展开得到的类型对象。
    """

    name: str
    abi_type: ABIType


@dataclass(frozen=True)
class ConstructorDefinition:
    """不可变 constructor 定义。

    - ``inputs``：按声明顺序保存的 :class:`ConstructorParameter`；ABI 中
      无 constructor 条目时为空 tuple，表示零输入构造函数。
    """

    inputs: tuple[ConstructorParameter, ...] = field(default=())


@dataclass(frozen=True)
class ConstructorArgument:
    """带类型标注的单个 constructor 实参。

    - ``name``：声明的参数名，未命名时为空串；
    - ``type``：规范 ABI 类型字符串（不含空白与参数名）；
    - ``value``：按值层口径表示的 Python 值。
    """

    name: str
    type: str
    value: object


@dataclass(frozen=True)
class EncodedDeploymentData:
    """部署数据编码结果。

    - ``constructor``：命中的 :class:`ConstructorDefinition`；
    - ``args``：按声明顺序排列的 :class:`ConstructorArgument`；
    - ``data``：``creation_bytecode + 参数 tuple 的 ABI 编码`` 的完整
      部署数据，零参数时即 creation bytecode 本身。
    """

    constructor: ConstructorDefinition
    args: tuple[ConstructorArgument, ...]
    data: bytes

    @property
    def data_hex(self) -> str:
        """``"0x"`` 前缀的完整部署数据小写十六进制字符串。"""
        return "0x" + self.data.hex()

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple。"""
        return tuple(arg.value for arg in self.args)


@dataclass(frozen=True)
class DecodedDeploymentData:
    """部署数据解码结果。

    - ``constructor``：命中的 :class:`ConstructorDefinition`；
    - ``args``：按声明顺序排列的 :class:`ConstructorArgument`；
    - ``data``：参与解码的完整部署数据（creation bytecode + 参数区）。
    """

    constructor: ConstructorDefinition
    args: tuple[ConstructorArgument, ...] = field(default=())
    data: bytes = b""

    @property
    def data_hex(self) -> str:
        """``"0x"`` 前缀的完整部署数据小写十六进制字符串。"""
        return "0x" + self.data.hex()

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple。"""
        return tuple(arg.value for arg in self.args)


# ---- ABI 解析 -------------------------------------------------------------


def _metadata(message: str) -> AbiMetadataError:
    return AbiMetadataError(message)


def _parse_constructor_entry(entry: dict) -> ConstructorDefinition:
    """从单个 ABI constructor 条目构造不可变定义。"""
    if "inputs" not in entry:
        raise _metadata("constructor 条目缺少 inputs")
    inputs_node = entry["inputs"]
    if not isinstance(inputs_node, (list, tuple)):
        raise _metadata("constructor 条目的 inputs 必须是数组")

    parameters: list[ConstructorParameter] = []
    for node in inputs_node:
        if not isinstance(node, dict):
            raise _metadata(
                "constructor 参数必须是 JSON 对象，"
                f"得到 {type(node).__name__}"
            )
        param_name = node.get("name", "")
        if not isinstance(param_name, str):
            raise _metadata(
                f"constructor 参数 name 必须是字符串，得到 {param_name!r}"
            )
        try:
            # 与事件/函数路径共用同一套 type + components 递归展开；其
            # 失败统一是事件层错误码，这里转译为构造函数元数据错误。
            abi_type = _build_type(node)
        except (AbiEventError, ABITypeError) as exc:
            raise _metadata(f"constructor 参数类型非法：{exc}") from None
        parameters.append(ConstructorParameter(param_name, abi_type))
    return ConstructorDefinition(tuple(parameters))


def parse_constructor_abi(abi) -> ConstructorDefinition:
    """解析 ABI，返回其中 constructor 条目的不可变定义。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只消费 ``type == "constructor"`` 的
    条目；function、event、error、receive、fallback 等其他条目原样保留
    在 ABI 中但不解析、不影响部署路径。ABI 中无 constructor 条目时返回
    零输入的 :class:`ConstructorDefinition`；存在多个 constructor 条目、
    ABI 根非法或 constructor 元数据非法抛
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

    definitions: list[ConstructorDefinition] = []
    for entry in root:
        if not isinstance(entry, dict):
            raise _metadata(
                f"ABI 条目必须是 JSON 对象，得到 {type(entry).__name__}"
            )
        # 非 constructor 条目（function/event/error/receive/fallback 及
        # 其他）保留在 ABI 中供各自入口使用，部署路径跳过且不校验。
        if entry.get("type") == "constructor":
            definitions.append(_parse_constructor_entry(entry))

    if len(definitions) > 1:
        raise _metadata(
            f"ABI 中存在 {len(definitions)} 个 constructor 条目，"
            "合法 ABI 至多一个"
        )
    if definitions:
        return definitions[0]
    return ConstructorDefinition(())


# ---- 部署数据编解码 -------------------------------------------------------


def _coerce_deployment_bytes(value, noun: str) -> bytes:
    """把 bytes 或可选 0x 前缀的偶数位十六进制字符串规范化为 bytes。"""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        text = value[2:] if value[:2] in ("0x", "0X") else value
        if len(text) % 2 or any(c not in _HEX_DIGITS for c in text):
            raise AbiDeploymentDataError(
                f"{noun} 必须是可选 0x 前缀的偶数位十六进制字符串"
            )
        return bytes.fromhex(text)
    raise AbiDeploymentDataError(
        f"{noun} 必须是 bytes 或十六进制 str，得到 {type(value).__name__}"
    )


def _input_types(constructor: ConstructorDefinition) -> TupleType:
    return TupleType(tuple(p.abi_type for p in constructor.inputs))


def _annotate(
    constructor: ConstructorDefinition, values: tuple
) -> tuple[ConstructorArgument, ...]:
    return tuple(
        ConstructorArgument(
            parameter.name, format_abi_type(parameter.abi_type), value
        )
        for parameter, value in zip(constructor.inputs, values)
    )


def encode_constructor_data(
    abi, creation_bytecode, args=None
) -> EncodedDeploymentData:
    """编码一次合约部署的完整数据。

    ``abi`` 接受 ABI JSON 字符串或等价条目数组；``creation_bytecode``
    接受 bytes 或可选 ``0x`` 前缀的偶数位十六进制字符串；``args`` 为按
    constructor inputs 声明顺序排列的实参列表（list/tuple），零输入构造
    函数可省略。

    返回 :class:`EncodedDeploymentData`，其中 ``data`` 为
    ``规范化 creation bytecode + 参数 tuple 的 ABI 编码``，零参数时即
    creation bytecode 本身；``args`` 按声明顺序保留参数名与规范类型。

    ABI 或 constructor 元数据非法（含多个 constructor 条目）抛
    :class:`abi_kit.AbiMetadataError`；creation_bytecode 类型或十六进制
    非法抛 :class:`abi_kit.AbiDeploymentDataError`；实参数量或类型与
    inputs 声明不符抛 :class:`abi_kit.ABIValueError`。
    """
    constructor = parse_constructor_abi(abi)
    bytecode = _coerce_deployment_bytes(creation_bytecode, "creation_bytecode")

    if args is None:
        values = ()
    elif isinstance(args, (list, tuple)):
        values = tuple(args)
    else:
        raise ABIValueError(
            f"实参列表必须是 list 或 tuple，得到 {type(args).__name__}"
        )

    payload = _encode(_input_types(constructor), values)
    return EncodedDeploymentData(
        constructor, _annotate(constructor, values), bytecode + payload
    )


def decode_constructor_data(
    abi, creation_bytecode, deployment_data
) -> DecodedDeploymentData:
    """从完整部署数据还原 constructor 实参。

    ``abi`` 与 ``creation_bytecode`` 口径同
    :func:`encode_constructor_data`；``deployment_data`` 接受 bytes 或
    可选 ``0x`` 前缀的偶数位十六进制字符串，其开头必须与
    ``creation_bytecode`` 逐字节一致，剩余部分按 constructor inputs 组成
    的 tuple 严格 ABI 解码。返回 :class:`DecodedDeploymentData`，携带
    constructor 定义、按声明顺序排列的带类型标注实参与完整部署数据；
    ``values`` 属性给出等价的纯值 tuple。

    ABI 或 constructor 元数据非法抛 :class:`abi_kit.AbiMetadataError`；
    creation_bytecode / deployment_data 类型或十六进制非法、
    deployment_data 短于 creation bytecode 或开头与之不一致抛
    :class:`abi_kit.AbiDeploymentDataError`；参数区不能按声明类型严格
    解码抛 :class:`abi_kit.ABIValueError`；解码后仍有尾随字节抛
    :class:`abi_kit.AbiTrailingDataError`。
    """
    constructor = parse_constructor_abi(abi)
    bytecode = _coerce_deployment_bytes(creation_bytecode, "creation_bytecode")
    raw = _coerce_deployment_bytes(deployment_data, "deployment_data")

    if len(raw) < len(bytecode):
        raise AbiDeploymentDataError(
            f"deployment_data 长度 {len(raw)} 字节短于 creation_bytecode "
            f"的 {len(bytecode)} 字节"
        )
    if raw[: len(bytecode)] != bytecode:
        raise AbiDeploymentDataError(
            "deployment_data 的开头与 creation_bytecode 不一致"
        )

    payload = raw[len(bytecode):]
    bound = len(payload)
    values, end = _decode(_input_types(constructor), payload, 0, bound)
    if end != bound:
        raise AbiTrailingDataError(
            f"constructor 参数解码完成后仍有 {bound - end} 字节尾随数据未被消费"
        )

    return DecodedDeploymentData(constructor, _annotate(constructor, values), raw)
