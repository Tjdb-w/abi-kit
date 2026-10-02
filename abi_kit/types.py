"""不可变的 ABI 类型对象。

类型对象分三种，可通过 :attr:`ABIType.kind` 区分：

``"elementary"``
    基础类型，见 :class:`ElementaryType`。
``"array"``
    数组类型（定长或变长），见 :class:`ArrayType`。
``"tuple"``
    元组类型，见 :class:`TupleType`。

所有类型对象均不可变，可安全地在后续编解码与事件日志功能中复用。
"""

from __future__ import annotations

from typing import Tuple


class ABITypeError(ValueError):
    """无效 ABI 类型字符串引发的唯一异常。"""


__all__ = [
    "ABIType",
    "ABITypeError",
    "ElementaryType",
    "ArrayType",
    "TupleType",
]


class ABIType:
    """所有 ABI 类型对象的抽象基类，本身不对外构造。"""

    kind = ""

    __slots__ = ()

    def format(self) -> str:
        """返回该类型的规范类型字符串（不含空白）。"""
        raise NotImplementedError

    def __str__(self) -> str:
        return self.format()

    def __repr__(self) -> str:
        return "{}({!r})".format(type(self).__name__, self.format())


class ElementaryType(ABIType):
    """基础类型。

    ``name`` 为基础类型名（``"uint256"``、``"address"``、``"bytes32"``
    等）。对 ``uintM`` / ``intM``，``bit_width`` 为整数位宽 ``M``；对
    ``bytesM``，``byte_width`` 为字节宽度 ``M``；其余字段为 ``None``。
    """

    kind = "elementary"

    __slots__ = ("_name", "_bit_width", "_byte_width")

    def __init__(
        self,
        name: str,
        bit_width: "int | None" = None,
        byte_width: "int | None" = None,
    ) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_bit_width", bit_width)
        object.__setattr__(self, "_byte_width", byte_width)

    @property
    def name(self) -> str:
        """基础类型名，如 ``"uint256"``、``"bytes"``、``"address"``。"""
        return self._name

    @property
    def bit_width(self) -> "int | None":
        """``uintM`` / ``intM`` 的位宽 ``M``，其余为 ``None``。"""
        return self._bit_width

    @property
    def byte_width(self) -> "int | None":
        """``bytesM`` 的字节宽度 ``M``，其余为 ``None``。"""
        return self._byte_width

    def format(self) -> str:
        return self._name

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            "{} 对象不可变".format(type(self).__name__)
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            "{} 对象不可变".format(type(self).__name__)
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ElementaryType):
            return NotImplemented
        return (
            self._name == other._name
            and self._bit_width == other._bit_width
            and self._byte_width == other._byte_width
        )

    def __hash__(self) -> int:
        return hash((ElementaryType, self._name))


class ArrayType(ABIType):
    """数组类型。

    ``element_type`` 为元素类型；``length`` 为定长数组长度（正整数），
    变长数组时为 ``None``。
    """

    kind = "array"

    __slots__ = ("_element_type", "_length")

    def __init__(self, element_type: ABIType, length: "int | None") -> None:
        if not isinstance(element_type, ABIType):
            raise TypeError("element_type 必须是 ABIType")
        if length is not None:
            if not isinstance(length, int) or isinstance(length, bool):
                raise TypeError("length 必须为正整数或 None")
            if length <= 0:
                raise ABITypeError("定长数组长度必须为正整数")
        object.__setattr__(self, "_element_type", element_type)
        object.__setattr__(self, "_length", length)

    @property
    def element_type(self) -> ABIType:
        """数组元素类型。"""
        return self._element_type

    @property
    def length(self) -> "int | None":
        """定长数组长度；变长数组为 ``None``。"""
        return self._length

    def format(self) -> str:
        if self._length is None:
            return self._element_type.format() + "[]"
        return "{}[{}]".format(self._element_type.format(), self._length)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            "{} 对象不可变".format(type(self).__name__)
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            "{} 对象不可变".format(type(self).__name__)
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ArrayType):
            return NotImplemented
        return (
            self._element_type == other._element_type
            and self._length == other._length
        )

    def __hash__(self) -> int:
        return hash((ArrayType, self._element_type, self._length))


class TupleType(ABIType):
    """元组类型，``components`` 按声明顺序保存组成类型。"""

    kind = "tuple"

    __slots__ = ("_components",)

    def __init__(self, components: "Tuple[ABIType, ...]") -> None:
        for component in components:
            if not isinstance(component, ABIType):
                raise TypeError("元组组成部分必须全部是 ABIType")
        object.__setattr__(self, "_components", tuple(components))

    @property
    def components(self) -> "Tuple[ABIType, ...]":
        """元组组成类型，按声明顺序排列（不可变元组）。"""
        return self._components

    def format(self) -> str:
        return "(" + ",".join(c.format() for c in self._components) + ")"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            "{} 对象不可变".format(type(self).__name__)
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            "{} 对象不可变".format(type(self).__name__)
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TupleType):
            return NotImplemented
        return self._components == other._components

    def __hash__(self) -> int:
        return hash((TupleType, self._components))
