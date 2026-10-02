"""ABI 类型字符串的递归下降解析器。

语法（空白规则见下）::

    type       := elementary | tuple ，后接零个或多个数组后缀
    elementary := uintM | intM | address | bool | string | bytes | bytesM
    tuple      := '(' [type (',' type)*] ')'
    suffix     := '[]' | '[' [1-9][0-9]* ']'

空白（仅 ASCII 空白）只允许出现在：
1. 整个类型字符串的两端；
2. 元组中逗号的两侧。
其余位置的空白（类型词内部、括号内侧、数组语法内部等）一律报错。
输入必须完整解析，尾随任何非空白字符均报错。
"""

from __future__ import annotations

from ._exceptions import ABITypeError
from ._types import ABIType, ArrayType, ElementaryType, TupleType

_WHITESPACE = " \t\n\r\f\v"

#: 解析器括号递归的安全上限：语义嵌套上限（128 层）由类型对象构造时
#: 检查；此阈值仅用于在触及 Python 递归限制（实测约 500 层括号）前抛出
#: ABITypeError，远低于递归上限且高于任何合法输入。
_RECURSION_SAFETY_LIMIT = 200


class _Parser:
    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0
        self._len = len(text)
        self._tuple_depth = 0

    def parse(self) -> ABIType:
        self._skip_whitespace()
        result = self._parse_type()
        self._skip_whitespace()
        if self._pos != self._len:
            raise self._error("存在无法解析的尾随内容")
        return result

    # ---- 通用构件 -------------------------------------------------------

    def _error(self, message: str) -> ABITypeError:
        return ABITypeError(f"无效的 ABI 类型 {self._text!r}（位置 {self._pos}）：{message}")

    def _skip_whitespace(self) -> None:
        text = self._text
        while self._pos < self._len and text[self._pos] in _WHITESPACE:
            self._pos += 1

    def _peek(self) -> str | None:
        if self._pos >= self._len:
            return None
        return self._text[self._pos]

    # ---- 类型 -----------------------------------------------------------

    def _parse_type(self) -> ABIType:
        ch = self._peek()
        if ch is None:
            raise self._error("类型不完整")
        if ch == "(":
            head: ABIType = self._parse_tuple()
        elif ch.isascii() and ch.isalpha():
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
        return self._classify(word)

    def _classify(self, word: str) -> ElementaryType:
        if word in ("address", "bool", "string"):
            return ElementaryType(word)
        if word == "bytes":
            return ElementaryType("bytes")
        if word.startswith("bytes") and len(word) > len("bytes"):
            return self._sized_bytes(word)
        if word.startswith("uint") or word.startswith("int"):
            return self._sized_integer(word)
        raise self._error(f"不是合法的基础类型：{word!r}")

    def _digits_suffix(self, word: str, prefix: str) -> str:
        digits = word[len(prefix):]
        if not digits or not digits.isascii() or not digits.isdigit():
            raise self._error(f"不是合法的基础类型：{word!r}")
        if len(digits) > 1 and digits[0] == "0":
            raise self._error(f"类型宽度不允许前导零：{word!r}")
        return digits

    def _sized_integer(self, word: str) -> ElementaryType:
        if word.startswith("uint"):
            prefix, kind = "uint", "uint"
        else:
            prefix, kind = "int", "int"
        digits = self._digits_suffix(word, prefix)
        try:
            return ElementaryType(kind, bit_size=int(digits))
        except ABITypeError:
            # 规范化错误信息：重新抛出由本解析器生成的错误
            raise self._error(
                f"{kind} 的位宽必须是 8 到 256 之间 8 的倍数，得到 {digits}"
            ) from None

    def _sized_bytes(self, word: str) -> ElementaryType:
        digits = self._digits_suffix(word, "bytes")
        try:
            return ElementaryType("bytes", byte_size=int(digits))
        except ABITypeError:
            raise self._error(f"bytesM 的 M 必须在 1 到 32 之间，得到 {digits}") from None

    def _parse_tuple(self) -> TupleType:
        # 调用前当前字符为 '('；括号内侧不允许空白。
        self._pos += 1
        self._tuple_depth += 1
        # 语义嵌套上限由类型对象构造检查；此处仅在触及 Python 递归限制
        # 前拦截过深（或大量未闭合）的括号，保证唯一异常约定。
        if self._tuple_depth > _RECURSION_SAFETY_LIMIT:
            self._tuple_depth -= 1
            raise self._error("类型嵌套过深或括号未正确闭合")
        try:
            if self._peek() == ")":
                self._pos += 1
                return TupleType(())
            components: list[ABIType] = []
            while True:
                components.append(self._parse_type())
                # 逗号左侧允许 ASCII 空白；但 ')' 内侧不允许，因此先记录
                # 是否跳过了空白，若空白后直接是 ')' 则属非法。
                anchor = self._pos
                self._skip_whitespace()
                had_whitespace = self._pos != anchor
                ch = self._peek()
                if ch == ",":
                    self._pos += 1
                    # 逗号右侧允许 ASCII 空白。
                    self._skip_whitespace()
                    if self._peek() in (None, ",", ")"):
                        raise self._error("逗号后缺少类型")
                    continue
                if ch == ")":
                    if had_whitespace:
                        raise self._error("元组 ')' 内侧不允许空白")
                    self._pos += 1
                    return TupleType(tuple(components))
                raise self._error("元组中应为 ',' 或 ')'")
        finally:
            self._tuple_depth -= 1

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
            # 定长数组：只接受 [1-9][0-9]*，拒绝 0 与前导零。
            if not (ch.isascii() and ch.isdigit()) or ch == "0":
                raise self._error("数组长度必须是无前导零的正整数")
            start = self._pos
            while True:
                ch = self._peek()
                if ch is not None and ch.isascii() and ch.isdigit():
                    self._pos += 1
                    continue
                break
            length = int(self._text[start:self._pos])
            if self._peek() != "]":
                raise self._error("数组后缀缺少 ']'")
            self._pos += 1
            current = ArrayType(current, length)
        return current


def parse_abi_type(type_string: str) -> ABIType:
    """解析 ABI 类型字符串，返回不可变类型对象。

    无效输入统一抛出 :class:`abi_kit.ABITypeError`。
    """
    if not isinstance(type_string, str):
        raise ABITypeError(f"ABI 类型必须是字符串，得到 {type(type_string).__name__}")
    if type_string == "":
        raise ABITypeError("无效的 ABI 类型 ''：类型为空")
    return _Parser(type_string).parse()
