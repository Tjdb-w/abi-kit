"""parse_abi_type / format_abi_type 的行为测试。

仅使用标准库 unittest，无第三方依赖。
"""

import unittest

from abi_kit import (
    ABITypeError,
    ArrayType,
    ElementaryType,
    TupleType,
    format_abi_type,
    parse_abi_type,
)
from abi_kit._types import MAX_TYPE_DEPTH


class ParseElementaryTests(unittest.TestCase):
    def test_uint_int_boundaries(self):
        for m in (8, 16, 128, 248, 256):
            self.assertEqual(format_abi_type(parse_abi_type(f"uint{m}")), f"uint{m}")
            self.assertEqual(format_abi_type(parse_abi_type(f"int{m}")), f"int{m}")

    def test_simple_elementary_types(self):
        for name in ("address", "bool", "string", "bytes"):
            t = parse_abi_type(name)
            self.assertIsInstance(t, ElementaryType)
            self.assertEqual(format_abi_type(t), name)

    def test_bytes_m_boundaries(self):
        for m in (1, 2, 16, 31, 32):
            self.assertEqual(format_abi_type(parse_abi_type(f"bytes{m}")), f"bytes{m}")

    def test_invalid_elementary_types(self):
        bad = [
            "uint7", "uint9", "uint10", "uint264", "uint257",
            "int7", "int264",
            "bytes0", "bytes33",
            "hash", "address20", "boolean",
            "uint08", "int016", "bytes03",
        ]
        for s in bad:
            with self.subTest(s=s):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(s)

    def test_aliases_expand_to_canonical(self):
        expected = {
            "uint": "uint256",
            "int": "int256",
            "fixed": "fixed128x18",
            "ufixed": "ufixed128x18",
            "byte": "bytes1",
        }
        for alias, canonical in expected.items():
            with self.subTest(alias=alias):
                t = parse_abi_type(alias)
                self.assertEqual(format_abi_type(t), canonical)
                # 别名展开后的对象与显式规范形式完全等价。
                self.assertEqual(t, parse_abi_type(canonical))

    def test_alias_near_misses_remain_invalid(self):
        # 既非别名也非既有合法形式：带宽度、带部分小数位或近似拼写。
        bad = [
            "uint0", "byte2",
            "fixed128", "fixed128x", "fixedx18",
            "ufixed0x18", "fixed0x18", "ufixed128x0",
        ]
        for s in bad:
            with self.subTest(s=s):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(s)

    def test_elementary_fields(self):
        u = parse_abi_type("uint256")
        self.assertEqual(u.kind, "uint")
        self.assertEqual(u.bit_size, 256)
        self.assertIsNone(u.byte_size)
        b = parse_abi_type("bytes32")
        self.assertEqual(b.kind, "bytes")
        self.assertEqual(b.byte_size, 32)
        self.assertIsNone(b.bit_size)
        d = parse_abi_type("bytes")
        self.assertEqual(d.kind, "bytes")
        self.assertIsNone(d.byte_size)


class ArrayTests(unittest.TestCase):
    def test_dynamic_and_fixed_arrays(self):
        t = parse_abi_type("uint256[]")
        self.assertIsInstance(t, ArrayType)
        self.assertIsNone(t.length)
        self.assertEqual(format_abi_type(t), "uint256[]")

        t = parse_abi_type("bytes32[][3]")
        self.assertIsInstance(t, ArrayType)
        self.assertEqual(t.length, 3)
        self.assertIsInstance(t.element_type, ArrayType)
        self.assertIsNone(t.element_type.length)
        self.assertEqual(format_abi_type(t), "bytes32[][3]")

    def test_array_of_tuple(self):
        t = parse_abi_type("(uint8,bool)[]")
        self.assertIsInstance(t, ArrayType)
        self.assertIsInstance(t.element_type, TupleType)
        self.assertEqual(
            t.element_type.components[0], ElementaryType("uint", bit_size=8)
        )

    def test_invalid_array_lengths(self):
        for s in ("address[0]", "address[01]", "address[001]", "address[1][]0",
                  "uint256[", "uint256[]]", "uint256[1 []", "uint256[-1]",
                  "uint256[1a]"):
            with self.subTest(s=s):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(s)


class TupleTests(unittest.TestCase):
    def test_nested_tuple_example(self):
        t = parse_abi_type("(uint8,address[2],(bool,string[]))")
        self.assertIsInstance(t, TupleType)
        self.assertEqual(len(t.components), 3)
        self.assertEqual(t.components[0], ElementaryType("uint", bit_size=8))
        middle = t.components[1]
        self.assertEqual(middle, ArrayType(ElementaryType("address"), 2))
        inner = t.components[2]
        self.assertEqual(
            inner,
            TupleType((ElementaryType("bool"),
                       ArrayType(ElementaryType("string")))),
        )
        self.assertEqual(
            format_abi_type(t), "(uint8,address[2],(bool,string[]))"
        )

    def test_empty_tuple(self):
        t = parse_abi_type("()")
        self.assertIsInstance(t, TupleType)
        self.assertEqual(t.components, ())
        self.assertEqual(format_abi_type(t), "()")
        self.assertEqual(format_abi_type(parse_abi_type("()[2]")), "()[2]")

    def test_invalid_tuples(self):
        for s in (
            "(uint8,)", "(,uint8)", "(uint8,,uint256)",
            "(uint8", "uint8)", "()(",
            "(uint8、uint256)", "(uint8]", "[uint8,bool)",
            "(uint8 )", "( uint8)", "(uint8, uint16 )",
            "( uint8,bool)", "(uint8,bool )",
        ):
            with self.subTest(s=s):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(s)

    def test_allowed_whitespace(self):
        cases = {
            "  uint256  ": "uint256",
            "\tuint256[]\n": "uint256[]",
            "(uint8, address[2] , (bool, string[]))":
                "(uint8,address[2],(bool,string[]))",
            "(uint8 ,address)": "(uint8,address)",
        }
        for raw, canonical in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(format_abi_type(parse_abi_type(raw)), canonical)

    def test_whitespace_inside_type_word_fails(self):
        for s in ("uint 256", "add ress", "by tes32", "uint256 []",
                  "uint256[ 3]", "uint256[3 ]", "( )", "b ool"):
            with self.subTest(s=s):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(s)


class FullConsumptionTests(unittest.TestCase):
    def test_trailing_content_fails(self):
        for s in ("uint256 x", "uint256\t", "uint256\n", "bool junk",
                  "uint256[] ", "uint256[]x", ""):
            # 纯尾随空白按整体两端空白处理，是允许的；单独区分：
            if s in ("uint256\t", "uint256\n", "uint256[] "):
                with self.subTest(s=s):
                    self.assertEqual(
                        format_abi_type(parse_abi_type(s)),
                        format_abi_type(parse_abi_type(s.strip())),
                    )
                continue
            with self.subTest(s=s):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(s)

    def test_non_string_input(self):
        for value in (None, 256, b"uint256", ["uint256"]):
            with self.subTest(value=value):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(value)


class RoundTripTests(unittest.TestCase):
    EXAMPLES = [
        "uint256", "int8", "address", "bool", "string", "bytes", "bytes32",
        "bytes32[][3]", "(uint8,address[2],(bool,string[]))", "()",
        "uint16[][][5]", "(uint256,(bytes3,address[]))[]",
    ]

    def test_round_trip_stable(self):
        for s in self.EXAMPLES:
            with self.subTest(s=s):
                t1 = parse_abi_type(s)
                canonical = format_abi_type(t1)
                t2 = parse_abi_type(canonical)
                self.assertEqual(t1, t2)
                self.assertEqual(format_abi_type(t2), canonical)
                self.assertNotIn(" ", canonical)


class ImmutabilityTests(unittest.TestCase):
    def test_objects_are_frozen(self):
        t = parse_abi_type("(uint8,address[])")
        with self.assertRaises(Exception):
            t.components = ()  # type: ignore[misc]
        with self.assertRaises(Exception):
            t.components[0] = parse_abi_type("bool")  # type: ignore[index]

    def test_objects_hashable(self):
        t1 = parse_abi_type("uint256[]")
        t2 = parse_abi_type("uint256[]")
        self.assertEqual(hash(t1), hash(t2))
        self.assertEqual({t1, t2}, {t1})


class DepthTests(unittest.TestCase):
    def test_depth_counts_layers(self):
        self.assertEqual(parse_abi_type("uint8").depth, 1)
        self.assertEqual(parse_abi_type("uint8[]").depth, 2)
        self.assertEqual(parse_abi_type("uint8[][]").depth, 3)
        self.assertEqual(parse_abi_type("(uint8)").depth, 2)
        self.assertEqual(parse_abi_type("((uint8))").depth, 3)
        # 元组自身一层，与最深成员的层数相加。
        self.assertEqual(parse_abi_type("(uint8[][])").depth, 4)
        self.assertEqual(parse_abi_type("()").depth, 1)

    def test_exactly_128_layers_ok(self):
        s = "uint8" + "[]" * 127
        t = parse_abi_type(s)
        self.assertEqual(t.depth, MAX_TYPE_DEPTH)
        self.assertEqual(format_abi_type(t), s)

    def test_129_array_layers_fails(self):
        s = "uint8" + "[]" * 128
        with self.assertRaises(ABITypeError):
            parse_abi_type(s)

    def test_129_tuple_layers_fails(self):
        s = "(" * 128 + "uint8" + ")" * 128
        with self.assertRaises(ABITypeError):
            parse_abi_type(s)

    def test_128_tuple_layers_ok(self):
        s = "(" * 127 + "uint8" + ")" * 127
        t = parse_abi_type(s)
        self.assertEqual(t.depth, MAX_TYPE_DEPTH)

    def test_128_empty_tuple_layers_ok(self):
        # 每层空元组只计 1 层，128 层空元组嵌套的语义层数恰为 128。
        s = "(" * 128 + ")" * 128
        t = parse_abi_type(s)
        self.assertEqual(t.depth, MAX_TYPE_DEPTH)

    def test_deep_unclosed_parens_raises_type_error_not_recursion_error(self):
        with self.assertRaises(ABITypeError):
            parse_abi_type("(" * 1000)


class DirectConstructionValidationTests(unittest.TestCase):
    def test_invalid_widths(self):
        with self.assertRaises(ABITypeError):
            ElementaryType("uint", bit_size=7)
        with self.assertRaises(ABITypeError):
            ElementaryType("bytes", byte_size=33)
        with self.assertRaises(ABITypeError):
            ArrayType(ElementaryType("bool"), 0)
        with self.assertRaises(ABITypeError):
            TupleType((ElementaryType("bool"), int))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
