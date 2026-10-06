"""不可变的 ABI 类型对象。

类型对象分五种，可通过类区分：
- ElementaryType：基础类型（uintM/intM/address/bool/string/bytes/bytesM）
- FixedPointType：固定小数类型（fixedMxN/ufixedMxN）
- FunctionType：函数类型（function，固定 24 字节）
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
class FixedPointType(ABIType):
    """固定小数类型 fixedMxN / ufixedMxN。

    signed 为 True 表示 fixedMxN（有符号），False 表示 ufixedMxN（无符号）；
    bit_size 为 M（8..256 的 8 的倍数），scale 为 N（1..80 的小数位数）。
    与基础类型同为静态单字类型，层数计 1。
    """

    signed: bool
    bit_size: int
    scale: int

    def __post_init__(self) -> None:
        if not isinstance(self.signed, bool):
            raise ABITypeError(
                f"固定小数类型的 signed 必须是 bool，得到 {self.signed!r}"
            )
        label = "fixed" if self.signed else "ufixed"
        if isinstance(self.bit_size, bool) or not isinstance(self.bit_size, int):
            raise ABITypeError(f"{label} 类型必须给出整数位宽")
        if not (8 <= self.bit_size <= 256 and self.bit_size % 8 == 0):
            raise ABITypeError(
                f"{label} 的位宽 M 必须是 8 到 256 之间 8 的倍数，"
                f"得到 {self.bit_size}"
            )
        if isinstance(self.scale, bool) or not isinstance(self.scale, int):
            raise ABITypeError(f"{label} 类型必须给出整数小数位数")
        if not (1 <= self.scale <= 80):
            raise ABITypeError(
                f"{label} 的小数位数 N 必须在 1 到 80 之间，得到 {self.scale}"
            )

    @property
    def depth(self) -> int:
        return 1


@dataclass(frozen=True)
class FunctionType(ABIType):
    """函数指针类型 function，固定 24 字节（地址 + selector）。

    与基础类型同为静态单字类型，层数计 1。
    """

    byte_size: int = 24

    def __post_init__(self) -> None:
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or self.byte_size != 24
        ):
            raise ABITypeError(
                f"function 类型的字节宽度必须为 24，得到 {self.byte_size!r}"
            )

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
    """元组类型，components 按声明顺序保存各组成部分。

    names 为 None 表示未命名元组（默认）；否则它必须与 components 等长，
    每项为字段名字符串或 None（该位置未命名）。字段名仅用于路径寻址，
    不参与规范化（:func:`format_abi_type` 不输出名称）。
    """

    components: tuple[ABIType, ...] = field(default=())
    names: tuple[str | None, ...] | None = None

    def __post_init__(self) -> None:
        try:
            components = tuple(self.components)
        except TypeError as exc:
            raise ABITypeError("元组组成部分必须是 ABIType 序列") from exc
        for component in components:
            if not isinstance(component, ABIType):
                raise ABITypeError("元组组成部分必须是 ABIType")
        object.__setattr__(self, "components", components)
        if self.names is not None:
            try:
                names = tuple(self.names)
            except TypeError as exc:
                raise ABITypeError("元组字段名必须是 str/None 序列") from exc
            if len(names) != len(components):
                raise ABITypeError(
                    f"字段名数量 {len(names)} 与元组组成部分数量 "
                    f"{len(components)} 不一致"
                )
            for name in names:
                if name is not None and not isinstance(name, str):
                    raise ABITypeError("元组字段名必须是 str 或 None")
            object.__setattr__(self, "names", names)
        if self.depth > MAX_TYPE_DEPTH:
            raise ABITypeError(f"类型嵌套超过 {MAX_TYPE_DEPTH} 层")

    @property
    def depth(self) -> int:
        return 1 + max((c.depth for c in self.components), default=0)
