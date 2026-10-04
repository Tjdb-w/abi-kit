"""函数 ABI 解析、四字节 selector 与函数调用 calldata 编解码。

- :func:`parse_function_abi`：解析 ABI JSON 的 function 条目，返回不可变
  :class:`FunctionDefinition`，保留声明顺序与参数名，tuple 参数由
  ``components`` 递归展开；规范签名与 selector 在构造时确定。
- :func:`function_selector`：按函数名与参数的规范 ABI 类型计算
  ``keccak256("name(type1,type2,...)")[:4]``，返回 4 字节 bytes。
- :func:`encode_function_call`：按函数名（唯一时）或规范函数签名选择
  函数，把实参按公开参数顺序编码，输出四字节 selector 与完整 calldata。
- :func:`decode_function_call`：读取完整 calldata 的 selector 还原函数，
  严格解码参数主体，返回函数名、规范签名、selector 与带类型标注的参数。

calldata 口径：selector 为 4 字节 bytes；完整 calldata 为
``selector + 参数 tuple 的 head/tail 编码``。解码输入接受 bytes 或可选
``0x`` 前缀的偶数位十六进制字符串；参数值口径与
:func:`abi_kit.decode_abi_value` 完全一致。

ABI 中 event / constructor / error / receive / fallback 条目对函数入口
透明（仅消费 ``type == "function"`` 的条目），事件日志还原仍由
:mod:`abi_kit._event` 负责，本模块不改变其行为。

错误约定（均为 ValueError 子类，各自独立）：

- :class:`abi_kit.AbiMetadataError`：ABI 或 function 条目元数据非法
  （缺 name/type/inputs、类型字符串无法解析、规范签名无法生成
  selector 等）；
- :class:`abi_kit.AbiFunctionNotFoundError`：找不到指定名称或规范签名
  的函数；
- :class:`abi_kit.AbiOverloadError`：只给函数名但存在多个重载；
- :class:`abi_kit.AbiSelectorError`：selector 匹配不到任何函数；
- :class:`abi_kit.AbiCalldataLengthError`：calldata 少于四字节；
- :class:`abi_kit.AbiValueError`：实参不能按声明类型编码，或主体不能
  按声明参数类型严格解码；
- :class:`abi_kit.AbiTrailingDataError`：参数主体消费完后仍有尾随字节。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ._codec import _decode, _encode
from ._exceptions import (
    ABITypeError,
    AbiCalldataLengthError,
    AbiFunctionNotFoundError,
    AbiMetadataError,
    AbiOverloadError,
    AbiSelectorError,
    AbiTrailingDataError,
    AbiValueError,
)
from ._format import format_abi_type
from ._keccak import keccak_256
from ._parser import parse_abi_type
from ._types import ABIType, ArrayType, TupleType

_HEX_DIGITS = set("0123456789abcdefABCDEF")
_SUFFIX_RE = re.compile(r"^(\[\d*\])*$")
_SUFFIX_PART = re.compile(r"\[(\d*)\]")

#: components 递归展开的安全上限，与类型/事件解析器一致。
_RECURSION_SAFETY_LIMIT = 200

#: 非 function 的合法 ABI 条目类型；函数入口一律跳过不消费。
_NON_FUNCTION_TYPES = frozenset(
    ("event", "constructor", "error", "receive", "fallback")
)


@dataclass(frozen=True)
class FunctionParameter:
    """函数的单个参数（声明顺序保留）。

    - ``name``：ABI JSON 中的参数名，缺省为空串；
    - ``abi_type``：由 ``type`` + ``components`` 展开得到的类型对象。
    """

    name: str
    abi_type: ABIType


@dataclass(frozen=True)
class FunctionDefinition:
    """不可变函数定义。

    - ``name``：非空 ASCII 函数标识符；
    - ``inputs``：按声明顺序保存的 :class:`FunctionParameter`；
    - ``signature``：规范签名 ``name(type1,type2,...)``（无空白）；
    - ``selector``：签名 keccak256 摘要的前四字节。
    """

    name: str
    inputs: tuple[FunctionParameter, ...] = field(default=())
    signature: str = ""
    selector: bytes = b""


@dataclass(frozen=True)
class FunctionCallEncoding:
    """函数调用编码结果。

    - ``selector``：四字节 selector；
    - ``calldata``：``selector + 参数主体`` 的完整 calldata。
    """

    selector: bytes
    calldata: bytes


@dataclass(frozen=True)
class FunctionCallResult:
    """函数调用解码结果。

    - ``name``：函数名；
    - ``signature``：规范签名；
    - ``selector``：四字节 selector；
    - ``inputs``：按声明顺序的 :class:`FunctionParameter`（带类型标注）；
    - ``args``：按声明顺序还原的实参 tuple。
    """

    name: str
    signature: str
    selector: bytes
    inputs: tuple[FunctionParameter, ...]
    args: tuple


# ---- ABI 元数据解析 -------------------------------------------------------


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
                raise AbiMetadataError(
                    f"数组长度必须是无前导零的正整数：[{digits}]"
                )
            current = ArrayType(current, int(digits))
        else:
            current = ArrayType(current)
    return current


def _build_type(node, depth: int = 1) -> ABIType:
    """从一个 ABI 参数 JSON 节点递归构造类型对象。"""
    if depth > _RECURSION_SAFETY_LIMIT:
        raise AbiMetadataError("components 嵌套过深")
    if not isinstance(node, dict):
        raise AbiMetadataError(
            f"参数描述必须是 JSON 对象，得到 {type(node).__name__}"
        )
    type_string = node.get("type")
    if not isinstance(type_string, str) or not type_string:
        raise AbiMetadataError(
            f"参数必须带有非空字符串 type，得到 {type_string!r}"
        )

    if type_string == "tuple" or type_string.startswith("tuple["):
        suffix = type_string[len("tuple"):]
        if not _SUFFIX_RE.match(suffix):
            raise AbiMetadataError(f"非法 tuple 类型字符串：{type_string!r}")
        components_node = node.get("components")
        if not isinstance(components_node, list):
            raise AbiMetadataError(
                f"tuple 参数必须带有列表形式的 components：{type_string!r}"
            )
        component_types: list[ABIType] = []
        component_names: list[str | None] = []
        for component in components_node:
            component_types.append(_build_type(component, depth + 1))
            if isinstance(component, dict) and isinstance(
                component.get("name"), str
            ):
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
            raise AbiMetadataError(f"tuple 类型构造失败：{exc}") from None
        return current

    if "components" in node:
        raise AbiMetadataError(
            f"非 tuple 参数不能带有 components：{type_string!r}"
        )
    try:
        return parse_abi_type(type_string)
    except ABITypeError as exc:
        raise AbiMetadataError(f"参数类型 {type_string!r} 非法：{exc}") from None


def _build_inputs(entry: dict) -> tuple[FunctionParameter, ...]:
    inputs_node = entry.get("inputs")
    if not isinstance(inputs_node, list):
        raise AbiMetadataError("function 条目必须带有列表形式的 inputs")
    inputs: list[FunctionParameter] = []
    for node in inputs_node:
        if not isinstance(node, dict):
            raise AbiMetadataError(
                f"函数参数必须是 JSON 对象，得到 {type(node).__name__}"
            )
        name = node.get("name", "")
        if not isinstance(name, str):
            raise AbiMetadataError(f"函数参数 name 必须是字符串，得到 {name!r}")
        inputs.append(FunctionParameter(name, _build_type(node)))
    return tuple(inputs)


def parse_function_abi(entry: dict) -> FunctionDefinition:
    """解析 ABI JSON 的单个 function 条目，返回 :class:`FunctionDefinition`。

    要求条目为 JSON 对象且 ``type == "function"``、``name`` 为非空
    ASCII 标识符、``inputs`` 为参数数组（缺省不接受）；tuple 参数由
    ``components`` 递归展开。任何非法输入抛出
    :class:`abi_kit.AbiMetadataError`。
    """
    if not isinstance(entry, dict):
        raise AbiMetadataError(
            f"函数描述必须是 JSON 对象，得到 {type(entry).__name__}"
        )
    if entry.get("type") != "function":
        raise AbiMetadataError(
            f"type 必须为 \"function\"，得到 {entry.get('type')!r}"
        )
    name = entry.get("name")
    if not _is_identifier(name):
        raise AbiMetadataError(f"函数 name 必须是非空 ASCII 标识符，得到 {name!r}")
    inputs = _build_inputs(entry)

    canonical = ",".join(format_abi_type(param.abi_type) for param in inputs)
    signature = f"{name}({canonical})"
    try:
        selector = keccak_256(signature.encode("utf-8"))[:4]
    except (UnicodeEncodeError, TypeError) as exc:
        raise AbiMetadataError(f"规范签名无法生成 selector：{signature!r}：{exc}") from None
    return FunctionDefinition(name, inputs, signature, selector)


def _normalize_abi(abi) -> list:
    """把 ABI JSON 字符串或公开 ABI 描述值规范化为条目列表。"""
    if isinstance(abi, str):
        try:
            parsed = json.loads(abi)
        except (json.JSONDecodeError, ValueError) as exc:
            raise AbiMetadataError(f"ABI JSON 字符串无法解析：{exc}") from None
        if not isinstance(parsed, list):
            raise AbiMetadataError(
                "ABI JSON 必须是条目数组，得到 "
                f"{type(parsed).__name__}"
            )
        return parsed
    if isinstance(abi, (list, tuple)):
        return list(abi)
    raise AbiMetadataError(
        f"ABI 必须是 JSON 字符串或条目数组，得到 {type(abi).__name__}"
    )


def parse_functions(abi) -> tuple[FunctionDefinition, ...]:
    """从完整 ABI 中提取全部 function 条目，保持声明顺序。

    event / constructor / error / receive / fallback 条目跳过不消费；
    其余元数据问题（条目不是对象、缺 type、未知 type 等）统一抛出
    :class:`abi_kit.AbiMetadataError`。
    """
    entries = _normalize_abi(abi)
    functions: list[FunctionDefinition] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AbiMetadataError(
                f"ABI 第 {index} 项必须是 JSON 对象，得到 {type(entry).__name__}"
            )
        if "type" not in entry:
            raise AbiMetadataError(f"ABI 第 {index} 项缺少 type 字段")
        entry_type = entry["type"]
        if entry_type == "function":
            functions.append(parse_function_abi(entry))
        elif entry_type in _NON_FUNCTION_TYPES:
            continue
        else:
            raise AbiMetadataError(
                f"ABI 第 {index} 项的 type 无法识别：{entry_type!r}"
            )
    return tuple(functions)


# ---- selector -------------------------------------------------------------


def function_selector(function: FunctionDefinition) -> bytes:
    """返回函数的四字节 selector（``keccak256(规范签名)[:4]``）。"""
    if not isinstance(function, FunctionDefinition):
        raise AbiMetadataError(
            f"需要 FunctionDefinition，得到 {type(function).__name__}"
        )
    return function.selector


# ---- 函数选择 -------------------------------------------------------------


def _split_signature(text: str) -> tuple[str, list[str]]:
    """拆分规范签名为函数名与顶层逗号分隔的类型串列表。"""
    open_paren = text.find("(")
    if open_paren <= 0 or not text.endswith(")"):
        raise AbiMetadataError(f"规范函数签名格式非法：{text!r}")
    name = text[:open_paren]
    if not _is_identifier(name):
        raise AbiMetadataError(f"规范签名中的函数名非法：{name!r}")
    inner = text[open_paren + 1:-1]
    if inner == "":
        return name, []

    segments: list[str] = []
    depth = 0
    start = 0
    for pos, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise AbiMetadataError(f"规范函数签名括号不匹配：{text!r}")
        elif ch == "," and depth == 0:
            segments.append(inner[start:pos])
            start = pos + 1
    if depth != 0:
        raise AbiMetadataError(f"规范函数签名括号不匹配：{text!r}")
    segments.append(inner[start:])
    return name, segments


def _canonical_signature(text: str) -> str:
    """把用户给出的规范签名解析并重新规范化为唯一签名字符串。"""
    if not isinstance(text, str) or not text:
        raise AbiMetadataError(
            f"函数名或规范签名必须是非空字符串，得到 {text!r}"
        )
    name, segments = _split_signature(text)
    canonical_types: list[str] = []
    for segment in segments:
        try:
            abi_type = parse_abi_type(segment)
        except ABITypeError as exc:
            raise AbiMetadataError(
                f"规范签名 {text!r} 中的类型 {segment!r} 无法解析：{exc}"
            ) from None
        canonical_types.append(format_abi_type(abi_type))
    return f"{name}({','.join(canonical_types)})"


def _select_function(
    functions: tuple[FunctionDefinition, ...], key: str
) -> FunctionDefinition:
    if not isinstance(key, str) or not key:
        raise AbiMetadataError(
            f"函数名或规范签名必须是非空字符串，得到 {key!r}"
        )

    if "(" not in key:
        # 纯函数名路径。
        matches = tuple(f for f in functions if f.name == key)
        if not matches:
            raise AbiFunctionNotFoundError(f"ABI 中找不到函数：{key!r}")
        distinct = {f.signature for f in matches}
        if len(distinct) > 1:
            raise AbiOverloadError(
                f"函数 {key!r} 存在 {len(distinct)} 个重载，"
                "请改用规范函数签名消歧"
            )
        return matches[0]

    # 规范签名路径：解析失败或无法生成 selector 统一属元数据错误。
    canonical = _canonical_signature(key)
    for function in functions:
        if function.signature == canonical:
            return function
    raise AbiFunctionNotFoundError(f"ABI 中找不到函数签名：{canonical!r}")


# ---- calldata 编解码 ------------------------------------------------------


def _inputs_tuple_type(function: FunctionDefinition) -> TupleType:
    return TupleType(tuple(param.abi_type for param in function.inputs))


def _coerce_args(args) -> list:
    if isinstance(args, (str, bytes)) or not isinstance(args, (list, tuple)):
        raise AbiValueError(
            f"实参列表必须是 list 或 tuple，得到 {type(args).__name__}"
        )
    return list(args)


def encode_function_call(
    abi, function_name_or_signature: str, args=()
) -> FunctionCallEncoding:
    """编码一次函数调用。

    ``abi`` 接受 ABI JSON 字符串或等价的条目数组；
    ``function_name_or_signature`` 为函数名（ABI 中唯一）或规范函数签名
    （``name(type1,type2,...)``，重载时必须使用签名）；``args`` 按公开
    参数顺序给出。返回 :class:`FunctionCallEncoding`，其中 ``selector``
    为四字节，``calldata`` 为完整调用字节。

    元数据非法抛 :class:`abi_kit.AbiMetadataError`；找不到函数抛
    :class:`abi_kit.AbiFunctionNotFoundError`；同名重载未消歧抛
    :class:`abi_kit.AbiOverloadError`；实参不能按声明类型编码抛
    :class:`abi_kit.AbiValueError`。
    """
    functions = parse_functions(abi)
    function = _select_function(functions, function_name_or_signature)
    values = _coerce_args(args)

    try:
        body = _encode(_inputs_tuple_type(function), tuple(values))
    except AbiValueError:
        raise
    except ValueError as exc:
        # 值层 ABIValueError 及严格编码的其他 ValueError 一律归为函数
        # 值错误；元数据问题在进入本路径前已被拦截。
        raise AbiValueError(f"实参无法按 {function.signature} 编码：{exc}") from None
    return FunctionCallEncoding(function.selector, function.selector + body)


def _coerce_calldata(calldata) -> bytes:
    if isinstance(calldata, bytes):
        return calldata
    if isinstance(calldata, str):
        text = calldata[2:] if calldata.startswith(("0x", "0X")) else calldata
        if len(text) % 2 or any(c not in _HEX_DIGITS for c in text):
            raise AbiValueError(
                "calldata 必须是 bytes 或可选 0x 前缀的偶数位十六进制字符串"
            )
        return bytes.fromhex(text)
    raise AbiValueError(
        f"calldata 必须是 bytes 或十六进制字符串，得到 {type(calldata).__name__}"
    )


def decode_function_call(abi, calldata) -> FunctionCallResult:
    """解码完整函数调用 calldata。

    先按四字节 selector 还原函数，再按其声明参数严格解码主体。空
    calldata 或不足四字节抛 :class:`abi_kit.AbiCalldataLengthError`；
    selector 匹配不到函数抛 :class:`abi_kit.AbiSelectorError`；主体不能
    按声明类型严格解码抛 :class:`abi_kit.AbiValueError`；主体消费完后
    仍有尾随字节抛 :class:`abi_kit.AbiTrailingDataError`。
    """
    functions = parse_functions(abi)
    raw = _coerce_calldata(calldata)
    if len(raw) < 4:
        raise AbiCalldataLengthError(
            f"calldata 至少需要 4 字节 selector，得到 {len(raw)} 字节"
        )

    selector = raw[:4]
    body = raw[4:]
    matches = tuple(f for f in functions if f.selector == selector)
    if not matches:
        raise AbiSelectorError(
            f"selector 0x{selector.hex()} 匹配不到 ABI 中的任何函数"
        )
    function = matches[0]

    bound = len(body)
    try:
        args, end = _decode(_inputs_tuple_type(function), body, 0, bound)
    except ValueError as exc:
        raise AbiValueError(
            f"calldata 主体无法按 {function.signature} 解码：{exc}"
        ) from None
    if end != bound:
        raise AbiTrailingDataError(
            f"参数主体在 {end} 字节处结束，但 calldata 另有 "
            f"{bound - end} 字节尾随数据"
        )
    return FunctionCallResult(
        function.name,
        function.signature,
        function.selector,
        function.inputs,
        tuple(args),
    )
