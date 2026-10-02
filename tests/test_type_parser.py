"""abi_kit 类型解析与规范化的单元测试。"""

import unittest

import abi_kit
from abi_kit import (
    ABITypeError,
    ArrayType,
    ElementaryType,
    TupleType,
    format_abi_type,
    parse_abi_type,
)


class ElementaryTypeTests(unittest.TestCase):
    def test_plain_elementary_types(self):
        for name in ("address", "bool", "string", "bytes"):
            with self.subTest(name=name):
                t = parse_abi_type(name)
                self.assertIsInstance(t, ElementaryType)
                self.assertEqual(t.kind, "elementary")
                self.assertEqual(t.name, name)
                self.assertIsNone(t.bit_width)
                self.assertIsNone(t.byte_width)
                self.assertEqual(format_abi_type(t), name)

    def test_uint_int_widths(self):
        for prefix in ("uint", "int"):
            for width in range(8, 257, 8):
                literal = "{}{}".format(prefix, width)
                with self.subTest(literal=literal):
                    t = parse_abi_type(literal)
                    self.assertIsInstance(t, ElementaryType)
                    self.assertEqual(t.name, literal)
                    self.assertEqual(t.bit_width, width)
                    self.assertIsNone(t.byte_width)
                    self.assertEqual(format_abi_type(t), literal)

    def test_bytes_widths(self):
        for width in range(1, 33):
            literal = "bytes{}".format(width)
            with self.subTest(literal=literal):
                t = parse_abi_type(literal)
                self.assertIsInstance(t, ElementaryType)
                self.assertEqual(t.name, literal)
                self.assertEqual(t.byte_width, width)
                self.assertIsNone(t.bit_width)
                self.assertEqual(format_abi_type(t), literal)


class InvalidElementaryTests(unittest.TestCase):
    INVALID = [
        "uint",
        "int",
        "uint7",
        "uint9",      # 非 8 的倍数
        "uint0",
        "uint264",
        "uint100",    # 非 8 的倍数（200 合法，勿混）
        "int7",
        "int264",
        "bytes0",
        "bytes33",
        "bytes01",
        "bytes100",
        "address1",
        "bool2",
        "string3",
        "hash",
        "uint256x",
        "UINT256",
        "Uint256",
        "address ",   # 尾随空白本身合法，故不在此（另测）
        " byte",
        "tuint256",
        "",
    ]

    def test_invalid_elementary(self):
        for literal in self.INVALID:
            if literal in ("address ",):
                continue
            with self.subTest(literal=repr(literal)):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(literal)

    def test_non_string_input(self):
        for value in (None, 256, b"uint256", ["uint256"]):
            with self.subTest(value=value):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(value)


class ArrayTests(unittest.TestCase):
    def test_dynamic_array(self):
        t = parse_abi_type("uint256[]")
        self.assertEqual(t.kind, "array")
        self.assertIsInstance(t, ArrayType)
        self.assertIsNone(t.length)
        self.assertEqual(t.element_type, ElementaryType("uint256", bit_width=256))
        self.assertEqual(format_abi_type(t), "uint256[]")

    def test_fixed_array(self):
        t = parse_abi_type("address[2]")
        self.assertEqual(t.kind, "array")
        self.assertEqual(t.length, 2)
        self.assertEqual(t.element_type, ElementaryType("address"))
        self.assertEqual(format_abi_type(t), "address[2]")

    def test_chained_arrays(self):
        t = parse_abi_type("bytes32[][3]")
        self.assertIsInstance(t, ArrayType)
        self.assertEqual(t.length, 3)
        outer = t.element_type
        self.assertIsInstance(outer, ArrayType)
        self.assertIsNone(outer.length)
        self.assertEqual(outer.element_type,
                         ElementaryType("bytes32", byte_width=32))
        self.assertEqual(format_abi_type(t), "bytes32[][3]")

    def test_large_array_length(self):
        t = parse_abi_type("uint8[123456789012345678901234567890]")
        self.assertEqual(t.length, 123456789012345678901234567890)
        self.assertEqual(format_abi_type(t),
                         "uint8[123456789012345678901234567890]")

    def test_invalid_arrays(self):
        invalid = [
            "address[0]",
            "address[01]",
            "address[00]",
            "address[-1]",
            "address[1x]",
            "address[ 1]",
            "address[1 ]",
            "address []",
            "address[1",
            "address1]",
            "address[]1",
            "address[1][]x",
        ]
        for literal in invalid:
            with self.subTest(literal=repr(literal)):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(literal)


class TupleTests(unittest.TestCase):
    def test_empty_tuple(self):
        t = parse_abi_type("()")
        self.assertEqual(t.kind, "tuple")
        self.assertIsInstance(t, TupleType)
        self.assertEqual(t.components, ())
        self.assertEqual(format_abi_type(t), "()")

    def test_single_component(self):
        t = parse_abi_type("(uint256)")
        self.assertEqual(t.components, (ElementaryType("uint256", bit_width=256),))
        self.assertEqual(format_abi_type(t), "(uint256)")

    def test_nested_example(self):
        literal = "(uint8,address[2],(bool,string[]))"
        t = parse_abi_type(literal)
        self.assertIsInstance(t, TupleType)
        self.assertEqual(len(t.components), 3)

        first, second, third = t.components
        self.assertEqual(first, ElementaryType("uint8", bit_width=8))

        self.assertIsInstance(second, ArrayType)
        self.assertEqual(second.length, 2)
        self.assertEqual(second.element_type, ElementaryType("address"))

        self.assertIsInstance(third, TupleType)
        inner_a, inner_b = third.components
        self.assertEqual(inner_a, ElementaryType("bool"))
        self.assertIsInstance(inner_b, ArrayType)
        self.assertIsNone(inner_b.length)
        self.assertEqual(inner_b.element_type, ElementaryType("string"))

        self.assertEqual(format_abi_type(t), literal)

    def test_tuple_of_tuples(self):
        t = parse_abi_type("((uint256),(bytes32))")
        self.assertEqual(format_abi_type(t), "((uint256),(bytes32))")

    def test_tuple_array_combinations(self):
        for literal in [
            "(uint256,bool)[]",
            "(uint256[])[3][]",
            "((address))[2]",
        ]:
            with self.subTest(literal=literal):
                t = parse_abi_type(literal)
                self.assertEqual(format_abi_type(t), literal)

    def test_invalid_tuples(self):
        invalid = [
            "(uint8,)",
            "(,uint256)",
            "(uint8,,uint256)",
            "(",
            ")",
            "((uint8)",
            "(uint8]]",
            "(uint8、uint256)",   # 中文逗号
            "(uint8,uint256]",
            "[uint8,uint256]",
            "uint256 x",
            "(uint8 uint256)",
            "(uint8, )",
            "( uint8,)",
        ]
        for literal in invalid:
            with self.subTest(literal=repr(literal)):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(literal)


class WhitespaceTests(unittest.TestCase):
    def test_allowed_whitespace(self):
        cases = [
            ("  uint256", "uint256"),
            ("uint256  ", "uint256"),
            ("\tuint256\n", "uint256"),
            ("( uint8 , address )", "(uint8,address)"),
            ("(uint8,   address[2] , (bool, string[]))",
             "(uint8,address[2],(bool,string[]))"),
            ("  ()  ", "()"),
            ("(  )", "()"),
            ("\n(\tuint8,\r\n address )\t", "(uint8,address)"),
        ]
        for raw, canonical in cases:
            with self.subTest(raw=repr(raw)):
                self.assertEqual(format_abi_type(parse_abi_type(raw)), canonical)

    def test_rejected_whitespace(self):
        invalid = [
            "uint 256",
            "uint25 6",
            "add ress",
            "byt es",
            "uint256 []",
            "uint256[ 2]",
            "uint256[2 ]",
            "(uint8 , address) []",
            "( uint8 ) [1]",
            "uint256\n[]",
        ]
        for literal in invalid:
            with self.subTest(literal=repr(literal)):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(literal)

    def test_non_ascii_whitespace_rejected(self):
        # 不间断空格 (U+00A0) 不是 ASCII 空白
        with self.assertRaises(ABITypeError):
            parse_abi_type(" uint256")


class TrailingContentTests(unittest.TestCase):
    def test_trailing_content_rejected(self):
        invalid = [
            "uint256 x",
            "uint256[] []",
            "uint256,",
            "uint256)",
            "bool true",
            "address 0x0",
            "uint256#",
        ]
        for literal in invalid:
            with self.subTest(literal=repr(literal)):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(literal)


class DepthTests(unittest.TestCase):
    def _wrap_layers(self, base, layers):
        """在 base 外交替包裹数组/元组，共 layers 层。"""
        text = base
        for i in range(layers):
            if i % 2 == 0:
                text = text + "[]"
            else:
                text = "(" + text + ")"
        return text

    def test_depth_boundary_ok(self):
        # 基础类型不计层；128 层包裹恰好允许
        t = parse_abi_type(self._wrap_layers("uint8", 128))
        self.assertEqual(format_abi_type(t), self._wrap_layers("uint8", 128))

    def test_depth_exceeded(self):
        with self.assertRaises(ABITypeError):
            parse_abi_type(self._wrap_layers("uint8", 129))

    def test_array_only_depth(self):
        with self.assertRaises(ABITypeError):
            parse_abi_type("uint8" + "[]" * 129)
        # 128 层数组合法
        t = parse_abi_type("uint8" + "[]" * 128)
        self.assertEqual(format_abi_type(t), "uint8" + "[]" * 128)

    def test_tuple_only_depth(self):
        text = "(" * 128 + "uint8" + ")" * 128
        t = parse_abi_type(text)
        self.assertEqual(format_abi_type(t), text)
        with self.assertRaises(ABITypeError):
            parse_abi_type("(" * 129 + "uint8" + ")" * 129)

    def test_sibling_components_do_not_accumulate(self):
        # 两个兄弟组件各自 2 层，元组整体应是 3 层而非 5 层
        literal = "(uint8[][],uint8[][])"
        t = parse_abi_type(literal)
        self.assertEqual(format_abi_type(t), literal)

    def test_deep_siblings_near_boundary(self):
        # 每个兄弟组件 127 层，外层元组使整体达到 128（合法）
        deep = "uint8" + "[]" * 127
        t = parse_abi_type("(" + deep + "," + deep + ")")
        # 再加一层则超界
        with self.assertRaises(ABITypeError):
            parse_abi_type("(" + deep + "," + deep + ")[]")


class ImmutabilityTests(unittest.TestCase):
    def test_objects_are_immutable(self):
        t = parse_abi_type("(uint8,address[2])[]")
        with self.assertRaises(AttributeError):
            t.length = 3
        with self.assertRaises(AttributeError):
            t._length = 3
        with self.assertRaises(AttributeError):
            del t.length

        inner_tuple = t.element_type
        with self.assertRaises(AttributeError):
            inner_tuple.components = ()
        with self.assertRaises(AttributeError):
            del inner_tuple.components

        elem = inner_tuple.components[0]
        with self.assertRaises(AttributeError):
            elem._name = "uint16"

    def test_components_tuple_is_copied(self):
        comps = [ElementaryType("uint8"), ElementaryType("bool")]
        t = TupleType(comps)
        comps.append(ElementaryType("address"))
        self.assertEqual(len(t.components), 2)

    def test_hashable_and_equal(self):
        a = parse_abi_type("(uint8,address[2])[]")
        b = parse_abi_type("(uint8,address[2])[]")
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertNotEqual(a, parse_abi_type("(uint8,address[3])[]"))


class RoundTripTests(unittest.TestCase):
    def test_spec_success_examples(self):
        for literal in [
            "uint256",
            "bytes32[][3]",
            "(uint8,address[2],(bool,string[]))",
        ]:
            with self.subTest(literal=literal):
                t = parse_abi_type(literal)
                self.assertEqual(format_abi_type(t), literal)
                # 再次格式化保持稳定
                self.assertEqual(t.format(), format_abi_type(t))
                self.assertEqual(str(t), literal)

    def test_spec_failure_examples(self):
        for literal in [
            "uint",
            "uint7",
            "uint264",
            "bytes0",
            "bytes33",
            "address[0]",
            "address[01]",
            "(uint8,)",
            "(uint8、uint256]",
            "uint256 x",
        ]:
            with self.subTest(literal=repr(literal)):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(literal)

    def test_all_failures_are_abi_type_error(self):
        # 规格：无效输入的唯一异常是 abi_kit.ABITypeError
        for literal in ["uint", "(", "bytes33", "address[01]", "(,)", "x[]"]:
            with self.subTest(literal=literal):
                try:
                    parse_abi_type(literal)
                except ABITypeError:
                    pass
                else:
                    self.fail("应当抛出 ABITypeError: {!r}".format(literal))

    def test_public_api_surface(self):
        for name in ("parse_abi_type", "format_abi_type", "ABITypeError"):
            self.assertTrue(hasattr(abi_kit, name))


if __name__ == "__main__":
    unittest.main()
