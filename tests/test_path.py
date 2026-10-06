"""get_abi_value_at_path / replace_abi_value_at_path 的行为测试。

仅使用标准库 unittest，无第三方依赖。
"""

import unittest

from abi_kit import (
    AbiPathError,
    ABITypeError,
    ABIValueError,
    ElementaryType,
    TupleType,
    decode_abi_value,
    encode_abi_value,
    format_abi_type,
    get_abi_value_at_path,
    parse_abi_type,
    replace_abi_value_at_path,
    replace_abi_values_at_paths,
)
from abi_kit._path import _NamedParser

ADDR_A = "0x" + "11" * 20
ADDR_B = "0x" + "22" * 20
ADDR_C = "0x" + "33" * 20


def named(type_string: str):
    return _NamedParser(type_string).parse()


# 与需求示例同形的类型：
#   struct Inner { address to; uint256[] amounts; bool flag; }
#   struct Outer { address sender; Inner[] items; uint256 ts; bytes32 tag; bool ok; }
OUTER_SRC = (
    "(address sender,"
    "(address to,uint256[] amounts,bool flag)[] items,"
    "uint256 ts,bytes32 tag,bool ok)"
)


def outer_value():
    return (
        ADDR_A,
        [
            (ADDR_B, [1, 2, 3], True),
            (ADDR_C, [40, 50], False),
        ],
        99,
        b"\xab" * 32,
        True,
    )


class NamedTupleTypeTests(unittest.TestCase):
    def test_names_carried_by_tuple_type(self):
        t = named(OUTER_SRC)
        self.assertIsInstance(t, TupleType)
        self.assertEqual(t.names, ("sender", "items", "ts", "tag", "ok"))
        inner = t.components[1].element_type
        self.assertEqual(inner.names, ("to", "amounts", "flag"))

    def test_some_components_may_be_unnamed(self):
        t = named("(uint256 a,uint256,uint256 c)")
        self.assertEqual(t.names, ("a", None, "c"))

    def test_names_optional_and_default_unnamed(self):
        t = parse_abi_type("(uint256,uint256)")
        self.assertIsNone(t.names)
        t2 = TupleType((ElementaryType("uint", bit_size=256),))
        self.assertIsNone(t2.names)
        # 带名解析器解析全未命名元组时，产物与严格解析器完全相等。
        t3 = named("(uint256,uint256)")
        self.assertIsNone(t3.names)
        self.assertEqual(t3, t)

    def test_names_do_not_affect_canonical_format(self):
        t = named("(uint256 a,(bool b,string c)[] rows)")
        self.assertEqual(format_abi_type(t), "(uint256,(bool,string)[])")

    def test_strict_parser_still_rejects_names(self):
        # parse_abi_type 的既有语义保持不变：字段名不属于规范语法。
        with self.assertRaises(ABITypeError):
            parse_abi_type("(uint256 amount)")

    def test_invalid_names_on_direct_construction(self):
        with self.assertRaises(ABITypeError):
            TupleType((ElementaryType("uint", bit_size=256),), names=("a", "b"))
        with self.assertRaises(ABITypeError):
            TupleType((ElementaryType("uint", bit_size=256),), names=(1,))

    def test_names_after_array_suffix(self):
        t = named("(uint256[2] coords,string[] notes)")
        self.assertEqual(t.names, ("coords", "notes"))

    def test_duplicate_field_names_resolve_first(self):
        t = named("(uint256 x,uint256 x)")
        data = encode_abi_value(t, (7, 8))
        self.assertEqual(get_abi_value_at_path(t, data, "x"), 7)


class PathSyntaxTests(unittest.TestCase):
    def setUp(self):
        self.t = named(OUTER_SRC)
        self.data = encode_abi_value(self.t, outer_value())

    def _bad(self, path):
        with self.assertRaises(AbiPathError) as caught:
            get_abi_value_at_path(self.t, self.data, path)
        self.assertEqual(caught.exception.code, "PATH_SYNTAX", path)

    def test_empty_path_is_root(self):
        self.assertEqual(get_abi_value_at_path(self.t, self.data, ""), outer_value())

    def test_invalid_syntax(self):
        for path in (
            ".", "items.", ".items", "a..b",
            "items[]", "items[", "items[2", "items[2]]",
            "items[-1]", "items[+1]", "items[ 2]", "items[2 ]",
            "items[0 1]", "items[01]", "items[1.0]", "items[x]",
            "sender extra", "items[2].", "items.[0]",
            "items[2]tail",
        ):
            with self.subTest(path=path):
                self._bad(path)

    def test_path_must_be_string(self):
        for path in (None, 0, ["items"], ("items",)):
            with self.subTest(path=path):
                self._bad(path)

    def test_valid_index_from_root(self):
        t = parse_abi_type("uint256[3]")
        data = encode_abi_value(t, [10, 20, 30])
        self.assertEqual(get_abi_value_at_path(t, data, "[1]"), 20)


class PathReadTests(unittest.TestCase):
    def setUp(self):
        self.t = named(OUTER_SRC)
        self.value = outer_value()
        self.data = encode_abi_value(self.t, self.value)

    def test_elementary_leaves_keep_value_conventions(self):
        self.assertEqual(get_abi_value_at_path(self.t, self.data, "sender"), ADDR_A)
        self.assertEqual(get_abi_value_at_path(self.t, self.data, "ts"), 99)
        self.assertEqual(get_abi_value_at_path(self.t, self.data, "tag"), b"\xab" * 32)
        self.assertIs(get_abi_value_at_path(self.t, self.data, "ok"), True)

    def test_string_and_bytes_leaves(self):
        t = named("(string name,bytes blob)")
        data = encode_abi_value(t, ("héllo", b"\x00\x01\x02"))
        self.assertEqual(get_abi_value_at_path(t, data, "name"), "héllo")
        self.assertEqual(get_abi_value_at_path(t, data, "blob"), b"\x00\x01\x02")

    def test_deeply_nested_path_from_spec(self):
        # inner.items[2].amount 同形：items[1].amounts[0]
        self.assertEqual(
            get_abi_value_at_path(self.t, self.data, "items[1].amounts[0]"), 40
        )
        self.assertEqual(
            get_abi_value_at_path(self.t, self.data, "items[0].amounts[2]"), 3
        )
        self.assertIs(
            get_abi_value_at_path(self.t, self.data, "items[0].flag"), True
        )

    def test_read_dynamic_array_returns_only_selected_container(self):
        items = get_abi_value_at_path(self.t, self.data, "items")
        self.assertEqual(items, self.value[1])
        amounts = get_abi_value_at_path(self.t, self.data, "items[1].amounts")
        self.assertEqual(amounts, [40, 50])
        one_item = get_abi_value_at_path(self.t, self.data, "items[1]")
        self.assertEqual(one_item, (ADDR_C, [40, 50], False))

    def test_fixed_and_nested_arrays(self):
        # 外层定长 2、内层动态。
        t = parse_abi_type("uint256[][2]")
        data = encode_abi_value(t, [[1, 2], [3, 4, 5]])
        self.assertEqual(get_abi_value_at_path(t, data, "[0][1]"), 2)
        self.assertEqual(get_abi_value_at_path(t, data, "[1]"), [3, 4, 5])
        self.assertEqual(get_abi_value_at_path(t, data, ""), [[1, 2], [3, 4, 5]])

        t2 = parse_abi_type("uint8[2][3]")
        data2 = encode_abi_value(t2, [[1, 2], [3, 4], [5, 6]])
        self.assertEqual(get_abi_value_at_path(t2, data2, "[2][0]"), 5)

    def test_tuple_nested_in_array_nested_in_tuple(self):
        t = named("((uint256 amount,address who)[] logs,uint256 block)")
        data = encode_abi_value(t, ([(1, ADDR_A), (2, ADDR_B)], 55))
        self.assertEqual(
            get_abi_value_at_path(t, data, "logs[1].who"), ADDR_B
        )
        # tuple 同时支持字段名与位置索引。
        self.assertEqual(
            get_abi_value_at_path(t, data, "logs[1][0]"), 2
        )

    def test_positional_index_into_tuple(self):
        t = parse_abi_type("(uint256,bool,string)")
        data = encode_abi_value(t, (42, True, "ok"))
        self.assertEqual(get_abi_value_at_path(t, data, "[2]"), "ok")
        self.assertEqual(get_abi_value_at_path(t, data, "[0]"), 42)

    def test_unnamed_tuple_only_accepts_positional_steps(self):
        t = parse_abi_type("(uint256,uint256)")
        data = encode_abi_value(t, (1, 2))
        self.assertEqual(get_abi_value_at_path(t, data, "[1]"), 2)
        with self.assertRaises(AbiPathError) as caught:
            get_abi_value_at_path(t, data, "x")
        self.assertEqual(caught.exception.code, "PATH_NOT_FOUND")

    def test_hex_string_input_with_and_without_prefix(self):
        hex_data = self.data.hex()
        self.assertEqual(
            get_abi_value_at_path(self.t, "0x" + hex_data, "ts"), 99
        )
        self.assertEqual(
            get_abi_value_at_path(self.t, hex_data, "ts"), 99
        )

    def test_malformed_encoding_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            get_abi_value_at_path(self.t, b"\x00" * 10, "ts")
        with self.assertRaises(ABIValueError):
            get_abi_value_at_path(self.t, "not-hex", "ts")

    def test_invalid_type_inputs(self):
        with self.assertRaises(ABITypeError):
            get_abi_value_at_path(123, self.data, "ts")
        with self.assertRaises(ABITypeError):
            get_abi_value_at_path("(uint256 a,)", self.data, "a")


class PathNavigationErrorTests(unittest.TestCase):
    def setUp(self):
        self.t = named(OUTER_SRC)
        self.data = encode_abi_value(self.t, outer_value())

    def _code(self, path):
        with self.assertRaises(AbiPathError) as caught:
            get_abi_value_at_path(self.t, self.data, path)
        return caught.exception.code

    def test_not_found(self):
        self.assertEqual(self._code("missing"), "PATH_NOT_FOUND")
        self.assertEqual(self._code("items[0].amount"), "PATH_NOT_FOUND")
        # 字段名只在当前 tuple 层查找，不向下穿透。
        self.assertEqual(self._code("to"), "PATH_NOT_FOUND")

    def test_out_of_range(self):
        self.assertEqual(self._code("items[2]"), "PATH_OUT_OF_RANGE")
        self.assertEqual(self._code("items[0].amounts[3]"), "PATH_OUT_OF_RANGE")
        self.assertEqual(self._code("tag[0]"), "PATH_TYPE_MISMATCH")
        t = parse_abi_type("(uint256,uint256)")
        data = encode_abi_value(t, (1, 2))
        with self.assertRaises(AbiPathError) as caught:
            get_abi_value_at_path(t, data, "[2]")
        self.assertEqual(caught.exception.code, "PATH_OUT_OF_RANGE")

    def test_absurdly_large_index_is_out_of_range_not_syntax(self):
        huge = "[" + "9" * 10000 + "]"
        with self.assertRaises(AbiPathError) as caught:
            get_abi_value_at_path(self.t, self.data, "items" + huge)
        self.assertEqual(caught.exception.code, "PATH_OUT_OF_RANGE")
        t = parse_abi_type("(uint256,uint256)")
        data = encode_abi_value(t, (1, 2))
        with self.assertRaises(AbiPathError) as caught:
            get_abi_value_at_path(t, data, huge)
        self.assertEqual(caught.exception.code, "PATH_OUT_OF_RANGE")

    def test_type_mismatch(self):
        # 对基础类型继续步进。
        self.assertEqual(self._code("ts[0]"), "PATH_TYPE_MISMATCH")
        self.assertEqual(self._code("ts.next"), "PATH_TYPE_MISMATCH")
        self.assertEqual(self._code("ok[0]"), "PATH_TYPE_MISMATCH")
        self.assertEqual(self._code("tag.name"), "PATH_TYPE_MISMATCH")
        # 对数组使用字段名。
        self.assertEqual(self._code("items.who"), "PATH_TYPE_MISMATCH")
        # 对数组元素（基础类型）步进在更深位置同样报 TYPE_MISMATCH。
        self.assertEqual(self._code("items[0].amounts[0].x"), "PATH_TYPE_MISMATCH")


class PathReplaceTests(unittest.TestCase):
    def setUp(self):
        self.t = named(OUTER_SRC)
        self.value = outer_value()
        self.data = encode_abi_value(self.t, self.value)

    def _decode(self, data):
        return decode_abi_value(self.t, data)

    def test_replace_static_leaf(self):
        new = replace_abi_value_at_path(self.t, self.data, "ts", 1234)
        self.assertEqual(get_abi_value_at_path(self.t, new, "ts"), 1234)
        decoded = self._decode(new)
        self.assertEqual(decoded[0], ADDR_A)
        self.assertEqual(decoded[1], self.value[1])
        self.assertEqual(decoded[3], b"\xab" * 32)
        self.assertIs(decoded[4], True)

    def test_replace_grows_dynamic_sibling_offsets_adjust(self):
        # 把第二个 Inner 的 amounts 拉长；同 tuple 内 bool 与外层兄弟均须完好。
        new = replace_abi_value_at_path(
            self.t, self.data, "items[1].amounts", [40, 50, 60, 70, 80]
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[1][1], (ADDR_C, [40, 50, 60, 70, 80], False))
        self.assertEqual(decoded[1][0], (ADDR_B, [1, 2, 3], True))
        self.assertEqual(decoded[0], ADDR_A)
        self.assertEqual(decoded[2], 99)
        self.assertEqual(decoded[3], b"\xab" * 32)

    def test_replace_shrinks_dynamic(self):
        new = replace_abi_value_at_path(
            self.t, self.data, "items[1].amounts", [40]
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[1][1], (ADDR_C, [40], False))
        self.assertEqual(decoded[1][0], (ADDR_B, [1, 2, 3], True))
        self.assertEqual(decoded[2], 99)

    def test_replace_string_changes_byte_length(self):
        t = named("(string a,uint256 b,string c)")
        data = encode_abi_value(t, ("hello", 7, "world"))
        bigger = replace_abi_value_at_path(
            t, data, "a", "a much longer string than before"
        )
        self.assertEqual(self._decode_at(t, bigger),
                         ("a much longer string than before", 7, "world"))
        smaller = replace_abi_value_at_path(t, data, "c", "")
        self.assertEqual(self._decode_at(t, smaller), ("hello", 7, ""))

    @staticmethod
    def _decode_at(t, data):
        return decode_abi_value(t, data)

    def test_replace_whole_inner_structure(self):
        replacement = (ADDR_A, [9] * 5, True)
        new = replace_abi_value_at_path(self.t, self.data, "items[0]", replacement)
        self.assertEqual(self._decode(new)[1][0], replacement)
        self.assertEqual(self._decode(new)[1][1], (ADDR_C, [40, 50], False))

    def test_replace_array_length_via_dynamic_array_value(self):
        new_items = [
            (ADDR_B, [1], True),
            (ADDR_C, [2, 3], False),
            (ADDR_A, [4, 5, 6], True),
        ]
        new = replace_abi_value_at_path(self.t, self.data, "items", new_items)
        self.assertEqual(self._decode(new)[1], new_items)
        self.assertEqual(self._decode(new)[2], 99)

    def test_fixed_array_element_replace(self):
        t = parse_abi_type("uint256[3]")
        data = encode_abi_value(t, [1, 2, 3])
        new = replace_abi_value_at_path(t, data, "[1]", 22)
        self.assertEqual(decode_abi_value(t, new), [1, 22, 3])

    def test_nested_dynamic_arrays_replace(self):
        t = parse_abi_type("uint256[][2]")
        data = encode_abi_value(t, [[1, 2], [3, 4, 5]])
        new = replace_abi_value_at_path(t, data, "[0]", [9, 8, 7, 6])
        self.assertEqual(decode_abi_value(t, new), [[9, 8, 7, 6], [3, 4, 5]])
        leaf = replace_abi_value_at_path(t, data, "[1][2]", 55)
        self.assertEqual(decode_abi_value(t, leaf), [[1, 2], [3, 4, 55]])

    def test_replace_root_value(self):
        replacement = (
            ADDR_C,
            [(ADDR_A, [5], False)],
            1,
            b"\x00" * 32,
            False,
        )
        new = replace_abi_value_at_path(self.t, self.data, "", replacement)
        self.assertEqual(new, encode_abi_value(self.t, replacement))
        self.assertEqual(self._decode(new), replacement)

    def test_original_bytes_never_mutated(self):
        before = bytes(self.data)
        new = replace_abi_value_at_path(
            self.t, self.data, "items[0].amounts", [1]
        )
        self.assertIsNot(new, self.data)
        self.assertEqual(self.data, before)
        self.assertEqual(self._decode(self.data), self.value)

        # 调用方持有解码出的 list/tuple，替换过程也不得改动它。
        items_view = self._decode(self.data)[1]
        replace_abi_value_at_path(self.t, self.data, "items[0].amounts", [1])
        self.assertEqual(items_view, self.value[1])

    def test_chained_replacements_round_trip(self):
        first = replace_abi_value_at_path(self.t, self.data, "ts", 1)
        second = replace_abi_value_at_path(self.t, first, "ok", False)
        third = replace_abi_value_at_path(
            self.t, second, "items[0].amounts[1]", 222
        )
        decoded = self._decode(third)
        self.assertEqual(decoded[2], 1)
        self.assertIs(decoded[4], False)
        self.assertEqual(decoded[1][0][1], [1, 222, 3])
        # 未选中的叶子始终保持。
        self.assertEqual(decoded[0], ADDR_A)

    def test_hex_input_and_bytes_output(self):
        new = replace_abi_value_at_path(
            self.t, "0x" + self.data.hex(), "tag", b"\xcd" * 32
        )
        self.assertIsInstance(new, bytes)
        self.assertEqual(get_abi_value_at_path(self.t, new, "tag"), b"\xcd" * 32)

    def test_value_mismatch_codes(self):
        cases = [
            ("ts", "not-an-int"),
            ("ts", -1),
            ("ts", 1 << 256),
            ("ok", 1),
            ("sender", "0x12"),
            ("sender", 123),
            ("tag", b"\x00" * 31),
            ("items", ("not", "a", "list")),
            ("items[0]", (ADDR_B, [1], True, "extra")),
            ("items[0].amounts", [1, "x"]),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_value_at_path(self.t, self.data, path, value)
                self.assertEqual(caught.exception.code, "PATH_VALUE_MISMATCH")

    def test_navigation_errors_precede_value_check(self):
        # 路径本身走不通时，先报路径导航错误，不评估替换值。
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_value_at_path(self.t, self.data, "nope", 1)
        self.assertEqual(caught.exception.code, "PATH_NOT_FOUND")
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_value_at_path(self.t, self.data, "items[9]", 1)
        self.assertEqual(caught.exception.code, "PATH_OUT_OF_RANGE")

    def test_equal_value_replace_reproduces_exact_bytes(self):
        # 用同值替换动态叶子，规范化重编码结果与原字节逐字节一致。
        new = replace_abi_value_at_path(
            self.t, self.data, "items[0].amounts", [1, 2, 3]
        )
        self.assertEqual(new, self.data)

    def test_success_returns_data_not_error_and_reenters_codec(self):
        new = replace_abi_value_at_path(self.t, self.data, "ts", 7)
        # 新编码可继续进入既有完整解码与路径读取。
        self.assertEqual(get_abi_value_at_path(self.t, new, "ts"), 7)
        self.assertEqual(decode_abi_value(self.t, new)[2], 7)
        # 替换下来的子值也能按其自身类型重新编码。
        item = get_abi_value_at_path(self.t, new, "items[0]")
        item_type = self.t.components[1].element_type
        self.assertEqual(
            encode_abi_value(item_type, item),
            encode_abi_value(item_type, self.value[1][0]),
        )


class PathReplaceManyTests(unittest.TestCase):
    def setUp(self):
        self.t = named(OUTER_SRC)
        self.value = outer_value()
        self.data = encode_abi_value(self.t, self.value)

    def _decode(self, data):
        return decode_abi_value(self.t, data)

    def test_empty_sequence_returns_equal_new_bytes(self):
        new = replace_abi_values_at_paths(self.t, self.data, [])
        self.assertIsInstance(new, bytes)
        self.assertEqual(new, self.data)
        self.assertIsNot(new, self.data)
        # tuple 形式的空序列同样合法。
        self.assertEqual(
            replace_abi_values_at_paths(self.t, self.data, ()), self.data
        )
        # 十六进制输入同样得到规范编码。
        self.assertEqual(
            replace_abi_values_at_paths(self.t, "0x" + self.data.hex(), []),
            self.data,
        )

    def test_replace_multiple_sibling_fields(self):
        new = replace_abi_values_at_paths(
            self.t,
            self.data,
            [("sender", ADDR_C), ("ts", 1234), ("ok", False)],
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[0], ADDR_C)
        self.assertEqual(decoded[2], 1234)
        self.assertIs(decoded[4], False)
        # 未选中的兄弟值保持不变。
        self.assertEqual(decoded[1], self.value[1])
        self.assertEqual(decoded[3], b"\xab" * 32)

    def test_replace_different_array_elements_and_their_children(self):
        new = replace_abi_values_at_paths(
            self.t,
            self.data,
            [
                ("items[0].to", ADDR_A),
                ("items[0].flag", False),
                ("items[1].amounts[0]", 77),
            ],
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[1][0], (ADDR_A, [1, 2, 3], False))
        self.assertEqual(decoded[1][1], (ADDR_C, [77, 50], False))
        # 外层兄弟字段不变。
        self.assertEqual(decoded[2], 99)

    def test_simultaneous_dynamic_siblings_grow_and_shrink(self):
        new = replace_abi_values_at_paths(
            self.t,
            self.data,
            [
                ("items[0].amounts", [1]),
                ("items[1].amounts", [40, 50, 60, 70, 80]),
            ],
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[1][0], (ADDR_B, [1], True))
        self.assertEqual(decoded[1][1], (ADDR_C, [40, 50, 60, 70, 80], False))
        self.assertEqual(decoded[0], ADDR_A)
        self.assertEqual(decoded[2], 99)
        self.assertEqual(decoded[3], b"\xab" * 32)
        self.assertIs(decoded[4], True)

    def test_dynamic_string_and_bytes_siblings(self):
        t = named("(string a,bytes b,string c,uint256 n)")
        data = encode_abi_value(
            t, ("hello", b"\x01\x02", "world", 5)
        )
        new = replace_abi_values_at_paths(
            t,
            data,
            [("a", "a much longer string"), ("b", b""), ("n", 42)],
        )
        self.assertEqual(
            decode_abi_value(t, new),
            ("a much longer string", b"", "world", 42),
        )
        new2 = replace_abi_values_at_paths(
            t, data, [("a", ""), ("c", "x" * 100)]
        )
        self.assertEqual(
            decode_abi_value(t, new2),
            ("", b"\x01\x02", "x" * 100, 5),
        )

    def test_replace_whole_dynamic_subtrees_as_siblings(self):
        t = named(
            "((address to,uint256[] amounts)[] items,"
            "(string note,bytes blob) meta,uint256 ts)"
        )
        value = (
            [(ADDR_A, [1, 2]), (ADDR_B, [3])],
            ("note", b"\xaa"),
            7,
        )
        data = encode_abi_value(t, value)
        new = replace_abi_values_at_paths(
            t,
            data,
            [
                ("items", [(ADDR_C, [9, 8, 7, 6])]),
                ("meta", ("longer note", b"\xbb\xcc\xdd")),
            ],
        )
        self.assertEqual(
            decode_abi_value(t, new),
            (
                [(ADDR_C, [9, 8, 7, 6])],
                ("longer note", b"\xbb\xcc\xdd"),
                7,
            ),
        )

    def test_replacements_container_and_pair_shapes_accepted(self):
        # list[list]、tuple[tuple]、tuple[list] 均可。
        expected = None
        for shape in (
            [["ts", 1], ["ok", False]],
            (("ts", 1), ("ok", False)),
            (["ts", 1], ("ok", False)),
        ):
            with self.subTest(shape=shape):
                new = replace_abi_values_at_paths(self.t, self.data, shape)
                decoded = self._decode(new)
                self.assertEqual(decoded[2], 1)
                self.assertIs(decoded[4], False)

    def test_single_root_path_replaces_whole_value(self):
        replacement = (
            ADDR_C,
            [(ADDR_A, [5], False)],
            1,
            b"\x00" * 32,
            False,
        )
        new = replace_abi_values_at_paths(self.t, self.data, [("", replacement)])
        self.assertEqual(new, encode_abi_value(self.t, replacement))

    def test_disjoint_multi_replace_matches_chained_single_replaces(self):
        first = replace_abi_value_at_path(self.t, self.data, "ts", 11)
        second = replace_abi_value_at_path(self.t, first, "ok", False)
        third = replace_abi_value_at_path(
            self.t, second, "items[0].amounts[1]", 222
        )
        bulk = replace_abi_values_at_paths(
            self.t,
            self.data,
            [("ts", 11), ("ok", False), ("items[0].amounts[1]", 222)],
        )
        self.assertEqual(bulk, third)

    def test_original_bytes_and_caller_value_views_not_mutated(self):
        before = bytes(self.data)
        items_view = self._decode(self.data)[1]
        new = replace_abi_values_at_paths(
            self.t,
            self.data,
            [
                ("items[0].amounts", [1]),
                ("items[1].to", ADDR_A),
                ("ts", 2),
            ],
        )
        self.assertEqual(self.data, before)
        self.assertEqual(self._decode(self.data), self.value)
        self.assertEqual(items_view, self.value[1])
        self.assertIsNot(new, self.data)

    def test_equal_value_replacements_reproduce_exact_bytes(self):
        new = replace_abi_values_at_paths(
            self.t,
            self.data,
            [
                ("ts", 99),
                ("items[0].amounts", [1, 2, 3]),
                ("sender", ADDR_A),
            ],
        )
        self.assertEqual(new, self.data)

    def test_hex_input_bytes_output(self):
        new = replace_abi_values_at_paths(
            self.t,
            "0x" + self.data.hex(),
            [("tag", b"\xcd" * 32)],
        )
        self.assertIsInstance(new, bytes)
        self.assertEqual(get_abi_value_at_path(self.t, new, "tag"), b"\xcd" * 32)

    def test_invalid_replacements_shape(self):
        bad_sequences = [
            None,
            {("ts", 1)},
            {"ts": 1},
            iter([("ts", 1)]),
            "ts",
        ]
        for replacements in bad_sequences:
            with self.subTest(replacements=replacements):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(self.t, self.data, replacements)
                self.assertEqual(
                    caught.exception.code, "PATH_REPLACEMENTS_INVALID"
                )

        bad_elements = [
            [("ts", 1), "not-a-pair"],
            [("ts",)],
            [("ts", 1, 2)],
            [42],
            [b"ts"],
            [("ts", 1), [1, 2, 3]],
        ]
        for replacements in bad_elements:
            with self.subTest(replacements=replacements):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(self.t, self.data, replacements)
                self.assertEqual(
                    caught.exception.code, "PATH_REPLACEMENTS_INVALID"
                )

    def test_shape_checked_before_encoding_and_navigation(self):
        # 序列形状非法优先于一切：即便原始编码损坏、路径不存在也先报形状。
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t, b"\x00" * 3, [("nope", 1), 2]
            )
        self.assertEqual(caught.exception.code, "PATH_REPLACEMENTS_INVALID")

    def test_duplicate_path_conflict(self):
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t, self.data, [("ts", 1), ("ts", 2)]
            )
        self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_same_target_via_field_name_and_position_conflicts(self):
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t, self.data, [("sender", ADDR_B), ("[0]", ADDR_C)]
            )
        self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_ancestor_descendant_conflicts(self):
        cases = [
            [("items", []), ("items[0].to", ADDR_A)],
            [("items[0].amounts", [1]), ("items[0].amounts[0]", 2)],
            # 祖先在后同样拒绝。
            [("items[0].to", ADDR_A), ("items", [])],
            [("items[0]", (ADDR_A, [1], True)), ("items[0].flag", False)],
        ]
        for replacements in cases:
            with self.subTest(replacements=replacements):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(
                        self.t, self.data, replacements
                    )
                self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_root_path_conflicts_with_any_other(self):
        root_replacement = self.value
        for other in ("ts", "items[0]", "items[1].amounts[0]"):
            with self.subTest(other=other):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(
                        self.t,
                        self.data,
                        [("", root_replacement), (other, 1)],
                    )
                self.assertEqual(caught.exception.code, "PATH_CONFLICT")
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(
                        self.t,
                        self.data,
                        [(other, 1), ("", root_replacement)],
                    )
                self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_conflict_detected_before_value_validation_regardless_of_order(self):
        # 后代的替换值类型错误，但冲突先于值校验、且与顺序无关。
        bad_value = ("wrong", "tuple")
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t, self.data, [("items[0]", bad_value), ("items", [])]
            )
        self.assertEqual(caught.exception.code, "PATH_CONFLICT")
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t, self.data, [("items", []), ("items[0]", bad_value)]
            )
        self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_conflict_rejects_whole_operation_atomically(self):
        before = bytes(self.data)
        with self.assertRaises(AbiPathError):
            replace_abi_values_at_paths(
                self.t,
                self.data,
                [("ts", 1), ("items", []), ("items[0].to", ADDR_A)],
            )
        # 原编码未受影响，也没有任何"部分应用"的产物。
        self.assertEqual(self.data, before)
        self.assertEqual(get_abi_value_at_path(self.t, self.data, "ts"), 99)

    def test_path_errors_chain_from_existing_codes(self):
        cases = [
            ("nope", 1, "PATH_NOT_FOUND"),
            ("items[9]", 1, "PATH_OUT_OF_RANGE"),
            ("items[0].amounts[3]", 1, "PATH_OUT_OF_RANGE"),
            ("ts[0]", 1, "PATH_TYPE_MISMATCH"),
            ("items.who", ADDR_A, "PATH_TYPE_MISMATCH"),
            ("items[", 1, "PATH_SYNTAX"),
            ("ts", "not-an-int", "PATH_VALUE_MISMATCH"),
            ("ok", 1, "PATH_VALUE_MISMATCH"),
            ("sender", 123, "PATH_VALUE_MISMATCH"),
            ("items[0].amounts", [1, "x"], "PATH_VALUE_MISMATCH"),
        ]
        for path, value, code in cases:
            with self.subTest(path=path, code=code):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(
                        self.t, self.data,
                        [("tag", b"\xab" * 32), (path, value)],
                    )
                self.assertEqual(caught.exception.code, code)

    def test_navigation_error_precedes_later_value_check(self):
        # 后面的替换值即使类型正确，前面路径走不通仍按导航错误拒绝。
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t,
                self.data,
                [("items[9]", 1), ("ts", "bad")],
            )
        self.assertEqual(caught.exception.code, "PATH_OUT_OF_RANGE")

    def test_non_string_path_is_path_syntax(self):
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(self.t, self.data, [(0, 1)])
        self.assertEqual(caught.exception.code, "PATH_SYNTAX")

    def test_malformed_encoding_still_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            replace_abi_values_at_paths(self.t, b"\x00" * 10, [])
        with self.assertRaises(ABIValueError):
            replace_abi_values_at_paths(
                self.t, b"\x00" * 10, [("ts", 1)]
            )
        with self.assertRaises(ABIValueError):
            replace_abi_values_at_paths(self.t, "not-hex", [])

    def test_deeply_nested_sibling_replacements(self):
        t = parse_abi_type("uint256[][2]")
        data = encode_abi_value(t, [[1, 2, 3], [4, 5, 6]])
        new = replace_abi_values_at_paths(
            t,
            data,
            [("[0][0]", 10), ("[0][2]", 30), ("[1][1]", 50)],
        )
        self.assertEqual(
            decode_abi_value(t, new), [[10, 2, 30], [4, 50, 6]]
        )

        t2 = named("((uint256 amount,address who)[] logs,uint256 block)")
        data2 = encode_abi_value(
            t2, ([(1, ADDR_A), (2, ADDR_B), (3, ADDR_C)], 55)
        )
        new2 = replace_abi_values_at_paths(
            t2,
            data2,
            [
                ("logs[0].who", ADDR_C),
                ("logs[2].amount", 33),
                ("block", 56),
            ],
        )
        self.assertEqual(
            decode_abi_value(t2, new2),
            ([(1, ADDR_C), (2, ADDR_B), (33, ADDR_C)], 56),
        )


if __name__ == "__main__":
    unittest.main()
