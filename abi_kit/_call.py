"""合约调用分派：统一处理 function、receive 与 fallback。

- :func:`parse_contract_call_registry`：解析 ABI JSON 字符串或等价条目
  数组，只登记 function / receive / fallback 三类条目，构建不可变
  :class:`ContractCallRegistry`；其余条目（event / constructor /
  error、缺 type、未知类型或非对象条目）跳过不解析。function 保留名
  称、inputs、outputs 与 selector 语义（同
  :func:`abi_kit.parse_function_abi`）；receive 与 fallback 至多各一
  个且不接受 inputs。
- :func:`encode_contract_call`：``target`` 接受无重载函数名、规范函数
  签名、``"receive"`` 或 ``"fallback"``。函数按 inputs 顺序编码实参，
  得到 ``selector + 参数区`` 的完整 calldata；receive 仅接受空实参与
  空 data，calldata 为 ``b""``；fallback 仅接受空实参，把 data 原样
  放入 calldata。
- :func:`decode_contract_call`：按完整 calldata 分派并严格解码，返回
  :class:`DecodedContractCall`（``kind`` / ``function`` / ``args`` /
  ``data`` / ``calldata``）。空 calldata 优先选 receive，否则选
  fallback；未知 selector（含不足四字节）有 fallback 时归入 fallback，
  否则报告目标不存在。

错误约定：

- 注册表构建与调用分派失败统一抛
  :class:`abi_kit.AbiContractCallError`，以 ``code`` 区分
  （CALL_ENTRY_INVALID / CALL_TARGET_NOT_FOUND /
  CALL_TARGET_AMBIGUOUS / CALL_DATA_INVALID /
  CALL_RECEIVE_NONEMPTY / CALL_FALLBACK_ARGS）；
- 实参值与声明类型不匹配（含数量不符）仍抛
  :class:`abi_kit.ABIValueError`；
- 函数参数区按声明类型消费完后仍有尾随字节仍抛
  :class:`abi_kit.AbiTrailingDataError`；
- 调用层失败只抛异常，不返回部分结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._codec import _decode, _encode
from ._exceptions import (
    AbiContractCallError,
    AbiMetadataError,
    AbiTrailingDataError,
    ABIValueError,
)
from ._format import format_abi_type
from ._function import (
    FunctionArgument,
    FunctionDefinition,
    _parse_function_entry,
    canonical_function_signature,
    function_selector,
)
from ._types import TupleType

_SELECTOR_LEN = 4

#: 调用种类：普通函数、receive、fallback。
KIND_FUNCTION = "function"
KIND_RECEIVE = "receive"
KIND_FALLBACK = "fallback"

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


# ---- 不可变注册表 ----------------------------------------------------------


@dataclass(frozen=True)
class ContractCallRegistry:
    """不可变合约调用注册表（function 按 ABI 声明顺序保存）。

    - ``functions``：按 ABI 顺序保存的 :class:`FunctionDefinition` tuple；
    - ``receive`` / ``fallback``：是否登记了 receive / fallback 条目
      （至多各一个，由 :func:`parse_contract_call_registry` 校验）。

    名称、规范签名与 selector 的查找索引在构造时预计算。
    """

    functions: tuple[FunctionDefinition, ...] = field(default=())
    receive: bool = False
    fallback: bool = False
    _by_selector: dict = field(init=False, repr=False, compare=False)
    _by_name: dict = field(init=False, repr=False, compare=False)
    _by_signature: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        by_selector: dict = {}
        by_name: dict = {}
        by_signature: dict = {}
        for function in self.functions:
            by_selector.setdefault(function_selector(function), function)
            by_name.setdefault(function.name, []).append(function)
            by_signature.setdefault(
                canonical_function_signature(function), function
            )
        object.__setattr__(self, "_by_selector", by_selector)
        object.__setattr__(self, "_by_name", by_name)
        object.__setattr__(self, "_by_signature", by_signature)

    def __len__(self) -> int:
        return len(self.functions)


# ---- 不可变结果对象 --------------------------------------------------------


class EncodedContractCall(bytes):
    """合约调用编码结果；值本身即完整 calldata（``bytes`` 子类）。

    - ``kind``：``"function"`` / ``"receive"`` / ``"fallback"``；
    - ``function``：命中的 :class:`FunctionDefinition`，receive /
      fallback 为 ``None``；
    - ``args``：编码所用的纯值实参 tuple（receive / fallback 为空）；
    - ``data``：函数为参数区，fallback 为完整 calldata，receive 为
      ``b""``；
    - ``calldata``：完整 calldata，与结果值本身相等。
    """

    def __new__(cls, kind, function, args, data, calldata):
        obj = super().__new__(cls, calldata)
        obj._kind = kind
        obj._function = function
        obj._args = args
        obj._data = data
        return obj

    @property
    def kind(self) -> str:
        """调用种类：``"function"`` / ``"receive"`` / ``"fallback"``。"""
        return self._kind

    @property
    def function(self):
        """命中的函数定义；receive / fallback 为 ``None``。"""
        return self._function

    @property
    def args(self) -> tuple:
        """编码所用的纯值实参 tuple。"""
        return self._args

    @property
    def data(self) -> bytes:
        """函数为参数区，fallback 为完整 calldata，receive 为 ``b""``。"""
        return self._data

    @property
    def calldata(self) -> bytes:
        """完整 calldata（与结果值本身相等的 bytes）。"""
        return bytes(self)

    @property
    def calldata_hex(self) -> str:
        """``"0x"`` 前缀的完整 calldata 十六进制字符串。"""
        return "0x" + self.hex()

    @property
    def selector(self):
        """函数调用的四字节 selector；receive / fallback 为 ``None``。"""
        if self._kind == KIND_FUNCTION:
            return bytes(self[:_SELECTOR_LEN])
        return None


@dataclass(frozen=True)
class DecodedContractCall:
    """合约调用解码结果。

    - ``kind``：``"function"`` / ``"receive"`` / ``"fallback"``；
    - ``function``：selector 命中的 :class:`FunctionDefinition`，
      receive / fallback 为 ``None``；
    - ``args``：按声明顺序排列的 :class:`FunctionArgument`（receive /
      fallback 为空 tuple）；
    - ``data``：函数为参数区，fallback 为完整 calldata，receive 为
      ``b""``；
    - ``calldata``：完整 calldata。
    """

    kind: str
    function: FunctionDefinition | None = None
    args: tuple[FunctionArgument, ...] = field(default=())
    data: bytes = b""
    calldata: bytes = b""

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple。"""
        return tuple(arg.value for arg in self.args)

    @property
    def calldata_hex(self) -> str:
        """``"0x"`` 前缀的完整 calldata 十六进制字符串。"""
        return "0x" + self.calldata.hex()

    @property
    def selector(self):
        """函数调用的四字节 selector；receive / fallback 为 ``None``。"""
        if self.kind == KIND_FUNCTION:
            return self.calldata[:_SELECTOR_LEN]
        return None


# ---- 注册表解析 ------------------------------------------------------------


def _entry_invalid(message: str) -> AbiContractCallError:
    return AbiContractCallError("CALL_ENTRY_INVALID", message)


def _check_special_entry(entry: dict, entry_type: str) -> None:
    """receive / fallback 条目不接受 inputs（缺省或空数组视为无）。"""
    inputs = entry.get("inputs", ())
    if not isinstance(inputs, (list, tuple)) or inputs:
        raise _entry_invalid(f"{entry_type} 条目不接受 inputs，得到 {inputs!r}")


def parse_contract_call_registry(abi) -> ContractCallRegistry:
    """解析 ABI，构建不可变 :class:`ContractCallRegistry`。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只登记 ``type`` 为
    ``function`` / ``receive`` / ``fallback`` 的条目，其余条目（event /
    constructor / error、缺 type、未知类型或非对象条目）跳过不解析。
    function 条目的名称、inputs、outputs 与 selector 语义同
    :func:`abi_kit.parse_function_abi`，规范签名不得重复；receive 与
    fallback 至多各一个且不接受 inputs。

    ABI 根非法、条目非法或重复时抛
    :class:`abi_kit.AbiContractCallError`（code 为
    ``CALL_ENTRY_INVALID``）。
    """
    if isinstance(abi, str):
        try:
            root = json.loads(abi)
        except (json.JSONDecodeError, ValueError) as exc:
            raise _entry_invalid(f"ABI JSON 解析失败：{exc}") from None
    elif isinstance(abi, (list, tuple)):
        root = abi
    else:
        raise _entry_invalid(
            f"ABI 必须是 JSON 字符串或条目数组，得到 {type(abi).__name__}"
        )

    if not isinstance(root, (list, tuple)):
        raise _entry_invalid("ABI 必须是条目数组，JSON 根不能是对象或标量")

    functions: list[FunctionDefinition] = []
    signatures: set[str] = set()
    receive = False
    fallback = False
    for entry in root:
        if not isinstance(entry, dict):
            # 非对象条目跳过不解析。
            continue
        entry_type = entry.get("type")
        if entry_type == KIND_FUNCTION:
            try:
                function = _parse_function_entry(entry)
            except AbiMetadataError as exc:
                raise _entry_invalid(f"function 条目非法：{exc}") from None
            signature = canonical_function_signature(function)
            if signature in signatures:
                raise _entry_invalid(f"函数规范签名重复：{signature}")
            signatures.add(signature)
            functions.append(function)
        elif entry_type in (KIND_RECEIVE, KIND_FALLBACK):
            _check_special_entry(entry, entry_type)
            if entry_type == KIND_RECEIVE:
                if receive:
                    raise _entry_invalid("receive 条目重复，至多登记一个")
                receive = True
            else:
                if fallback:
                    raise _entry_invalid("fallback 条目重复，至多登记一个")
                fallback = True
        else:
            # event / constructor / error、缺 type 或未知类型条目跳过。
            continue
    return ContractCallRegistry(tuple(functions), receive, fallback)


# ---- 输入规范化 ------------------------------------------------------------


def _as_registry(abi) -> ContractCallRegistry:
    if isinstance(abi, ContractCallRegistry):
        return abi
    return parse_contract_call_registry(abi)


def _data_invalid(message: str) -> AbiContractCallError:
    return AbiContractCallError("CALL_DATA_INVALID", message)


def _coerce_hex_bytes(value, noun: str) -> bytes:
    """把 bytes 或可选 0x 前缀的偶数位十六进制字符串规范化为 bytes。"""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        text = value[2:] if value[:2] in ("0x", "0X") else value
        if len(text) % 2 or any(c not in _HEX_DIGITS for c in text):
            raise _data_invalid(
                f"{noun}必须是可选 0x 前缀的偶数位十六进制字符串"
            )
        return bytes.fromhex(text)
    raise _data_invalid(
        f"{noun}必须是 bytes 或十六进制 str，得到 {type(value).__name__}"
    )


def _coerce_data(data) -> bytes:
    """编码入口的 data 规范化；缺省（``None``）视为空。"""
    if data is None:
        return b""
    return _coerce_hex_bytes(data, "data ")


def _coerce_args(args) -> tuple:
    """实参列表规范化；类型非法属值层错误，抛 ABIValueError。"""
    if args is None:
        return ()
    if isinstance(args, (list, tuple)):
        return tuple(args)
    raise ABIValueError(
        f"实参列表必须是 list 或 tuple，得到 {type(args).__name__}"
    )


# ---- 目标解析 --------------------------------------------------------------


def _target_not_found(message: str) -> AbiContractCallError:
    return AbiContractCallError("CALL_TARGET_NOT_FOUND", message)


def _resolve_target(registry: ContractCallRegistry, target):
    """把 target 解析为 (kind, function|None)。"""
    if not isinstance(target, str) or not target:
        raise _target_not_found(
            f"调用目标必须是非空字符串（函数名、规范签名、receive 或 "
            f"fallback），得到 {target!r}"
        )

    if target == KIND_RECEIVE:
        if not registry.receive:
            raise _target_not_found("注册表中未登记 receive 条目")
        return KIND_RECEIVE, None
    if target == KIND_FALLBACK:
        if not registry.fallback:
            raise _target_not_found("注册表中未登记 fallback 条目")
        return KIND_FALLBACK, None

    if "(" in target or ")" in target:
        function = registry._by_signature.get(target)
        if function is None:
            raise _target_not_found(
                f"注册表中找不到规范签名为 {target!r} 的函数"
            )
        return KIND_FUNCTION, function

    candidates = registry._by_name.get(target, [])
    if not candidates:
        raise _target_not_found(f"注册表中找不到函数 {target!r}")
    if len(candidates) > 1:
        signatures = ", ".join(
            canonical_function_signature(function) for function in candidates
        )
        raise AbiContractCallError(
            "CALL_TARGET_AMBIGUOUS",
            f"函数 {target!r} 存在 {len(candidates)} 个重载，无法唯一选择，"
            f"请改用规范签名之一：{signatures}",
        )
    return KIND_FUNCTION, candidates[0]


# ---- 编码 ------------------------------------------------------------------


def encode_contract_call(abi, target, args=None, data=None) -> EncodedContractCall:
    """编码一次合约调用（function / receive / fallback）。

    ``abi`` 接受 ABI JSON 字符串、等价条目数组或已构建的
    :class:`ContractCallRegistry`；``target`` 接受无重载函数名、规范
    函数签名、``"receive"`` 或 ``"fallback"``；``args`` 为按 inputs
    声明顺序排列的实参列表（list/tuple），仅函数目标可用；``data``
    接受 bytes 或可选 ``0x`` 前缀的偶数位十六进制字符串，仅 fallback
    目标可用（原样放入 calldata）。

    返回 :class:`EncodedContractCall`，其值本身即完整 calldata：函数为
    ``selector + 参数区``，receive 为 ``b""``，fallback 为 data 原样。

    目标不存在、函数名歧义、calldata/data 非法、receive 非空、
    fallback 带实参抛 :class:`abi_kit.AbiContractCallError`；实参值与
    声明类型不匹配（含数量不符）抛 :class:`abi_kit.ABIValueError`。
    """
    registry = _as_registry(abi)
    kind, function = _resolve_target(registry, target)
    values = _coerce_args(args)
    payload = _coerce_data(data)

    if kind == KIND_RECEIVE:
        if values or payload:
            raise AbiContractCallError(
                "CALL_RECEIVE_NONEMPTY",
                "receive 只接受空实参与空 data",
            )
        return EncodedContractCall(kind, None, (), b"", b"")

    if kind == KIND_FALLBACK:
        if values:
            raise AbiContractCallError(
                "CALL_FALLBACK_ARGS",
                f"fallback 不接受实参，得到 {len(values)} 个",
            )
        return EncodedContractCall(kind, None, (), payload, payload)

    if payload:
        raise _data_invalid("function 调用不接受额外 data")
    parameter_types = TupleType(
        tuple(parameter.abi_type for parameter in function.inputs)
    )
    encoded = _encode(parameter_types, values)
    selector = function_selector(function)
    return EncodedContractCall(
        kind, function, values, encoded, selector + encoded
    )


# ---- 解码 ------------------------------------------------------------------


def decode_contract_call(abi, calldata) -> DecodedContractCall:
    """按完整 calldata 分派并严格解码一次合约调用。

    ``abi`` 口径同 :func:`encode_contract_call`；``calldata`` 接受
    bytes 或可选 ``0x`` 前缀的偶数位十六进制字符串。

    分派规则：空 calldata 优先选 receive，否则选 fallback；非空且不
    足四字节或 selector 未匹配任何函数时，有 fallback 归入 fallback，
    否则报告目标不存在。函数命中后按 inputs 组成的 tuple 严格解码参
    数区。

    返回 :class:`DecodedContractCall`（``kind`` / ``function`` /
    ``args`` / ``data`` / ``calldata``）：``function`` 为
    :class:`FunctionDefinition` 或 ``None``；``args`` 为
    :class:`FunctionArgument` tuple；函数 ``data`` 为参数区，
    fallback ``data`` 为完整 calldata，receive ``data`` 为 ``b""``。

    calldata 非法或无法分派抛
    :class:`abi_kit.AbiContractCallError`；参数区不能严格解码抛
    :class:`abi_kit.ABIValueError`；解码后仍有尾随字节抛
    :class:`abi_kit.AbiTrailingDataError`。
    """
    registry = _as_registry(abi)
    raw = _coerce_hex_bytes(calldata, "calldata ")

    if not raw:
        if registry.receive:
            return DecodedContractCall(KIND_RECEIVE, None, (), b"", b"")
        if registry.fallback:
            return DecodedContractCall(KIND_FALLBACK, None, (), b"", b"")
        raise _target_not_found(
            "空 calldata 需要 receive 或 fallback，注册表均未登记"
        )

    function = None
    if len(raw) >= _SELECTOR_LEN:
        function = registry._by_selector.get(raw[:_SELECTOR_LEN])
    if function is not None:
        payload = raw[_SELECTOR_LEN:]
        bound = len(payload)
        parameter_types = TupleType(
            tuple(parameter.abi_type for parameter in function.inputs)
        )
        values, end = _decode(parameter_types, payload, 0, bound)
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
        return DecodedContractCall(
            KIND_FUNCTION, function, arguments, payload, raw
        )

    if registry.fallback:
        return DecodedContractCall(KIND_FALLBACK, None, (), raw, raw)
    raise _target_not_found(
        f"calldata 未匹配任何函数 selector，且注册表未登记 fallback"
    )
