"""ABI 类型布局的编码前预检与结构说明。

- :func:`describeAbiType`：输入单个 ABI JSON 类型对象（含 ``type`` 文本，
  ``tuple`` / ``tuple[...]`` 可带 ``components``），只做结构诊断，不编解码、
  不读写文件，返回只含
  ``canonical`` / ``kind`` / ``isDynamic`` / ``arrayLength`` / ``base`` /
  ``components`` 六个键的普通字典并递归展开。

诊断口径：

- ``canonical``：无空白、无名称、可直接用于签名的规范类型串，如
  ``uint256``、``bytes32``、``address[]``、``(bytes32,uint256)[2]``；
  Solidity 常用别名（``uint``→``uint256``、``byte``→``bytes1``、
  ``fixed``→``fixed128x18`` 等）沿用解析层归一；
- ``kind``：``uint`` / ``int`` / ``address`` / ``bool`` / ``bytesN`` /
  ``bytes`` / ``string`` / ``fixed`` / ``ufixed`` / ``function`` /
  ``array`` / ``tuple``；
- ``isDynamic``：按 Solidity ABI 规则，``bytes``、``string``、动态数组、
  元素动态的数组、含动态成员的 tuple 为 True，其余（含空 tuple）为 False；
- ``arrayLength``：定长数组为正整数，动态数组为 None，非数组为 None；
- ``base``：数组为元素诊断，其余为 None；
- ``components``：tuple 为按声明顺序的子诊断列表（空 tuple 为 ``[]``），
  叶子为 None；数组链指向 tuple 时，每层数组都透传最内层 tuple 的
  子诊断，否则为 None。

参数名与成员名（``name``）不参与诊断，不出现在结果中；相同输入始终得到
相同结果。

异常约定（消息只指出层级与字段，不含文件路径）：

- :class:`TypeError`（内建）：输入非字典、``type`` 非字符串、tuple 缺
  ``components`` 或 ``components`` 非数组；
- :class:`abi_kit.RangeError`（包导出的 ``ValueError`` 子类）：类型语法、
  整数位宽、bytesM、fixedMxN、数组长度非法，tuple 数组后缀非法，非
  tuple 类型带有 ``components``，或类型嵌套超过 128 层。
"""

from __future__ import annotations

import re

from ._exceptions import ABITypeError, RangeError
from ._format import format_abi_type
from ._parser import parse_abi_type
from ._types import (
    ABIType,
    ArrayType,
    ElementaryType,
    FixedPointType,
    FunctionType,
    MAX_TYPE_DEPTH,
    TupleType,
)

#: components 递归展开的安全上限：语义嵌套上限（128 层）与类型对象一致；
#: 此阈值仅用于在触及 Python 递归限制前抛出 RangeError。
_RECURSION_SAFETY_LIMIT = 200

_SUFFIX_RE = re.compile(r"^(\[\d*\])*$")
_SUFFIX_PART = re.compile(r"\[(\d*)\]")


# ---- 异常构件 -------------------------------------------------------------


def _type_error(path: str, field: str | None, message: str) -> TypeError:
    """构造指出层级与字段的 TypeError（path 为 "" 表示根）。"""
    if field is None:
        where = "输入" if path == "" else path
    else:
        where = field if path == "" else f"{path}.{field}"
    return TypeError(f"{where}：{message}")


def _range_error(path: str, field: str | None, message: str) -> RangeError:
    """构造指出层级与字段的 RangeError（path 为 "" 表示根）。"""
    if field is None:
        where = "输入" if path == "" else path
    else:
        where = field if path == "" else f"{path}.{field}"
    return RangeError(f"{where}：{message}")


# ---- 叶子诊断 -------------------------------------------------------------


def _elementary_diagnostic(abi_type: ABIType) -> dict:
    """把基础类型对象转成叶子诊断（base/components 均为 None）。"""
    if isinstance(abi_type, ElementaryType):
        if abi_type.kind in ("uint", "int"):
            kind = abi_type.kind
        elif abi_type.kind == "bytes":
            kind = "bytes" if abi_type.byte_size is None else "bytesN"
        else:
            # address / bool / string
            kind = abi_type.kind
        is_dynamic = kind in ("bytes", "string")
    elif isinstance(abi_type, FixedPointType):
        kind = "fixed" if abi_type.signed else "ufixed"
        is_dynamic = False
    elif isinstance(abi_type, FunctionType):
        kind = "function"
        is_dynamic = False
    else:  # pragma: no cover - 调用方只传入基础类型对象
        raise RangeError(f"不是合法的基础类型：{abi_type!r}")
    return {
        "canonical": format_abi_type(abi_type),
        "kind": kind,
        "isDynamic": is_dynamic,
        "arrayLength": None,
        "base": None,
        "components": None,
    }


def _tuple_diagnostic(children: list[dict], canonical: str) -> dict:
    """构造 tuple 诊断；canonical 与 children 已按声明顺序准备好。"""
    return {
        "canonical": canonical,
        "kind": "tuple",
        "isDynamic": any(child["isDynamic"] for child in children),
        "arrayLength": None,
        "base": None,
        "components": children,
    }


def _array_diagnostic(length: int | None, base: dict) -> dict:
    """包装一层数组诊断。

    components 自动透传：base 为 tuple 或其链上含 tuple 时，base 的
    components 即最内层 tuple 的子诊断；否则为 None。
    """
    suffix = "[]" if length is None else f"[{length}]"
    return {
        "canonical": base["canonical"] + suffix,
        "kind": "array",
        # 动态数组本身动态；定长数组在元素动态时同样动态。
        "isDynamic": length is None or base["isDynamic"],
        "arrayLength": length,
        "base": base,
        "components": base["components"],
    }


def _diagnostic_from_type(abi_type: ABIType) -> dict:
    """把解析出的类型对象（可为数组/文本元组）递归转成诊断。"""
    if isinstance(abi_type, (ElementaryType, FixedPointType, FunctionType)):
        return _elementary_diagnostic(abi_type)
    if isinstance(abi_type, TupleType):
        children = [_diagnostic_from_type(component)
                    for component in abi_type.components]
        return _tuple_diagnostic(children, format_abi_type(abi_type))
    if isinstance(abi_type, ArrayType):
        base = _diagnostic_from_type(abi_type.element_type)
        return _array_diagnostic(abi_type.length, base)
    raise RangeError(f"不是合法的 ABI 类型：{abi_type!r}")  # pragma: no cover


def _split_tuple_suffix(type_string: str, path: str) -> str:
    """取出 ``tuple`` 之后的数组后缀串并校验整体形状。"""
    suffix = type_string[len("tuple"):]
    if not _SUFFIX_RE.match(suffix):
        raise _range_error(
            path,
            "type",
            f"tuple 类型字符串的数组后缀非法，得到 {type_string!r}",
        )
    return suffix


def _array_lengths(suffix: str, type_string: str, path: str) -> list[int | None]:
    """把 ``[n]`` / ``[]`` 后缀序列解析为从内到外的长度列表。"""
    lengths: list[int | None] = []
    for match in _SUFFIX_PART.finditer(suffix):
        digits = match.group(1)
        if not digits:
            lengths.append(None)
            continue
        # 与类型解析器一致：拒绝 0 长度与前导零。
        if digits[0] == "0":
            raise _range_error(
                path,
                "type",
                "数组长度必须是无前导零的正整数，得到 "
                f"[{digits}]（类型 {type_string!r}）",
            )
        lengths.append(int(digits))
    return lengths


# ---- 递归诊断 -------------------------------------------------------------


def _describe_node(node, path: str, depth: int) -> tuple[dict, int]:
    """递归诊断一个 ABI JSON 类型节点。

    返回 ``(diagnostic, semantic_depth)``，深度口径与
    :attr:`abi_kit.ABIType.depth` 一致（基础类型为 1，每个 tuple 与每层
    数组各计一层）。

    ``path`` 为该节点相对根的层级定位（根为 ""），例如
    ``components[0].components[2]``，仅用于异常消息。
    """
    if depth > _RECURSION_SAFETY_LIMIT:
        raise _range_error(path, "components", "components 嵌套过深")
    if not isinstance(node, dict):
        raise _type_error(
            path,
            None,
            f"类型描述必须是 JSON 对象（dict），得到 {type(node).__name__}",
        )

    if "type" not in node:
        raise _type_error(
            path,
            "type",
            "类型描述必须给出非空字符串 type 字段，得到缺失",
        )
    type_string = node["type"]
    if not isinstance(type_string, str):
        raise _type_error(
            path,
            "type",
            f"必须是字符串，得到 {type(type_string).__name__}",
        )

    has_components = "components" in node
    is_tuple = type_string == "tuple" or type_string.startswith("tuple[")

    if is_tuple:
        components_node = node.get("components")
        if not isinstance(components_node, list):
            kind_note = "缺少 components 字段" if not has_components else (
                f"components 必须是数组（list），得到 "
                f"{type(components_node).__name__}"
            )
            raise _type_error(
                path,
                "components",
                f"tuple 类型（{type_string!r}）{kind_note}",
            )

        child_path_prefix = "components" if path == "" else f"{path}.components"
        children: list[dict] = []
        child_depths: list[int] = []
        for index, child in enumerate(components_node):
            child_diag, child_depth = _describe_node(
                child, f"{child_path_prefix}[{index}]", depth + 1
            )
            children.append(child_diag)
            child_depths.append(child_depth)
        # tuple 自身一层，叠加最深成员层数；空 tuple 为 1。
        semantic_depth = 1 + max(child_depths, default=0)
        diagnostic = _tuple_diagnostic(
            children,
            "(" + ",".join(child["canonical"] for child in children) + ")",
        )

        suffix = _split_tuple_suffix(type_string, path)
        if suffix:
            lengths = _array_lengths(suffix, type_string, path)
            # _array_diagnostic 自动透传 base 的 components，故数组链上
            # 每层都携带最内层 tuple 的同一份子诊断。
            for length in lengths:
                diagnostic = _array_diagnostic(length, diagnostic)
            semantic_depth += len(lengths)
        _check_semantic_depth(semantic_depth, path, type_string)
        return diagnostic, semantic_depth

    if has_components:
        raise _range_error(
            path,
            "components",
            f"只有 tuple 类型才能带 components，type 为非 tuple 类型 "
            f"{type_string!r}",
        )

    try:
        abi_type = parse_abi_type(type_string)
    except ABITypeError as exc:
        raise _range_error(
            path,
            "type",
            f"不是合法的 ABI 类型 {type_string!r}：{exc}",
        ) from None
    semantic_depth = abi_type.depth
    _check_semantic_depth(semantic_depth, path, type_string)
    return _diagnostic_from_type(abi_type), semantic_depth


def _check_semantic_depth(semantic_depth: int, path: str, type_string: str) -> None:
    """语义嵌套超过 128 层按值域非法处理（RangeError）。"""
    if semantic_depth > MAX_TYPE_DEPTH:
        raise _range_error(
            path,
            "type",
            f"类型嵌套超过 {MAX_TYPE_DEPTH} 层，得到 {semantic_depth} 层"
            f"（类型 {type_string!r}）",
        )


def describeAbiType(abi_type_json: dict) -> dict:
    """说明单个 ABI JSON 类型对象的布局并拒绝非法声明（只预检不编解码）。

    :param abi_type_json: ABI JSON 类型对象，例如 ``{"type": "uint256"}``
        或 ``{"type": "tuple[]", "components": [{"type": "bytes32"},
        {"type": "uint256"}]}``；``name`` 等其余字段允许存在但不参与诊断。
    :returns: 只含 ``canonical`` / ``kind`` / ``isDynamic`` /
        ``arrayLength`` / ``base`` / ``components`` 的普通字典，数组与
        tuple 递归展开；相同输入得到相同结果。
    :raises TypeError: 输入非字典、``type`` 非字符串、tuple 缺
        ``components`` 或 ``components`` 非数组。
    :raises RangeError: 类型语法、整数位宽、bytesM、fixedMxN、数组长度或
        tuple 数组后缀非法，非 tuple 类型带有 ``components``，或类型嵌套
        超过 128 层。
    """
    diagnostic, _ = _describe_node(abi_type_json, "", 1)
    return diagnostic
