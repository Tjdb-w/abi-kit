"""嵌套值的路径化读取与定点替换。

公开入口：
- abi_get_at_path(abi_type, data, path) -> 选中的子值
- abi_replace_at_path(abi_type, data, path, replacement) -> 替换后的完整编码 bytes

路径语法：
- 点号 ``.`` 连接 tuple 字段名；
- 方括号内非负十进制数字选择 tuple 或数组元素（从 0 开始）；
- 数组下标可直接跟在字段或另一对方括号之后（``items[2]``、``a[0][1]``），
  进入字段则必须用点号；
- 空路径表示整个根值。

读取返回的子值与 :func:`abi_kit.decode_abi_value` 完全同口径（int/bool/str/
bytes/address 十六进制串/list/tuple），且只包含被选中的子结构。替换不就地
修改原始编码字节：先解码、在新构造的值树上定点替换，再整体重新编码，因此
动态偏移与长度会随替换内容重新计算，未选中兄弟节点的值保持不变。

五类失败统一抛出 :class:`abi_kit.AbiPathError`，其 ``code`` 分别为
PATH_SYNTAX / PATH_OUT_OF_RANGE / PATH_NOT_FOUND / PATH_TYPE_MISMATCH /
PATH_VALUE_MISMATCH；编码字节本身非法仍按值层约定抛出 ABIValueError。
"""

from __future__ import annotations

from ._codec import decode_abi_value, encode_abi_value
from ._exceptions import ABIValueError, AbiPathError
from ._parser import parse_abi_type
from ._types import ABIType, ArrayType, ElementaryType, TupleType

_TAG_FIELD = "field"
_TAG_INDEX = "index"


# ---- 路径语法 -----------------------------------------------------------


def _is_ident_start(ch: str) -> bool:
    return ch.isascii() and (ch.isalpha() or ch in "_$")


def _is_ident_part(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "_$")


def _is_digit(ch: str) -> bool:
    return ch in "0123456789"


def _parse_path(path: object) -> list[tuple[str, object]]:
    """把路径字符串解析为 ``(种类, 负载)`` 序列；非法即 PATH_SYNTAX。"""
    if not isinstance(path, str) or isinstance(path, bool):
        raise AbiPathError(
            AbiPathError.PATH_SYNTAX,
            f"路径必须是字符串，得到 {type(path).__name__}",
        )
    text = path
    n = len(text)
    segments: list[tuple[str, object]] = []
    i = 0
    while i < n:
        ch = text[i]
        if ch == "[":
            # 索引：方括号内至少一位 ASCII 十进制数字，且必须以 ']' 收尾。
            j = i + 1
            digit_start = j
            while j < n and _is_digit(text[j]):
                j += 1
            if j == digit_start or j >= n or text[j] != "]":
                raise AbiPathError(
                    AbiPathError.PATH_SYNTAX,
                    f"方括号索引必须是非负十进制整数：{path!r}",
                )
            segments.append((_TAG_INDEX, int(text[digit_start:j])))
            i = j + 1
        elif _is_ident_start(ch):
            j = i + 1
            while j < n and _is_ident_part(text[j]):
                j += 1
            segments.append((_TAG_FIELD, text[i:j]))
            i = j
        else:
            # 前导点号、多余点号、裸数字、空格或其它分隔符都落到这里。
            raise AbiPathError(
                AbiPathError.PATH_SYNTAX,
                f"路径包含非法段或多余分隔符：{path!r}",
            )
        # 一个段之后只允许：直接跟 '['（继续下标）或 '.' 后接字段名。
        if i < n:
            ch = text[i]
            if ch == "[":
                continue
            if ch == ".":
                i += 1
                if i >= n or not _is_ident_start(text[i]):
                    raise AbiPathError(
                        AbiPathError.PATH_SYNTAX,
                        f"点号后必须紧跟字段名：{path!r}",
                    )
                continue
            raise AbiPathError(
                AbiPathError.PATH_SYNTAX,
                f"路径段之间缺少合法分隔符：{path!r}",
            )
    return segments


# ---- 类型与数据规整 ------------------------------------------------------


def _coerce_type(abi_type: object) -> ABIType:
    if isinstance(abi_type, ABIType):
        return abi_type
    if isinstance(abi_type, str):
        return parse_abi_type(abi_type)
    raise ABIValueError(f"不是合法的 ABI 类型对象：{abi_type!r}")


def _coerce_data(data: object) -> bytes:
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    raise ABIValueError(
        f"编码数据必须是 bytes 类型，得到 {type(data).__name__}"
    )


# ---- 沿路径步进 ----------------------------------------------------------


def _resolve(
    abi_type: ABIType, value: object, segments: list[tuple[str, object]]
) -> tuple[ABIType, object, list[int]]:
    """沿类型与已解码值同步步进，返回 (目标类型, 目标值, 整数键路径)。"""
    current_type = abi_type
    current_value = value
    keys: list[int] = []
    for tag, payload in segments:
        if tag == _TAG_FIELD:
            name = payload
            if isinstance(current_type, TupleType):
                names = current_type.names
                position = -1
                if names is not None:
                    for idx, candidate in enumerate(names):
                        if candidate == name:
                            position = idx
                            break
                if position < 0:
                    raise AbiPathError(
                        AbiPathError.PATH_NOT_FOUND,
                        f"tuple 中不存在字段 {name!r}",
                    )
                keys.append(position)
                current_value = current_value[position]
                current_type = current_type.components[position]
            elif isinstance(current_type, ArrayType):
                # 数组是容器但只有数字下标、没有命名字段：该字段在此不存在。
                raise AbiPathError(
                    AbiPathError.PATH_NOT_FOUND,
                    f"数组没有命名字段 {name!r}",
                )
            else:
                raise AbiPathError(
                    AbiPathError.PATH_TYPE_MISMATCH,
                    f"基础类型是非容器值，不能按字段 {name!r} 步进",
                )
        else:
            index = payload
            if isinstance(current_type, ArrayType):
                length = len(current_value)
                if not 0 <= index < length:
                    raise AbiPathError(
                        AbiPathError.PATH_OUT_OF_RANGE,
                        f"数组索引 {index} 越界（长度 {length}）",
                    )
                keys.append(index)
                current_value = current_value[index]
                current_type = current_type.element_type
            elif isinstance(current_type, TupleType):
                length = len(current_type.components)
                if not 0 <= index < length:
                    raise AbiPathError(
                        AbiPathError.PATH_OUT_OF_RANGE,
                        f"tuple 位置 {index} 越界（共 {length} 个组成部分）",
                    )
                keys.append(index)
                current_value = current_value[index]
                current_type = current_type.components[index]
            else:
                raise AbiPathError(
                    AbiPathError.PATH_TYPE_MISMATCH,
                    "数字索引只能作用于数组或 tuple，当前是基础类型",
                )
    return current_type, current_value, keys


def _apply_keys(root: object, keys: list[int], replacement: object) -> object:
    """在不可变/可变嵌套值树上做一次纯函数式定点替换，返回新根。"""
    if not keys:
        return replacement
    key = keys[0]
    child_keys = keys[1:]
    if isinstance(root, tuple):
        child = _apply_keys(root[key], child_keys, replacement)
        return root[:key] + (child,) + root[key + 1:]
    # 数组解码为 list：复制后再改，不动原始解码结果。
    seq = list(root)
    seq[key] = _apply_keys(seq[key], child_keys, replacement)
    return seq


# ---- 公开入口 -----------------------------------------------------------


def abi_get_at_path(abi_type: object, data: object, path: object):
    """按路径从 ABI 编码中读取选中的子值。

    参数与 :func:`abi_kit.decode_abi_value` 同口径；``abi_type`` 可以是已解析
    的 ABIType，也可以直接给类型字符串。空路径返回整个根值。失败抛出
    :class:`abi_kit.AbiPathError`，编码字节非法时抛出 ABIValueError。
    """
    target_type = _coerce_type(abi_type)
    blob = _coerce_data(data)
    segments = _parse_path(path)
    root_value = decode_abi_value(target_type, blob)
    _, target_value, _ = _resolve(target_type, root_value, segments)
    return target_value


def abi_replace_at_path(
    abi_type: object, data: object, path: object, replacement: object
) -> bytes:
    """按路径定点替换子值，返回替换后完整编码的全新 bytes。

    原始 ``data`` 不会被就地修改；替换值必须与路径所指类型一致，否则抛出
    code 为 PATH_VALUE_MISMATCH 的 :class:`abi_kit.AbiPathError`。动态偏移与
    长度随结果整体重算，替换前后动态数据长度不同也不影响未选中兄弟节点。
    """
    target_type = _coerce_type(abi_type)
    blob = _coerce_data(data)
    segments = _parse_path(path)
    root_value = decode_abi_value(target_type, blob)
    leaf_type, _, keys = _resolve(target_type, root_value, segments)
    # 先用既有编码器校验替换值与目标类型一致（同时覆盖所有基础类型口径）。
    try:
        encode_abi_value(leaf_type, replacement)
    except ABIValueError as exc:
        raise AbiPathError(
            AbiPathError.PATH_VALUE_MISMATCH,
            f"替换值与路径所指类型不匹配：{exc}",
        ) from exc
    new_root = _apply_keys(root_value, keys, replacement)
    return encode_abi_value(target_type, new_root)
