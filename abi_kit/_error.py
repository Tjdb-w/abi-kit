"""Solidity error ABI 解析、selector 与 revert data 编解码。

- :func:`parse_error_abi`：解析 ABI JSON 字符串或等价条目数组，只消费
  ``type == "error"`` 的条目，按声明顺序返回不可变
  :class:`ErrorDefinition`；function / event / constructor / receive /
  fallback 条目跳过不解析。
- :func:`canonical_error_signature`：返回 ``Name(type1,type2,...)`` 形式
  的规范错误签名（参数名不参与）。
- :func:`error_selector`：返回 ``keccak256(规范签名)[:4]`` 的四字节
  selector（bytes）。
- :func:`encode_error_data`：按错误名（无重载时）或规范签名选择 error，
  按 inputs 声明顺序把 list/tuple 实参编码为 ``selector + ABI(参数
  tuple)`` 的完整 revert data，返回 :class:`EncodedErrorData`。
- :func:`decode_error_data`：按 revert data 前四字节 selector 定位
  error，再严格解码参数 tuple，返回 :class:`DecodedErrorData`，携带
  名称、规范类型与值 tuple；无参数 error 的 revert data 即四字节
  selector，解码为空 tuple。

错误约定：

- 元数据非法（ABI 根不是数组、error 条目缺 name/type/inputs、标识符
  非法、参数类型字符串无法解析、规范签名重复等）抛
  :class:`abi_kit.AbiMetadataError`；
- 错误名或规范签名无匹配抛 :class:`abi_kit.AbiErrorNotFoundError`；
- 只给错误名但同名重载不止一个、无法唯一选择抛
  :class:`abi_kit.AbiErrorOverloadError`；
- revert data 少于四字节抛 :class:`abi_kit.AbiErrorDataLengthError`；
- selector 匹配不到任何 error 抛 :class:`abi_kit.AbiErrorSelectorError`；
- 实参不能按声明类型编码、或参数区不能按声明类型严格解码抛
  :class:`abi_kit.ABIValueError`；
- 参数消费完后仍有尾随字节抛 :class:`abi_kit.AbiErrorTrailingDataError`；
- revert data 不是 bytes 或可选 ``0x`` 前缀的偶数位十六进制字符串抛
  :class:`abi_kit.ABIValueError`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._codec import _decode, _encode
from ._event import _build_type, _is_identifier
from ._exceptions import (
    AbiErrorDataLengthError,
    AbiErrorNotFoundError,
    AbiErrorOverloadError,
    AbiErrorSelectorError,
    AbiErrorTrailingDataError,
    AbiEventError,
    AbiMetadataError,
    ABITypeError,
    ABIValueError,
)
from ._format import format_abi_type
from ._keccak import keccak_256
from ._types import ABIType, TupleType

_SELECTOR_LEN = 4
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

#: error 路径跳过但允许保留在 ABI 中的非 error 条目类型；这些条目的合法性
#: 由各自入口（如函数、事件路径）负责，本模块不解析。
_NON_ERROR_ENTRY_TYPES = frozenset(
    ("function", "event", "constructor", "receive", "fallback")
)


# ---- 不可变定义与结果对象 -------------------------------------------------


@dataclass(frozen=True)
class ErrorArgument:
    """error 的单个参数（声明顺序保留）。

    - ``name``：ABI JSON 中的参数名，缺省为空串；
    - ``abi_type``：由 ``type`` + ``components`` 展开得到的类型对象；
    - ``value``：解码结果中按值层口径还原的 Python 值；定义中为 None。
    """

    name: str
    abi_type: ABIType
    value: object = None

    @property
    def type(self) -> str:
        """规范 ABI 类型字符串（不含空白与参数名）。"""
        return format_abi_type(self.abi_type)


@dataclass(frozen=True)
class ErrorDefinition:
    """不可变 error 定义。

    - ``name``：非空 ASCII 错误标识符；
    - ``inputs``：按声明顺序保存的 :class:`ErrorArgument`。
    """

    name: str
    inputs: tuple[ErrorArgument, ...] = field(default=())


@dataclass(frozen=True)
class EncodedErrorData:
    """error revert data 编码结果。

    - ``error``：命中的 :class:`ErrorDefinition`；
    - ``selector``：四字节 selector（bytes）；
    - ``data``：``selector + 参数 ABI 编码`` 的完整 revert data。
    """

    error: ErrorDefinition
    selector: bytes
    data: bytes

    @property
    def error_name(self) -> str:
        """命中 error 的名称。"""
        return self.error.name

    @property
    def signature(self) -> str:
        """命中 error 的规范签名，如 ``InsufficientBalance(address,uint256)``。"""
        return canonical_error_signature(self.error)

    @property
    def selector_hex(self) -> str:
        """``"0x"`` + 8 个小写十六进制字符形式的 selector。"""
        return "0x" + self.selector.hex()

    @property
    def data_hex(self) -> str:
        """``"0x"`` 前缀的完整 revert data 十六进制字符串。"""
        return "0x" + self.data.hex()


@dataclass(frozen=True)
class DecodedErrorData:
    """error revert data 解码结果。

    - ``error``：selector 命中的 :class:`ErrorDefinition`；
    - ``selector``：revert data 前四字节（bytes）；
    - ``args``：按声明顺序排列的 :class:`ErrorArgument`，``value`` 为
      还原值。
    """

    error: ErrorDefinition
    selector: bytes
    args: tuple[ErrorArgument, ...] = field(default=())

    @property
    def error_name(self) -> str:
        """命中 error 的名称。"""
        return self.error.name

    @property
    def signature(self) -> str:
        """命中 error 的规范签名，如 ``InsufficientBalance(address,uint256)``。"""
        return canonical_error_signature(self.error)

    @property
    def selector_hex(self) -> str:
        """``"0x"`` + 8 个小写十六进制字符形式的 selector。"""
        return "0x" + self.selector.hex()

    @property
    def types(self) -> tuple[str, ...]:
        """按声明顺序排列的规范 ABI 类型字符串 tuple。"""
        return tuple(arg.type for arg in self.args)

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple。"""
        return tuple(arg.value for arg in self.args)


# ---- ABI 解析 -------------------------------------------------------------


def _metadata(message: str) -> AbiMetadataError:
    return AbiMetadataError(message)


def _parse_error_entry(entry: dict) -> ErrorDefinition:
    """从单个 ABI error 条目构造不可变错误定义。"""
    name = entry.get("name")
    if not _is_identifier(name):
        raise _metadata(f"error name 必须是非空 ASCII 标识符，得到 {name!r}")
    if "inputs" not in entry:
        raise _metadata(f"error {name!r} 缺少 inputs")
    inputs_node = entry["inputs"]
    if not isinstance(inputs_node, (list, tuple)):
        raise _metadata(f"error {name!r} 的 inputs 必须是数组")

    parameters: list[ErrorArgument] = []
    for node in inputs_node:
        if not isinstance(node, dict):
            raise _metadata(
                f"error {name!r} 的参数必须是 JSON 对象，"
                f"得到 {type(node).__name__}"
            )
        param_name = node.get("name", "")
        if not isinstance(param_name, str):
            raise _metadata(
                f"error {name!r} 的参数 name 必须是字符串，得到 {param_name!r}"
            )
        try:
            # 与事件、函数路径共用同一套 type + components 递归展开；其
            # 失败统一是事件层错误码，这里转译为 error 元数据错误。
            abi_type = _build_type(node)
        except (AbiEventError, ABITypeError) as exc:
            raise _metadata(f"error {name!r} 的参数类型非法：{exc}") from None
        parameters.append(ErrorArgument(param_name, abi_type))
    return ErrorDefinition(name, tuple(parameters))


def parse_error_abi(abi) -> tuple[ErrorDefinition, ...]:
    """解析 ABI，返回其中全部 error 条目的不可变定义。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只消费 ``type == "error"`` 的条目，
    按声明顺序返回；function、event、constructor、receive、fallback 等
    其他条目原样保留在 ABI 中但不解析、不影响 error 路径。error 条目的
    ``inputs`` 必填；签名只由 name 与 inputs 的规范类型构成，参数名不
    参与。任何 error 元数据非法（含规范签名重复）抛
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

    errors: list[ErrorDefinition] = []
    for entry in root:
        if not isinstance(entry, dict):
            raise _metadata(
                f"ABI 条目必须是 JSON 对象，得到 {type(entry).__name__}"
            )
        entry_type = entry.get("type")
        # function/event/constructor/receive/fallback 条目保留在 ABI 中供
        # 各自入口使用，error 路径跳过且不校验；缺 type 或未知条目类型属
        # 元数据非法，统一抛 AbiMetadataError。
        if entry_type == "error":
            errors.append(_parse_error_entry(entry))
        elif entry_type in _NON_ERROR_ENTRY_TYPES:
            continue
        else:
            raise _metadata(
                f"ABI 条目缺少 type 或带有未知类型 {entry_type!r}，"
                "合法值为 function/event/constructor/error/receive/fallback"
            )

    seen: set[str] = set()
    for error in errors:
        signature = canonical_error_signature(error)
        if signature in seen:
            raise _metadata(f"error 规范签名重复：{signature!r}")
        seen.add(signature)
    return tuple(errors)


# ---- 规范签名与 selector --------------------------------------------------


def canonical_error_signature(error: ErrorDefinition) -> str:
    """返回规范错误签名 ``Name(type1,type2,...)``。

    参数名不参与签名；tuple 输出为 ``(t1,t2)``，数组保留 ``[]``/``[n]``
    后缀。
    """
    if not isinstance(error, ErrorDefinition):
        raise _metadata(
            f"需要 ErrorDefinition，得到 {type(error).__name__}"
        )
    canonical = ",".join(format_abi_type(p.abi_type) for p in error.inputs)
    return f"{error.name}({canonical})"


def error_selector(error: ErrorDefinition) -> bytes:
    """返回 error 四字节 selector：``keccak256(规范签名)[:4]``。"""
    signature = canonical_error_signature(error)
    try:
        digest = keccak_256(signature.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise _metadata(f"规范签名无法生成 selector：{exc}") from None
    return digest[:_SELECTOR_LEN]


# ---- 选择 error -----------------------------------------------------------


def _resolve_error(errors: tuple[ErrorDefinition, ...], key) -> ErrorDefinition:
    """按错误名或规范签名在已解析 error 中唯一定位。"""
    if not isinstance(key, str) or not key:
        raise _metadata(f"错误名或规范签名必须是非空字符串，得到 {key!r}")

    if "(" in key or ")" in key:
        for error in errors:
            if canonical_error_signature(error) == key:
                return error
        raise AbiErrorNotFoundError(
            f"ABI 中找不到规范签名为 {key!r} 的 error"
        )

    if not _is_identifier(key):
        raise _metadata(f"非法错误名：{key!r}")

    candidates = [error for error in errors if error.name == key]
    if not candidates:
        raise AbiErrorNotFoundError(f"ABI 中找不到 error {key!r}")
    if len(candidates) > 1:
        signatures = ", ".join(
            canonical_error_signature(error) for error in candidates
        )
        raise AbiErrorOverloadError(
            f"error {key!r} 存在 {len(candidates)} 个重载，无法唯一选择，"
            f"请改用规范签名之一：{signatures}"
        )
    return candidates[0]


# ---- revert data 编解码 ---------------------------------------------------


def _coerce_error_data(data) -> bytes:
    """把 bytes 或可选 0x 前缀的偶数位十六进制字符串规范化为 bytes。"""
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        text = data[2:] if data[:2] in ("0x", "0X") else data
        if len(text) % 2 or any(c not in _HEX_DIGITS for c in text):
            raise ABIValueError(
                "revert data 必须是可选 0x 前缀的偶数位十六进制字符串"
            )
        return bytes.fromhex(text)
    raise ABIValueError(
        f"revert data 必须是 bytes 或十六进制 str，得到 {type(data).__name__}"
    )


def _input_types(error: ErrorDefinition) -> TupleType:
    return TupleType(tuple(parameter.abi_type for parameter in error.inputs))


def encode_error_data(abi, error_name, args=None) -> EncodedErrorData:
    """编码一次 error revert data。

    ``abi`` 接受 ABI JSON 字符串或等价条目数组；``error_name`` 接受
    错误名（ABI 中无同名重载时）或规范错误签名
    （``InsufficientBalance(address,uint256)``）；``args`` 为按 inputs
    声明顺序排列的实参列表（list/tuple），无参 error 可省略。

    返回 :class:`EncodedErrorData`，其中 ``selector`` 为四字节 bytes，
    ``data`` 为 ``selector + 参数 tuple 的 ABI 编码`` 的完整 revert
    data。实参不能按声明类型编码（含数量不符）抛
    :class:`abi_kit.ABIValueError`。
    """
    errors = parse_error_abi(abi)
    error = _resolve_error(errors, error_name)

    if args is None:
        values = ()
    elif isinstance(args, (list, tuple)):
        values = tuple(args)
    else:
        raise ABIValueError(
            f"实参列表必须是 list 或 tuple，得到 {type(args).__name__}"
        )

    payload = _encode(_input_types(error), values)
    selector = error_selector(error)
    return EncodedErrorData(error, selector, selector + payload)


def decode_error_data(abi, data) -> DecodedErrorData:
    """解码完整 error revert data。

    ``data`` 接受 bytes 或可选 ``0x`` 前缀的偶数位十六进制字符串，前四
    字节必须是 ABI 中某个 error 的 selector；其后按该 error inputs 组成
    的 tuple 严格 ABI 解码。返回 :class:`DecodedErrorData`，携带 error
    定义、selector、规范签名（属性）与按声明顺序排列的带类型标注参数；
    ``values`` 属性给出等价的纯值 tuple。无参数 error 的 revert data 即
    四字节 selector，解码为空 tuple。

    data 类型或十六进制非法抛 :class:`abi_kit.ABIValueError`；少于四字节
    抛 :class:`abi_kit.AbiErrorDataLengthError`；selector 无匹配抛
    :class:`abi_kit.AbiErrorSelectorError`；参数区不能严格解码抛
    :class:`abi_kit.ABIValueError`；解码后仍有尾随字节抛
    :class:`abi_kit.AbiErrorTrailingDataError`。
    """
    errors = parse_error_abi(abi)
    raw = _coerce_error_data(data)

    if len(raw) < _SELECTOR_LEN:
        raise AbiErrorDataLengthError(
            f"revert data 长度必须至少为 {_SELECTOR_LEN} 字节以容纳 "
            f"selector，得到 {len(raw)} 字节"
        )
    selector = raw[:_SELECTOR_LEN]

    matches = [error for error in errors if error_selector(error) == selector]
    if not matches:
        raise AbiErrorSelectorError(
            f"selector 0x{selector.hex()} 在 ABI 中匹配不到任何 error"
        )
    error = matches[0]

    payload = raw[_SELECTOR_LEN:]
    bound = len(payload)
    values, end = _decode(_input_types(error), payload, 0, bound)
    if end != bound:
        raise AbiErrorTrailingDataError(
            f"参数解码完成后仍有 {bound - end} 字节尾随数据未被消费"
        )

    arguments = tuple(
        ErrorArgument(parameter.name, parameter.abi_type, value)
        for parameter, value in zip(error.inputs, values)
    )
    return DecodedErrorData(error, selector, arguments)
