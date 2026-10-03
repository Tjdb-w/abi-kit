"""事件 ABI 解析、签名 topic0 与日志还原。

- :func:`parse_event_abi`：解析 ABI JSON 的 event 对象，返回不可变的
  :class:`EventDefinition`，保留声明顺序与名称、indexed、anonymous 等
  声明元数据；tuple 参数由 ``components`` 递归展开。
- :func:`event_topic0`：按事件名与参数的规范 ABI 类型计算
  ``keccak256("Name(type1,type2,...)")``，返回小写 ``"0x"`` + 64 位
  十六进制；参数名与 indexed 不参与签名，anonymous 事件返回 None。
- :func:`decode_event_log`：校验 topics 并严格还原 data，按声明顺序
  合并为一个 tuple。

日志口径：

- 非 indexed 参数由 data 按 tuple 严格 ABI 解码，值口径与
  :func:`decode_abi_value` 完全一致；没有非 indexed 参数时 data 必须
  为空。
- indexed 的 address/bool/intM/uintM/bytesM 从各自 32 字节 topic 严格
  解码；indexed 的 string、动态 bytes、数组与 tuple 不可逆，原样返回
  32 字节 topic，口径为 bytes。

所有失败统一抛出 :class:`abi_kit.AbiEventError`，以 ``code`` 区分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ._codec import decode_abi_value
from ._exceptions import ABITypeError, AbiEventError
from ._format import format_abi_type
from ._keccak import keccak_256
from ._parser import parse_abi_type
from ._types import ABIType, ArrayType, ElementaryType, TupleType

_WORD = 32
_HEX_DIGITS = set("0123456789abcdefABCDEF")
_SUFFIX_RE = re.compile(r"^(\[\d*\])*$")
_SUFFIX_PART = re.compile(r"\[(\d*)\]")

#: components 递归展开的安全上限，与类型解析器一致：语义嵌套上限
#: （128 层）由类型对象构造时检查；此阈值仅用于在触及 Python 递归限制
#: 前抛出 EVENT_ABI_INVALID。
_RECURSION_SAFETY_LIMIT = 200


@dataclass(frozen=True)
class EventParameter:
    """事件的单个参数（声明顺序保留）。

    - ``name``：ABI JSON 中的参数名，缺省为空串；
    - ``abi_type``：由 ``type`` + ``components`` 展开得到的类型对象；
    - ``indexed``：是否进入 topics。
    """

    name: str
    abi_type: ABIType
    indexed: bool = False


@dataclass(frozen=True)
class EventDefinition:
    """不可变事件定义。

    - ``name``：非空 ASCII 事件标识符；
    - ``inputs``：按声明顺序保存的 :class:`EventParameter`；
    - ``anonymous``：是否为匿名事件（缺省视为 False）。
    """

    name: str
    inputs: tuple[EventParameter, ...] = field(default=())
    anonymous: bool = False


# ---- ABI JSON 解析 -------------------------------------------------------


def _invalid(message: str) -> AbiEventError:
    return AbiEventError("EVENT_ABI_INVALID", message)


def _is_identifier(name) -> bool:
    if not isinstance(name, str) or not name or not name.isascii():
        return False
    first, rest = name[0], name[1:]
    if not (first.isalpha() or first in "_$"):
        return False
    return all(ch.isalnum() or ch in "_$" for ch in rest)


def _apply_suffixes(inner: ABIType, suffix: str) -> ABIType:
    """把 ``"[]"`` / ``"[n]"`` 后缀序列依次套到 inner 外层。"""
    current = inner
    for match in _SUFFIX_PART.finditer(suffix):
        digits = match.group(1)
        if digits:
            # 与类型解析器一致：拒绝 0 长度与前导零。
            if digits[0] == "0":
                raise _invalid(f"数组长度必须是无前导零的正整数：[{digits}]")
            current = ArrayType(current, int(digits))
        else:
            current = ArrayType(current)
    return current


def _build_type(node, depth: int = 1) -> ABIType:
    """从一个 ABI 参数 JSON 节点递归构造类型对象。"""
    if depth > _RECURSION_SAFETY_LIMIT:
        raise _invalid("components 嵌套过深")
    if not isinstance(node, dict):
        raise _invalid(f"参数描述必须是 JSON 对象，得到 {type(node).__name__}")
    type_string = node.get("type")
    if not isinstance(type_string, str) or not type_string:
        raise _invalid(f"参数必须带有非空字符串 type，得到 {type_string!r}")

    if type_string == "tuple" or type_string.startswith("tuple["):
        suffix = type_string[len("tuple"):]
        if not _SUFFIX_RE.match(suffix):
            raise _invalid(f"非法 tuple 类型字符串：{type_string!r}")
        components_node = node.get("components")
        if not isinstance(components_node, list):
            raise _invalid(
                f"tuple 参数必须带有列表形式的 components：{type_string!r}"
            )
        component_types: list[ABIType] = []
        component_names: list[str | None] = []
        for component in components_node:
            component_types.append(_build_type(component, depth + 1))
            if isinstance(component, dict) and isinstance(component.get("name"), str):
                component_names.append(component["name"] or None)
            else:
                component_names.append(None)
        try:
            current: ABIType = TupleType(
                tuple(component_types), names=tuple(component_names)
            )
            if suffix:
                current = _apply_suffixes(current, suffix)
        except ABITypeError as exc:
            raise _invalid(f"tuple 类型构造失败：{exc}") from None
        return current

    if "components" in node:
        raise _invalid(f"非 tuple 参数不能带有 components：{type_string!r}")
    try:
        return parse_abi_type(type_string)
    except ABITypeError as exc:
        raise _invalid(f"参数类型 {type_string!r} 非法：{exc}") from None


def _build_inputs(event_json: dict) -> tuple[EventParameter, ...]:
    inputs_node = event_json.get("inputs", [])
    if not isinstance(inputs_node, list):
        raise _invalid("事件 inputs 必须是数组")
    inputs: list[EventParameter] = []
    for node in inputs_node:
        if not isinstance(node, dict):
            raise _invalid(f"事件参数必须是 JSON 对象，得到 {type(node).__name__}")
        name = node.get("name", "")
        if not isinstance(name, str):
            raise _invalid(f"事件参数 name 必须是字符串，得到 {name!r}")
        indexed = node.get("indexed", False)
        if not isinstance(indexed, bool):
            raise _invalid(f"indexed 必须是布尔值，得到 {indexed!r}")
        inputs.append(EventParameter(name, _build_type(node), indexed))
    return tuple(inputs)


def parse_event_abi(event_json: dict) -> EventDefinition:
    """解析 ABI JSON 的 event 对象，返回不可变 :class:`EventDefinition`。

    要求 ``type == "event"``、``name`` 为非空 ASCII 标识符；布尔标志
    缺失视为 False；tuple 参数由 ``components`` 递归展开。任何非法输入
    抛出带 ``EVENT_ABI_INVALID`` 的 :class:`abi_kit.AbiEventError`。
    """
    if not isinstance(event_json, dict):
        raise _invalid(
            f"事件描述必须是 JSON 对象，得到 {type(event_json).__name__}"
        )
    if event_json.get("type") != "event":
        raise _invalid(f"type 必须为 \"event\"，得到 {event_json.get('type')!r}")
    name = event_json.get("name")
    if not _is_identifier(name):
        raise _invalid(f"事件 name 必须是非空 ASCII 标识符，得到 {name!r}")
    anonymous = event_json.get("anonymous", False)
    if not isinstance(anonymous, bool):
        raise _invalid(f"anonymous 必须是布尔值，得到 {anonymous!r}")
    return EventDefinition(name, _build_inputs(event_json), anonymous)


# ---- topic0 --------------------------------------------------------------


def _event_signature(event: EventDefinition) -> str:
    canonical = ",".join(format_abi_type(param.abi_type) for param in event.inputs)
    return f"{event.name}({canonical})"


def event_topic0(event: EventDefinition) -> str | None:
    """计算事件签名 topic0。

    返回小写 ``"0x"`` + 64 位十六进制；anonymous 事件返回 None。
    参数名与 indexed 不参与签名。
    """
    if not isinstance(event, EventDefinition):
        raise _invalid(
            f"需要 EventDefinition，得到 {type(event).__name__}"
        )
    if event.anonymous:
        return None
    digest = keccak_256(_event_signature(event).encode("utf-8"))
    return "0x" + digest.hex()


# ---- 日志还原 ------------------------------------------------------------


def _error(code: str, message: str) -> AbiEventError:
    return AbiEventError(code, message)


def _topic_bytes(item) -> bytes:
    """把单个 topic 规范化为 32 字节，失败抛 EVENT_TOPIC_VALUE。"""
    if isinstance(item, bytes):
        if len(item) != _WORD:
            raise _error(
                "EVENT_TOPIC_VALUE",
                f"topic 必须是 32 字节，得到 {len(item)} 字节",
            )
        return item
    if isinstance(item, str):
        text = item[2:] if item.startswith(("0x", "0X")) else item
        if len(text) != 64 or any(c not in _HEX_DIGITS for c in text):
            raise _error(
                "EVENT_TOPIC_VALUE",
                "topic 必须是可选 0x 前缀的 64 位十六进制字符串",
            )
        return bytes.fromhex(text)
    raise _error(
        "EVENT_TOPIC_VALUE",
        f"topic 必须是 bytes 或十六进制字符串，得到 {type(item).__name__}",
    )


def _data_bytes(data) -> bytes:
    """把 data 规范化为字节串，失败抛 EVENT_DATA_INVALID。"""
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        text = data[2:] if data.startswith(("0x", "0X")) else data
        if len(text) % 2 or any(c not in _HEX_DIGITS for c in text):
            raise _error(
                "EVENT_DATA_INVALID",
                "data 必须是可选 0x 前缀的偶数位十六进制字符串",
            )
        return bytes.fromhex(text)
    raise _error(
        "EVENT_DATA_INVALID",
        f"data 必须是 bytes 或十六进制字符串，得到 {type(data).__name__}",
    )


def _decode_indexed(abi_type: ABIType, topic: bytes):
    """从 32 字节 topic 严格解码可还原的 indexed 基础值。"""
    if isinstance(abi_type, ArrayType) or isinstance(abi_type, TupleType):
        return topic
    if not isinstance(abi_type, ElementaryType):
        return topic
    kind = abi_type.kind
    if kind == "string" or (kind == "bytes" and abi_type.byte_size is None):
        return topic
    if kind == "address":
        if topic[:12] != b"\x00" * 12:
            raise _error("EVENT_TOPIC_VALUE", "address topic 高位 12 字节非零")
        return "0x" + topic[12:].hex()
    if kind == "bool":
        if topic == b"\x00" * _WORD:
            return False
        if topic == b"\x00" * (_WORD - 1) + b"\x01":
            return True
        raise _error("EVENT_TOPIC_VALUE", "bool topic 既非全 0 也非末尾字节为 1")
    if kind == "uint":
        value = int.from_bytes(topic, "big", signed=False)
        if value >= 1 << abi_type.bit_size:
            raise _error(
                "EVENT_TOPIC_VALUE",
                f"uint{abi_type.bit_size} topic 解码值越界：{value}",
            )
        return value
    if kind == "int":
        value = int.from_bytes(topic, "big", signed=True)
        bits = abi_type.bit_size
        low = -(1 << (bits - 1))
        high = (1 << (bits - 1)) - 1
        if not low <= value <= high:
            raise _error(
                "EVENT_TOPIC_VALUE",
                f"int{bits} topic 解码值超出补码范围：{value}",
            )
        return value
    # bytesM
    if topic[abi_type.byte_size:] != b"\x00" * (_WORD - abi_type.byte_size):
        raise _error(
            "EVENT_TOPIC_VALUE",
            f"bytes{abi_type.byte_size} topic 尾部填充非零",
        )
    return topic[:abi_type.byte_size]


def decode_event_log(event: EventDefinition, topics, data) -> tuple:
    """校验日志并还原事件参数。

    非匿名事件的 topics 必须以正确 topic0 开头，总数为 indexed 参数数
    加一；匿名事件总数等于 indexed 参数数。data 必须能按非 indexed
    参数组成的 tuple 严格解码，无此类参数时必须为空。返回值按参数声明
    顺序合并为一个 tuple。任何失败抛 :class:`abi_kit.AbiEventError`。
    """
    if not isinstance(event, EventDefinition):
        raise _invalid(f"需要 EventDefinition，得到 {type(event).__name__}")
    if isinstance(topics, (str, bytes)) or not isinstance(topics, (list, tuple)):
        raise _error(
            "EVENT_TOPIC_VALUE",
            f"topics 必须是列表或元组，得到 {type(topics).__name__}",
        )

    indexed_params = [param for param in event.inputs if param.indexed]
    indexed_count = len(indexed_params)
    expected = indexed_count if event.anonymous else indexed_count + 1
    if len(topics) != expected:
        raise _error(
            "EVENT_TOPIC_COUNT",
            f"topics 数量应为 {expected}，得到 {len(topics)}",
        )

    normalized_topics = [_topic_bytes(item) for item in topics]
    if not event.anonymous:
        expected_topic0 = bytes.fromhex(event_topic0(event)[2:])
        if normalized_topics[0] != expected_topic0:
            raise _error(
                "EVENT_TOPIC0_MISMATCH",
                "topics[0] 与事件签名 topic0 不一致",
            )

    payload = _data_bytes(data)
    non_indexed = [param for param in event.inputs if not param.indexed]
    if non_indexed:
        data_type = TupleType(tuple(param.abi_type for param in non_indexed))
        try:
            decoded_data = decode_abi_value(data_type, payload)
        except ValueError as exc:
            # decode_abi_value 对严格布局的任何违反都抛 ValueError；
            # 统一为日志层错误码 EVENT_DATA_INVALID。
            raise _error("EVENT_DATA_INVALID", f"非 indexed 参数解码失败：{exc}") from None
    else:
        if payload:
            raise _error(
                "EVENT_DATA_INVALID",
                f"事件无非 indexed 参数，但 data 含有 {len(payload)} 字节",
            )
        decoded_data = ()

    result = []
    indexed_iter = iter(normalized_topics if event.anonymous else normalized_topics[1:])
    data_iter = iter(decoded_data)
    for param in event.inputs:
        if param.indexed:
            result.append(_decode_indexed(param.abi_type, next(indexed_iter)))
        else:
            result.append(next(data_iter))
    return tuple(result)
