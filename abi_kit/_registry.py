"""合约级事件注册表与日志批量分派还原。

- :func:`parse_event_registry`：接收 ABI JSON 字符串或等价条目数组，只
  保留 ``type == "event"`` 的条目（其余条目原样跳过、不校验），按 ABI
  顺序构造不可变 :class:`EventRegistry`；事件名、规范类型、indexed、
  anonymous 与 tuple components 语义与 :func:`parse_event_abi` 一致。
- :func:`decode_contract_event_log`：按注册表为单条日志分派事件并还原。
- :func:`decode_contract_event_logs`：对日志序列逐条分派，按输入顺序
  返回结果元组；任一条失败整体失败，不返回部分结果。

日志是映射，``topics`` 与 ``data`` 必填，可选 ``event`` 指定规范签名
（``Transfer(address,uint256)``）或唯一事件名；``topics`` / ``data``
沿用单事件入口的 bytes 或可选 ``0x`` 前缀十六进制字符串口径。

分派规则：

- 未给 ``event`` 时，非匿名事件用 ``topics[0]`` 匹配签名 topic0；匿名
  事件没有 topic0，必须显式选择；
- 显式给出的非匿名事件，其 topic0 必须与 ``topics[0]`` 一致；
- 只给事件名但存在多个同名事件时报歧义，应改用规范签名。

成功返回不可变 :class:`DecodedContractEventLog`：携带命中的
:class:`EventDefinition`、规范化后的 topics bytes 元组、data bytes 与按
声明顺序排列的 values tuple。values 口径与
:func:`decode_event_log` 完全一致：非 indexed 参数严格 ABI 解码；
indexed 的 string、动态 bytes、数组与 tuple 不可逆，原样得到 32 字节
topic bytes。

所有失败统一抛出 :class:`abi_kit.AbiLogDispatchError`，以 ``code`` 区分。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ._event import (
    EventDefinition,
    _data_bytes,
    _event_signature,
    _is_identifier,
    _topic_bytes,
    decode_event_log,
    event_topic0,
    parse_event_abi,
)
from ._exceptions import AbiEventError, AbiLogDispatchError


# ---- 不可变注册表 ---------------------------------------------------------


@dataclass(frozen=True)
class EventRegistry:
    """不可变合约事件注册表。

    - ``events``：按 ABI 顺序保存的 :class:`EventDefinition` tuple；
    - 内部按规范签名、topic0（仅非匿名事件）与事件名建立分派索引，
      同名事件保留 ABI 顺序。
    """

    events: tuple[EventDefinition, ...] = field(default=())

    def __post_init__(self) -> None:
        by_signature: dict[str, EventDefinition] = {}
        by_topic0: dict[bytes, EventDefinition] = {}
        by_name: dict[str, list[EventDefinition]] = {}
        has_anonymous = False
        for event in self.events:
            signature = _event_signature(event)
            # 规范签名（事件名 + 参数规范类型）在注册表内唯一；匿名事件
            # 的签名同样参与去重——签名本身不含 anonymous 标志。
            if signature in by_signature:
                raise _abi_invalid(f"规范事件签名重复：{signature!r}")
            by_signature[signature] = event
            by_name.setdefault(event.name, []).append(event)
            if event.anonymous:
                has_anonymous = True
            else:
                by_topic0[_topic0_bytes(event)] = event
        object.__setattr__(
            self, "_by_signature", MappingProxyType(by_signature)
        )
        object.__setattr__(self, "_by_topic0", MappingProxyType(by_topic0))
        object.__setattr__(
            self,
            "_by_name",
            MappingProxyType(
                {name: tuple(group) for name, group in by_name.items()}
            ),
        )
        object.__setattr__(self, "_has_anonymous", has_anonymous)


def _topic0_bytes(event: EventDefinition) -> bytes:
    """非匿名事件签名 topic0 的 32 字节形式。"""
    return bytes.fromhex(event_topic0(event)[2:])


# ---- 错误构造 -------------------------------------------------------------


def _abi_invalid(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_ABI_INVALID", message)


def _entry_invalid(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_ENTRY_INVALID", message)


def _not_found(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_EVENT_NOT_FOUND", message)


def _ambiguous(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_EVENT_AMBIGUOUS", message)


def _required(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_EVENT_REQUIRED", message)


def _mismatch(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_TOPIC_MISMATCH", message)


# ---- ABI 解析 -------------------------------------------------------------


def parse_event_registry(abi) -> EventRegistry:
    """从 ABI 构造不可变合约事件注册表。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只消费 ``type == "event"`` 的条目，
    按 ABI 顺序解析为 :class:`EventDefinition`；function、constructor、
    error、receive、fallback、缺 type 或未知类型的非 event 条目一律跳过，
    不影响注册表。

    ABI 根不是数组、event 条目非法（沿用 :func:`parse_event_abi` 的全部
    校验）或两个 event 条目的规范签名重复时，抛带 ``LOG_ABI_INVALID`` 的
    :class:`abi_kit.AbiLogDispatchError`。
    """
    if isinstance(abi, str):
        try:
            root = json.loads(abi)
        except (json.JSONDecodeError, ValueError) as exc:
            raise _abi_invalid(f"ABI JSON 解析失败：{exc}") from None
    elif isinstance(abi, (list, tuple)):
        root = abi
    else:
        raise _abi_invalid(
            f"ABI 必须是 JSON 字符串或条目数组，得到 {type(abi).__name__}"
        )

    if not isinstance(root, (list, tuple)):
        raise _abi_invalid("ABI 必须是条目数组，JSON 根不能是对象或标量")

    events: list[EventDefinition] = []
    for entry in root:
        if not isinstance(entry, dict):
            raise _abi_invalid(
                f"ABI 条目必须是 JSON 对象，得到 {type(entry).__name__}"
            )
        if entry.get("type") == "event":
            try:
                events.append(parse_event_abi(entry))
            except AbiEventError as exc:
                raise _abi_invalid(f"event 条目非法：{exc}") from None
        # 其余条目（function/constructor/error/receive/fallback 以及缺
        # type 或未知类型）全部跳过：注册表只负责事件分派。
    return EventRegistry(tuple(events))


def _as_registry(registry) -> EventRegistry:
    """把注册表参数规范化为 EventRegistry，原始 ABI 即时解析。"""
    if isinstance(registry, EventRegistry):
        return registry
    if isinstance(registry, (str, list, tuple)):
        return parse_event_registry(registry)
    raise _abi_invalid(
        "需要 EventRegistry 或 ABI（JSON 字符串/条目数组），"
        f"得到 {type(registry).__name__}"
    )


# ---- 日志分派 -------------------------------------------------------------


@dataclass(frozen=True)
class DecodedContractEventLog:
    """单条合约日志的分派还原结果。

    - ``event``：命中的 :class:`EventDefinition`；
    - ``topics``：规范化后的 32 字节 topics bytes tuple；
    - ``data``：规范化后的 data bytes；
    - ``values``：按参数声明顺序排列的还原值 tuple，口径同
      :func:`decode_event_log`。
    """

    event: EventDefinition
    topics: tuple[bytes, ...]
    data: bytes
    values: tuple = field(default=())

    @property
    def event_name(self) -> str:
        """命中事件的名称。"""
        return self.event.name

    @property
    def signature(self) -> str:
        """命中事件的规范签名，如 ``Transfer(address,uint256)``。"""
        return _event_signature(self.event)

    @property
    def topics_hex(self) -> tuple[str, ...]:
        """各项 topics 的 ``"0x"`` + 64 位小写十六进制 tuple。"""
        return tuple("0x" + topic.hex() for topic in self.topics)

    @property
    def data_hex(self) -> str:
        """``data`` 的 ``"0x"`` 前缀小写十六进制字符串。"""
        return "0x" + self.data.hex()


def _normalize_topics(topics) -> list[bytes]:
    """把日志 topics 字段规范化为 32 字节列表，失败抛 LOG_ENTRY_INVALID。"""
    if isinstance(topics, (str, bytes)) or not isinstance(topics, (list, tuple)):
        raise _entry_invalid(
            f"topics 必须是列表或元组，得到 {type(topics).__name__}"
        )
    try:
        return [_topic_bytes(item) for item in topics]
    except AbiEventError as exc:
        raise _entry_invalid(f"topics 非法：{exc}") from None


def _normalize_data(data) -> bytes:
    """把日志 data 字段规范化为字节串，失败抛 LOG_ENTRY_INVALID。"""
    try:
        return _data_bytes(data)
    except AbiEventError as exc:
        raise _entry_invalid(f"data 非法：{exc}") from None


def _resolve_named(registry: EventRegistry, key: str) -> EventDefinition:
    """按规范签名或唯一事件名解析显式给出的事件。"""
    if "(" in key or ")" in key:
        event = registry._by_signature.get(key)
        if event is None:
            raise _not_found(f"注册表中找不到规范签名为 {key!r} 的事件")
        return event

    if not _is_identifier(key):
        raise _entry_invalid(
            "event 必须是规范事件签名或非空 ASCII 事件名，"
            f"得到 {key!r}"
        )

    group = registry._by_name.get(key)
    if not group:
        raise _not_found(f"注册表中找不到事件 {key!r}")
    if len(group) > 1:
        signatures = ", ".join(_event_signature(event) for event in group)
        raise _ambiguous(
            f"事件 {key!r} 存在 {len(group)} 个同名定义，无法唯一选择，"
            f"请改用规范签名之一：{signatures}"
        )
    return group[0]


def _dispatch(
    registry: EventRegistry, log
) -> tuple[EventDefinition, list[bytes], bytes]:
    """校验日志映射并分派，返回 (事件, 规范化 topics, 规范化 data)。

    校验顺序对齐单事件入口：日志形状 → topics 规范化 → 事件解析与
    topic0 一致性 → data 规范化，使 topic0 不匹配优先于 data 非法。
    """
    if not isinstance(log, Mapping):
        raise _entry_invalid(f"日志必须是映射，得到 {type(log).__name__}")
    if "topics" not in log:
        raise _entry_invalid("日志缺少必填字段 topics")
    if "data" not in log:
        raise _entry_invalid("日志缺少必填字段 data")

    normalized_topics = _normalize_topics(log["topics"])

    explicit = log.get("event")
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit:
            raise _entry_invalid(
                f"event 必须是非空字符串，得到 {explicit!r}"
            )
        event = _resolve_named(registry, explicit)
        if not event.anonymous:
            if not normalized_topics:
                raise _entry_invalid(
                    "非匿名事件需要 topics[0] 携带签名 topic0，但 topics 为空"
                )
            if normalized_topics[0] != _topic0_bytes(event):
                raise _mismatch(
                    f"显式事件 {_event_signature(event)!r} 的 topic0 与 "
                    "topics[0] 不一致"
                )
    else:
        # 未显式选择：非匿名事件靠 topics[0] 匹配 topic0；匿名事件没有
        # topic0，必须显式选择。注册表里存在匿名事件而 topic0 又匹配不
        # 上时，日志可能正是某匿名事件（其 topics[0] 只是 indexed 值），
        # 按"匿名事件未显式选择"处理。
        if not normalized_topics:
            if registry._has_anonymous:
                raise _required(
                    "topics 为空且未显式给出 event；匿名事件必须用 event "
                    "显式选择"
                )
            raise _not_found(
                "topics 为空且未显式给出 event，没有可用于匹配的 topic0"
            )
        event = registry._by_topic0.get(normalized_topics[0])
        if event is None:
            if registry._has_anonymous:
                raise _required(
                    "topics[0] 未匹配到任何非匿名事件，且注册表中存在匿名"
                    "事件；匿名事件没有 topic0，必须用 event 显式选择"
                )
            raise _not_found(
                "topics[0] 匹配不到注册表中的任何事件"
                f"（topic0=0x{normalized_topics[0].hex()}）"
            )

    data = _normalize_data(log["data"])
    return event, normalized_topics, data


def decode_contract_event_log(registry, log) -> DecodedContractEventLog:
    """按注册表分派并还原单条合约日志。

    ``registry`` 接受 :class:`EventRegistry`，也可直接给 ABI（JSON 字符串
    或条目数组）即时解析。``log`` 必须是映射，``topics`` 与 ``data``
    必填，可选 ``event`` 为规范签名或唯一事件名。返回
    :class:`DecodedContractEventLog`，携带命中事件、规范化 topics bytes
    元组、data bytes 与声明顺序的 values tuple。

    ABI/注册表非法抛 ``LOG_ABI_INVALID``；日志形状或 topics/data/event
    字段非法、或命中事件后 topics 数量与 data 不能按声明严格还原抛
    ``LOG_ENTRY_INVALID``；事件或 topic0 未知抛 ``LOG_EVENT_NOT_FOUND``；
    同名事件不唯一抛 ``LOG_EVENT_AMBIGUOUS``；匿名事件未显式选择抛
    ``LOG_EVENT_REQUIRED``；显式事件与 topics[0] 不一致抛
    ``LOG_TOPIC_MISMATCH``。
    """
    reg = _as_registry(registry)
    event, normalized_topics, data = _dispatch(reg, log)
    try:
        values = decode_event_log(event, normalized_topics, data)
    except AbiEventError as exc:
        # 命中事件后的 topics 数量、indexed 严格值与 data tuple 解码失败，
        # 在分派层统一归为日志条目非法（不泄漏既有事件层错误码）。
        raise _entry_invalid(
            f"日志与事件 {_event_signature(event)!r} 不符：{exc}"
        ) from None
    return DecodedContractEventLog(event, tuple(normalized_topics), data, values)


def decode_contract_event_logs(
    registry, logs
) -> tuple[DecodedContractEventLog, ...]:
    """按输入顺序分派并还原日志序列。

    ``logs`` 必须是 list 或 tuple；逐条按
    :func:`decode_contract_event_log` 的口径分派与严格还原，返回等长的
    :class:`DecodedContractEventLog` tuple。任一条目失败立即抛出对应
    :class:`abi_kit.AbiLogDispatchError`（消息带序号），不返回任何部分
    结果。
    """
    if isinstance(logs, (str, bytes)) or not isinstance(logs, (list, tuple)):
        raise _entry_invalid(
            f"日志序列必须是列表或元组，得到 {type(logs).__name__}"
        )
    reg = _as_registry(registry)
    results: list[DecodedContractEventLog] = []
    for index, log in enumerate(logs):
        try:
            results.append(decode_contract_event_log(reg, log))
        except AbiLogDispatchError as exc:
            raise AbiLogDispatchError(
                exc.code, f"第 {index} 条日志分派失败：{exc.message}"
            ) from None
    return tuple(results)
