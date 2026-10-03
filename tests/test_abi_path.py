"""abi_get_at_path / abi_replace_at_path 的行为测试。

仅使用标准库 unittest，无第三方依赖。覆盖 tuple 字段、动态/定长数组、
嵌套数组、tuple 内嵌数组的读取与定点替换，以及五类 AbiPathError 错误码。
"""

import unittest

from abi_kit import (
    AbiPathError,
    ABITypeError,
    ABIValueError,
    abi_get_at_path,
    abi_replace_at_path,
    decode_abi_value,
    encode_abi_value,
    parse_abi_type,
)


def enc(type_string, value):
    return encode_abi_value(parse_abi_type(type_string), value)


def get(type_string, data, path):
    return abi_get_at_path(parse_abi_type(type_string), data, path)


def put(type_string, data, path, replacement):
    return abi_replace_at_path(parse_abi_type(type_string), data, path, replacement)


# 带字段名的示例类型：外层 order，内含 buyer / items（元素为 amount/price）。
ORDER_TYPE = "(address buyer, (uint256 amount, uint256 price)[] items)"
ORDER_VALUE = (
    "0x" + "11" * 20,
    [(10, 100), (20, 200), (30, 300)],
)


class ReadTupleFieldTests(unittest.TestCase):
    def setUp(self):
        self.data = enc(ORDER_TYPE, ORDER_VALUE)

    def test_shallow_field(self):
        self.assertEqual(get(ORDER_TYPE, self.data, "buyer"), "0x" + "11" * 20)

    def test_field_returns_address_lowercased(self):
        result = get(ORDER_TYPE, self.data, "buyer")
        self.assertEqual(result, result.lower())

    def test_nested_tuple_field(self):
        # items 动态数组整体：只返回被选中的数组，不包含外层 order。
        self.assertEqual(get(ORDER_TYPE, self.data, "items"), ORDER_VALUE[1])

    def test_nested_array_element(self):
        self.assertEqual(get(ORDER_TYPE, self.data, "items[1]"), (20, 200))

    def test_deep_field_via_array(self):
        self.assertEqual(get(ORDER_TYPE, self.data, "items[2].amount"), 30)
        self.assertEqual(get(ORDER_TYPE, self.data, "items[0].price"), 100)

    def test_selected_substructure_excludes_container(self):
        item = get(ORDER_TYPE, self.data, "items[2]")
        # 数组元素是一个二元 tuple，而不是把整个 order 包回来。
        self.assertIsInstance(item, tuple)
        self.assertEqual(len(item), 2)


class ReadArrayTests(unittest.TestCase):
    def test_dynamic_array_index_at_root(self):
        data = enc("uint256[]", [7, 8, 9])
        self.assertEqual(get("uint256[]", data, "[0]"), 7)
        self.assertEqual(get("uint256[]", data, "[2]"), 9)

    def test_fixed_array_index(self):
        data = enc("bool[3]", [True, False, True])
        self.assertIs(get("bool[3]", data, "[1]"), False)

    def test_nested_dynamic_arrays(self):
        data = enc("uint8[][]", [[1, 2], [], [3]])
        self.assertEqual(get("uint8[][]", data, "[0]"), [1, 2])
        self.assertEqual(get("uint8[][]", data, "[0][1]"), 2)
        self.assertEqual(get("uint8[][]", data, "[2][0]"), 3)
        self.assertEqual(get("uint8[][]", data, "[1]"), [])

    def test_fixed_of_dynamic_arrays(self):
        data = enc("uint8[][2]", [[1], [2, 3]])
        self.assertEqual(get("uint8[][2]", data, "[1][0]"), 2)

    def test_numeric_index_into_tuple(self):
        data = enc("(uint256 a, uint256 b)", (5, 6))
        self.assertEqual(get("(uint256 a, uint256 b)", data, "[0]"), 5)
        self.assertEqual(get("(uint256 a, uint256 b)", data, "[1]"), 6)

    def test_tuple_containing_nested_arrays(self):
        type_string = "((uint8 x)[2][] grid, bool flag)"
        value = ([[(1,), (9,)], [(2,), (3,)]], True)
        data = enc(type_string, value)
        self.assertEqual(get(type_string, data, "grid[1][0].x"), 2)
        self.assertIs(get(type_string, data, "flag"), True)


class EmptyPathTests(unittest.TestCase):
    def test_empty_path_reads_root(self):
        data = enc("uint256[2]", [4, 5])
        self.assertEqual(get("uint256[2]", data, ""), [4, 5])

    def test_empty_path_reads_tuple_root(self):
        data = enc(ORDER_TYPE, ORDER_VALUE)
        self.assertEqual(get(ORDER_TYPE, data, ""), ORDER_VALUE)

    def test_empty_path_replaces_root_static(self):
        data = enc("uint256", 1)
        new = put("uint256", data, "", 42)
        self.assertEqual(decode_abi_value(parse_abi_type("uint256"), new), 42)

    def test_empty_path_replaces_root_with_length_change(self):
        data = enc("bytes", b"old")
        replacement = b"y" * 33  # 跨越一个 32 字节填充边界，编码总长随之增加
        new = put("bytes", data, "", replacement)
        self.assertEqual(
            decode_abi_value(parse_abi_type("bytes"), new),
            replacement,
        )
        self.assertNotEqual(len(new), len(data))


class ReplaceTests(unittest.TestCase):
    def test_replace_leaf_keeps_siblings(self):
        data = enc(ORDER_TYPE, ORDER_VALUE)
        new = put(ORDER_TYPE, data, "items[1].amount", 21)
        decoded = decode_abi_value(parse_abi_type(ORDER_TYPE), new)
        self.assertEqual(decoded[0], ORDER_VALUE[0])           # buyer 不变
        self.assertEqual(decoded[1][0], (10, 100))
        self.assertEqual(decoded[1][1], (21, 200))            # 定点替换
        self.assertEqual(decoded[1][2], (30, 300))

    def test_replace_dynamic_content_length_preserves_siblings(self):
        type_string = "(string a, string b, uint256 c)"
        data = enc(type_string, ("hi", "keep", 7))
        new = put(type_string, data, "a", "x" * 100)
        self.assertEqual(
            decode_abi_value(parse_abi_type(type_string), new),
            ("x" * 100, "keep", 7),
        )
        self.assertGreater(len(new), len(data))

    def test_replace_string_shorter_preserves_siblings(self):
        type_string = "(string a, string b)"
        data = enc(type_string, ("longfield", "sibling"))
        new = put(type_string, data, "a", "")
        self.assertEqual(
            decode_abi_value(parse_abi_type(type_string), new),
            ("", "sibling"),
        )

    def test_replace_array_element_in_nested_dynamic(self):
        type_string = "(string[] tags)"
        data = enc(type_string, (["a", "bb"],))
        new = put(type_string, data, "tags[0]", "z" * 50)
        self.assertEqual(
            decode_abi_value(parse_abi_type(type_string), new),
            (["z" * 50, "bb"],),
        )

    def test_replace_whole_dynamic_array_leaf(self):
        type_string = "(uint256 fixed_pad, uint256[] dyn)"
        data = enc(type_string, (0, [3, 4, 5]))
        new = put(type_string, data, "dyn", [9])
        self.assertEqual(
            decode_abi_value(parse_abi_type(type_string), new),
            (0, [9]),
        )

    def test_replace_tuple_leaf(self):
        type_string = "((uint256 a, bool b) inner)"
        data = enc(type_string, ((1, True),))
        new = put(type_string, data, "inner", (8, False))
        self.assertEqual(
            decode_abi_value(parse_abi_type(type_string), new),
            ((8, False),),
        )

    def test_replace_elementary_value_kinds(self):
        type_string = "(address to, bool ok, bytes data, bytes32 h, uint256 n)"
        data = enc(
            type_string,
            ("0x" + "a" * 40, True, b"x", b"\x01" * 32, 0),
        )
        new = put(type_string, data, "to", "0x" + "b" * 40)
        new = put(type_string, new, "ok", False)
        new = put(type_string, new, "data", b"hello")
        new = put(type_string, new, "h", b"\x02" * 32)
        new = put(type_string, new, "n", 123)
        self.assertEqual(
            decode_abi_value(parse_abi_type(type_string), new),
            ("0x" + "b" * 40, False, b"hello", b"\x02" * 32, 123),
        )

    def test_replace_does_not_mutate_input(self):
        type_string = "(string[] tags)"
        original = enc(type_string, (["a", "bb"],))
        buf = bytearray(original)
        snapshot = bytes(buf)
        put(type_string, buf, "tags[1]", "qq")
        self.assertEqual(bytes(buf), snapshot)
        # 返回 bytes 后再解码，原始编码对应的值不变。
        self.assertEqual(
            decode_abi_value(parse_abi_type(type_string), bytes(buf)),
            (["a", "bb"],),
        )

    def test_result_re_enters_codec_round_trip(self):
        data = enc(ORDER_TYPE, ORDER_VALUE)
        new = put(ORDER_TYPE, data, "items[0]", (99, 999))
        # 新编码可继续进入既有解码/编码流程且保持稳定。
        decoded = decode_abi_value(parse_abi_type(ORDER_TYPE), new)
        self.assertEqual(enc(ORDER_TYPE, decoded), new)


class PathSyntaxTests(unittest.TestCase):
    DATA = enc(ORDER_TYPE, ORDER_VALUE)

    def assert_syntax(self, path):
        with self.assertRaises(AbiPathError) as cm:
            abi_get_at_path(parse_abi_type(ORDER_TYPE), self.DATA, path)
        self.assertEqual(cm.exception.code, AbiPathError.PATH_SYNTAX)

    def test_bad_syntax(self):
        for path in (
            ".", "..", "a..b", "inner.", ".inner",
            "items[]", "items[-1]", "items[+1]",
            "items[ 2]", "items[2 ]", "items[1.5]",
            "buyer .items", "items,", "[2]x", "items.",
            "123", "items[2].", "items [2]",
        ):
            with self.subTest(path=path):
                self.assert_syntax(path)

    def test_leading_zero_index_is_decimal_one(self):
        # 规范只要求非负十进制数字，未禁止前导零：[01] 等价于 [1]。
        self.assertEqual(
            abi_get_at_path(
                parse_abi_type(ORDER_TYPE), self.DATA, "items[01].amount"
            ),
            20,
        )

    def test_zero_index_accepted(self):
        self.assertEqual(
            abi_get_at_path(parse_abi_type(ORDER_TYPE), self.DATA, "items[0].amount"),
            10,
        )

    def test_non_string_path(self):
        for path in (None, 5, ["items"], ("items",)):
            with self.subTest(path=path):
                self.assert_syntax(path)


class PathResolutionErrorTests(unittest.TestCase):
    def setUp(self):
        self.data = enc(ORDER_TYPE, ORDER_VALUE)

    def assert_code(self, path, code):
        with self.assertRaises(AbiPathError) as cm:
            abi_get_at_path(parse_abi_type(ORDER_TYPE), self.data, path)
        self.assertEqual(cm.exception.code, code)

    def test_index_out_of_range(self):
        self.assert_code("items[3]", AbiPathError.PATH_OUT_OF_RANGE)
        self.assert_code("[2]", AbiPathError.PATH_OUT_OF_RANGE)  # 根 tuple 仅 2 个成员

    def test_field_not_found(self):
        self.assert_code("missing", AbiPathError.PATH_NOT_FOUND)
        self.assert_code("items.nope", AbiPathError.PATH_NOT_FOUND)

    def test_step_into_non_container(self):
        self.assert_code(
            "items[0].amount[0]", AbiPathError.PATH_TYPE_MISMATCH
        )
        self.assert_code(
            "buyer.x", AbiPathError.PATH_TYPE_MISMATCH
        )

    def test_field_on_array_is_not_found(self):
        # 数组是容器但没有命名字段：该字段在数组上不存在。
        data = enc("uint256[]", [1, 2])
        with self.assertRaises(AbiPathError) as cm:
            get("uint256[]", data, "length")
        self.assertEqual(cm.exception.code, AbiPathError.PATH_NOT_FOUND)

    def test_unnamed_tuple_numeric_ok_field_not_found(self):
        data = enc("(uint256,uint256)", (5, 6))
        self.assertEqual(get("(uint256,uint256)", data, "[0]"), 5)
        with self.assertRaises(AbiPathError) as cm:
            get("(uint256,uint256)", data, "a")
        self.assertEqual(cm.exception.code, AbiPathError.PATH_NOT_FOUND)

    def test_empty_dynamic_array_index_is_out_of_range(self):
        data = enc("uint256[]", [])
        with self.assertRaises(AbiPathError) as cm:
            get("uint256[]", data, "[0]")
        self.assertEqual(cm.exception.code, AbiPathError.PATH_OUT_OF_RANGE)


class PathValueMismatchTests(unittest.TestCase):
    def setUp(self):
        self.data = enc(ORDER_TYPE, ORDER_VALUE)

    def assert_value_mismatch(self, path, replacement):
        with self.assertRaises(AbiPathError) as cm:
            abi_replace_at_path(
                parse_abi_type(ORDER_TYPE), self.data, path, replacement
            )
        self.assertEqual(cm.exception.code, AbiPathError.PATH_VALUE_MISMATCH)

    def test_wrong_python_type(self):
        self.assert_value_mismatch("items[0].amount", "10")
        self.assert_value_mismatch("buyer", 123)

    def test_integer_out_of_range(self):
        self.assert_value_mismatch("items[0].amount", 2**256)
        self.assert_value_mismatch("items[0].amount", -1)

    def test_bool_strict(self):
        type_string = "(bool ok)"
        data = enc(type_string, (True,))
        with self.assertRaises(AbiPathError) as cm:
            put(type_string, data, "ok", 1)
        self.assertEqual(cm.exception.code, AbiPathError.PATH_VALUE_MISMATCH)

    def test_fixed_array_length_mismatch(self):
        type_string = "(uint256[2] fixed)"
        data = enc(type_string, ([1, 2],))
        with self.assertRaises(AbiPathError) as cm:
            put(type_string, data, "fixed", [1, 2, 3])
        self.assertEqual(cm.exception.code, AbiPathError.PATH_VALUE_MISMATCH)

    def test_tuple_arity_mismatch(self):
        type_string = "((uint256 a, bool b) inner)"
        data = enc(type_string, ((1, True),))
        with self.assertRaises(AbiPathError) as cm:
            put(type_string, data, "inner", (1,))
        self.assertEqual(cm.exception.code, AbiPathError.PATH_VALUE_MISMATCH)


class InputAndTypeHandlingTests(unittest.TestCase):
    def test_accepts_type_string_directly(self):
        data = enc("uint256[]", [4, 5])
        self.assertEqual(abi_get_at_path("uint256[]", data, "[1]"), 5)
        new = abi_replace_at_path("uint256[]", data, "[1]", 6)
        self.assertEqual(decode_abi_value(parse_abi_type("uint256[]"), new), [4, 6])

    def test_malformed_data_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            abi_get_at_path("uint256", b"\x00" * 10, "")
        with self.assertRaises(ABIValueError):
            abi_get_at_path("uint256", "not-bytes", "")

    def test_bad_type_string_raises_type_error(self):
        with self.assertRaises(ABITypeError):
            abi_get_at_path("uint9", b"\x00" * 32, "")

    def test_non_type_object_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            abi_get_at_path(123, b"\x00" * 32, "")


if __name__ == "__main__":
    unittest.main()
