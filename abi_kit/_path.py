"""嵌套值的路径化读取与定点替换。

在完整值编解码（:mod:`abi_kit._codec`）之上提供三个面向调用方的入口：

- :func:`get_abi_value_at_path`：按 ABI 类型、完整编码字节与路径，只返回
  路径选中的子值；
- :func:`replace_abi_value_at_path`：在相同类型与原始编码下，用替换值
  定点替换路径所指子值，返回整体重新编码后的全新字节。原字节不会被就地
  修改；动态偏移与长度随结果重新计算，替换前后动态数据长度不同也不会
  影响未选中的兄弟节点；
- :func:`replace_abi_values_at_paths`：一次调用按顺序给出多对路径与新值，
  基于同一份原始编码原子地替换多个互不重叠的子值，返回整体重新编码后的
  全新字节；空的替换序列返回与输入内容相同的新 bytes，路径重复或互为
  祖先与后代时拒绝整次操作。

路径语法：

- 点号 ``.`` 连接 tuple 字段名，字段名匹配类型中声明的名称；
- 方括号内的非负十进制数字（从 0 开始）选择数组元素，也可按位置选择
  tuple 元素；
- 空路径表示整个根值；
- 不接受空段、空字段名、负数、带符号或带空格索引及多余分隔符。

tuple 字段名通过两种方式提供：直接构造带 ``names`` 的
:class:`abi_kit.TupleType`，或向本模块传入 Solidity 声明风格的类型字符串
（组件类型后可空白跟一个字段名），例如::

    (uint256 amount,(address to,uint256 value)[] items)

该带名语法仅用于本模块，:func:`abi_kit.parse_abi_type` 的严格规范语法保持
不变。

五类单路径失败统一抛出带唯一错误码的 :class:`abi_kit.AbiPathError`：

- ``PATH_SYNTAX`` / ``PATH_OUT_OF_RANGE`` / ``PATH_NOT_FOUND`` /
  ``PATH_TYPE_MISMATCH`` / ``PATH_VALUE_MISMATCH``。

批量替换另有两类失败：

- ``PATH_REPLACEMENTS_INVALID``：替换序列不是 list/tuple，或元素不是
  恰好含路径与新值的二元 list/tuple；
- ``PATH_CONFLICT``：两条路径指向同一节点或互为祖先与后代。

原始编码本身非法时仍按值层约定抛出 :class:`abi_kit.ABIValueError`，不属于
上述五类路径失败。
"""

from __future__ import annotations

from ._codec import _encode, decode_abi_value
from ._exceptions import ABITypeError, ABIValueError, AbiPathError
from ._types import ABIType, ArrayType, ElementaryType, TupleType

_WHITESPACE = " \t\n\r\f\v"
_NAME_START = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$"
)
_NAME_PART = _NAME_START | set("0123456789")

PATH_SYNTAX = "PATH_SYNTAX"
PATH_OUT_OF_RANGE = "PATH_OUT_OF_RANGE"
PATH_NOT_FOUND = "PATH_NOT_FOUND"
PATH_TYPE_MISMATCH = "PATH_TYPE_MISMATCH"
PATH_VALUE_MISMATCH = "PATH_VALUE_MISMATCH"
PATH_REPLACEMENTS_INVALID = "PATH_REPLACEMENTS_INVALID"
PATH_CONFLICT = "PATH_CONFLICT"


# ---- 带字段名的类型字符串 -------------------------------------------------


class _NamedParser:
    """解析带可选组件名的类型字符串（Solidity 声明风格）。

    与 :func:`abi_kit.parse_abi_type` 使用同一套类型校验（构造相同的不可变
    类型对象），区别仅在于 tuple 组件后允许空白跟一个字段名。
    """

    _RECURSION_SAFETY_LIMIT = 200

    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0
        self._len = len(text)
        self._tuple_depth = 0

    def _error(self, message: str) -> ABITypeError:
        return ABITypeError(
            f"无效的 ABI 类型 {self._text!r}（位置 {self._pos}）：{message}"
        )

    def _skip_whitespace(self) -> None:
        text = self._text
        while self._pos < self._len and text[self._pos] in _WHITESPACE:
            self._pos += 1

    def _peek(self) -> str | None:
        if self._pos >= self._len:
            return None
        return self._text[self._pos]

    def parse(self) -> ABIType:
        self._skip_whitespace()
        result = self._parse_type()
        self._skip_whitespace()
        if self._pos != self._len:
            raise self._error("存在无法解析的尾随内容")
        return result

    def _parse_type(self) -> ABIType:
        ch = self._peek()
        if ch is None:
            raise self._error("类型不完整")
        if ch == "(":
            head: ABIType = self._parse_tuple()
        elif ch in _NAME_START:
            head = self._parse_elementary()
        else:
            raise self._error("此处应为基础类型或元组")
        return self._parse_suffixes(head)

    def _parse_elementary(self) -> ElementaryType:
        text = self._text
        start = self._pos
        while self._pos < self._len:
            ch = text[self._pos]
            if ch.isascii() and ch.isalnum():
                self._pos += 1
            else:
                break
        word = text[start:self._pos]
        if word in ("address", "bool", "string", "bytes"):
            return ElementaryType(word)
        if word.startswith("bytes") and len(word) > len("bytes"):
            digits = self._digits(word, "bytes")
            try:
                return ElementaryType("bytes", byte_size=int(digits))
            except ABITypeError:
                raise self._error(
                    f"bytesM 的 M 必须在 1 到 32 之间，得到 {digits}"
                ) from None
        if word.startswith("uint") or word.startswith("int"):
            kind = "uint" if word.startswith("uint") else "int"
            prefix = kind
            digits = self._digits(word, prefix)
            try:
                return ElementaryType(kind, bit_size=int(digits))
            except ABITypeError:
                raise self._error(
                    f"{kind} 的位宽必须是 8 到 256 之间 8 的倍数，得到 {digits}"
                ) from None
        raise self._error(f"不是合法的基础类型：{word!r}")

    def _digits(self, word: str, prefix: str) -> str:
        digits = word[len(prefix):]
        if not digits or not digits.isascii() or not digits.isdigit():
            raise self._error(f"不是合法的基础类型：{word!r}")
        if len(digits) > 1 and digits[0] == "0":
            raise self._error(f"类型宽度不允许前导零：{word!r}")
        return digits

    def _parse_tuple(self) -> TupleType:
        self._pos += 1
        self._tuple_depth += 1
        if self._tuple_depth > self._RECURSION_SAFETY_LIMIT:
            self._tuple_depth -= 1
            raise self._error("类型嵌套过深或括号未正确闭合")
        try:
            self._skip_whitespace()
            if self._peek() == ")":
                self._pos += 1
                return TupleType(())
            component_types: list[ABIType] = []
            component_names: list[str | None] = []
            while True:
                component_types.append(self._parse_type())
                self._skip_whitespace()
                component_names.append(self._read_optional_name())
                self._skip_whitespace()
                ch = self._peek()
                if ch == ",":
                    self._pos += 1
                    self._skip_whitespace()
                    if self._peek() in (None, ",", ")"):
                        raise self._error("逗号后缺少类型")
                    continue
                if ch == ")":
                    self._pos += 1
                    names = (
                        tuple(component_names)
                        if any(name is not None for name in component_names)
                        else None
                    )
                    return TupleType(tuple(component_types), names)
                raise self._error("元组中应为 ',' 或 ')'")
        finally:
            self._tuple_depth -= 1

    def _read_optional_name(self) -> str | None:
        """读取紧跟在组件类型之后的可选字段名；调用前已跳过空白。"""
        ch = self._peek()
        if ch is None or ch not in _NAME_START:
            return None
        start = self._pos
        self._pos += 1
        while self._pos < self._len and self._text[self._pos] in _NAME_PART:
            self._pos += 1
        name = self._text[start:self._pos]
        self._skip_whitespace()
        if self._peek() not in (",", ")", None):
            raise self._error(f"字段名 {name!r} 后应为 ',' 或 ')'")
        return name

    def _parse_suffixes(self, head: ABIType) -> ABIType:
        current = head
        while self._peek() == "[":
            self._pos += 1
            ch = self._peek()
            if ch is None:
                raise self._error("数组后缀不完整")
            if ch == "]":
                self._pos += 1
                current = ArrayType(current)
                continue
            if not (ch.isascii() and ch.isdigit()) or ch == "0":
                raise self._error("数组长度必须是无前导零的正整数")
            start = self._pos
            while self._pos < self._len:
                ch = self._text[self._pos]
                if ch.isascii() and ch.isdigit():
                    self._pos += 1
                    continue
                break
            length = int(self._text[start:self._pos])
            if self._peek() != "]":
                raise self._error("数组后缀缺少 ']'")
            self._pos += 1
            current = ArrayType(current, length)
        return current


def _coerce_type(abi_type: ABIType | str) -> ABIType:
    if isinstance(abi_type, ABIType):
        return abi_type
    if isinstance(abi_type, str):
        return _NamedParser(abi_type).parse()
    raise ABITypeError(
        f"ABI 类型必须是 ABIType 或类型字符串，得到 {type(abi_type).__name__}"
    )


def _coerce_data(data: bytes | str) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        text = data[2:] if data[:2] in ("0x", "0X") else data
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise ABIValueError(f"编码十六进制字节非法：{exc}") from exc
    raise ABIValueError(
        f"编码数据必须是 bytes 或十六进制 str，得到 {type(data).__name__}"
    )


# ---- 路径语法 -------------------------------------------------------------


class _Field:
    """tuple 字段名步进。"""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _Index:
    """数组或 tuple 的位置步进；保存数字原文，导航时按字符串比较越界。"""

    __slots__ = ("digits",)

    def __init__(self, digits: str) -> None:
        self.digits = digits

    def exceeds(self, bound: int) -> bool:
        """该索引是否 >= bound；不经过大整数转换，无位数上限。"""
        target = str(bound)
        digits = self.digits
        if len(digits) != len(target):
            return len(digits) > len(target)
        return digits >= target

    @property
    def display(self) -> str:
        return self.digits if len(self.digits) <= 32 else self.digits[:29] + "..."


def _parse_path(path) -> tuple[object, ...]:
    if not isinstance(path, str):
        raise AbiPathError(PATH_SYNTAX, f"路径必须是 str，得到 {type(path).__name__}")
    if path == "":
        return ()

    steps: list[object] = []
    pos = 0
    length = len(path)

    def read_name(start: int) -> tuple[str, int] | None:
        if start >= length or path[start] not in _NAME_START:
            return None
        end = start + 1
        while end < length and path[end] in _NAME_PART:
            end += 1
        return path[start:end], end

    while pos < length:
        ch = path[pos]
        if ch == "[":
            close = path.find("]", pos + 1)
            if close == -1:
                raise AbiPathError(PATH_SYNTAX, "数组索引缺少闭合的 ']'")
            digits = path[pos + 1:close]
            if (
                not digits
                or not digits.isascii()
                or not digits.isdigit()
                or (len(digits) > 1 and digits[0] == "0")
            ):
                raise AbiPathError(
                    PATH_SYNTAX, f"方括号内必须是非负十进制整数：{digits!r}"
                )
            steps.append(_Index(digits))
            pos = close + 1
            if pos < length and path[pos] not in (".", "["):
                raise AbiPathError(PATH_SYNTAX, "索引之后只允许 '.'、'[' 或结束")
            continue
        if ch == ".":
            if pos == 0:
                raise AbiPathError(PATH_SYNTAX, "路径不允许以 '.' 开头")
            pos += 1
            if pos >= length:
                raise AbiPathError(PATH_SYNTAX, "路径不允许以 '.' 结尾")
            read = read_name(pos)
            if read is None or path[pos] == ".":
                raise AbiPathError(PATH_SYNTAX, "点号后缺少字段名")
            name, pos = read
            steps.append(_Field(name))
            if pos < length and path[pos] not in (".", "["):
                raise AbiPathError(PATH_SYNTAX, "字段名之后只允许 '.'、'[' 或结束")
            continue
        read = read_name(pos)
        if read is None:
            raise AbiPathError(PATH_SYNTAX, f"位置 {pos} 处不是合法字段名")
        name, pos = read
        steps.append(_Field(name))
        if pos < length and path[pos] not in (".", "["):
            raise AbiPathError(PATH_SYNTAX, "字段名之后只允许 '.'、'[' 或结束")

    return tuple(steps)


# ---- 导航 -----------------------------------------------------------------


def _navigate(
    root_type: ABIType, root_value, steps: tuple[object, ...]
) -> tuple[ABIType, object, list[int]]:
    """按步骤在已解码值上前进。

    返回 ``(目标类型, 目标值, 位置路径)``；位置路径把字段名解析为 tuple
    组件下标，因而每一级都是可直接重放的整数下标。
    """
    current_type = root_type
    current_value = root_value
    positions: list[int] = []

    for step in steps:
        if isinstance(step, _Field):
            name = step.name
            if not isinstance(current_type, TupleType):
                kind = "数组" if isinstance(current_type, ArrayType) else "基础类型值"
                raise AbiPathError(
                    PATH_TYPE_MISMATCH,
                    f"不能对{kind}按字段名 {name!r} 步进",
                )
            names = current_type.names
            index = -1
            if names is not None:
                for candidate_index, candidate in enumerate(names):
                    if candidate == name:
                        index = candidate_index
                        break
            if index < 0:
                raise AbiPathError(
                    PATH_NOT_FOUND, f"tuple 中不存在名为 {name!r} 的字段"
                )
            positions.append(index)
            current_type = current_type.components[index]
            current_value = current_value[index]
            continue

        if isinstance(current_type, TupleType):
            bound = len(current_type.components)
            if step.exceeds(bound):
                raise AbiPathError(
                    PATH_OUT_OF_RANGE,
                    f"tuple 位置索引 {step.display} 超出范围 [0, {bound - 1}]",
                )
            index = int(step.digits)
            positions.append(index)
            current_type = current_type.components[index]
            current_value = current_value[index]
        elif isinstance(current_type, ArrayType):
            bound = len(current_value)
            if step.exceeds(bound):
                raise AbiPathError(
                    PATH_OUT_OF_RANGE,
                    f"数组索引 {step.display} 超出长度 {bound}",
                )
            index = int(step.digits)
            positions.append(index)
            current_type = current_type.element_type
            current_value = current_value[index]
        else:
            raise AbiPathError(
                PATH_TYPE_MISMATCH,
                f"不能对基础类型值按索引 [{step.display}] 步进",
            )

    return current_type, current_value, positions


def _set_at(root_value, positions: list[int], new_value):
    """沿整数下标路径不可变地写入，返回新的根值，不改动原容器。"""
    if not positions:
        return new_value
    index = positions[0]
    if isinstance(root_value, tuple):
        sequence = list(root_value)
        sequence[index] = _set_at(sequence[index], positions[1:], new_value)
        return tuple(sequence)
    # 数组在值口径中为 list；逐层复制以保证原 list 不被修改。
    sequence = list(root_value)
    sequence[index] = _set_at(sequence[index], positions[1:], new_value)
    return sequence


# ---- 公开入口 -------------------------------------------------------------


def _prepare(abi_type, data, path):
    resolved_type = _coerce_type(abi_type)
    raw = _coerce_data(data)
    steps = _parse_path(path)
    root_value = decode_abi_value(resolved_type, raw)
    return resolved_type, root_value, steps


def get_abi_value_at_path(abi_type: ABIType | str, data: bytes | str, path: str):
    """按路径从完整编码中读取选中的子值。

    ``abi_type`` 可以是 :class:`abi_kit.ABIType`，也可以是带组件名的类型
    字符串；``data`` 接受 bytes 或可选 ``0x`` 前缀的十六进制 str。返回值
    沿用完整值编解码的值口径（int/bool/str/bytes/address 十六进制串/
    list/tuple），且只包含路径选中的子结构本身。

    路径失败抛出带错误码的 :class:`abi_kit.AbiPathError`；原始编码非法抛出
    :class:`abi_kit.ABIValueError`。
    """
    resolved_type, root_value, steps = _prepare(abi_type, data, path)
    _target_type, target_value, _positions = _navigate(
        resolved_type, root_value, steps
    )
    return target_value


def replace_abi_value_at_path(
    abi_type: ABIType | str, data: bytes | str, path: str, value
) -> bytes:
    """按路径定点替换子值，返回整体重新编码后的全新 bytes。

    原始 ``data`` 不会被就地修改。替换值必须与路径所指的 ABI 类型一致，
    否则抛出 ``PATH_VALUE_MISMATCH``；动态偏移与长度统一重新计算，未选中
    的兄弟节点值保持不变。

    其余错误约定同 :func:`get_abi_value_at_path`。
    """
    resolved_type, root_value, steps = _prepare(abi_type, data, path)
    target_type, _target_value, positions = _navigate(
        resolved_type, root_value, steps
    )

    # 先用目标类型单独校验替换值，把值不匹配归类为 PATH_VALUE_MISMATCH；
    # 兄弟节点均来自既有合法编码，整树重编码不会再引入值层错误。
    try:
        _encode(target_type, value)
    except ABIValueError as exc:
        raise AbiPathError(
            PATH_VALUE_MISMATCH,
            f"替换值与路径所指类型不一致：{exc}",
        ) from exc

    new_root = _set_at(root_value, positions, value) if positions else value
    return _encode(resolved_type, new_root)


def replace_abi_values_at_paths(
    abi_type: ABIType | str, data: bytes | str, replacements
) -> bytes:
    """一次调用原子地替换多条路径所指的子值，返回整体重新编码的新 bytes。

    ``replacements`` 必须是 list 或 tuple，每个元素是恰好含
    ``(路径, 新值)`` 的二元 list/tuple；路径语法与
    :func:`replace_abi_value_at_path` 完全一致。所有替换基于同一份原始
    编码解码出的值树，互不重叠地写入后一次性重新编码：动态偏移、数组
    长度与内层结构布局统一重算，未被选中的兄弟值保持不变，原始 ``data``
    不会被就地修改。空的替换序列返回与输入内容相同的新 bytes。

    替换序列结构不合法抛出 ``PATH_REPLACEMENTS_INVALID``；同一条路径
    出现两次，或两条路径互为祖先与后代（含空路径根值与任何其他路径），
    抛出 ``PATH_CONFLICT`` 并拒绝整次操作。路径语法、越界、字段缺失、
    步进类型与替换值类型失败分别沿用 ``PATH_SYNTAX`` /
    ``PATH_OUT_OF_RANGE`` / ``PATH_NOT_FOUND`` / ``PATH_TYPE_MISMATCH`` /
    ``PATH_VALUE_MISMATCH``；原始编码非法仍抛 :class:`abi_kit.ABIValueError`。
    """
    if not isinstance(replacements, (list, tuple)):
        raise AbiPathError(
            PATH_REPLACEMENTS_INVALID,
            f"替换序列必须是 list 或 tuple，得到 {type(replacements).__name__}",
        )
    pairs: list[tuple[object, object]] = []
    for index, item in enumerate(replacements):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise AbiPathError(
                PATH_REPLACEMENTS_INVALID,
                f"替换序列第 {index} 项必须是恰好含 (路径, 新值) 的二元 "
                f"list/tuple，得到 {item!r}",
            )
        pairs.append((item[0], item[1]))

    resolved_type = _coerce_type(abi_type)
    raw = _coerce_data(data)
    root_value = decode_abi_value(resolved_type, raw)

    # 先按顺序解析并导航全部路径，把字段名解析成整数位置路径；同节点的
    # 不同写法（字段名与位置下标）由此归一为同一位置序列。
    resolved: list[tuple[str, list[int], ABIType, object]] = []
    for path, value in pairs:
        steps = _parse_path(path)
        target_type, _target_value, positions = _navigate(
            resolved_type, root_value, steps
        )
        resolved.append((path, positions, target_type, value))

    # 重叠写入检测：位置路径相等（同一节点）或互为前缀（祖先与后代）即
    # 冲突，整次操作拒绝，不应用任何一项。
    for later in range(len(resolved)):
        later_path, later_positions = resolved[later][0], resolved[later][1]
        for earlier in range(later):
            earlier_path, earlier_positions = (
                resolved[earlier][0],
                resolved[earlier][1],
            )
            if _paths_overlap(earlier_positions, later_positions):
                raise AbiPathError(
                    PATH_CONFLICT,
                    f"路径 {earlier_path!r} 与 {later_path!r} 存在重叠写入",
                )

    # 冲突排除后再逐项校验替换值，把值不匹配归类为 PATH_VALUE_MISMATCH；
    # 兄弟节点均来自既有合法编码，整树重编码不会再引入值层错误。
    for _path, _positions, target_type, value in resolved:
        try:
            _encode(target_type, value)
        except ABIValueError as exc:
            raise AbiPathError(
                PATH_VALUE_MISMATCH,
                f"替换值与路径所指类型不一致：{exc}",
            ) from exc

    new_root = root_value
    for _path, positions, _target_type, value in resolved:
        new_root = _set_at(new_root, positions, value) if positions else value
    return _encode(resolved_type, new_root)


def _paths_overlap(first: list[int], second: list[int]) -> bool:
    """两条位置路径是否指向同一节点或互为祖先与后代。"""
    shared = min(len(first), len(second))
    return first[:shared] == second[:shared]
