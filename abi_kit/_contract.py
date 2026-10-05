"""合约级事件注册表与日志分派还原。

- :func:`parse_event_registry`：解析 ABI JSON 字符串或等价条目数组，只
  保留 ``type == "event"`` 的条目，按 ABI 声明顺序构建不可变
  :class:`EventRegistry`；其余条目跳过不解析。事件名、规范类型、
  indexed、anonymous 与 tuple components 语义与
  :func:`abi_kit.parse_event_abi` 完全一致。
- :func:`decode_contract_event_log`：分派并还原单条日志，返回
  ``(EventDefinition, topics, data, values)`` 四元组——命中的事件定义、
  规范化的 32 字节 topics tuple、data bytes 与按声明顺序排列的 values
  tuple。
- :func:`decode_contract_event_logs`：按输入顺序批量还原日志序列，返回
  等长 tuple；任一日志失败即整体抛出，不返回部分结果。

日志口径：

- 日志是映射（Mapping），``topics`` 与 ``data`` 必填，``event`` 可选；
  其余键忽略。
- ``topics`` 每项接受 32 字节 ``bytes`` 或可选 ``0x`` 前缀的 64 位
  十六进制字符串；``data`` 接受 ``bytes`` 或可选 ``0x`` 前缀的偶数位
  十六进制字符串——与 :func:`abi_kit.decode_event_log` 口径一致。
- 可选 ``event`` 为规范签名（``Transfer(address,address,uint256)``）或
  唯一事件名；缺省时非匿名事件按 topics 首项匹配注册表 topic0，匿名
  事件必须显式选择。显式事件与 topics[0] 的 topic0 不一致时不得解码。
- values 语义同 :func:`abi_kit.decode_event_log`：非 indexed 参数由
  data 严格 ABI 解码；indexed 的 string、动态 bytes、数组与 tuple 不可
  逆，values 中对应位置为 32 字节 topic bytes。

错误约定：

- 注册表构建与分派失败统一抛 :class:`abi_kit.AbiLogDispatchError`，以
  ``code`` 区分（LOG_ABI_INVALID / LOG_ENTRY_INVALID /
  LOG_EVENT_NOT_FOUND / LOG_EVENT_AMBIGUOUS / LOG_EVENT_REQUIRED /
  LOG_TOPIC_MISMATCH）。
- 分派成功后的单条日志还原委托 :func:`abi_kit.decode_event_log`，其
  :class:`abi_kit.AbiEventError` 与错误码原样传播，保持不变。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from ._event import (
    EventDefinition,
    _data_bytes,
    _event_signature,
    _topic_bytes,
    decode_event_log,
    event_topic0,
    parse_event_abi,
)
from ._exceptions import AbiEventError, AbiLogDispatchError


# ---- 不可变注册表 ----------------------------------------------------------


@dataclass(frozen=True)
class EventRegistry:
    """不可变合约事件注册表（按 ABI 声明顺序保存事件）。

    - ``events``：按 ABI 顺序保存的 :class:`EventDefinition` tuple。

    规范签名唯一的约束由 :func:`parse_event_registry` 校验；直接构造不
    做该项检查。名称、规范签名与 topic0 的查找索引在构造时预计算。
    """

    events: tuple[EventDefinition, ...] = field(default=())
    _by_signature: dict = field(init=False, repr=False, compare=False)
    _by_name: dict = field(init=False, repr=False, compare=False)
    _by_topic0: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        by_signature: dict = {}
        by_name: dict = {}
        by_topic0: dict = {}
        for event in self.events:
            by_signature.setdefault(_event_signature(event), event)
            by_name.setdefault(event.name, []).append(event)
            if not event.anonymous:
                topic0 = event_topic0(event)
                by_topic0.setdefault(bytes.fromhex(topic0[2:]), event)
        object.__setattr__(self, "_by_signature", by_signature)
        object.__setattr__(self, "_by_name", by_name)
        object.__setattr__(self, "_by_topic0", by_topic0)

    def __len__(self) -> int:
        return len(self.events)


# ---- 注册表解析 ------------------------------------------------------------


def _abi_invalid(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_ABI_INVALID", message)


def parse_event_registry(abi) -> EventRegistry:
    """解析 ABI，按声明顺序构建不可变 :class:`EventRegistry`。

    ``abi`` 接受 ABI JSON 字符串（:func:`json.loads` 口径）或等价的
    Python 条目数组（list/tuple）。只保留 ``type == "event"`` 的条目，
    其余条目（function/constructor/error/receive/fallback、缺 type、
    未知类型或非对象条目）跳过不解析。事件条目的 name、inputs、
    indexed、anonymous 与 tuple components 语义同
    :func:`abi_kit.parse_event_abi`；事件的规范签名
    （``Name(type1,type2,...)``，anonymous 不参与）不得重复。

    ABI 根非法、event 条目非法或规范签名重复时抛
    :class:`abi_kit.AbiLogDispatchError`（code 为 ``LOG_ABI_INVALID``）。
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
    signatures: set[str] = set()
    for entry in root:
        if not isinstance(entry, dict) or entry.get("type") != "event":
            # 非 event 条目（含缺 type、未知类型、非对象）跳过不解析。
            continue
        try:
            event = parse_event_abi(entry)
        except AbiEventError as exc:
            raise _abi_invalid(f"event 条目非法：{exc}") from None
        signature = _event_signature(event)
        if signature in signatures:
            raise _abi_invalid(f"事件规范签名重复：{signature}")
        signatures.add(signature)
        events.append(event)
    return EventRegistry(tuple(events))


# ---- 日志字段规范化 --------------------------------------------------------


def _entry_invalid(message: str) -> AbiLogDispatchError:
    return AbiLogDispatchError("LOG_ENTRY_INVALID", message)


def _normalize_topics(topics) -> tuple[bytes, ...]:
    """把日志 topics 规范化为 32 字节 bytes tuple，失败抛 LOG_ENTRY_INVALID。"""
    if isinstance(topics, (str, bytes)) or not isinstance(topics, (list, tuple)):
        raise _entry_invalid(
            f"topics 必须是列表或元组，得到 {type(topics).__name__}"
        )
    normalized: list[bytes] = []
    for item in topics:
        try:
            normalized.append(_topic_bytes(item))
        except AbiEventError as exc:
            raise _entry_invalid(f"topics 项非法：{exc}") from None
    return tuple(normalized)


def _normalize_data(data) -> bytes:
    """把日志 data 规范化为 bytes，失败抛 LOG_ENTRY_INVALID。"""
    try:
        return _data_bytes(data)
    except AbiEventError as exc:
        raise _entry_invalid(f"data 非法：{exc}") from None


# ---- 事件分派 --------------------------------------------------------------


def _require_registry(registry) -> EventRegistry:
    if not isinstance(registry, EventRegistry):
        raise _abi_invalid(
            f"需要 EventRegistry，得到 {type(registry).__name__}"
        )
    return registry


def _resolve_event(registry: EventRegistry, selector) -> EventDefinition:
    """按规范签名或唯一事件名在注册表中定位事件。"""
    if not isinstance(selector, str) or not selector:
        raise _entry_invalid(f"event 字段必须是非空字符串，得到 {selector!r}")

    if "(" in selector or ")" in selector:
        event = registry._by_signature.get(selector)
        if event is None:
            raise AbiLogDispatchError(
                "LOG_EVENT_NOT_FOUND",
                f"注册表中找不到规范签名为 {selector!r} 的事件",
            )
        return event

    candidates = registry._by_name.get(selector, [])
    if not candidates:
        raise AbiLogDispatchError(
            "LOG_EVENT_NOT_FOUND", f"注册表中找不到事件 {selector!r}"
        )
    if len(candidates) > 1:
        signatures = ", ".join(_event_signature(event) for event in candidates)
        raise AbiLogDispatchError(
            "LOG_EVENT_AMBIGUOUS",
            f"事件 {selector!r} 存在 {len(candidates)} 个同名事件，无法唯一"
            f"选择，请改用规范签名之一：{signatures}",
        )
    return candidates[0]


def _dispatch_by_topic0(registry: EventRegistry, topics: tuple[bytes, ...]):
    """无显式事件时按 topics 首项的 topic0 分派非匿名事件。"""
    if not topics:
        # 只有匿名事件（且无 indexed 参数）才可能产生空 topics 的日志。
        if any(event.anonymous for event in registry.events):
            raise AbiLogDispatchError(
                "LOG_EVENT_REQUIRED",
                "topics 为空且未显式指定 event：匿名事件必须显式选择",
            )
        raise AbiLogDispatchError(
            "LOG_EVENT_NOT_FOUND",
            "topics 为空，无法匹配任何非匿名事件的 topic0",
        )
    event = registry._by_topic0.get(topics[0])
    if event is None:
        raise AbiLogDispatchError(
            "LOG_EVENT_NOT_FOUND",
            f"topic0 0x{topics[0].hex()} 未匹配注册表中的任何事件",
        )
    return event


def _dispatch(registry: EventRegistry, log):
    """校验日志映射并分派事件，返回 (event, topics, data)。"""
    if not isinstance(log, Mapping):
        raise _entry_invalid(f"日志必须是映射，得到 {type(log).__name__}")
    if "topics" not in log:
        raise _entry_invalid("日志缺少必填字段 topics")
    if "data" not in log:
        raise _entry_invalid("日志缺少必填字段 data")

    topics = _normalize_topics(log["topics"])
    data = _normalize_data(log["data"])

    selector = log.get("event")
    if selector is None:
        event = _dispatch_by_topic0(registry, topics)
    else:
        event = _resolve_event(registry, selector)
        if not event.anonymous:
            expected_topic0 = bytes.fromhex(event_topic0(event)[2:])
            if not topics or topics[0] != expected_topic0:
                raise AbiLogDispatchError(
                    "LOG_TOPIC_MISMATCH",
                    f"显式事件 {_event_signature(event)} 与 topics[0] 的 "
                    "topic0 不一致",
                )
    return event, topics, data


# ---- 日志还原 --------------------------------------------------------------


def decode_contract_event_log(registry, log) -> tuple:
    """分派并还原单条合约事件日志。

    ``registry`` 为 :func:`parse_event_registry` 构建的
    :class:`EventRegistry`；``log`` 为映射，``topics`` 与 ``data`` 必填，
    可选 ``event`` 指定规范签名或唯一事件名。

    返回 ``(event, topics, data, values)`` 四元组：命中的
    :class:`EventDefinition`、规范化的 32 字节 topics bytes tuple、data
    bytes 与按声明顺序排列的 values tuple（口径同
    :func:`abi_kit.decode_event_log`）。

    分派失败抛 :class:`abi_kit.AbiLogDispatchError`；分派成功后的日志
    还原失败抛 :class:`abi_kit.AbiEventError`（错误码不变）。
    """
    _require_registry(registry)
    event, topics, data = _dispatch(registry, log)
    values = decode_event_log(event, topics, data)
    return (event, topics, data, values)


def decode_contract_event_logs(registry, logs) -> tuple:
    """按输入顺序批量分派并还原日志序列。

    ``logs`` 为日志映射组成的 list/tuple；返回与输入等长、顺序一致的
    四元组 tuple，每项口径同 :func:`decode_contract_event_log`。任一
    日志失败即整体抛出，不返回部分结果。
    """
    _require_registry(registry)
    if isinstance(logs, (str, bytes)) or not isinstance(logs, (list, tuple)):
        raise _entry_invalid(
            f"日志序列必须是 list 或 tuple，得到 {type(logs).__name__}"
        )
    return tuple(decode_contract_event_log(registry, log) for log in logs)
