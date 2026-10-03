"""事件 ABI 解析、签名 topic0 与日志还原校验。

三个公开入口：

- :func:`parse_event_abi`：解析 ABI JSON 的 event 对象，返回不可变的
  :class:`EventDefinition`（含按声明顺序的 :class:`EventParameter`）；
- :func:`event_topic0`：按 ``事件名(规范参数类型,...)`` 计算 Ethereum
  Keccak-256 签名 topic；``anonymous`` 事件返回 ``None``；
- :func:`decode_event_log`：校验 topics 数量与 topic0，并把 indexed /
  非 indexed 参数还原后按原声明顺序合并为一个 tuple。

indexed 的 ``address``/``bool``/``intM``/``uintM``/``bytesM`` 按 32 字节字
严格解码（填充必须为零、布尔只能是 0/1、整数不得越界）；indexed 的
``string``、动态 ``bytes``、数组与 tuple 在日志中不可逆，直接返回对应的
原始 32 字节 topic（``bytes`` 口径）。非 indexed 参数按现有值层严格规范
从 ``data`` 解码。

所有失败统一抛出带唯一错误码的 :class:`abi_kit.AbiEventError`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._codec import decode_abi_value
from ._exceptions import ABITypeError, ABIValueError, AbiEventError
from ._format import format_abi_type
from ._keccak import keccak256
from ._parser import parse_abi_type
from ._types import ABIType, ArrayType, ElementaryType, TupleType

EVENT_ABI_INVALID = "EVENT_ABI_INVALID"
EVENT_TOPIC_COUNT = "EVENT_TOPIC_COUNT"
EVENT_TOPIC0_MISMATCH = "EVENT_TOPIC0_MISMATCH"
EVENT_TOPIC_VALUE = "EVENT_TOPIC_VALUE"
EVENT_DATA_INVALID = "EVENT_DATA_INVALID"

_WORD = 32
_HEX = "0123456789abcdefABCDEF"
_NAME_START = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$"
)
_NAME_PART = _NAME_START | set("0123456789")


# ---- 不可变声明对象 -------------------------------------------------------


@dataclass(frozen=True)
class EventParameter:
    """事件参数的声明元数据。

    - ``name``：ABI JSON 中的参数名（未命名参数为 ``""``），仅保存元数据，
      不参与签名；
    - ``abi_type``：解析后的不可变 ABI 类型对象；
    - ``indexed``：是否进入 topics（缺失时为 ``False``）。
    """

    name: str
    abi_type: ABIType
    indexed: bool = False


@dataclass(frozen=True)
class EventDefinition:
    """不可变的事件声明。"""

    name: str
    inputs: tuple[EventParameter, ...] = field(default=())
    anonymous: bool = False


# ---- ABI JSON 解析 --------------------------------------------------------


def _invalid(message: str) -> AbiEventError:
    return AbiEventError(EVENT_ABI_INVALID, message)


def _is_identifier(name) -> bool:
    return (
        isinstance(name, str)
        and len(name) > 0
        and name.isascii()
        and name[0] in _NAME_START
        and all(ch in _NAME_PART for ch in name[1:])
    )


def _bool_field(obj: dict, key: str) -> bool:
    if key not in obj:
        return False
    value = obj[key]
    if not isinstance(value, bool):
        raise _invalid(f"{key} 必须是布尔值，得到 {value!r}")
    return value


def _apply_array_suffixes(abi_type: ABIType, suffix: str) -> ABIType:
    """把 ``tuple`` 之后的 ``[]`` / ``[N]`` 后缀逐层包到类型外。"""
    current = abi_type
    while suffix:
        if suffix[0] != "[":
            raise _invalid(f"tuple 类型后缀非法：{suffix!r}")
        close = suffix.find("]")
        if close < 0:
            raise _invalid("数组后缀缺少闭合的 ']'")
        inner = suffix[1:close]
        if inner == "":
            current = ArrayType(current)
        else:
            if (
                not inner
                or not inner.isascii()
                or not inner.isdigit()
                or (len(inner) > 1 and inner[0] == "0")
            ):
                raise _invalid(f"数组长度必须是无前导零的正整数：{inner!r}")
            current = ArrayType(current, int(inner))
        suffix = suffix[close + 1:]
    return current


def _parse_parameter(param) -> EventParameter:
    if not isinstance(param, dict):
        raise _invalid(f"事件参数必须是 JSON 对象，得到 {type(param).__name__}")

    type_string = param.get("type")
    if not isinstance(type_string, str) or type_string == "":
        raise _invalid(f"事件参数必须给出非空字符串 type，得到 {type_string!r}")

    name = param.get("name", "")
    if not isinstance(name, str):
        raise _invalid(f"事件参数 name 必须是字符串，得到 {name!r}")

    indexed = _bool_field(param, "indexed")

    if type_string == "tuple" or type_string.startswith("tuple["):
        components = param.get("components")
        if not isinstance(components, list):
            raise _invalid(
                f"tuple 参数 {name!r} 必须给出 list 形式的 components"
            )
        try:
            sub_params = [_parse_parameter(component) for component in components]
            component_types = tuple(sub.abi_type for sub in sub_params)
            component_names = tuple(sub.name or None for sub in sub_params)
            tuple_type = TupleType(component_types, component_names)
            abi_type = _apply_array_suffixes(
                tuple_type, type_string[len("tuple"):]
            )
        except ABITypeError as exc:
            raise _invalid(f"tuple 参数 {name!r} 类型非法：{exc}") from exc
    else:
        if "components" in param and param["components"] is not None:
            raise _invalid(
                f"非 tuple 参数 {name!r} 不应带有 components"
            )
        try:
            abi_type = parse_abi_type(type_string)
        except ABITypeError as exc:
            raise _invalid(f"参数 {name!r} 的类型非法：{exc}") from exc

    return EventParameter(name=name, abi_type=abi_type, indexed=indexed)


def parse_event_abi(event_json) -> EventDefinition:
    """解析 ABI JSON 的 event 对象，返回不可变 :class:`EventDefinition`。

    要求 ``type`` 恰为 ``"event"``，``name`` 为非空 ASCII 标识符；
    ``anonymous`` / 各参数的 ``indexed`` 缺失时视为 ``False``；tuple 参数
    通过 ``components`` 递归展开（可带 ``[]`` / ``[N]`` 后缀）。非法输入
    抛出 ``EVENT_ABI_INVALID``。
    """
    if not isinstance(event_json, dict):
        raise _invalid(
            f"事件 ABI 必须是 JSON 对象，得到 {type(event_json).__name__}"
        )
    if event_json.get("type") != "event":
        raise _invalid(
            f"事件对象的 type 必须为 'event'，得到 {event_json.get('type')!r}"
        )

    name = event_json.get("name")
    if not _is_identifier(name):
        raise _invalid(f"事件名必须是非空 ASCII 标识符，得到 {name!r}")

    anonymous = _bool_field(event_json, "anonymous")

    inputs = event_json.get("inputs", [])
    if not isinstance(inputs, list):
        raise _invalid(f"事件 inputs 必须是数组，得到 {type(inputs).__name__}")
    parameters = tuple(_parse_parameter(param) for param in inputs)

    return EventDefinition(name=name, inputs=parameters, anonymous=anonymous)


# ---- 签名 topic0 ----------------------------------------------------------


def _signature(event: EventDefinition) -> str:
    canonical = ",".join(
        format_abi_type(param.abi_type) for param in event.inputs
    )
    return f"{event.name}({canonical})"


def event_topic0(event: EventDefinition) -> str | None:
    """返回事件签名 topic0（小写 ``0x`` + 64 位十六进制）。

    参数名与 ``indexed`` 不参与签名；``anonymous`` 事件没有签名 topic，
    返回 ``None``。
    """
    if not isinstance(event, EventDefinition):
        raise _invalid(
            f"需要 EventDefinition 对象，得到 {type(event).__name__}"
        )
    if event.anonymous:
        return None
    digest = keccak256(_signature(event).encode("ascii"))
    return "0x" + digest.hex()


# ---- 日志还原 -------------------------------------------------------------


def _is_indexed_composite(abi_type: ABIType) -> bool:
    """indexed 后无法从单字还原的类型：string、动态 bytes、数组、tuple。"""
    if isinstance(abi_type, ElementaryType):
        return abi_type.kind == "string" or (
            abi_type.kind == "bytes" and abi_type.byte_size is None
        )
    if isinstance(abi_type, (ArrayType, TupleType)):
        return True
    raise _invalid(f"无法识别的参数类型对象：{abi_type!r}")


def _coerce_topic(item) -> bytes:
    if isinstance(item, bytes):
        raw = item
    elif isinstance(item, str):
        text = item[2:] if item[:2] in ("0x", "0X") else item
        if len(text) != 64 or any(ch not in _HEX for ch in text):
            raise AbiEventError(
                EVENT_TOPIC_VALUE,
                "topic 必须是 32 字节 bytes 或可选 0x 前缀的 64 位十六进制 str",
            )
        raw = bytes.fromhex(text)
    else:
        raise AbiEventError(
            EVENT_TOPIC_VALUE,
            f"topic 项必须是 bytes 或十六进制 str，得到 {type(item).__name__}",
        )
    if len(raw) != _WORD:
        raise AbiEventError(
            EVENT_TOPIC_VALUE, f"topic 必须恰好为 32 字节，得到 {len(raw)} 字节"
        )
    return raw


def _coerce_data(data) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        text = data[2:] if data[:2] in ("0x", "0X") else data
        if len(text) % 2 or any(ch not in _HEX for ch in text):
            raise AbiEventError(
                EVENT_DATA_INVALID,
                "data 必须是 bytes 或可选 0x 前缀的偶数位十六进制 str",
            )
        return bytes.fromhex(text)
    raise AbiEventError(
        EVENT_DATA_INVALID,
        f"data 必须是 bytes 或十六进制 str，得到 {type(data).__name__}",
    )


def _decode_indexed(abi_type: ABIType, word: bytes):
    """把一个 indexed topic 字严格解码为 Python 值。"""
    if _is_indexed_composite(abi_type):
        # 链上只存 Keccak 摘要（动态值）或单字装不下的静态复合值，
        # 均不可逆：按 bytes 口径原样返回 32 字节。
        return word

    kind = abi_type.kind
    if kind == "address":
        if word[:12] != b"\x00" * 12:
            raise AbiEventError(
                EVENT_TOPIC_VALUE, "address topic 高位填充非零，超出 20 字节"
            )
        return "0x" + word[12:].hex()
    if kind == "bool":
        if word == b"\x00" * _WORD:
            return False
        if word == b"\x00" * (_WORD - 1) + b"\x01":
            return True
        raise AbiEventError(
            EVENT_TOPIC_VALUE, "bool topic 非法：既非全 0 也非末尾字节为 1"
        )
    if kind == "uint":
        value = int.from_bytes(word, "big", signed=False)
        if value >= 1 << abi_type.bit_size:
            raise AbiEventError(
                EVENT_TOPIC_VALUE,
                f"uint{abi_type.bit_size} topic 解码值越界：{value}",
            )
        return value
    if kind == "int":
        value = int.from_bytes(word, "big", signed=True)
        bits = abi_type.bit_size
        low = -(1 << (bits - 1))
        high = (1 << (bits - 1)) - 1
        if not low <= value <= high:
            raise AbiEventError(
                EVENT_TOPIC_VALUE,
                f"int{bits} topic 解码值超出补码范围 [{low}, {high}]：{value}",
            )
        return value
    # bytesM
    if word[abi_type.byte_size:] != b"\x00" * (_WORD - abi_type.byte_size):
        raise AbiEventError(
            EVENT_TOPIC_VALUE,
            f"bytes{abi_type.byte_size} topic 尾部填充非零，编码不规范",
        )
    return word[:abi_type.byte_size]


def decode_event_log(event: EventDefinition, topics, data):
    """校验并还原事件日志，返回按原声明顺序合并的参数 tuple。

    - 非匿名事件：topics 必须以正确签名 topic0 开头，总数为 indexed 参数
      数加一；匿名事件：总数恰为 indexed 参数数；
    - indexed 的基础类型按字严格解码，不可逆的复合类型返回原始 32 字节；
    - 非 indexed 参数组成匿名 tuple，按现有值层严格规范从 data 解码
      （无此类参数时 data 必须为空）。
    """
    if not isinstance(event, EventDefinition):
        raise _invalid(
            f"需要 EventDefinition 对象，得到 {type(event).__name__}"
        )
    if not isinstance(topics, (list, tuple)):
        raise AbiEventError(
            EVENT_TOPIC_COUNT,
            f"topics 必须是数组，得到 {type(topics).__name__}",
        )

    indexed_params = [param for param in event.inputs if param.indexed]
    expected_count = len(indexed_params) + (0 if event.anonymous else 1)
    if len(topics) != expected_count:
        raise AbiEventError(
            EVENT_TOPIC_COUNT,
            f"topic 数量应为 {expected_count}，得到 {len(topics)}",
        )

    topic_words = [_coerce_topic(item) for item in topics]

    if not event.anonymous:
        expected_topic0 = event_topic0(event)
        if topic_words[0].hex() != expected_topic0[2:]:
            raise AbiEventError(
                EVENT_TOPIC0_MISMATCH,
                f"topic0 不匹配：应为 {expected_topic0}，"
                f"实际为 0x{topic_words[0].hex()}",
            )
        indexed_words = topic_words[1:]
    else:
        indexed_words = topic_words

    # indexed 基础值先于 data 严格校验；不可逆的复合 indexed 类型直接
    # 保留原始 32 字节 topic。
    indexed_values = [
        _decode_indexed(param.abi_type, word)
        for param, word in zip(indexed_params, indexed_words)
    ]

    raw_data = _coerce_data(data)
    non_indexed_types = [
        param.abi_type for param in event.inputs if not param.indexed
    ]
    try:
        non_indexed_values = decode_abi_value(
            TupleType(tuple(non_indexed_types)), raw_data
        )
    except ABIValueError as exc:
        # 值层任何非法编码（长度、偏移、填充、尾部残留等）统一归类。
        raise AbiEventError(
            EVENT_DATA_INVALID, f"非 indexed 参数 data 非法：{exc}"
        ) from exc

    result = [None] * len(event.inputs)
    indexed_cursor = 0
    non_indexed_cursor = 0
    for index, param in enumerate(event.inputs):
        if param.indexed:
            result[index] = indexed_values[indexed_cursor]
            indexed_cursor += 1
        else:
            result[index] = non_indexed_values[non_indexed_cursor]
            non_indexed_cursor += 1

    return tuple(result)
