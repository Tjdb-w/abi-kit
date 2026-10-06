"""只读合约 ABI 清单解析。

- :func:`parse_contract_abi`：一次解析整份 ABI（ABI JSON 字符串或等价
  list/tuple 条目数组），按声明顺序登记全部六类条目，返回不可变
  :class:`ContractAbiDefinition`：

  - ``functions`` / ``events`` / ``errors``：现有
    :class:`FunctionDefinition` / :class:`EventDefinition` /
    :class:`ErrorDefinition` 的有序 tuple；
  - ``constructor``：唯一的 :class:`ConstructorDefinition`，没有该条目时
    为 ``None``；
  - ``receive`` / ``fallback``：条目是否已登记的布尔值；
  - ``function_signatures`` / ``event_signatures`` /
    ``error_signatures``：各类条目的规范签名（按声明顺序）；
  - ``function_selectors`` / ``error_selectors``：小写 ``"0x"`` 十六进制
    四字节 selector（按声明顺序）；
  - ``event_topic0s``：非匿名事件的小写 ``"0x"`` topic0（按声明顺序，
    匿名事件不进入此序列）。

各类条目的字段、名称与类型语义完全沿用现有解析入口
（:func:`parse_function_abi` / :func:`parse_event_abi` /
:func:`parse_error_abi` / :func:`parse_constructor_abi` 的单条目口径），
本入口只做清单解析与跨条目校验，不要求各类条目齐全。

错误约定：任何失败统一抛 :class:`abi_kit.AbiContractAbiError`，以
``code`` 唯一区分：

- ``ABI_ENTRY_INVALID``：条目不是 JSON 对象、缺 ``type`` 或类型未知、
  缺必填字段、name 或 type 非法、参数无法形成 ABI 类型；
- ``ABI_ROOT_INVALID``：输入不是 JSON 字符串/list/tuple、JSON 解析失败，
  或 JSON 根不是数组；
- ``ABI_ENTRY_DUPLICATE``：constructor、receive、fallback 重复登记，或
  receive/fallback 带有非空 inputs；
- ``ABI_SIGNATURE_COLLISION``：同类 function/error/event 的规范签名重复，
  或同类 function、同类 error 的四字节 selector 相同；
- ``ABI_TOPIC0_COLLISION``：不同非匿名事件的签名 topic0 相同；匿名事件
  不进入 topic0 序列，不参与此项冲突。

function 与 error 的 selector 互不相干，相同不构成冲突。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._constructor import ConstructorDefinition, _parse_constructor_entry
from ._error import (
    ErrorDefinition,
    canonical_error_signature,
    error_selector,
    _parse_error_entry,
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
    ABITypeError,
)
from ._function import (
    FunctionDefinition,
    canonical_function_signature,
    function_selector,
    _parse_function_entry,
)

#: 合约 ABI 允许的全部条目类型。
_ENTRY_TYPES = frozenset(
    ("function", "event", "error", "constructor", "receive", "fallback")
)


# ---- 不可变合约 ABI 清单 ---------------------------------------------------


@dataclass(frozen=True)
class ContractAbiDefinition:
    """不可变合约 ABI 清单（严格按 ABI 声明顺序登记）。

    主字段：

    - ``functions`` / ``events`` / ``errors``：按声明顺序保存的现有不可变
      定义 tuple；
    - ``constructor``：唯一 :class:`ConstructorDefinition`，无该条目时为
      ``None``；
    - ``receive`` / ``fallback``：对应条目是否已登记。

    派生只读属性（同样按声明顺序，构造时一次性预计算）：

    - ``function_signatures`` / ``event_signatures`` /
      ``error_signatures``：规范签名 ``name(t1,t2,...)`` tuple；
    - ``function_selectors`` / ``error_selectors``：小写 ``"0x"`` + 8 位
      十六进制 selector tuple；
    - ``event_topic0s``：非匿名事件的小写 ``"0x"`` topic0 tuple，匿名事件
      跳过。
    """

    functions: tuple[FunctionDefinition, ...] = field(default=())
    events: tuple[EventDefinition, ...] = field(default=())
    errors: tuple[ErrorDefinition, ...] = field(default=())
    constructor: ConstructorDefinition | None = None
    receive: bool = False
    fallback: bool = False
    _derived: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        function_signatures = tuple(
            canonical_function_signature(function) for function in self.functions
        )
        function_selectors = tuple(
            "0x" + function_selector(function).hex() for function in self.functions
        )
        event_signatures = tuple(
            _event_signature(event) for event in self.events
        )
        event_topic0s = tuple(
            topic0
            for event in self.events
            if (topic0 := event_topic0(event)) is not None
        )
        error_signatures = tuple(
            canonical_error_signature(error) for error in self.errors
        )
        error_selectors = tuple(
            "0x" + error_selector(error).hex() for error in self.errors
        )
        object.__setattr__(
            self,
            "_derived",
            {
                "function_signatures": function_signatures,
                "function_selectors": function_selectors,
                "event_signatures": event_signatures,
                "event_topic0s": event_topic0s,
                "error_signatures": error_signatures,
                "error_selectors": error_selectors,
            },
        )

    @property
    def function_signatures(self) -> tuple[str, ...]:
        """全部 function 的规范签名，按声明顺序排列。"""
        return self._derived["function_signatures"]

    @property
    def function_selectors(self) -> tuple[str, ...]:
        """全部 function 的小写 ``0x`` selector，按声明顺序排列。"""
        return self._derived["function_selectors"]

    @property
    def event_signatures(self) -> tuple[str, ...]:
        """全部 event（含匿名事件）的规范签名，按声明顺序排列。"""
        return self._derived["event_signatures"]

    @property
    def event_topic0s(self) -> tuple[str, ...]:
        """全部非匿名 event 的小写 ``0x`` topic0，按声明顺序排列。"""
        return self._derived["event_topic0s"]

    @property
    def error_signatures(self) -> tuple[str, ...]:
        """全部 error 的规范签名，按声明顺序排列。"""
        return self._derived["error_signatures"]

    @property
    def error_selectors(self) -> tuple[str, ...]:
        """全部 error 的小写 ``0x`` selector，按声明顺序排列。"""
        return self._derived["error_selectors"]


# ---- 异常构造与根规范化 ----------------------------------------------------


def _error(code: str, message: str) -> AbiContractAbiError:
    return AbiContractAbiError(code, message)


def _load_abi_root(abi):
    """把 ABI JSON 字符串或等价条目数组规范化为根数组。

    输入类型错误、JSON 解析失败或 JSON 根不是数组统一抛
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


def _ensure_no_inputs(entry: dict, index: int, kind: str) -> None:
    """receive / fallback 条目不接受 inputs，违反时抛 ABI_ENTRY_DUPLICATE。"""
    if "inputs" not in entry:
        return
    inputs = entry["inputs"]
    if not isinstance(inputs, (list, tuple)) or inputs:
        raise _error(
            "ABI_ENTRY_DUPLICATE",
            f"第 {index} 个 {kind} 条目不接受 inputs，得到 {inputs!r}",
        )


# ---- 清单解析 --------------------------------------------------------------


def parse_contract_abi(abi) -> ContractAbiDefinition:
    """一次解析整份 ABI，返回不可变 :class:`ContractAbiDefinition`。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。六类条目（function、event、error、
    constructor、receive、fallback）按声明顺序全部登记，字段与类型语义
    沿用各自现有单条目解析器；本入口只做清单解析与跨条目校验，不要求
    各类条目齐全。

    - 条目未知（不是 JSON 对象、缺 ``type`` 或类型不在六类之内）、缺字段、
      名称或类型非法、参数无法形成 ABI 类型时抛
      :class:`abi_kit.AbiContractAbiError`（``ABI_ENTRY_INVALID``）；
    - ABI 输入类型错误或 JSON 根不是数组时抛
      :class:`abi_kit.AbiContractAbiError`（``ABI_ROOT_INVALID``）；
    - constructor、receive、fallback 重复，或 receive/fallback 带非空
      inputs 时抛 :class:`abi_kit.AbiContractAbiError`
      （``ABI_ENTRY_DUPLICATE``）；
    - function、error、event 同类规范签名重复，或同类 function、同类
      error 的 selector 相同时抛 :class:`abi_kit.AbiContractAbiError`
      （``ABI_SIGNATURE_COLLISION``）；
    - 不同非匿名事件的 topic0 相同时抛
      :class:`abi_kit.AbiContractAbiError`（``ABI_TOPIC0_COLLISION``）。

    空 ABI 得到各集合为空、``constructor`` 为 ``None``、receive/fallback
    为 ``False`` 的空清单；相同输入重复解析得到相等结果。function 与
    error 的 selector 互不相干，相同不构成冲突。
    """
    root = _load_abi_root(abi)

    functions: list[FunctionDefinition] = []
    events: list[EventDefinition] = []
    errors: list[ErrorDefinition] = []
    constructor: ConstructorDefinition | None = None
    receive = False
    fallback = False

    for index, entry in enumerate(root):
        if not isinstance(entry, dict):
            raise _error(
                "ABI_ENTRY_INVALID",
                f"第 {index} 个 ABI 条目必须是 JSON 对象，"
                f"得到 {type(entry).__name__}",
            )
        entry_type = entry.get("type")
        if entry_type not in _ENTRY_TYPES:
            raise _error(
                "ABI_ENTRY_INVALID",
                f"第 {index} 个 ABI 条目缺少 type 或带有未知类型 "
                f"{entry_type!r}，合法值为 "
                "function/event/error/constructor/receive/fallback",
            )

        if entry_type == "function":
            try:
                functions.append(_parse_function_entry(entry))
            except (AbiMetadataError, AbiEventError, ABITypeError) as exc:
                raise _error(
                    "ABI_ENTRY_INVALID",
                    f"第 {index} 个 function 条目非法：{exc}",
                ) from None
        elif entry_type == "error":
            try:
                errors.append(_parse_error_entry(entry))
            except (AbiMetadataError, AbiEventError, ABITypeError) as exc:
                raise _error(
                    "ABI_ENTRY_INVALID",
                    f"第 {index} 个 error 条目非法：{exc}",
                ) from None
        elif entry_type == "event":
            try:
                events.append(parse_event_abi(entry))
            except (AbiEventError, ABITypeError) as exc:
                raise _error(
                    "ABI_ENTRY_INVALID",
                    f"第 {index} 个 event 条目非法：{exc}",
                ) from None
        elif entry_type == "constructor":
            # 与 parse_constructor_abi 一致：先判重复再解析条目内容。
            if constructor is not None:
                raise _error(
                    "ABI_ENTRY_DUPLICATE",
                    f"第 {index} 个 constructor 条目重复："
                    "ABI 中至多允许一个 constructor 条目",
                )
            try:
                constructor = _parse_constructor_entry(entry)
            except (AbiMetadataError, AbiEventError, ABITypeError) as exc:
                raise _error(
                    "ABI_ENTRY_INVALID",
                    f"第 {index} 个 constructor 条目非法：{exc}",
                ) from None
        elif entry_type == "receive":
            _ensure_no_inputs(entry, index, "receive")
            if receive:
                raise _error(
                    "ABI_ENTRY_DUPLICATE",
                    f"第 {index} 个 receive 条目重复："
                    "ABI 中至多允许一个 receive 条目",
                )
            receive = True
        else:  # fallback
            _ensure_no_inputs(entry, index, "fallback")
            if fallback:
                raise _error(
                    "ABI_ENTRY_DUPLICATE",
                    f"第 {index} 个 fallback 条目重复："
                    "ABI 中至多允许一个 fallback 条目",
                )
            fallback = True

    _check_function_collisions(functions)
    _check_error_collisions(errors)
    _check_event_collisions(events)

    return ContractAbiDefinition(
        tuple(functions),
        tuple(events),
        tuple(errors),
        constructor,
        receive,
        fallback,
    )


# ---- 跨条目冲突校验 --------------------------------------------------------


def _check_function_collisions(functions: list[FunctionDefinition]) -> None:
    """同类 function 的规范签名与四字节 selector 均不得重复。"""
    signatures: set[str] = set()
    selectors: dict[bytes, str] = {}
    for function in functions:
        signature = canonical_function_signature(function)
        if signature in signatures:
            raise _error(
                "ABI_SIGNATURE_COLLISION",
                f"function 规范签名重复：{signature}",
            )
        signatures.add(signature)
        selector = function_selector(function)
        if selector in selectors:
            raise _error(
                "ABI_SIGNATURE_COLLISION",
                f"function selector 冲突：0x{selector.hex()} 同时对应 "
                f"{selectors[selector]!r} 与 {signature!r}",
            )
        selectors[selector] = signature


def _check_error_collisions(errors: list[ErrorDefinition]) -> None:
    """同类 error 的规范签名与四字节 selector 均不得重复。"""
    signatures: set[str] = set()
    selectors: dict[bytes, str] = {}
    for error in errors:
        signature = canonical_error_signature(error)
        if signature in signatures:
            raise _error(
                "ABI_SIGNATURE_COLLISION",
                f"error 规范签名重复：{signature}",
            )
        signatures.add(signature)
        selector = error_selector(error)
        if selector in selectors:
            raise _error(
                "ABI_SIGNATURE_COLLISION",
                f"error selector 冲突：0x{selector.hex()} 同时对应 "
                f"{selectors[selector]!r} 与 {signature!r}",
            )
        selectors[selector] = signature


def _check_event_collisions(events: list[EventDefinition]) -> None:
    """事件规范签名不得重复；不同非匿名事件的 topic0 不得相同。"""
    signatures: set[str] = set()
    topic0s: dict[str, str] = {}
    for event in events:
        signature = _event_signature(event)
        if signature in signatures:
            raise _error(
                "ABI_SIGNATURE_COLLISION",
                f"event 规范签名重复：{signature}",
            )
        signatures.add(signature)
        # 匿名事件不生成 topic0，也不参与 topic0 冲突校验。
        if event.anonymous:
            continue
        topic0 = event_topic0(event)
        if topic0 in topic0s:
            raise _error(
                "ABI_TOPIC0_COLLISION",
                f"非匿名事件 topic0 冲突：{topic0} 同时对应 "
                f"{topic0s[topic0]!r} 与 {signature!r}",
            )
        topic0s[topic0] = signature
