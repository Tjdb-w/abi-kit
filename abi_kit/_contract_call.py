"""合约调用分派：统一处理 function、receive 与 fallback。

- :func:`parse_contract_call_registry`：解析 ABI JSON 字符串或等价条目
  数组，只登记 ``type == "function" / "receive" / "fallback"`` 的条目，
  按 ABI 声明顺序构建不可变 :class:`ContractCallRegistry`；event /
  constructor / error 等其他条目跳过不解析。function 保留名称、inputs、
  outputs 与 selector 语义（与 :class:`abi_kit.FunctionDefinition` 完全
  一致）；receive / fallback 至多各登记一个且不接受 inputs；function 的
  规范签名不得重复。
- :func:`encode_contract_call`：``target`` 接受无同名重载的函数名、规范
  函数签名，或字面量 ``"receive"`` / ``"fallback"``。函数按 inputs 顺序
  编码实参，返回 ``selector + 参数区`` 的完整 calldata；receive 仅接受
  空实参与空 data，calldata 为 ``b""``；fallback 仅接受空实参，并把
  data 原样放入 calldata。
- :func:`decode_contract_call`：按完整 calldata 分派并严格解码。空
  calldata 优先分派 receive，无 receive 时分派 fallback；前四字节命中
  函数 selector 时按函数 inputs 严格解码；未知 selector（或短于四字节
  的非空 calldata）有 fallback 时分派 fallback，否则报告目标不存在。返回
  :class:`DecodedContractCall`（``kind`` / ``function`` / ``args`` /
  ``data`` / ``calldata``）：``function`` 为
  :class:`abi_kit.FunctionDefinition` 或 ``None``，``args`` 为
  :class:`abi_kit.FunctionArgument` tuple；函数的 ``data`` 为参数区，
  fallback 的 ``data`` 为完整 calldata，receive 的 ``data`` 为空字节。

错误约定：

- 注册表构建、目标选择与数据口径失败统一抛
  :class:`abi_kit.AbiContractCallError`，以 ``code`` 唯一区分
  （CONTRACT_CALL_ABI_INVALID / CONTRACT_CALL_TARGET_NOT_FOUND /
  CONTRACT_CALL_AMBIGUOUS / CONTRACT_CALL_DATA_INVALID /
  CONTRACT_CALL_RECEIVE_NONEMPTY / CONTRACT_CALL_FALLBACK_ARGS）；
- 函数实参不能按声明类型编码、或参数区不能按声明类型严格解码仍抛
  :class:`abi_kit.ABIValueError`；参数解码完成后仍有尾随字节继续抛
  :class:`abi_kit.AbiTrailingDataError`；
- 调用层任一步失败即整体抛出，不返回部分结果。
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

#: 分派结果的三类目标名称，同时作为 ABI 条目类型与 target 字面量。
KIND_FUNCTION = "function"
KIND_RECEIVE = "receive"
KIND_FALLBACK = "fallback"

_RECEIVE_TARGET = frozenset((KIND_RECEIVE,))
_FALLBACK_TARGET = frozenset((KIND_FALLBACK,))

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

#: 注册表登记的三类 ABI 条目。
_CALL_ENTRY_TYPES = frozenset(
    (KIND_FUNCTION, KIND_RECEIVE, KIND_FALLBACK)
)
#: 跳过的其余 ABI 条目类型；其合法性由各自入口负责。
_SKIPPED_ENTRY_TYPES = frozenset(("event", "constructor", "error"))


# ---- 不可变注册表与结果对象 -----------------------------------------------


@dataclass(frozen=True)
class ContractCallRegistry:
    """不可变合约调用注册表。

    - ``functions``：按 ABI 顺序保存的 :class:`FunctionDefinition` tuple；
    - ``receive``：是否登记了至多一个 receive 条目；
    - ``fallback``：是否登记了至多一个 fallback 条目。

    名称、规范签名与 selector 的查找索引在构造时预计算；条目合法性与
    唯一性约束由 :func:`parse_contract_call_registry` 校验。
    """

    functions: tuple[FunctionDefinition, ...] = field(default=())
    receive: bool = False
    fallback: bool = False
    _by_signature: dict = field(init=False, repr=False, compare=False)
    _by_name: dict = field(init=False, repr=False, compare=False)
    _by_selector: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        by_signature: dict = {}
        by_name: dict = {}
        by_selector: dict = {}
        for function in self.functions:
            by_signature[canonical_function_signature(function)] = function
            by_name.setdefault(function.name, []).append(function)
            by_selector.setdefault(function_selector(function), function)
        object.__setattr__(self, "_by_signature", by_signature)
        object.__setattr__(self, "_by_name", by_name)
        object.__setattr__(self, "_by_selector", by_selector)

    def __len__(self) -> int:
        return len(self.functions) + int(self.receive) + int(self.fallback)


@dataclass(frozen=True)
class DecodedContractCall:
    """合约调用解码结果。

    - ``kind``：``"function"`` / ``"receive"`` / ``"fallback"``；
    - ``function``：函数分派时为命中的 :class:`FunctionDefinition`，
      receive / fallback 分派时为 ``None``；
    - ``args``：按声明顺序排列的 :class:`FunctionArgument`，receive /
      fallback 为空 tuple；
    - ``data``：函数分派时为去掉 selector 的参数区，fallback 分派时为
      完整 calldata，receive 分派时为空字节；
    - ``calldata``：规范化后的完整 calldata bytes。
    """

    kind: str
    function: FunctionDefinition | None
    args: tuple[FunctionArgument, ...]
    data: bytes
    calldata: bytes

    @property
    def function_name(self) -> str | None:
        """命中函数的名称；receive / fallback 分派时为 ``None``。"""
        return None if self.function is None else self.function.name

    @property
    def signature(self) -> str | None:
        """命中函数的规范签名；receive / fallback 分派时为 ``None``。"""
        if self.function is None:
            return None
        return canonical_function_signature(self.function)

    @property
    def values(self) -> tuple:
        """按声明顺序去掉类型标注后的纯值 tuple。"""
        return tuple(arg.value for arg in self.args)

    @property
    def data_hex(self) -> str:
        """``"0x"`` 前缀的 data 小写十六进制字符串。"""
        return "0x" + self.data.hex()

    @property
    def calldata_hex(self) -> str:
        """``"0x"`` 前缀的完整 calldata 小写十六进制字符串。"""
        return "0x" + self.calldata.hex()


# ---- 异常构造与 ABI 规范化 ------------------------------------------------


def _error(code: str, message: str) -> AbiContractCallError:
    return AbiContractCallError(code, message)


def _load_abi_root(abi):
    """把 ABI JSON 字符串或等价条目数组规范化为根数组。"""
    if isinstance(abi, str):
        try:
            root = json.loads(abi)
        except (json.JSONDecodeError, ValueError) as exc:
            raise _error(
                "CONTRACT_CALL_ABI_INVALID", f"ABI JSON 解析失败：{exc}"
            ) from None
    elif isinstance(abi, (list, tuple)):
        root = abi
    else:
        raise _error(
            "CONTRACT_CALL_ABI_INVALID",
            f"ABI 必须是 JSON 字符串或条目数组，得到 {type(abi).__name__}",
        )
    if not isinstance(root, (list, tuple)):
        raise _error(
            "CONTRACT_CALL_ABI_INVALID",
            "ABI 必须是条目数组，JSON 根不能是对象或标量",
        )
    return root


def _validate_no_inputs(entry: dict, entry_type: str) -> None:
    """receive / fallback 条目不接受 inputs。"""
    if "inputs" not in entry:
        return
    inputs = entry["inputs"]
    if not isinstance(inputs, (list, tuple)) or inputs:
        raise _error(
            "CONTRACT_CALL_ABI_INVALID",
            f"{entry_type} 条目不接受 inputs，得到 {inputs!r}",
        )


def parse_contract_call_registry(abi) -> ContractCallRegistry:
    """解析 ABI，构建 function / receive / fallback 调用注册表。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只登记 ``type == "function"``、
    ``"receive"``、``"fallback"`` 的条目；event / constructor / error、
    缺 type、未知类型或非对象条目一律跳过不解析。function 条目语义同
    :func:`abi_kit.parse_function_abi`（name、inputs、outputs、selector），
    规范签名不得重复；receive / fallback 至多各登记一个且不接受 inputs。

    ABI 根非法、三类条目非法或重复登记时抛
    :class:`abi_kit.AbiContractCallError`（code 为
    ``CONTRACT_CALL_ABI_INVALID``）。
    """
    if isinstance(abi, ContractCallRegistry):
        return abi
    root = _load_abi_root(abi)

    functions: list[FunctionDefinition] = []
    signatures: set[str] = set()
    receive = False
    fallback = False
    for entry in root:
        if not isinstance(entry, dict):
            # 非对象条目不可能是三类调用条目，跳过不解析。
            continue
        entry_type = entry.get("type")
        if entry_type == KIND_FUNCTION:
            try:
                function = _parse_function_entry(entry)
            except AbiMetadataError as exc:
                # _parse_function_entry 的元数据失败统一转译为调用分派层
                # 的条目非法错误。
                raise _error(
                    "CONTRACT_CALL_ABI_INVALID",
                    f"function 条目非法：{exc}",
                ) from None
            signature = canonical_function_signature(function)
            if signature in signatures:
                raise _error(
                    "CONTRACT_CALL_ABI_INVALID",
                    f"函数规范签名重复：{signature}",
                )
            signatures.add(signature)
            functions.append(function)
        elif entry_type in _RECEIVE_TARGET:
            _validate_no_inputs(entry, KIND_RECEIVE)
            if receive:
                raise _error(
                    "CONTRACT_CALL_ABI_INVALID",
                    "ABI 中至多允许一个 receive 条目，检测到多个",
                )
            receive = True
        elif entry_type in _FALLBACK_TARGET:
            _validate_no_inputs(entry, KIND_FALLBACK)
            if fallback:
                raise _error(
                    "CONTRACT_CALL_ABI_INVALID",
                    "ABI 中至多允许一个 fallback 条目，检测到多个",
                )
            fallback = True
        elif entry_type in _SKIPPED_ENTRY_TYPES:
            continue
        else:
            # 缺 type 或未知类型条目跳过不登记。
            continue

    return ContractCallRegistry(tuple(functions), receive, fallback)


def _as_registry(abi) -> ContractCallRegistry:
    """接受注册表本身或原始 ABI，返回注册表。"""
    if isinstance(abi, ContractCallRegistry):
        return abi
    return parse_contract_call_registry(abi)


# ---- 实参与 data 规范化 ----------------------------------------------------


def _normalize_args(args) -> tuple:
    """把实参 list/tuple 规范化为 tuple；容器类型非法是值层错误。"""
    if args is None:
        return ()
    if isinstance(args, (list, tuple)):
        return tuple(args)
    raise ABIValueError(
        f"实参列表必须是 list 或 tuple，得到 {type(args).__name__}"
    )


def _coerce_call_data(data, noun: str) -> bytes:
    """把 bytes 或可选 0x 前缀的偶数位十六进制字符串规范化为 bytes。

    任何失败统一抛 ``CONTRACT_CALL_DATA_INVALID``。
    """
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        text = data[2:] if data[:2] in ("0x", "0X") else data
        if len(text) % 2 or any(c not in _HEX_DIGITS for c in text):
            raise _error(
                "CONTRACT_CALL_DATA_INVALID",
                f"{noun}必须是可选 0x 前缀的偶数位十六进制字符串",
            )
        return bytes.fromhex(text)
    raise _error(
        "CONTRACT_CALL_DATA_INVALID",
        f"{noun}必须是 bytes 或十六进制 str，得到 {type(data).__name__}",
    )


# ---- 目标选择 --------------------------------------------------------------


def _resolve_function(
    registry: ContractCallRegistry, key: str
) -> FunctionDefinition:
    """按函数名或规范签名在注册表中唯一定位函数。"""
    if "(" in key or ")" in key:
        function = registry._by_signature.get(key)
        if function is None:
            raise _error(
                "CONTRACT_CALL_TARGET_NOT_FOUND",
                f"注册表中找不到规范签名为 {key!r} 的函数",
            )
        return function

    candidates = registry._by_name.get(key, [])
    if not candidates:
        raise _error(
            "CONTRACT_CALL_TARGET_NOT_FOUND",
            f"注册表中找不到函数 {key!r}",
        )
    if len(candidates) > 1:
        signatures = ", ".join(
            canonical_function_signature(function) for function in candidates
        )
        raise _error(
            "CONTRACT_CALL_AMBIGUOUS",
            f"函数 {key!r} 存在 {len(candidates)} 个重载，无法唯一选择，"
            f"请改用规范签名之一：{signatures}",
        )
    return candidates[0]


def _resolve_target(registry: ContractCallRegistry, target) -> str:
    """解析 target 字面量并校验 receive/fallback 存在性。

    返回 ``"receive"`` / ``"fallback"``；函数目标由调用方继续解析，
    非特殊字面量时返回 ``"function"``。
    """
    if not isinstance(target, str) or not target:
        raise _error(
            "CONTRACT_CALL_TARGET_NOT_FOUND",
            f"target 必须是非空字符串，得到 {target!r}",
        )
    if target == KIND_RECEIVE:
        if not registry.receive:
            raise _error(
                "CONTRACT_CALL_TARGET_NOT_FOUND",
                "注册表中没有 receive 条目",
            )
        return KIND_RECEIVE
    if target == KIND_FALLBACK:
        if not registry.fallback:
            raise _error(
                "CONTRACT_CALL_TARGET_NOT_FOUND",
                "注册表中没有 fallback 条目",
            )
        return KIND_FALLBACK
    return KIND_FUNCTION


# ---- 编码 ------------------------------------------------------------------


def encode_contract_call(abi, target, args=None, data=None) -> bytes:
    """按 target 编码一次合约调用，返回完整 calldata bytes。

    ``abi`` 接受 :class:`ContractCallRegistry` 或 ABI JSON 字符串/等价
    条目数组（每次调用按同一套规则解析）；``target`` 接受无同名重载的
    函数名、规范函数签名（``transfer(address,uint256)``），或字面量
    ``"receive"`` / ``"fallback"``。

    - 函数：``args`` 为按 inputs 声明顺序排列的实参 list/tuple，返回
      ``selector + 参数 tuple 的 ABI 编码``；实参不能按声明类型编码
      （含数量不符）抛 :class:`abi_kit.ABIValueError`。
    - receive：仅接受空实参与空 data（``data`` 缺省即空），返回
      ``b""``；非空实参或非空 data 抛
      :class:`abi_kit.AbiContractCallError`
      （``CONTRACT_CALL_RECEIVE_NONEMPTY``）。
    - fallback：仅接受空实参，``data``（bytes 或可选 ``0x`` 前缀的偶数
      位十六进制字符串，缺省为空）原样作为 calldata 返回；给出非空实参
      抛 :class:`abi_kit.AbiContractCallError`
      （``CONTRACT_CALL_FALLBACK_ARGS``）。

    目标不存在、函数名歧义与 data 口径非法分别抛
    :class:`abi_kit.AbiContractCallError` 的对应唯一错误码。
    """
    registry = _as_registry(abi)
    kind = _resolve_target(registry, target)
    values = _normalize_args(args)

    if kind == KIND_RECEIVE:
        if values:
            raise _error(
                "CONTRACT_CALL_RECEIVE_NONEMPTY",
                "receive 不接受实参，编码 calldata 必须为空",
            )
        raw = _coerce_call_data(
            b"" if data is None else data, "receive data "
        )
        if raw:
            raise _error(
                "CONTRACT_CALL_RECEIVE_NONEMPTY",
                "receive 只接受空 data，calldata 必须为空字节",
            )
        return b""

    if kind == KIND_FALLBACK:
        if values:
            raise _error(
                "CONTRACT_CALL_FALLBACK_ARGS",
                "fallback 不接受实参，只接受原样 data",
            )
        return _coerce_call_data(
            b"" if data is None else data, "fallback data "
        )

    function = _resolve_function(registry, target)
    if data is not None:
        raise _error(
            "CONTRACT_CALL_DATA_INVALID",
            "function 目标不接受 data，实参必须通过 args 提供",
        )
    parameter_types = TupleType(
        tuple(parameter.abi_type for parameter in function.inputs)
    )
    payload = _encode(parameter_types, values)
    return function_selector(function) + payload


# ---- 解码与分派 ------------------------------------------------------------


def _decode_function_call(
    registry: ContractCallRegistry, raw: bytes
) -> DecodedContractCall:
    """按前四字节 selector 分派函数并严格解码参数区。"""
    selector = raw[:_SELECTOR_LEN]
    function = registry._by_selector.get(selector)
    if function is None:
        return None

    parameter_types = TupleType(
        tuple(parameter.abi_type for parameter in function.inputs)
    )
    payload = raw[_SELECTOR_LEN:]
    bound = len(payload)
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


def decode_contract_call(abi, calldata) -> DecodedContractCall:
    """按完整 calldata 分派并严格解码一次合约调用。

    ``abi`` 接受 :class:`ContractCallRegistry` 或 ABI JSON 字符串/等价
    条目数组；``calldata`` 接受 bytes 或可选 ``0x`` 前缀的偶数位十六进制
    字符串。分派顺序：

    1. 空 calldata 优先分派 receive；注册表无 receive 时分派 fallback；
       两者都没有时报告目标不存在。
    2. 非空 calldata 前四字节命中函数 selector 时，按该函数 inputs 组成
       的 tuple 严格解码参数区；参数区非法抛
       :class:`abi_kit.ABIValueError`，解码后仍有尾随字节抛
       :class:`abi_kit.AbiTrailingDataError`。
    3. 未知 selector（含短于四字节的非空 calldata）在注册表存在
       fallback 时分派 fallback（``data`` 即完整 calldata），否则报告
       目标不存在。

    返回 :class:`DecodedContractCall`，含 ``kind`` / ``function`` /
    ``args`` / ``data`` / ``calldata``；receive 的 ``data`` 为空字节，
    fallback 的 ``data`` 为完整 calldata。任一步失败即整体抛出，不返回
    部分结果。
    """
    registry = _as_registry(abi)
    raw = _coerce_call_data(calldata, "calldata ")

    if not raw:
        # 空 calldata 优先分派 receive，否则退回 fallback；两者都没有
        # 才报告目标不存在。
        if registry.receive:
            return DecodedContractCall(KIND_RECEIVE, None, (), b"", raw)
        if registry.fallback:
            return DecodedContractCall(KIND_FALLBACK, None, (), raw, raw)
        raise _error(
            "CONTRACT_CALL_TARGET_NOT_FOUND",
            "空 calldata 优先分派 receive、其次 fallback，"
            "但注册表中两者都不存在",
        )

    result = _decode_function_call(registry, raw)
    if result is not None:
        return result

    if registry.fallback:
        return DecodedContractCall(KIND_FALLBACK, None, (), raw, raw)
    selector_preview = raw[:_SELECTOR_LEN]
    raise _error(
        "CONTRACT_CALL_TARGET_NOT_FOUND",
        "calldata 的前四字节 "
        f"0x{selector_preview.hex()} 未匹配任何函数，且注册表中没有 "
        "fallback 条目可供分派",
    )
