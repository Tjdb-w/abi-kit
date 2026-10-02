"""ABI 类型字符串的解析与规范化。

公开入口：

- :func:`parse_abi_type`：把 ABI 类型字符串解析为不可变类型对象。
- :func:`format_abi_type`：把类型对象格式化为规范类型字符串。

层级计数（从基础类型向外，每增加一层数组或元组计一层，最多 128 层）
按最长链计算：元组中各兄弟组件的层数不累加。
"""

from __future__ import annotations

from .types import (
    ABIType,
    ABITypeError,
    ArrayType,
    ElementaryType,
    TupleType,
)

__all__ = ["parse_abi_type", "format_abi_type"]

_MAX_DEPTH = 128

# 无参数的基础类型
_PLAIN_ELEMENTARY = frozenset(
    {"address", "bool", "string", "bytes"}
)

# 允许的 ASCII 空白：空格、制表、换行、回车、换页、垂直制表
_WHITESPACE = frozenset(" \t\n\r\x0b\x0c")


def format_abi_type(abi_type: ABIType) -> str:
    """返回类型对象的规范类型字符串（不含任何空白）。"""
    if not isinstance(abi_type, ABIType):
        raise TypeError("abi_type 必须是 abi_kit.ABIType 实例")
    return abi_type.format()


def parse_abi_type(type_string: str) -> ABIType:
    """解析 ABI 类型字符串，返回不可变类型对象。

    支持的语法：

    - 基础类型：``uintM`` / ``intM``（``8 <= M <= 256`` 且为 8 的倍数）、
      ``address``、``bool``、``string``、``bytes``、``bytesM``
      （``1 <= M <= 32``）。
    - 数组：任意元素类型的 ``T[]``（变长）与 ``T[n]``（定长，``n`` 为
      无前导零的十进制正整数），可重复后缀。
    - 元组：任意深度的 ``(T1,T2,...)``，允许空元组 ``()``。

    字符串两端与元组逗号之间允许 ASCII 空白，空白不保留；其余位置出现
    空白或任何不合语法的写法均抛出 :class:`ABITypeError`。输入必须完整
    解析，尾随内容同样报错。
    """
    if not isinstance(type_string, str):
        raise ABITypeError("ABI 类型必须是字符串")

    parser = _Parser(type_string)
    result = parser.parse_top()
    return result


class _Parser:
    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0
        self._length = len(text)
        # 当前已解析到的层数（从基础类型向外计数）
        self._depth = 0

    # -- 公开的解析入口 -------------------------------------------------

    def parse_top(self) -> ABIType:
        self._skip_spaces()
        result = self._parse_type()
        self._skip_spaces()
        if self._pos != self._length:
            raise self._error("存在无法解析的尾随内容")
        return result

    # -- 内部方法 -------------------------------------------------------

    def _parse_type(self) -> ABIType:
        base = self._parse_base()
        return self._parse_array_suffixes(base)

    def _parse_base(self) -> ABIType:
        ch = self._peek()
        if ch is None:
            raise self._error("缺少类型")
        if ch == "(":
            return self._parse_tuple()
        return self._parse_elementary()

    def _parse_elementary(self) -> ElementaryType:
        start = self._pos
        while True:
            ch = self._peek()
            if ch is not None and (ch.isascii() and ch.isalnum()):
                self._pos += 1
                continue
            break
        name = self._text[start:self._pos]
        if not name:
            # 指针落在括号、空白或其他非字母数字字符上
            raise self._error("无效的基础类型")
        # 注意：名称后的空白不在此报错。字符串两端的空白合法，由
        # parse_top 收尾；若空白后还有 '[]' 等内容（如 "uint8 []"），
        # 后缀匹配失败后会以尾随内容统一报错。
        # 只接受小写字母与数字（首字符为字母），拒绝大写等写法
        if not name[0].isalpha() or any(
            not (c.islower() or c.isdigit()) for c in name
        ):
            raise self._error("未知的基础类型: {!r}".format(name))
        return self._build_elementary(name)

    def _build_elementary(self, name: str) -> ElementaryType:
        if name in _PLAIN_ELEMENTARY:
            return ElementaryType(name)

        if name.startswith(("uint", "int")):
            prefix = "uint" if name.startswith("uint") else "int"
            digits = name[len(prefix):]
            if not digits or not digits.isascii() or not digits.isdigit():
                raise self._error("未知的基础类型: {!r}".format(name))
            if len(digits) > 1 and digits[0] == "0":
                raise self._error(
                    "{} 位宽不允许前导零: {!r}".format(prefix, name)
                )
            width = int(digits)
            if width < 8 or width > 256 or width % 8 != 0:
                raise self._error(
                    "{} 位宽必须是 8 到 256 之间 8 的倍数: {!r}".format(
                        prefix, name
                    )
                )
            return ElementaryType(name, bit_width=width)

        if name.startswith("bytes"):
            digits = name[len("bytes"):]
            if not digits:
                # 已被 _PLAIN_ELEMENTARY 覆盖，此处仅为防御
                return ElementaryType("bytes")
            if not digits.isascii() or not digits.isdigit():
                raise self._error("未知的基础类型: {!r}".format(name))
            if len(digits) > 1 and digits[0] == "0":
                raise self._error(
                    "bytesM 字节宽度不允许前导零: {!r}".format(name)
                )
            width = int(digits)
            if width < 1 or width > 32:
                raise self._error(
                    "bytesM 的字节宽度必须在 1 到 32 之间: {!r}".format(name)
                )
            return ElementaryType(name, byte_width=width)

        raise self._error("未知的基础类型: {!r}".format(name))

    def _parse_tuple(self) -> TupleType:
        # 当前字符为 '('
        self._expect("(")
        # 元组外层（语法上先于本元组出现的包裹结构）已有的层数。
        baseline = self._depth
        max_child_depth = baseline
        components = []

        self._skip_spaces()
        if self._peek() == ")":
            self._pos += 1
        else:
            while True:
                # 元组各兄弟组件的层数互不累加：每个组件都从基线
                # 重新计数，最后取最深组件链，元组自身再加一层。
                self._depth = baseline
                component = self._parse_type()
                max_child_depth = max(max_child_depth, self._depth)
                components.append(component)

                self._skip_spaces()
                ch = self._peek()
                if ch == ",":
                    self._pos += 1
                    self._skip_spaces()
                    # 逗号之后必须还有类型，"(uint8,)" 非法
                    if self._peek() in (None, ",", ")"):
                        raise self._error("元组逗号后缺少类型")
                    continue
                if ch == ")":
                    self._pos += 1
                    break
                raise self._error("元组中应为 ',' 或 ')'")

        self._depth = max_child_depth
        self._enter_layer()
        return TupleType(tuple(components))

    def _parse_array_suffixes(self, element_type: ABIType) -> ABIType:
        current = element_type
        while self._peek() == "[":
            # '[' 之前不允许空白：_parse_elementary 已排除类型词后的
            # 空白；元组 ')' 后若有空白，'[' 匹配不到，最终尾随报错。
            self._pos += 1  # 消费 '['
            # 括号语法内部不允许空白
            if self._peek() in _WHITESPACE:
                raise self._error("数组语法中不允许空白")
            length = None
            if self._peek() != "]":
                length = self._parse_array_length()
            if self._peek() != "]":
                raise self._error("数组缺少 ']'")
            self._pos += 1  # 消费 ']'
            current = ArrayType(current, length)
            self._enter_layer()
        return current

    def _parse_array_length(self) -> int:
        start = self._pos
        ch = self._peek()
        if ch is None or not (ch.isascii() and ch.isdigit()):
            raise self._error("数组长度必须是十进制正整数")
        if ch == "0":
            self._pos += 1
            # "0" 本身非法；前导零（如 "01"）也非法
            raise self._error("数组长度必须是无前导零的正整数")
        while True:
            ch = self._peek()
            if ch is not None and ch.isascii() and ch.isdigit():
                self._pos += 1
                continue
            break
        return int(self._text[start:self._pos])

    def _enter_layer(self) -> None:
        self._depth += 1
        if self._depth > _MAX_DEPTH:
            raise self._error(
                "类型嵌套层数超过上限 {} 层".format(_MAX_DEPTH)
            )

    def _skip_spaces(self) -> None:
        while self._pos < self._length and self._text[self._pos] in _WHITESPACE:
            self._pos += 1

    def _peek(self) -> "str | None":
        if self._pos >= self._length:
            return None
        return self._text[self._pos]

    def _expect(self, expected: str) -> None:
        ch = self._peek()
        if ch != expected:
            if ch is None:
                raise self._error("缺少 {!r}".format(expected))
            raise self._error(
                "应为 {!r}，实际为 {!r}".format(expected, ch)
            )
        self._pos += 1

    def _error(self, message: str) -> ABITypeError:
        return ABITypeError(
            "无效的 ABI 类型 {!r}: {}".format(self._text, message)
        )
