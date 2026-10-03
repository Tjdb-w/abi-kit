"""encode_abi_value / decode_abi_value 的行为测试。

仅使用标准库 unittest，无第三方依赖。
"""

import unittest

from abi_kit import (
    ABIValueError,
    decode_abi_value,
    encode_abi_value,
    parse_abi_type,
)


def word(number):
    return number.to_bytes(32, "big")


def enc(type_string, value):
    return encode_abi_value(parse_abi_type(type_string), value)


def dec(type_string, data):
    return decode_abi_value(parse_abi_type(type_string), data)


class EncodeElementaryTests(unittest.TestCase):
    def test_uint_boundaries(self):
        self.assertEqual(enc("uint8", 0), word(0))
        self.assertEqual(enc("uint8", 255), word(255))
        self.assertEqual(enc("uint256", 2**256 - 1), word(2**256 - 1))
        for type_string, value in (
            ("uint8", 256),
            ("uint8", -1),
            ("uint256", 2**256),
            ("uint16", 65536),
        ):
            with self.subTest(type=type_string, value=value):
                with self.assertRaises(ABIValueError):
                    enc(type_string, value)

    def test_int_boundaries(self):
        self.assertEqual(enc("int8", -128), word(2**256 - 128))
        self.assertEqual(enc("int8", 127), word(127))
        self.assertEqual(enc("int256", -(2**255)), word(2**255))
        self.assertEqual(enc("int256", 2**255 - 1), word(2**255 - 1))
        for type_string, value in (
            ("int8", -129),
            ("int8", 128),
            ("int256", 2**255),
            ("int256", -(2**255) - 1),
        ):
            with self.subTest(type=type_string, value=value):
                with self.assertRaises(ABIValueError):
                    enc(type_string, value)

    def test_int_rejects_bool_and_non_int(self):
        for value in (True, False, 1.0, "1", None):
            with self.subTest(value=value):
                with self.assertRaises(ABIValueError):
                    enc("uint8", value)
                with self.assertRaises(ABIValueError):
                    enc("int8", value)

    def test_bool_strict(self):
        self.assertEqual(enc("bool", True), word(1))
        self.assertEqual(enc("bool", False), word(0))
        for value in (0, 1, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ABIValueError):
                    enc("bool", value)

    def test_address(self):
        raw = "a" * 40
        self.assertEqual(enc("address", raw), word(int(raw, 16)))
        self.assertEqual(enc("address", "0x" + raw), word(int(raw, 16)))
        self.assertEqual(enc("address", "0x" + "A" * 40), word(int(raw, 16)))
        for value in ("0x" + "a" * 39, "a" * 41, "0x", "g" * 40, 123, b"\x00" * 20):
            with self.subTest(value=value):
                with self.assertRaises(ABIValueError):
                    enc("address", value)

    def test_string(self):
        self.assertEqual(enc("string", ""), word(32) + word(0))
        self.assertEqual(
            enc("string", "abc"),
            word(32) + word(3) + b"abc" + b"\x00" * 29,
        )
        with self.assertRaises(ABIValueError):
            enc("string", b"abc")

    def test_dynamic_bytes(self):
        self.assertEqual(enc("bytes", b""), word(32) + word(0))
        payload = bytes(range(40))
        self.assertEqual(
            enc("bytes", payload),
            word(32) + word(40) + payload + b"\x00" * 24,
        )
        with self.assertRaises(ABIValueError):
            enc("bytes", "abc")

    def test_bytes_m(self):
        self.assertEqual(enc("bytes1", b"\xab"), b"\xab" + b"\x00" * 31)
        self.assertEqual(enc("bytes32", b"\x01" * 32), b"\x01" * 32)
        for type_string, value in (
            ("bytes1", b""),
            ("bytes1", b"\x01\x02"),
            ("bytes32", b"\x01" * 31),
        ):
            with self.subTest(type=type_string, value=value):
                with self.assertRaises(ABIValueError):
                    enc(type_string, value)


class DecodeElementaryTests(unittest.TestCase):
    def test_value_shapes(self):
        self.assertEqual(dec("uint8", word(7)), 7)
        self.assertIsInstance(dec("uint8", word(7)), int)
        self.assertIs(dec("bool", word(1)), True)
        self.assertIs(dec("bool", word(0)), False)
        self.assertEqual(dec("string", word(32) + word(0)), "")
        self.assertEqual(dec("bytes", word(32) + word(0)), b"")
        self.assertEqual(dec("bytes2", b"\x01\x02" + b"\x00" * 30), b"\x01\x02")
        self.assertEqual(
            dec("address", word(0xABCDEF0000000000000000000000000000000001)),
            "0xabcdef0000000000000000000000000000000001",
        )

    def test_address_is_lowercased(self):
        data = word(int("ABCDEF" + "0" * 34, 16))
        self.assertEqual(dec("address", data), "0x" + "abcdef" + "0" * 34)

    def test_int_decode_sign_extension(self):
        self.assertEqual(dec("int8", word(2**256 - 1)), -1)
        self.assertEqual(dec("int8", word(2**256 - 128)), -128)
        self.assertEqual(dec("int8", word(127)), 127)
        # 200 不是合法的 int8 符号扩展
        with self.assertRaises(ABIValueError):
            dec("int8", word(200))

    def test_uint_decode_out_of_range(self):
        with self.assertRaises(ABIValueError):
            dec("uint8", word(256))

    def test_address_decode_out_of_range(self):
        with self.assertRaises(ABIValueError):
            dec("address", word(1 << 160))

    def test_invalid_bool_word(self):
        for number in (2, 255, 2**256 - 1):
            with self.subTest(number=number):
                with self.assertRaises(ABIValueError):
                    dec("bool", word(number))

    def test_invalid_utf8(self):
        data = word(32) + word(1) + b"\xff" + b"\x00" * 31
        with self.assertRaises(ABIValueError):
            dec("string", data)

    def test_bytes_m_nonzero_padding(self):
        with self.assertRaises(ABIValueError):
            dec("bytes1", b"\x01" + b"\x00" * 30 + b"\x01")

    def test_truncated_data(self):
        with self.assertRaises(ABIValueError):
            dec("uint256", word(1)[:31])
        full = enc("string", "hello")
        with self.assertRaises(ABIValueError):
            dec("string", full[:-1])

    def test_offset_out_of_bounds(self):
        with self.assertRaises(ABIValueError):
            dec("string", word(4096) + word(0))
        with self.assertRaises(ABIValueError):
            dec("uint8[]", word(32) + word(2) + word(1))

    def test_trailing_residue(self):
        with self.assertRaises(ABIValueError):
            dec("uint256", word(1) + word(2))
        with self.assertRaises(ABIValueError):
            dec("string", enc("string", "hi") + word(0))

    def test_input_data_not_modified(self):
        data = bytearray(enc("uint256[]", [1, 2, 3]))
        snapshot = bytes(data)
        self.assertEqual(dec("uint256[]", data), [1, 2, 3])
        self.assertEqual(bytes(data), snapshot)

    def test_rejects_non_bytes_data(self):
        for data in ("not-bytes", 123, None, [0] * 32):
            with self.subTest(data=data):
                with self.assertRaises(ABIValueError):
                    dec("uint8", data)


class ArrayTests(unittest.TestCase):
    def test_dynamic_array_layout(self):
        self.assertEqual(
            enc("uint256[]", [1, 2, 3]),
            word(32) + word(3) + word(1) + word(2) + word(3),
        )

    def test_empty_dynamic_array(self):
        self.assertEqual(enc("uint8[]", []), word(32) + word(0))
        self.assertEqual(dec("uint8[]", word(32) + word(0)), [])

    def test_fixed_array(self):
        self.assertEqual(enc("uint8[3]", [1, 2, 3]), word(1) + word(2) + word(3))
        self.assertEqual(dec("uint8[3]", word(1) + word(2) + word(3)), [1, 2, 3])
        self.assertEqual(dec("uint8[3]", enc("uint8[3]", (1, 2, 3))), [1, 2, 3])

    def test_fixed_array_length_mismatch(self):
        for value in ([1, 2], [1, 2, 3, 4], []):
            with self.subTest(value=value):
                with self.assertRaises(ABIValueError):
                    enc("uint8[3]", value)
        # 解码时数据字数与声明长度不符
        with self.assertRaises(ABIValueError):
            dec("uint8[2]", word(1))
        with self.assertRaises(ABIValueError):
            dec("uint8[2]", word(1) + word(2) + word(3))

    def test_array_of_dynamic_elements(self):
        value = ["ab", "", "c"]
        self.assertEqual(dec("string[]", enc("string[]", value)), value)

    def test_nested_arrays(self):
        value = [[1, 2], [], [3]]
        self.assertEqual(dec("uint8[][]", enc("uint8[][]", value)), value)
        fixed = [[True, False], [False, True]]
        self.assertEqual(dec("bool[2][2]", enc("bool[2][2]", fixed)), fixed)

    def test_array_rejects_non_sequence(self):
        for value in (123, "ab", b"ab", None):
            with self.subTest(value=value):
                with self.assertRaises(ABIValueError):
                    enc("uint8[]", value)


class TupleTests(unittest.TestCase):
    def test_static_tuple(self):
        data = enc("(uint8,bool)", (7, True))
        self.assertEqual(data, word(7) + word(1))
        self.assertEqual(dec("(uint8,bool)", data), (7, True))

    def test_empty_tuple(self):
        self.assertEqual(enc("()", ()), b"")
        self.assertEqual(dec("()", b""), ())

    def test_dynamic_tuple_layout(self):
        expected = (
            word(32)            # 顶层偏移
            + word(64)          # string 成员偏移（相对元组起点）
            + word(7)           # uint8 成员
            + word(2) + b"hi" + b"\x00" * 30
        )
        self.assertEqual(enc("(string,uint8)", ("hi", 7)), expected)
        self.assertEqual(dec("(string,uint8)", expected), ("hi", 7))

    def test_nested_dynamic_tuples(self):
        value = ((1, ("a", b"\x01\x02")), [True, False], "尾")
        type_string = "((uint8,(string,bytes)),bool[],string)"
        self.assertEqual(dec(type_string, enc(type_string, value)), value)

    def test_tuple_of_arrays(self):
        value = ([1, 2, 3], ["x", "yz"], ())
        type_string = "(uint256[],string[],())"
        self.assertEqual(dec(type_string, enc(type_string, value)), value)

    def test_tuple_arity_mismatch(self):
        with self.assertRaises(ABIValueError):
            enc("(uint8,bool)", (1,))
        with self.assertRaises(ABIValueError):
            enc("(uint8,bool)", (1, True, 2))

    def test_decode_returns_tuple_and_list(self):
        result = dec("(uint8[],uint8)", enc("(uint8[],uint8)", ([1, 2], 3)))
        self.assertIsInstance(result, tuple)
        self.assertIsInstance(result[0], list)
        self.assertEqual(result, ([1, 2], 3))


class RoundTripTests(unittest.TestCase):
    CASES = [
        ("uint8", 0),
        ("uint8", 255),
        ("uint256", 2**256 - 1),
        ("int8", -128),
        ("int256", -(2**255)),
        ("int256", 2**255 - 1),
        ("address", "0x" + "0" * 39 + "1"),
        ("address", "Ff" * 20),
        ("bool", True),
        ("bool", False),
        ("string", ""),
        ("string", "hello 世界"),
        ("bytes", b""),
        ("bytes", bytes(range(256))),
        ("bytes1", b"\x00"),
        ("bytes32", b"\xff" * 32),
        ("uint8[]", []),
        ("string[]", ["", "a", "bc"]),
        ("bytes[]", [b"", b"\x00" * 33]),
        ("uint16[4]", [0, 1, 65535, 256]),
        ("(int8,bytes2,string)", (-1, b"\x01\x02", "x")),
        ("(uint8,(bool,string)[])", (9, [(True, "a"), (False, "")])),
        ("((uint8,bytes)[],(string,bool)[2])", ([(1, b"\x00")], [("s", True), ("", False)])),
        ("()[]", [(), (), ()]),
    ]

    def test_round_trip(self):
        for type_string, value in self.CASES:
            with self.subTest(type=type_string, value=value):
                decoded = dec(type_string, enc(type_string, value))
                if type_string == "address":
                    hex_part = value[2:] if value[:2].lower() == "0x" else value
                    self.assertEqual(decoded, "0x" + hex_part.lower())
                else:
                    self.assertEqual(decoded, value)

    def test_encode_is_deterministic(self):
        for type_string, value in self.CASES:
            with self.subTest(type=type_string, value=value):
                self.assertEqual(enc(type_string, value), enc(type_string, value))


class TypeObjectTests(unittest.TestCase):
    def test_non_type_objects_rejected(self):
        for bad in ("uint8", 123, None, [parse_abi_type("uint8")]):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(bad, 0)
                with self.assertRaises(ABIValueError):
                    decode_abi_value(bad, b"")


if __name__ == "__main__":
    unittest.main()
