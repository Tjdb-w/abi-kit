"""合约部署阶段 constructor 参数的 ABI 解析与部署数据编解码。

- :func:`parse_constructor_abi`：解析 ABI JSON 字符串或等价条目数组，只
  消费 ``type == "constructor"`` 的条目，返回不可变
  :class:`ConstructorDefinition`；ABI 中没有 constructor 条目时返回零
  输入定义；出现多个 constructor 条目抛
  :class:`abi_kit.AbiMetadataError`；function / event / error / receive /
  fallback 条目跳过不解析。
- :func:`encode_constructor_data`：按 constructor inputs 声明顺序把
  list/tuple 实参编码为参数 tuple 的 ABI 编码，接在规范化后的 creation
  bytecode 之后，返回 :class:`EncodedDeploymentData`；零参数时 ``data``
  就是 creation bytecode 本身。
- :func:`decode_constructor_data`：先逐字节核对 deployment data 开头与
  creation bytecode 完全一致，再严格解码剩余的完整参数 tuple，返回
  :class:`DecodedDeploymentData`，携带按声明顺序排列的带名称与规范类型
  标注的参数。

creation bytecode 与 deployment data 均接受 ``bytes`` 或可选 ``0x``
前缀的偶数位十六进制字符串。

错误约定：

- 元数据非法（ABI 根不是数组、constructor 条目缺 inputs、参数描述非法、
  类型字符串无法解析、出现多个 constructor 等）抛
  :class:`abi_kit.AbiMetadataError`；
- creation bytecode / deployment data 类型或十六进制非法、deployment
  data 短于 creation bytecode，或开头与 creation bytecode 逐字节不一致
  抛 :class:`abi_kit.AbiDeploymentDataError`；
- 实参不能按声明类型编码（含数量不符）、或参数区不能按声明类型严格
  解码抛 :class:`abi_kit.ABIValueError`；
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

#: constructor 路径跳过但允许保留在 ABI 中的非 constructor 条目类型；
#: 这些条目的合法性由各自入口（如函数、事件、error 路径）负责，本模块
#: 不解析。
_NON_CONSTRUCTOR_ENTRY_TYPES = frozenset(
    ("function", "event", "error", "receive", "fallback")
)


# ---- 不可变定义与结果对象 -------------------------------------------------


@dataclass(frozen=True)
class ConstructorParameter:
    """constructor 的单个参数（声明顺序保留）。

    - ``name``：ABI JSON 中的参数名，缺省为空串；
    - ``abi_type``：由 ``type`` + ``components`` 展开得到的类型对象。
    """

    name: str
    abi_type: ABIType


@dataclass(frozen=True)
class ConstructorDefinition:
    """不可变 constructor 定义。

    constructor 没有名称；ABI 中无 constructor 条目时得到
    ``inputs == ()`` 的零输入定义。

    - ``inputs``：按声明顺序保存的 :class:`ConstructorParameter`。
    """

    inputs: tuple[ConstructorParameter, ...] = field(default=())


@dataclass(frozen=True)
class EncodedDeploymentData:
    """合约部署数据编码结果。

    - ``constructor``：命中的 :class:`ConstructorDefinition`；
    - ``args``：按 inputs 声明顺序排列的实参纯值 tuple；
    - ``data``：``creation bytecode + 参数 tuple 的 ABI 编码`` 的完整
      部署数据；零参数时即规范化后的 creation bytecode。
    """

    constructor: ConstructorDefinition
    args: tuple = ()
    data: bytes = b""

    @property
    def data_hex(self) -> str:
        """``"0x"`` 前缀的完整部署数据小写十六进制字符串。"""
        return "0x" + self.data.hex()


@dataclass(frozen=True)
class ConstructorArgument:
    """解码后带类型标注的单个 constructor 实参。

    - ``name``：声明的参数名，未命名时为空串；
    - ``type``：规范 ABI 类型字符串（不含空白与参数名）；
    - ``value``：按值层口径还原的 Python 值。
    """

    name: str
    type: str
    value: object


@dataclass(frozen=True)
class DecodedDeploymentData:
    """合约部署数据解码结果。

    - ``constructor``：使用的 :class:`ConstructorDefinition`；
    - ``args``：按声明顺序排列的 :class:`ConstructorArgument`，携带名称、
      规范类型与还原值；
    - ``data``：规范化后的完整部署数据（creation bytecode + 参数编码）。
    """

    constructor: ConstructorDefinition
    args: tuple[ConstructorArgument, ...] = field(default=())
    data: bytes = b""

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple。"""
        return tuple(arg.value for arg in self.args)

    @property
    def data_hex(self) -> str:
        """``"0x"`` 前缀的完整部署数据小写十六进制字符串。"""
        return "0x" + self.data.hex()


# ---- ABI 解析 -------------------------------------------------------------


def _metadata(message: str) -> AbiMetadataError:
    return AbiMetadataError(message)


def _parse_constructor_entry(entry: dict) -> ConstructorDefinition:
    """从单个 ABI constructor 条目构造不可变 constructor 定义。"""
    if "inputs" not in entry:
        raise _metadata("constructor 条目缺少 inputs")
    inputs_node = entry["inputs"]
    if not isinstance(inputs_node, (list, tuple)):
        raise _metadata("constructor 的 inputs 必须是数组")

    parameters: list[ConstructorParameter] = []
    for node in inputs_node:
        if not isinstance(node, dict):
            raise _metadata(
                "constructor 的参数必须是 JSON 对象，"
                f"得到 {type(node).__name__}"
            )
        param_name = node.get("name", "")
        if not isinstance(param_name, str):
            raise _metadata(
                "constructor 的参数 name 必须是字符串，"
                f"得到 {param_name!r}"
            )
        try:
            # 与事件、函数路径共用同一套 type + components 递归展开；其
            # 失败统一是事件层错误码，这里转译为元数据错误。
            abi_type = _build_type(node)
        except (AbiEventError, ABITypeError) as exc:
            raise _metadata(f"constructor 的参数类型非法：{exc}") from None
        parameters.append(ConstructorParameter(param_name, abi_type))
    return ConstructorDefinition(tuple(parameters))


def parse_constructor_abi(abi) -> ConstructorDefinition:
    """解析 ABI，返回其中唯一 constructor 条目的不可变定义。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只消费 ``type == "constructor"`` 的
    条目；function、event、error、receive、fallback 等其他条目原样保留
    在 ABI 中但不解析、不影响部署路径。ABI 中没有 constructor 条目时
    返回零输入的 :class:`ConstructorDefinition`；出现两个及以上
    constructor 条目抛 :class:`abi_kit.AbiMetadataError`。constructor
    条目的 ``inputs`` 必填，支持基础类型、数组与 ``components`` 嵌套
    tuple；``payable`` / ``stateMutability`` 等其余字段不参与解析。
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

    constructor = None
    for entry in root:
        if not isinstance(entry, dict):
            raise _metadata(
                f"ABI 条目必须是 JSON 对象，得到 {type(entry).__name__}"
            )
        entry_type = entry.get("type")
        if entry_type == "constructor":
            if constructor is not None:
                raise _metadata(
                    "ABI 中至多允许一个 constructor 条目，"
                    f"检测到多个（{entry_type!r}）"
                )
            constructor = _parse_constructor_entry(entry)
        elif entry_type in _NON_CONSTRUCTOR_ENTRY_TYPES:
            # function/event/error/receive/fallback 条目保留在 ABI 中供
            # 各自入口使用，部署路径跳过且不校验。
            continue
        else:
            raise _metadata(
                f"ABI 条目缺少 type 或带有未知类型 {entry_type!r}，"
                "合法值为 function/event/constructor/error/receive/fallback"
            )

    # 无 constructor 条目即零输入构造。
    return constructor if constructor is not None else ConstructorDefinition()


# ---- bytecode / deployment data 规范化 ------------------------------------


def _coerce_bytecode(data, noun: str) -> bytes:
    """把 bytes 或可选 0x 前缀的偶数位十六进制字符串规范化为 bytes。

    ``noun`` 为 ``"creation bytecode"`` / ``"deployment data"`` 等字段
    中文名，用于错误信息；任何失败统一抛 :class:`AbiDeploymentDataError`。
    """
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        text = data[2:] if data[:2] in ("0x", "0X") else data
        if len(text) % 2 or any(c not in _HEX_DIGITS for c in text):
            raise AbiDeploymentDataError(
                f"{noun}必须是可选 0x 前缀的偶数位十六进制字符串"
            )
        return bytes.fromhex(text)
    raise AbiDeploymentDataError(
        f"{noun}必须是 bytes 或十六进制 str，得到 {type(data).__name__}"
    )


def _input_types(constructor: ConstructorDefinition) -> TupleType:
    return TupleType(tuple(parameter.abi_type for parameter in constructor.inputs))


# ---- 部署数据编解码 -------------------------------------------------------


def encode_constructor_data(abi, creation_bytecode, args=None) -> EncodedDeploymentData:
    """编码一次合约部署的 deployment data。

    ``abi`` 接受 ABI JSON 字符串或等价条目数组（无 constructor 条目即
    零输入）；``creation_bytecode`` 接受 ``bytes`` 或可选 ``0x`` 前缀的
    偶数位十六进制字符串；``args`` 为按 constructor inputs 声明顺序排列
    的实参列表（list/tuple），零参数 constructor 可省略。

    返回 :class:`EncodedDeploymentData`，其中 ``data`` 为
    ``creation bytecode + 参数 tuple 的 ABI 编码`` 的完整部署数据，
    ``data_hex`` 为其 ``"0x"`` 前缀小写十六进制形式；零参数时 ``data``
    就是规范化后的 creation bytecode。

    creation bytecode 类型或十六进制非法抛
    :class:`abi_kit.AbiDeploymentDataError`；实参不能按声明类型编码（含
    数量不符）抛 :class:`abi_kit.ABIValueError`。
    """
    constructor = parse_constructor_abi(abi)
    bytecode = _coerce_bytecode(creation_bytecode, "creation bytecode ")

    if args is None:
        values = ()
    elif isinstance(args, (list, tuple)):
        values = tuple(args)
    else:
        raise ABIValueError(
            f"实参列表必须是 list 或 tuple，得到 {type(args).__name__}"
        )

    payload = _encode(_input_types(constructor), values)
    return EncodedDeploymentData(constructor, values, bytecode + payload)


def decode_constructor_data(
    abi, creation_bytecode, deployment_data
) -> DecodedDeploymentData:
    """解码完整合约部署数据，还原 constructor 实参。

    ``abi`` 与 ``creation_bytecode`` 口径同
    :func:`encode_constructor_data`；``deployment_data`` 接受 ``bytes``
    或可选 ``0x`` 前缀的偶数位十六进制字符串。函数先逐字节核对
    deployment data 以 creation bytecode 开头（长度不足或前缀不一致即
    失败），再对剩余字节按 constructor inputs 组成的 tuple 严格 ABI
    解码。返回 :class:`DecodedDeploymentData`，携带 constructor 定义、
    规范化后的完整部署数据与按声明顺序排列的带类型标注参数；``values``
    属性给出等价的纯值 tuple。零参数 constructor 的 deployment data 必
    须恰好等于 creation bytecode，解码为空 tuple。

    creation bytecode / deployment data 类型或十六进制非法抛
    :class:`abi_kit.AbiDeploymentDataError`；deployment data 短于
    creation bytecode 或前缀逐字节不一致同样抛
    :class:`abi_kit.AbiDeploymentDataError`；参数数量、类型或 ABI 编码
    布局不合法抛 :class:`abi_kit.ABIValueError`；参数解码完成后仍有尾随
    字节抛 :class:`abi_kit.AbiTrailingDataError`。
    """
    constructor = parse_constructor_abi(abi)
    bytecode = _coerce_bytecode(creation_bytecode, "creation bytecode ")
    raw = _coerce_bytecode(deployment_data, "deployment data ")

    prefix_len = len(bytecode)
    if len(raw) < prefix_len:
        raise AbiDeploymentDataError(
            f"deployment data 长度必须至少为 creation bytecode 的 "
            f"{prefix_len} 字节，得到 {len(raw)} 字节"
        )
    if raw[:prefix_len] != bytecode:
        raise AbiDeploymentDataError(
            "deployment data 开头与 creation bytecode 逐字节不一致"
        )

    payload = raw[prefix_len:]
    bound = len(payload)
    values, end = _decode(_input_types(constructor), payload, 0, bound)
    if end != bound:
        raise AbiTrailingDataError(
            f"constructor 参数解码完成后仍有 {bound - end} 字节尾随数据未被消费"
        )

    arguments = tuple(
        ConstructorArgument(
            parameter.name, format_abi_type(parameter.abi_type), value
        )
        for parameter, value in zip(constructor.inputs, values)
    )
    return DecodedDeploymentData(constructor, arguments, raw)
