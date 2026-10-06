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


class MultiReplaceTests(unittest.TestCase):
    """replace_abi_values_at_paths：一次调用原子替换多条路径。"""

    def setUp(self):
        self.t = named(OUTER_SRC)
        self.value = outer_value()
        self.data = encode_abi_value(self.t, self.value)

    def _decode(self, data):
        return decode_abi_value(self.t, data)

    def test_replace_multiple_sibling_fields(self):
        new = replace_abi_values_at_paths(
            self.t, self.data, [("ts", 1234), ("ok", False), ("sender", ADDR_C)]
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[0], ADDR_C)
        self.assertEqual(decoded[1], self.value[1])
        self.assertEqual(decoded[2], 1234)
        self.assertEqual(decoded[3], b"\xab" * 32)
        self.assertIs(decoded[4], False)

    def test_replace_multiple_array_elements(self):
        new = replace_abi_values_at_paths(
            self.t,
            self.data,
            [("items[0].amounts[1]", 222), ("items[1].flag", True)],
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[1][0], (ADDR_B, [1, 222, 3], True))
        self.assertEqual(decoded[1][1], (ADDR_C, [40, 50], True))
        self.assertEqual(decoded[2], 99)

    def test_replace_nested_dynamic_values_offsets_recomputed_once(self):
        t = named("(string a,(uint256 n,bytes b) inner,string[] tags)")
        value = ("hello", (7, b"\x01\x02"), ["x", "yy"])
        data = encode_abi_value(t, value)
        new = replace_abi_values_at_paths(
            t,
            data,
            [
                ("a", "a much longer string than before"),
                ("inner.b", b"\x09" * 40),
                ("tags", ["one", "two", "three"]),
                ("inner.n", 8),
            ],
        )
        self.assertEqual(
            decode_abi_value(t, new),
            ("a much longer string than before", (8, b"\x09" * 40),
             ["one", "two", "three"]),
        )

    def test_tuple_and_list_pairs_both_accepted(self):
        new = replace_abi_values_at_paths(
            self.t, self.data, (["ts", 1], ["ok", False])
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[2], 1)
        self.assertIs(decoded[4], False)

    def test_empty_replacements_returns_equal_new_bytes(self):
        new = replace_abi_values_at_paths(self.t, self.data, [])
        self.assertIsInstance(new, bytes)
        self.assertEqual(new, self.data)
        # 十六进制字符串输入同样返回内容相同的新 bytes。
        new_hex = replace_abi_values_at_paths(self.t, "0x" + self.data.hex(), ())
        self.assertEqual(new_hex, self.data)

    def test_empty_replacements_still_validates_encoding(self):
        with self.assertRaises(ABIValueError):
            replace_abi_values_at_paths(self.t, self.data[:-1], [])

    def test_root_and_descendant_replacements(self):
        # 根路径与其他路径冲突；单独用根路径则整体替换。
        replacement = (ADDR_C, [(ADDR_A, [5], False)], 1, b"\x00" * 32, False)
        new = replace_abi_values_at_paths(self.t, self.data, [("", replacement)])
        self.assertEqual(new, encode_abi_value(self.t, replacement))

    def test_original_bytes_never_mutated(self):
        before = bytes(self.data)
        new = replace_abi_values_at_paths(
            self.t, self.data, [("items[0].amounts", [1]), ("ts", 5)]
        )
        self.assertIsNot(new, self.data)
        self.assertEqual(self.data, before)
        self.assertEqual(self._decode(self.data), self.value)

    def test_result_reenters_codec_and_path_reads(self):
        new = replace_abi_values_at_paths(
            self.t, self.data, [("items[1].amounts", [40]), ("tag", b"\xcd" * 32)]
        )
        self.assertEqual(
            get_abi_value_at_path(self.t, new, "items[1].amounts"), [40]
        )
        self.assertEqual(get_abi_value_at_path(self.t, new, "tag"), b"\xcd" * 32)

    def test_replacements_not_a_sequence(self):
        for bad in ("ts", 123, {"ts": 1}, None, b"xx"):
            with self.subTest(bad=bad):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(self.t, self.data, bad)
                self.assertEqual(
                    caught.exception.code, "PATH_REPLACEMENTS_INVALID"
                )

    def test_replacement_element_not_a_pair(self):
        for bad_item in (
            [("ts", 1, 2)],
            [("ts",)],
            ["ts"],
            [None],
            [["ts", 1], ("ok",)],
        ):
            with self.subTest(bad_item=bad_item):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(self.t, self.data, bad_item)
                self.assertEqual(
                    caught.exception.code, "PATH_REPLACEMENTS_INVALID"
                )

    def test_duplicate_path_rejected(self):
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t, self.data, [("ts", 1), ("ts", 2)]
            )
        self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_same_node_via_field_and_position_rejected(self):
        with self.assertRaises(AbiPathError) as caught:
            replace_abi_values_at_paths(
                self.t, self.data, [("ts", 1), ("[2]", 2)]
            )
        self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_ancestor_descendant_rejected(self):
        cases = [
            [("", 1), ("ts", 2)],
            [("items", []), ("items[0]", (ADDR_B, [1], True))],
            [("items[1]", (ADDR_C, [40, 50], False)), ("items[1].amounts", [1])],
            [("items[0].amounts", [1, 2, 3]), ("items[0].amounts[0]", 9)],
        ]
        for pairs in cases:
            with self.subTest(pairs=pairs):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(self.t, self.data, pairs)
                self.assertEqual(caught.exception.code, "PATH_CONFLICT")

    def test_sibling_paths_do_not_conflict(self):
        new = replace_abi_values_at_paths(
            self.t,
            self.data,
            [("items[0]", (ADDR_A, [9], False)), ("items[1].to", ADDR_A)],
        )
        decoded = self._decode(new)
        self.assertEqual(decoded[1][0], (ADDR_A, [9], False))
        self.assertEqual(decoded[1][1], (ADDR_A, [40, 50], False))

    def test_single_path_errors_keep_existing_codes(self):
        cases = [
            ([("ts..x", 1)], "PATH_SYNTAX"),
            ([("items[9]", 1)], "PATH_OUT_OF_RANGE"),
            ([("nope", 1)], "PATH_NOT_FOUND"),
            ([("ts.x", 1)], "PATH_TYPE_MISMATCH"),
            ([("ts", "not-an-int")], "PATH_VALUE_MISMATCH"),
        ]
        for pairs, code in cases:
            with self.subTest(pairs=pairs):
                with self.assertRaises(AbiPathError) as caught:
                    replace_abi_values_at_paths(self.t, self.data, pairs)
                self.assertEqual(caught.exception.code, code)

    def test_invalid_encoding_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            replace_abi_values_at_paths(self.t, self.data[:-1], [("ts", 1)])
        with self.assertRaises(ABIValueError):
            replace_abi_values_at_paths(self.t, "0xzz", [("ts", 1)])

    def test_invalid_type_inputs(self):
        with self.assertRaises(ABITypeError):
            replace_abi_values_at_paths(123, self.data, [("ts", 1)])
        with self.assertRaises(ABITypeError):
            replace_abi_values_at_paths("uint256[", self.data, [("ts", 1)])


if __name__ == "__main__":
    unittest.main()
