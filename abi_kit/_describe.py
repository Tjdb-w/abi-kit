"""ABI 类型布局诊断（编码前预检与结构说明）。

:func:`describeAbiType` 接受一个 ABI JSON 参数节点（含 ``type`` 文本，
tuple 或以 tuple 为元素的数组可带 ``components``），只做声明校验与
结构展开，返回纯字典诊断结果：不编解码、不读写文件，参数名与成员名
不进入结果。

诊断字典只含以下键并递归展开：

- ``canonical``：无空白、无名称、可直接用于签名的规范类型字符串，如
  ``uint256``、``bytes32``、``address[]``、``(bytes32,uint256)[2]``；
  别名按解析层既有规则归一（``uint``→``uint256`` 等）。
- ``kind``：``uint`` / ``int`` / ``address`` / ``bool`` / ``bytesN`` /
  ``bytes`` / ``string`` / ``fixed`` / ``ufixed`` / ``function`` /
  ``array`` / ``tuple``。
- ``isDynamic``：按 Solidity ABI 规则——``bytes``、``string``、动态
  数组、元素动态的数组、含动态成员的 tuple 为 True，其余 False。
- ``arrayLength``：定长数组为正整数，动态数组为 None，非数组为 None。
- ``base``：数组的元素诊断，其余类型为 None。
- ``components``：tuple 的子诊断列表（空 tuple 为 ``[]``）；数组链
  指向 tuple 时，每层数组的 components 都给出最内层 tuple 的同一组
  子诊断，其余情形为 None。

声明校验与既有的 type + components 递归展开（事件/函数/error/
constructor 路径）完全同构：tuple 分支同样构造不可变类型对象，因此
位宽、bytesN、fixedMxN、嵌套上限等值域规则与
:func:`parse_abi_type` 保持一致。

错误约定（同输入稳定抛出同一异常，消息指出输入层级与字段，不含文件
路径）：

- 输入不是 dict、``type`` 缺失或不是字符串、tuple 缺 components 或
  components 不是列表：内置 :class:`TypeError`；
- 类型语法、整数位宽、bytesN、fixedMxN、数组长度非法，或非 tuple
  声明带有 components：:class:`abi_kit.RangeError`。
"""

from __future__ import annotations

from ._exceptions import ABITypeError, RangeError
from ._format import format_abi_type
from ._parser import parse_abi_type
from ._types import (
    ABIType,
    ArrayType,
    ElementaryType,
    FixedPointType,
    FunctionType,
    TupleType,
)

#: components 递归展开的安全上限，与事件/解析路径一致：语义嵌套上限
#: （128 层）由类型对象构造时检查，此阈值仅用于在触及 Python 递归
#: 限制前抛出确定的 RangeError。
_RECURSION_SAFETY_LIMIT = 200

_TUPLE_PREFIX = "tuple"


def _type_error(location: str, message: str) -> TypeError:
    return TypeError(f"{location}：{message}")


def _range_error(location: str, message: str) -> RangeError:
    return RangeError(f"{location}：{message}")


def _validate_tuple_suffix(type_string: str, location: str) -> str:
    """校验 ``tuple`` / ``tuple[...]`` 文本，返回数组后缀串（可能为空）。"""
    suffix = type_string[len(_TUPLE_PREFIX):]
    if not suffix:
        return ""
    index = 0
    length = len(suffix)
    while index < length:
        if suffix[index] != "[":
            raise _range_error(location, f"非法 tuple 类型文本：{type_string!r}")
        index += 1
        if index >= length:
            raise _range_error(location, f"数组后缀不完整：{type_string!r}")
        if suffix[index] == "]":
            index += 1
            continue
        # 定长后缀只接受 [1-9][0-9]*：拒绝 0 长度与前导零。
        if not suffix[index].isdigit() or suffix[index] == "0":
            raise _range_error(
                location, f"数组长度必须是无前导零的正整数：{type_string!r}"
            )
        while index < length and suffix[index].isdigit():
            index += 1
        if index >= length or suffix[index] != "]":
            raise _range_error(location, f"数组后缀缺少 ']'：{type_string!r}")
        index += 1
    return suffix


def _apply_suffixes(inner: ABIType, suffix: str, location: str) -> ABIType:
    """把 ``[]`` / ``[n]`` 后缀依次套到 inner 外层，构造数组类型对象。"""
    current = inner
    index = 0
    length = len(suffix)
    try:
        while index < length:
            # 循环不变量：suffix[index] == "["，语法已由
            # _validate_tuple_suffix 完整校验。
            index += 1
            if suffix[index] == "]":
                current = ArrayType(current)
                index += 1
            else:
                start = index
                while suffix[index].isdigit():
                    index += 1
                current = ArrayType(current, int(suffix[start:index]))
                index += 1  # 跳过 ']'
    except ABITypeError as exc:
        raise _range_error(location, f"数组类型构造失败：{exc}") from None
    return current


def _build_type(node, location: str, depth: int) -> ABIType:
    """从一个 ABI JSON 类型节点递归构造经过完整校验的类型对象。"""
    if depth > _RECURSION_SAFETY_LIMIT:
        raise _range_error(location, "components 嵌套过深")
    if not isinstance(node, dict):
        raise _type_error(
            location, f"类型声明必须是 JSON 对象，得到 {type(node).__name__}"
        )
    if "type" not in node:
        raise _type_error(location, "缺少 type 字段")
    type_string = node["type"]
    if not isinstance(type_string, str):
        raise _type_error(
            location, f"type 必须是字符串，得到 {type(type_string).__name__}"
        )
    if not type_string:
        raise _range_error(location, "type 不能为空字符串")

    if type_string == _TUPLE_PREFIX or type_string.startswith("tuple["):
        suffix = _validate_tuple_suffix(type_string, location)
        components_node = node.get("components")
        if not isinstance(components_node, list):
            if "components" not in node:
                raise _type_error(
                    location,
                    f"tuple 类型必须带有列表形式的 components：{type_string!r}",
                )
            raise _type_error(
                location,
                f"components 必须是数组，得到 {type(components_node).__name__}",
            )
        child_types = [
            _build_type(child, f"{location}.components[{index}]", depth + 1)
            for index, child in enumerate(components_node)
        ]
        try:
            current: ABIType = TupleType(tuple(child_types))
        except ABITypeError as exc:
            raise _range_error(location, f"tuple 类型构造失败：{exc}") from None
        if suffix:
            current = _apply_suffixes(current, suffix, location)
        return current

    if "components" in node:
        raise _range_error(
            location, f"非 tuple 类型不能带有 components：{type_string!r}"
        )

    # 非 tuple 路径：完全复用既有类型解析器，别名归一与全部语法/值域
    # 规则（位宽、bytesN、fixedMxN、数组长度、嵌套上限等）与其一致。
    try:
        return parse_abi_type(type_string)
    except ABITypeError as exc:
        raise _range_error(
            location, f"非法类型文本 {type_string!r}：{exc}"
        ) from None


# ---- 类型对象到诊断字典 ---------------------------------------------------


def _leaf_diagnostic(abi_type: ABIType) -> dict:
    """把非数组、非元组的叶子类型对象转为诊断字典。"""
    if isinstance(abi_type, ElementaryType):
        raw_kind = abi_type.kind
        is_dynamic = raw_kind == "string" or (
            raw_kind == "bytes" and abi_type.byte_size is None
        )
        kind = (
            "bytesN"
            if raw_kind == "bytes" and abi_type.byte_size is not None
            else raw_kind
        )
        return {
            "canonical": format_abi_type(abi_type),
            "kind": kind,
            "isDynamic": is_dynamic,
            "arrayLength": None,
            "base": None,
            "components": None,
        }
    if isinstance(abi_type, FixedPointType):
        return {
            "canonical": format_abi_type(abi_type),
            "kind": "fixed" if abi_type.signed else "ufixed",
            "isDynamic": False,
            "arrayLength": None,
            "base": None,
            "components": None,
        }
    if isinstance(abi_type, FunctionType):
        return {
            "canonical": "function",
            "kind": "function",
            "isDynamic": False,
            "arrayLength": None,
            "base": None,
            "components": None,
        }
    raise _range_error("根类型声明", f"不支持的叶子类型：{abi_type!r}")


def _diagnostic(abi_type: ABIType) -> dict:
    """把经过校验的类型对象递归转为诊断字典。"""
    if isinstance(abi_type, ArrayType):
        base_diagnostic = _diagnostic(abi_type.element_type)
        # 沿 base 链到达最内层元素：若是 tuple，则各层数组共享其同一组
        # 子诊断；否则各层 components 均为 None。
        innermost = base_diagnostic
        while innermost["kind"] == "array":
            innermost = innermost["base"]
        components = innermost["components"] if innermost["kind"] == "tuple" else None
        length = abi_type.length
        return {
            "canonical": format_abi_type(abi_type),
            "kind": "array",
            "isDynamic": length is None or base_diagnostic["isDynamic"],
            "arrayLength": length,
            "base": base_diagnostic,
            "components": components,
        }
    if isinstance(abi_type, TupleType):
        children = [_diagnostic(component) for component in abi_type.components]
        return {
            "canonical": format_abi_type(abi_type),
            "kind": "tuple",
            "isDynamic": any(child["isDynamic"] for child in children),
            "arrayLength": None,
            "base": None,
            "components": children,
        }
    return _leaf_diagnostic(abi_type)


# ---- 公开入口 -------------------------------------------------------------


def describeAbiType(node: dict) -> dict:
    """描述一个 ABI JSON 类型声明，返回纯字典布局诊断。

    典型用法（编码前预检）::

        describeAbiType({"type": "tuple[2]", "components": [
            {"type": "bytes32"}, {"type": "uint256"},
        ]})
        # {
        #   "canonical": "(bytes32,uint256)[2]",
        #   "kind": "array", "isDynamic": False, "arrayLength": 2,
        #   "base": { ...tuple 诊断... },
        #   "components": [ {...bytes32...}, {...uint256...} ],
        # }

    本入口只做声明校验与结构说明，不编解码、不读写文件；参数名与
    成员名不进入结果，同输入稳定得到同一结果。

    - 输入不是 dict、``type`` 缺失或不是字符串、tuple 缺 components 或
      components 不是数组时抛 :class:`TypeError`；
    - 类型文本语法、整数位宽、bytesN、fixedMxN、数组长度非法，或非
      tuple 声明带有 components 时抛 :class:`abi_kit.RangeError`。
    """
    if not isinstance(node, dict):
        raise TypeError(
            f"根类型声明必须是 JSON 对象，得到 {type(node).__name__}"
        )
    return _diagnostic(_build_type(node, "根类型声明", 1))
