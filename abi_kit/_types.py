"""不可变的 ABI 类型对象。

类型对象分三种，可通过类区分：
- ElementaryType：基础类型（uintM/intM/address/bool/string/bytes/bytesM）
- ArrayType：数组类型（T[] 动态数组、T[n] 定长数组）
- TupleType：元组类型（(T1,T2,...)，允许空元组）

所有对象均为 frozen dataclass，构造时即完成自洽校验，包括
从基础类型向外最多 MAX_TYPE_DEPTH 层的嵌套上限。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._exceptions import ABITypeError

#: 从基础类型向外计数的最大嵌套层数（基础类型自身计第 1 层）。
MAX_TYPE_DEPTH = 128

_UINT = "uint"
_INT = "int"
_ADDRESS = "address"
_BOOL = "bool"
_STRING = "string"
_BYTES = "bytes"


@dataclass(frozen=True)
class ABIType:
    """所有 ABI 类型对象的抽象基类。"""

    @property
    def depth(self) -> int:
        """从基础类型向外的层数，基础类型为 1。"""
        raise NotImplementedError


@dataclass(frozen=True)
class ElementaryType(ABIType):
    """基础类型。

    kind 取值：
    - ``"uint"`` / ``"int"``：bit_size 为 8..256 的 8 的倍数
    - ``"address"`` / ``"bool"`` / ``"string"``：两个宽度均为 None
    - ``"bytes"``：byte_size 为 None 表示动态 bytes，否则为 1..32
    """

    kind: str
    bit_size: int | None = None
    byte_size: int | None = None

    def __post_init__(self) -> None:
        kind = self.kind
        if kind in (_UINT, _INT):
            if not isinstance(self.bit_size, int) or isinstance(self.bit_size, bool):
                raise ABITypeError(f"{kind} 类型必须给出整数位宽")
            if self.byte_size is not None:
                raise ABITypeError(f"{kind} 类型不能带有字节宽度")
            if not (8 <= self.bit_size <= 256 and self.bit_size % 8 == 0):
                raise ABITypeError(
                    f"{kind} 的位宽必须是 8 到 256 之间 8 的倍数，得到 {self.bit_size}"
                )
        elif kind in (_ADDRESS, _BOOL, _STRING, _BYTES):
            if self.bit_size is not None:
                raise ABITypeError(f"{kind} 类型不能带有整数位宽")
            if kind == _BYTES and self.byte_size is not None:
                if (
                    isinstance(self.byte_size, bool)
                    or not isinstance(self.byte_size, int)
                    or not (1 <= self.byte_size <= 32)
                ):
                    raise ABITypeError(
                        f"bytesM 的 M 必须在 1 到 32 之间，得到 {self.byte_size!r}"
                    )
            elif self.byte_size is not None:
                raise ABITypeError(f"{kind} 类型不能带有字节宽度")
        else:
            raise ABITypeError(f"未知的基础类型：{kind!r}")

    @property
    def depth(self) -> int:
        return 1


@dataclass(frozen=True)
class ArrayType(ABIType):
    """数组类型；length 为 None 表示动态数组 T[]，否则为定长数组 T[n]。"""

    element_type: ABIType
    length: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.element_type, ABIType):
            raise ABITypeError("数组元素类型必须是 ABIType")
        if self.length is not None:
            if isinstance(self.length, bool) or not isinstance(self.length, int):
                raise ABITypeError(f"数组长度必须是正整数，得到 {self.length!r}")
            if self.length <= 0:
                raise ABITypeError(f"数组长度必须是正整数，得到 {self.length}")
        if self.depth > MAX_TYPE_DEPTH:
            raise ABITypeError(f"类型嵌套超过 {MAX_TYPE_DEPTH} 层")

    @property
    def depth(self) -> int:
        return self.element_type.depth + 1


@dataclass(frozen=True)
class TupleType(ABIType):
    """元组类型，components 按声明顺序保存各组成部分。"""

    components: tuple[ABIType, ...] = field(default=())

    def __post_init__(self) -> None:
        try:
            components = tuple(self.components)
        except TypeError as exc:
            raise ABITypeError("元组组成部分必须是 ABIType 序列") from exc
        for component in components:
            if not isinstance(component, ABIType):
                raise ABITypeError("元组组成部分必须是 ABIType")
        object.__setattr__(self, "components", components)
        if self.depth > MAX_TYPE_DEPTH:
            raise ABITypeError(f"类型嵌套超过 {MAX_TYPE_DEPTH} 层")

    @property
    def depth(self) -> int:
        return 1 + max((c.depth for c in self.components), default=0)
