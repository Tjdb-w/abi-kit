"""整份合约 ABI 的只读清单解析与校验。

- :func:`parse_contract_abi`：解析 ABI JSON 字符串或等价条目数组，一次
  遍历沿用各现有入口的解析语义，返回不可变
  :class:`ContractAbiDefinition`。与各单项入口不同，本入口解析全部六类
  条目（function / event / error / constructor / receive / fallback），
  不跳过、不要求条目齐全，也不做任何编解码。

返回的 :class:`ContractAbiDefinition`：

- ``functions`` / ``events`` / ``errors``：现有
  :class:`FunctionDefinition` / :class:`EventDefinition` /
  :class:`ErrorDefinition` 的有序 tuple，按 ABI 声明顺序排列；
- ``constructor``：现有 :class:`ConstructorDefinition`，ABI 中无
  constructor 条目时为 ``None``；
- ``receive`` / ``fallback``：是否登记了对应条目的布尔值，各至多一个；
- ``function_signatures`` / ``event_signatures`` /
  ``error_signatures``：按声明顺序给出的规范签名 tuple
  （``name(type1,type2,...)``，参数名不参与）；
- ``function_selectors`` / ``error_selectors``：按声明顺序给出的小写
  ``"0x"`` + 8 位十六进制 selector tuple；
- ``event_topic0s``：按声明顺序给出的非匿名事件签名 topic0 tuple（小写
  ``"0x"`` + 64 位十六进制）；匿名事件没有签名 topic0，不进入此 tuple。

错误约定（本入口只抛 :class:`abi_kit.AbiContractAbiError`，以 ``code``
唯一区分）：

- ``ABI_ROOT_INVALID``：ABI 输入类型错误（既不是 JSON 字符串也不是
  list/tuple）、JSON 字符串无法解析，或 JSON 根不是数组；
- ``ABI_ENTRY_INVALID``：条目未知（非对象、缺 type、未知类型）、条目缺
  必填字段、名称或类型非法、参数不能形成 ABI 类型；
- ``ABI_ENTRY_DUPLICATE``：constructor / receive / fallback 重复登记，
  或 receive / fallback 带有非空 inputs；
- ``ABI_SIGNATURE_COLLISION``：同类 function / error / event 的规范签名
  重复，或同类 function / error 的四字节 selector 相同；
- ``ABI_TOPIC0_COLLISION``：两个不同的非匿名事件 topic0 相同
  （规范签名不同但 Keccak 截位冲突）。

function 与 error 之间 selector 相同不冲突；匿名事件同样参与规范签名
去重，但没有 topic0，不参与 topic0 去重。空 ABI 时各集合为空、
constructor 为 None、receive/fallback 为 False；相同输入重复解析稳定。
本入口只做清单解析与校验，不改变其他入口的输入输出和异常。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._constructor import ConstructorDefinition, _parse_constructor_entry
from ._error import (
    ErrorDefinition,
    _parse_error_entry,
    canonical_error_signature,
    error_selector,
)
from ._event import (
    EventDefinition,
    _event_signature,
    event_topic0,
    parse_event_abi,
)
from ._exceptions import (
    AbiContractAbiError,
    AbiEventError,
    AbiMetadataError,
)
from ._function import (
    FunctionDefinition,
    _parse_function_entry,
    canonical_function_signature,
    function_selector,
)

#: 六类合法 ABI 条目。
_KIND_FUNCTION = "function"
_KIND_EVENT = "event"
_KIND_ERROR = "error"
_KIND_CONSTRUCTOR = "constructor"
_KIND_RECEIVE = "receive"
_KIND_FALLBACK = "fallback"

_VALID_ENTRY_TYPES = frozenset(
    (
        _KIND_FUNCTION,
        _KIND_EVENT,
        _KIND_ERROR,
        _KIND_CONSTRUCTOR,
        _KIND_RECEIVE,
        _KIND_FALLBACK,
    )
)


# ---- 不可变合约 ABI 定义 ---------------------------------------------------


@dataclass(frozen=True)
class ContractAbiDefinition:
    """不可变的整份合约 ABI 只读清单。

    - ``functions`` / ``events`` / ``errors``：按 ABI 声明顺序保存的
      现有定义对象 tuple；
    - ``constructor``：:class:`ConstructorDefinition` 或 ``None``；
    - ``receive`` / ``fallback``：对应条目是否已登记。

    规范签名、selector 与 topic0 的派生序列在构造时按声明顺序预计算，
    通过同名只读属性访问。
    """

    functions: tuple[FunctionDefinition, ...] = field(default=())
    events: tuple[EventDefinition, ...] = field(default=())
    errors: tuple[ErrorDefinition, ...] = field(default=())
    constructor: ConstructorDefinition | None = None
    receive: bool = False
    fallback: bool = False

    _function_signatures: tuple[str, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _event_signatures: tuple[str, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _error_signatures: tuple[str, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _function_selectors: tuple[str, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _error_selectors: tuple[str, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _event_topic0s: tuple[str, ...] = field(
        init=False, repr=False, compare=False, default=()
    )

    def __post_init__(self):
        function_signatures = tuple(
            canonical_function_signature(function) for function in self.functions
        )
        event_signatures = tuple(
            _event_signature(event) for event in self.events
        )
        error_signatures = tuple(
            canonical_error_signature(error) for error in self.errors
        )
        function_selectors = tuple(
            "0x" + function_selector(function).hex() for function in self.functions
        )
        error_selectors = tuple(
            "0x" + error_selector(error).hex() for error in self.errors
        )
        # 匿名事件没有签名 topic0，不进入 topic0 序列。
        event_topic0s = tuple(
            topic0
            for event in self.events
            if (topic0 := event_topic0(event)) is not None
        )
        object.__setattr__(self, "_function_signatures", function_signatures)
        object.__setattr__(self, "_event_signatures", event_signatures)
        object.__setattr__(self, "_error_signatures", error_signatures)
        object.__setattr__(self, "_function_selectors", function_selectors)
        object.__setattr__(self, "_error_selectors", error_selectors)
        object.__setattr__(self, "_event_topic0s", event_topic0s)

    @property
    def function_signatures(self) -> tuple[str, ...]:
        """按声明顺序排列的规范函数签名 tuple。"""
        return self._function_signatures

    @property
    def event_signatures(self) -> tuple[str, ...]:
        """按声明顺序排列的规范事件签名 tuple（含匿名事件）。"""
        return self._event_signatures

    @property
    def error_signatures(self) -> tuple[str, ...]:
        """按声明顺序排列的规范 error 签名 tuple。"""
        return self._error_signatures

    @property
    def function_selectors(self) -> tuple[str, ...]:
        """按声明顺序排列的函数 selector（小写 ``"0x"`` 十六进制）tuple。"""
        return self._function_selectors

    @property
    def error_selectors(self) -> tuple[str, ...]:
        """按声明顺序排列的 error selector（小写 ``"0x"`` 十六进制）tuple。"""
        return self._error_selectors

    @property
    def event_topic0s(self) -> tuple[str, ...]:
        """按声明顺序排列的非匿名事件 topic0（小写 ``"0x"`` 十六进制）。"""
        return self._event_topic0s


# ---- 异常构造与 ABI 根规范化 ----------------------------------------------


def _error(code: str, message: str) -> AbiContractAbiError:
    return AbiContractAbiError(code, message)


def _load_abi_root(abi):
    """把 ABI JSON 字符串或等价条目数组规范化为根数组。

    输入类型错误、JSON 无法解析或 JSON 根不是数组统一抛
    ``ABI_ROOT_INVALID``。
    """
    if isinstance(abi, str):
        try:
            root = json.loads(abi)
        except (json.JSONDecodeError, ValueError) as exc:
            raise _error("ABI_ROOT_INVALID", f"ABI JSON 解析失败：{exc}") from None
    elif isinstance(abi, (list, tuple)):
        root = abi
    else:
        raise _error(
            "ABI_ROOT_INVALID",
            f"ABI 必须是 JSON 字符串或条目数组，得到 {type(abi).__name__}",
        )
    if not isinstance(root, (list, tuple)):
        raise _error(
            "ABI_ROOT_INVALID",
            "ABI 必须是条目数组，JSON 根不能是对象或标量",
        )
    return root


# ---- 条目解析 -------------------------------------------------------------


def _entry_invalid(message: str) -> AbiContractAbiError:
    return _error("ABI_ENTRY_INVALID", message)


def _validate_no_inputs(entry: dict, entry_type: str) -> None:
    """receive / fallback 条目不接受非空 inputs。"""
    if "inputs" not in entry:
        return
    inputs = entry["inputs"]
    if not isinstance(inputs, (list, tuple)):
        raise _entry_invalid(f"{entry_type} 条目的 inputs 必须是数组或缺省")
    if inputs:
        raise _error(
            "ABI_ENTRY_DUPLICATE",
            f"{entry_type} 条目不接受非空 inputs，得到 {len(inputs)} 个参数",
        )


# ---- 清单解析 -------------------------------------------------------------


def parse_contract_abi(abi) -> ContractAbiDefinition:
    """一次解析整份合约 ABI，返回不可变 :class:`ContractAbiDefinition`。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。六类条目全部按现有语义解析：
    function / event / error 收集为按声明顺序排列的定义 tuple；
    constructor 至多一个，缺省为 ``None``；receive / fallback 各至多一个，
    以布尔登记，且不接受非空 inputs。

    除清单解析与签名/selector/topic0 唯一性校验外不做其他检查，不要求
    各类条目齐全。空 ABI（``"[]"`` 或空数组）得到全空集合、
    ``constructor is None``、``receive is fallback is False``。相同输入
    重复解析得到相等的结果。

    任何失败只抛 :class:`abi_kit.AbiContractAbiError`，``code`` 取
    ``ABI_ROOT_INVALID`` / ``ABI_ENTRY_INVALID`` /
    ``ABI_ENTRY_DUPLICATE`` / ``ABI_SIGNATURE_COLLISION`` /
    ``ABI_TOPIC0_COLLISION`` 之一；其他入口的异常类型不会泄漏。
    """
    root = _load_abi_root(abi)

    functions: list[FunctionDefinition] = []
    events: list[EventDefinition] = []
    errors: list[ErrorDefinition] = []
    constructor: ConstructorDefinition | None = None
    receive = False
    fallback = False

    function_signatures: set[str] = set()
    event_signatures: set[str] = set()
    error_signatures: set[str] = set()
    function_selector_bytes: set[bytes] = set()
    error_selector_bytes: set[bytes] = set()
    topic0_bytes: set[bytes] = set()

    for index, entry in enumerate(root):
        if not isinstance(entry, dict):
            raise _entry_invalid(
                f"第 {index} 个 ABI 条目必须是 JSON 对象，"
                f"得到 {type(entry).__name__}"
            )
        entry_type = entry.get("type")
        if entry_type not in _VALID_ENTRY_TYPES:
            raise _entry_invalid(
                f"第 {index} 个 ABI 条目缺少 type 或带有未知类型 "
                f"{entry_type!r}，合法值为 "
                "function/event/error/constructor/receive/fallback"
            )

        if entry_type == _KIND_FUNCTION:
            try:
                function = _parse_function_entry(entry)
            except AbiMetadataError as exc:
                # 现有函数解析语义的元数据失败统一转译为条目非法。
                raise _entry_invalid(f"function 条目非法：{exc}") from None
            signature = canonical_function_signature(function)
            if signature in function_signatures:
                raise _error(
                    "ABI_SIGNATURE_COLLISION",
                    f"函数规范签名重复：{signature}",
                )
            selector = function_selector(function)
            if selector in function_selector_bytes:
                raise _error(
                    "ABI_SIGNATURE_COLLISION",
                    f"函数 selector 0x{selector.hex()} 重复"
                    f"（规范签名 {signature}）",
                )
            function_signatures.add(signature)
            function_selector_bytes.add(selector)
            functions.append(function)

        elif entry_type == _KIND_ERROR:
            try:
                error = _parse_error_entry(entry)
            except AbiMetadataError as exc:
                raise _entry_invalid(f"error 条目非法：{exc}") from None
            signature = canonical_error_signature(error)
            if signature in error_signatures:
                raise _error(
                    "ABI_SIGNATURE_COLLISION",
                    f"error 规范签名重复：{signature}",
                )
            selector = error_selector(error)
            if selector in error_selector_bytes:
                raise _error(
                    "ABI_SIGNATURE_COLLISION",
                    f"error selector 0x{selector.hex()} 重复"
                    f"（规范签名 {signature}）",
                )
            error_signatures.add(signature)
            error_selector_bytes.add(selector)
            errors.append(error)

        elif entry_type == _KIND_EVENT:
            try:
                event = parse_event_abi(entry)
            except AbiEventError as exc:
                # 现有事件解析语义的失败（EVENT_ABI_INVALID）统一转译为
                # 条目非法。
                raise _entry_invalid(f"event 条目非法：{exc}") from None
            signature = _event_signature(event)
            if signature in event_signatures:
                raise _error(
                    "ABI_SIGNATURE_COLLISION",
                    f"事件规范签名重复：{signature}",
                )
            event_signatures.add(signature)
            if not event.anonymous:
                topic0_hex = event_topic0(event)
                topic0 = bytes.fromhex(topic0_hex[2:])
                if topic0 in topic0_bytes:
                    # 规范签名已确认不同：走到这里只可能是 Keccak 截位
                    # 冲突，与签名重复区分开。
                    raise _error(
                        "ABI_TOPIC0_COLLISION",
                        f"不同非匿名事件的 topic0 相同：{topic0_hex}"
                        f"（规范签名 {signature}）",
                    )
                topic0_bytes.add(topic0)
            events.append(event)

        elif entry_type == _KIND_CONSTRUCTOR:
            if constructor is not None:
                raise _error(
                    "ABI_ENTRY_DUPLICATE",
                    "ABI 中至多允许一个 constructor 条目，检测到多个",
                )
            try:
                constructor = _parse_constructor_entry(entry)
            except AbiMetadataError as exc:
                raise _entry_invalid(
                    f"constructor 条目非法：{exc}"
                ) from None

        elif entry_type == _KIND_RECEIVE:
            _validate_no_inputs(entry, _KIND_RECEIVE)
            if receive:
                raise _error(
                    "ABI_ENTRY_DUPLICATE",
                    "ABI 中至多允许一个 receive 条目，检测到多个",
                )
            receive = True

        else:  # _KIND_FALLBACK
            _validate_no_inputs(entry, _KIND_FALLBACK)
            if fallback:
                raise _error(
                    "ABI_ENTRY_DUPLICATE",
                    "ABI 中至多允许一个 fallback 条目，检测到多个",
                )
            fallback = True

    return ContractAbiDefinition(
        tuple(functions),
        tuple(events),
        tuple(errors),
        constructor,
        receive,
        fallback,
    )
