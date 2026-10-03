"""encode_abi_value / decode_abi_value 的行为测试。

仅使用标准库 unittest，无第三方依赖。
"""

import unittest

from abi_kit import (
    ABITypeError,
    ABIValueError,
    ArrayType,
    ElementaryType,
    TupleType,
    decode_abi_value,
    encode_abi_value,
    format_abi_type,
    parse_abi_type,
)

W = 32
ZERO = b"\x00" * W


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def words(*values: int) -> bytes:
    return b"".join(word(v) for v in values)


class ExceptionTypeTests(unittest.TestCase):
    def test_hierarchy(self):
        self.assertTrue(issubclass(ABIValueError, ValueError))
        self.assertTrue(issubclass(ABITypeError, ValueError))
        # 值层异常与类型层异常互不隶属。
        self.assertFalse(issubclass(ABIValueError, ABITypeError))
        self.assertFalse(issubclass(ABITypeError, ABIValueError))


class ElementaryEncodeTests(unittest.TestCase):
    def test_uint_boundaries(self):
        self.assertEqual(encode_abi_value(parse_abi_type("uint8"), 0), word(0))
        self.assertEqual(encode_abi_value(parse_abi_type("uint8"), 255), word(255))
        self.assertEqual(
            encode_abi_value(parse_abi_type("uint256"), 2**256 - 1), b"\xff" * W
        )
        self.assertEqual(encode_abi_value(parse_abi_type("uint16"), 1), word(1))

    def test_uint_out_of_range(self):
        for bad in (-1, 256, 2**256):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(parse_abi_type("uint8"), bad)

    def test_uint_wrong_python_type(self):
        for bad in ("1", 1.0, True, False, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(parse_abi_type("uint256"), bad)

    def test_int_boundaries_and_twos_complement(self):
        self.assertEqual(
            encode_abi_value(parse_abi_type("int8"), -128),
            (-128).to_bytes(W, "big", signed=True),
        )
        self.assertEqual(encode_abi_value(parse_abi_type("int8"), 127), word(127))
        self.assertEqual(encode_abi_value(parse_abi_type("int8"), -1), b"\xff" * W)
        self.assertEqual(
            encode_abi_value(parse_abi_type("int256"), -(2**255)), b"\x80" + b"\x00" * 31
        )
        self.assertEqual(
            encode_abi_value(parse_abi_type("int256"), 2**255 - 1), b"\x7f" + b"\xff" * 31
        )

    def test_int_out_of_range(self):
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("int8"), -129)
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("int8"), 128)
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("int256"), 2**255)
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("int256"), -(2**255) - 1)

    def test_bool_strict(self):
        self.assertEqual(encode_abi_value(parse_abi_type("bool"), True), word(1))
        self.assertEqual(encode_abi_value(parse_abi_type("bool"), False), ZERO)
        for bad in (0, 1, 2, None, "true"):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(parse_abi_type("bool"), bad)

    def test_address_forms(self):
        raw = "00112233445566778899aabbccddeeff00112233"
        expected = b"\x00" * 12 + bytes.fromhex(raw)
        self.assertEqual(
            encode_abi_value(parse_abi_type("address"), "0x" + raw), expected
        )
        # 不带 0x 与大写字母都可输入。
        self.assertEqual(
            encode_abi_value(parse_abi_type("address"), raw.upper()), expected
        )
        self.assertEqual(
            encode_abi_value(parse_abi_type("address"), "0X" + raw.upper()), expected
        )

    def test_address_invalid(self):
        for bad in (
            "0x00112233445566778899aabbccddeeff001122",  # 39 位
            "00112233445566778899aabbccddeeff0011223344",  # 41 位
            "0x" + "z" * 40,
            0x11223344,
            b"\x00" * 20,
            None,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(parse_abi_type("address"), bad)

    def test_empty_string_and_bytes(self):
        self.assertEqual(encode_abi_value(parse_abi_type("string"), ""), ZERO)
        self.assertEqual(encode_abi_value(parse_abi_type("bytes"), b""), ZERO)

    def test_string_requires_str(self):
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("string"), b"abc")
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("string"), 123)

    def test_dynamic_bytes_padding(self):
        self.assertEqual(
            encode_abi_value(parse_abi_type("bytes"), b"abc"),
            word(3) + b"abc" + b"\x00" * 29,
        )
        self.assertEqual(
            encode_abi_value(parse_abi_type("bytes"), b"a" * 32),
            word(32) + b"a" * 32,
        )
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("bytes"), "abc")

    def test_fixed_bytes_exact_length(self):
        self.assertEqual(
            encode_abi_value(parse_abi_type("bytes1"), b"a"), b"a" + b"\x00" * 31
        )
        self.assertEqual(
            encode_abi_value(parse_abi_type("bytes32"), b"a" * 32), b"a" * 32
        )
        for bad in (b"", b"ab", b"a" * 33, "a"):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(parse_abi_type("bytes1"), bad)


class ElementaryDecodeTests(unittest.TestCase):
    def test_decode_integers(self):
        t = parse_abi_type("uint8")
        self.assertEqual(decode_abi_value(t, word(0)), 0)
        self.assertEqual(decode_abi_value(t, word(255)), 255)
        self.assertIsInstance(decode_abi_value(t, word(5)), int)
        self.assertEqual(decode_abi_value(parse_abi_type("int8"), word(127)), 127)
        self.assertEqual(
            decode_abi_value(parse_abi_type("int256"), b"\xff" * W), -1
        )
        # int8 的 -1 必须符号扩展到整字；仅末字节为 ff 属非法编码。
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("int8"), word(255))

    def test_decode_value_out_of_bit_width(self):
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("uint8"), word(256))
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("uint16"), word(2**16))
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("int8"), word(128))

    def test_decode_bool(self):
        t = parse_abi_type("bool")
        self.assertIs(decode_abi_value(t, word(1)), True)
        self.assertIs(decode_abi_value(t, ZERO), False)
        self.assertIsInstance(decode_abi_value(t, word(1)), bool)
        for bad in (word(2), word(256), b"\x01" + b"\x00" * 31):
            with self.subTest(bad=bad.hex()):
                with self.assertRaises(ABIValueError):
                    decode_abi_value(t, bad)

    def test_decode_address_lowercase(self):
        raw = "AbCdEf0123456789aabbccddeeff001122334456"[:40]
        encoded = b"\x00" * 12 + bytes.fromhex(raw)
        result = decode_abi_value(parse_abi_type("address"), encoded)
        self.assertEqual(result, "0x" + raw.lower())
        self.assertIsInstance(result, str)
        with self.assertRaises(ABIValueError):
            decode_abi_value(
                parse_abi_type("address"), b"\x01" + b"\x00" * 11 + b"\x00" * 20
            )

    def test_decode_empty_dynamic(self):
        self.assertEqual(decode_abi_value(parse_abi_type("string"), ZERO), "")
        self.assertEqual(decode_abi_value(parse_abi_type("bytes"), ZERO), b"")

    def test_decode_string_invalid_utf8(self):
        bad = word(1) + b"\xff" + b"\x00" * 31
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("string"), bad)
        # 同样的字节作为动态 bytes 合法。
        self.assertEqual(decode_abi_value(parse_abi_type("bytes"), bad), b"\xff")

    def test_decode_fixed_bytes_padding(self):
        self.assertEqual(
            decode_abi_value(parse_abi_type("bytes1"), b"a" + b"\x00" * 31), b"a"
        )
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("bytes1"), b"a" + b"\x01" + b"\x00" * 30)

    def test_truncated(self):
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("uint256"), b"\x00" * 31)
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("bytes"), word(32) + b"a" * 31)

    def test_trailing_residue(self):
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("uint256"), word(1) + b"\x00")
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("string"), ZERO + b"\x00")


class ArrayTests(unittest.TestCase):
    def test_static_fixed_array_reference_vector(self):
        encoded = encode_abi_value(parse_abi_type("uint256[3]"), [1, 2, 3])
        self.assertEqual(encoded, words(1, 2, 3))
        self.assertEqual(decode_abi_value(parse_abi_type("uint256[3]"), encoded),
                         [1, 2, 3])

    def test_dynamic_array_empty(self):
        encoded = encode_abi_value(parse_abi_type("uint256[]"), [])
        self.assertEqual(encoded, ZERO)
        self.assertEqual(decode_abi_value(parse_abi_type("uint256[]"), encoded), [])

    def test_dynamic_static_elements_reference_vector(self):
        encoded = encode_abi_value(parse_abi_type("uint256[]"), [1, 2])
        self.assertEqual(encoded, word(2) + words(1, 2))
        self.assertEqual(decode_abi_value(parse_abi_type("uint256[]"), encoded),
                         [1, 2])

    def test_dynamic_array_of_strings(self):
        encoded = encode_abi_value(parse_abi_type("string[]"), ["a", ""])
        # length=2；元素块 head：偏移 64（"a"，长度字+填充载荷共 64 字节）、
        # 128（空串，仅长度字 32 字节）。
        self.assertEqual(
            encoded,
            word(2) + words(64, 128) + word(1) + b"a" + b"\x00" * 31 + ZERO,
        )
        self.assertEqual(
            decode_abi_value(parse_abi_type("string[]"), encoded), ["a", ""]
        )

    def test_fixed_array_of_dynamic_elements(self):
        t = parse_abi_type("string[2]")
        encoded = encode_abi_value(t, ["x", "yy"])
        self.assertEqual(encoded[:32], word(64))
        self.assertEqual(encoded[32:64], word(128))
        self.assertEqual(decode_abi_value(t, encoded), ["x", "yy"])
        with self.assertRaises(ABIValueError):
            encode_abi_value(t, ["x"])
        with self.assertRaises(ABIValueError):
            encode_abi_value(t, ["x", "yy", "z"])

    def test_nested_arrays(self):
        t = parse_abi_type("uint256[][2]")
        value = [[1, 2], [3]]
        encoded = encode_abi_value(t, value)
        # 外层是含动态成员的定长数组：两个偏移。
        self.assertEqual(encoded[:32], word(64))
        self.assertEqual(encoded[32:64], word(160))
        self.assertEqual(decode_abi_value(t, encoded), value)

    def test_array_value_type_and_empty_tuple_elements(self):
        with self.assertRaises(ABIValueError):
            encode_abi_value(parse_abi_type("uint256[]"), (1, 2))
        # 空元组静态尺寸为 0：()[3] 编码为空，解码为三个空元组。
        t = parse_abi_type("()[3]")
        self.assertEqual(encode_abi_value(t, [(), (), ()]), b"")
        self.assertEqual(decode_abi_value(t, b""), [(), (), ()])
        # 动态空元组数组只有长度字。
        td = parse_abi_type("()[]")
        self.assertEqual(encode_abi_value(td, [(), ()]), word(2))
        self.assertEqual(decode_abi_value(td, word(2)), [(), ()])
        self.assertEqual(encode_abi_value(td, []), ZERO)

    def test_array_decode_length_mismatch(self):
        # 声明 3 个元素但只放了 2 个字。
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("uint256[3]"), words(1, 2))
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("uint256[3]"), words(1, 2, 3, 4))
        # 动态数组长度字伪造为超大值。
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("uint256[]"), word(10**9))

    def test_array_decode_bad_inner_offset(self):
        # 两个动态元素，第二个偏移没有紧接第一个实体。
        bogus = word(2) + words(64, 100) + word(1) + b"a" + b"\x00" * 31 + ZERO
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("string[]"), bogus)
        # 偏移越界。
        oob = word(1) + word(96) + word(1) + b"a" + b"\x00" * 31
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("string[]"), oob)


class TupleTests(unittest.TestCase):
    def test_static_tuple(self):
        t = parse_abi_type("(uint8,bool,bytes1)")
        value = (7, True, b"z")
        encoded = encode_abi_value(t, value)
        self.assertEqual(
            encoded, word(7) + word(1) + b"z" + b"\x00" * 31
        )
        self.assertEqual(decode_abi_value(t, encoded), value)

    def test_dynamic_tuple_reference_vector(self):
        t = parse_abi_type("(uint256,string)")
        encoded = encode_abi_value(t, (42, "hello"))
        self.assertEqual(encoded, word(42) + word(64) + word(5) + b"hello" + b"\x00" * 27)
        self.assertEqual(decode_abi_value(t, encoded), (42, "hello"))

    def test_nested_dynamic_tuple_order(self):
        t = parse_abi_type("(string,uint256[])")
        value = ("x", [1, 2])
        encoded = encode_abi_value(t, value)
        # head: string 偏移 64，数组偏移 128（字符串实体长度字+填充共 64 字节）。
        self.assertEqual(encoded[:64], words(64, 128))
        self.assertEqual(decode_abi_value(t, encoded), value)

    def test_tuple_of_dynamic_array_of_tuples(self):
        t = parse_abi_type("(uint256,(bool,string)[])")
        value = (9, [(True, "a"), (False, "")])
        encoded = encode_abi_value(t, value)
        self.assertEqual(decode_abi_value(t, encoded), value)

    def test_empty_tuple(self):
        t = parse_abi_type("()")
        self.assertEqual(encode_abi_value(t, ()), b"")
        self.assertEqual(decode_abi_value(t, b""), ())
        # 空元组外层带残留即非法。
        with self.assertRaises(ABIValueError):
            decode_abi_value(t, b"\x00")

    def test_tuple_value_shape(self):
        t = parse_abi_type("(uint256,uint256)")
        with self.assertRaises(ABIValueError):
            encode_abi_value(t, [1, 2])
        with self.assertRaises(ABIValueError):
            encode_abi_value(t, (1,))
        with self.assertRaises(ABIValueError):
            encode_abi_value(t, (1, 2, 3))

    def test_tuple_bad_offset(self):
        # 偏移倒序/越界。
        bad = words(96, 64) + word(1) + b"a" + b"\x00" * 31 + ZERO
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("(string,string)"), bad)
        oob = words(128) + word(1) + b"a" + b"\x00" * 31
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("(string,uint256)"), oob)


class RoundTripTests(unittest.TestCase):
    CASES = [
        ("uint256", 0),
        ("uint8", 255),
        ("int8", -128),
        ("int256", 2**255 - 1),
        ("address", "0x" + "ab" * 20),
        ("bool", True),
        ("bool", False),
        ("string", ""),
        ("string", "你好，ABI"),
        ("bytes", b""),
        ("bytes", bytes(range(256))),
        ("bytes4", b"\xde\xad\xbe\xef"),
        ("uint256[2]", [11, 22]),
        ("uint256[]", []),
        ("string[]", ["", "a", "bb"]),
        ("bytes32[][3]", [[b"\x01" * 32], [], [b"\x02" * 32, b"\x03" * 32]]),
        ("(uint8,address[2],(bool,string[]))",
         (1, ["0x" + "11" * 20, "0x" + "22" * 20], (False, ["x", ""]))),
        ("(uint256,(bytes3,address[]))[]",
         [(1, (b"abc", ["0x" + "00" * 20])), (2, (b"def", []))]),
        ("()", ()),
        ("uint16[][][5]", [[[], [1]], [[2, 3]], [], [[]], [[65535]]]),
    ]

    def test_round_trip(self):
        for type_string, value in self.CASES:
            with self.subTest(type_string=type_string):
                t = parse_abi_type(type_string)
                encoded = encode_abi_value(t, value)
                decoded = decode_abi_value(t, encoded)
                self.assertEqual(decoded, value)
                self.assertEqual(type(decoded), type(value))

    def test_encode_stable(self):
        t = parse_abi_type("(uint256,string[])")
        value = (5, ["a", "bb"])
        self.assertEqual(encode_abi_value(t, value), encode_abi_value(t, value))
        # 同一规范值（地址大小写不同）编码一致。
        at = parse_abi_type("address")
        self.assertEqual(
            encode_abi_value(at, "0x" + "ab" * 20),
            encode_abi_value(at, "AB" * 20),
        )

    def test_decode_does_not_consume_partially(self):
        t = parse_abi_type("(uint256,string)")
        encoded = encode_abi_value(t, (1, "ok"))
        # 多种非法变体均不应返回值。
        for bad in (encoded[:-1], encoded + b"\x00", b"", encoded[:40]):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    decode_abi_value(t, bad)


class ApiSurfaceTests(unittest.TestCase):
    def test_type_objects_required(self):
        for bad in ("uint256", None, 42, object()):
            with self.subTest(bad=bad):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(bad, 1)
                with self.assertRaises(ABIValueError):
                    decode_abi_value(bad, ZERO)

    def test_decode_requires_bytes(self):
        t = parse_abi_type("uint256")
        for bad in (ZERO.hex(), bytearray(ZERO), list(ZERO), None):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(ABIValueError):
                    decode_abi_value(t, bad)

    def test_direct_type_objects_work(self):
        t = TupleType((ElementaryType("uint", bit_size=8),
                       ArrayType(ElementaryType("bool"))))
        value = (8, [True, False])
        self.assertEqual(decode_abi_value(t, encode_abi_value(t, value)), value)

    def test_parser_errors_still_type_error(self):
        # 既有行为不变：坏类型字符串仍是 ABITypeError，而非 ABIValueError。
        with self.assertRaises(ABITypeError):
            parse_abi_type("uint7")
        self.assertEqual(format_abi_type(parse_abi_type("uint256 []".replace(" []", "[]"))),
                         "uint256[]")


if __name__ == "__main__":
    unittest.main()
