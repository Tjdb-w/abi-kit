"""fixedMxN / ufixedMxN / function 三类新增基础类型的行为测试。

覆盖：
- 类型字符串解析、规范名格式化、不可变类型对象构造校验；
- Decimal 固定小数与 24 字节 function 的值编解码、边界与错误矩阵；
- 新类型作为静态单字参与数组、元组与路径寻址；
- 事件 indexed topic、function/error/constructor/合约清单 ABI 入口。

仅使用标准库 unittest，无第三方依赖。
"""

import json
import unittest
from decimal import Decimal

from abi_kit import (
    ABITypeError,
    ABIValueError,
    AbiContractAbiError,
    AbiEventError,
    FixedPointType,
    FunctionType,
    decode_abi_value,
    decode_constructor_data,
    decode_error_data,
    decode_event_log,
    encode_abi_value,
    encode_constructor_data,
    encode_error_data,
    encode_event_log,
    format_abi_type,
    get_abi_value_at_path,
    parse_abi_type,
    parse_constructor_abi,
    parse_contract_abi,
    parse_error_abi,
    parse_event_abi,
    replace_abi_value_at_path,
)
from abi_kit._keccak import keccak_256
from abi_kit._path import _NamedParser

W = 32
ZERO = b"\x00" * W

FN_BYTES = bytes(range(24))


def word_int(value: int, signed: bool = False) -> bytes:
    return value.to_bytes(W, "big", signed=signed)


def dec_scaled(integer: int, scale: int) -> Decimal:
    """由缩放整数与 N 构造恰有 scale 位小数的 Decimal，不经算术舍入。"""
    magnitude = -integer if integer < 0 else integer
    digits = tuple(int(ch) for ch in str(magnitude)) or (0,)
    return Decimal((1 if integer < 0 else 0, digits, -scale))


class FixedFunctionParseTests(unittest.TestCase):
    def test_fixed_roundtrip_names(self):
        cases = (
            ("fixed8x1", True, 8, 1),
            ("fixed128x3", True, 128, 3),
            ("fixed256x80", True, 256, 80),
            ("fixed248x18", True, 248, 18),
            ("ufixed8x1", False, 8, 1),
            ("ufixed16x80", False, 16, 80),
        )
        for name, signed, bits, scale in cases:
            with self.subTest(name=name):
                t = parse_abi_type(name)
                self.assertIsInstance(t, FixedPointType)
                self.assertEqual(t.signed, signed)
                self.assertEqual(t.bit_size, bits)
                self.assertEqual(t.scale, scale)
                self.assertEqual(t.depth, 1)
                self.assertEqual(format_abi_type(t), name)

    def test_function_parse(self):
        t = parse_abi_type("function")
        self.assertIsInstance(t, FunctionType)
        self.assertEqual(t.byte_size, 24)
        self.assertEqual(t.depth, 1)
        self.assertEqual(format_abi_type(t), "function")

    def test_new_types_in_containers(self):
        t = parse_abi_type("fixed128x3[2][]")
        self.assertEqual(format_abi_type(t), "fixed128x3[2][]")
        self.assertEqual(t.depth, 3)
        t = parse_abi_type("(ufixed64x4,function)[2]")
        self.assertEqual(format_abi_type(t), "(ufixed64x4,function)[2]")
        self.assertEqual(t.depth, 3)

    def test_invalid_spellings(self):
        bad = [
            "fixedx", "fixed128", "fixed128x", "fixedx3",
            "fixed7x1", "fixed9x1", "fixed10x1", "fixed264x1", "fixed257x8",
            "fixed128x0", "fixed128x81", "fixed8x00",
            "fixed08x3", "fixed128x03", "fixed128X3",
            "ufixed0x1", "ufixed8x080",
            "FIXED128x3", "fixed128x3x1", "fixed128xa",
            "function24", "functions", "function[]x",
        ]
        for s in bad:
            with self.subTest(s=s):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(s)

    def test_bare_fixed_aliases(self):
        self.assertEqual(
            format_abi_type(parse_abi_type("fixed")), "fixed128x18"
        )
        self.assertEqual(
            format_abi_type(parse_abi_type("ufixed")), "ufixed128x18"
        )
        self.assertEqual(
            format_abi_type(parse_abi_type("(fixed,ufixed)[]")),
            "(fixed128x18,ufixed128x18)[]",
        )

    def test_type_object_construction(self):
        self.assertEqual(FixedPointType(True, 8, 1).signed, True)
        self.assertEqual(FixedPointType(False, 256, 80).scale, 80)
        self.assertEqual(FunctionType().byte_size, 24)
        bad_fixed = [
            (True, 7, 3), (True, 9, 3), (True, 257, 3), (True, 128, 0),
            (True, 128, 81), (False, 127, 3), ("x", 128, 3),
            (True, 8.0, 3), (True, 8, 3.0), (1, 8, 3),
        ]
        for args in bad_fixed:
            with self.subTest(args=args):
                with self.assertRaises(ABITypeError):
                    FixedPointType(*args)
        for width in (0, 23, 25, 24.0, True, None):
            with self.subTest(width=width):
                with self.assertRaises(ABITypeError):
                    FunctionType(width)

    def test_immutable(self):
        import dataclasses

        for obj in (FixedPointType(True, 128, 3), FunctionType()):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                obj.byte_size = 5


class FixedCodecTests(unittest.TestCase):
    def test_fixed128x3_example(self):
        t = parse_abi_type("fixed128x3")
        enc = encode_abi_value(t, Decimal("1.250"))
        self.assertEqual(enc, word_int(1250))
        dec = decode_abi_value(t, enc)
        self.assertEqual(dec, Decimal("1.250"))
        self.assertEqual(dec.as_tuple().exponent, -3)
        self.assertEqual(str(dec), "1.250")

    def test_fixed_negative_two_complement(self):
        t = parse_abi_type("fixed128x3")
        enc = encode_abi_value(t, Decimal("-1.250"))
        self.assertEqual(int.from_bytes(enc, "big", signed=True), -1250)
        self.assertEqual(decode_abi_value(t, enc), Decimal("-1.250"))

    def test_ufixed_encoding_and_scale(self):
        t = parse_abi_type("ufixed8x1")
        self.assertEqual(encode_abi_value(t, Decimal("25")), word_int(250))
        self.assertEqual(decode_abi_value(t, word_int(255)), Decimal("25.5"))
        self.assertEqual(decode_abi_value(t, ZERO), Decimal("0.0"))
        # 整数值的 Decimal 也接受，但解码严格补出 N 位小数。
        self.assertEqual(decode_abi_value(t, word_int(50)), Decimal("5.0"))

    def test_256bit_precision_exact_roundtrip(self):
        t = parse_abi_type("ufixed256x80")
        value = dec_scaled(2**256 - 1, 80)
        dec = decode_abi_value(t, encode_abi_value(t, value))
        self.assertEqual(dec, value)
        self.assertEqual(dec.as_tuple().exponent, -80)
        self.assertGreater(len(dec.as_tuple().digits), 70)

    def test_signed_boundaries(self):
        t = parse_abi_type("fixed128x3")
        maximum = dec_scaled(2**127 - 1, 3)
        minimum = dec_scaled(-(2**127), 3)
        self.assertEqual(decode_abi_value(t, encode_abi_value(t, maximum)), maximum)
        self.assertEqual(decode_abi_value(t, encode_abi_value(t, minimum)), minimum)
        for value in (dec_scaled(2**127, 3), dec_scaled(-(2**127) - 1, 3)):
            with self.assertRaises(ABIValueError):
                encode_abi_value(t, value)

    def test_unsigned_boundaries(self):
        t = parse_abi_type("ufixed8x1")
        self.assertEqual(
            decode_abi_value(t, encode_abi_value(t, Decimal("25.5"))),
            Decimal("25.5"),
        )
        for value in (Decimal("-0.1"), Decimal("25.6")):
            with self.assertRaises(ABIValueError):
                encode_abi_value(t, value)

    def test_encode_wrong_python_type(self):
        t = parse_abi_type("fixed128x3")
        for value in (1, 1.0, "1.250", True, False, None, [1], object()):
            with self.subTest(value=value):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(t, value)

    def test_encode_non_finite(self):
        t = parse_abi_type("fixed128x3")
        for text in ("NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(text=text):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(t, Decimal(text))

    def test_encode_too_many_fractional_digits(self):
        t = parse_abi_type("fixed128x3")
        for value in (Decimal("1.0001"), Decimal("1.2500")):
            with self.subTest(value=value):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(t, value)

    def test_decode_out_of_range(self):
        with self.assertRaises(ABIValueError):
            decode_abi_value(parse_abi_type("ufixed8x1"), word_int(256))
        with self.assertRaises(ABIValueError):
            decode_abi_value(
                parse_abi_type("fixed128x3"),
                (2**127).to_bytes(W, "big", signed=False),
            )
        with self.assertRaises(ABIValueError):
            decode_abi_value(
                parse_abi_type("fixed128x3"),
                ((2**127) | ((1 << 128) - 1)).to_bytes(W, "big", signed=False),
            )


class FunctionCodecTests(unittest.TestCase):
    def test_roundtrip(self):
        t = parse_abi_type("function")
        enc = encode_abi_value(t, FN_BYTES)
        self.assertEqual(enc, FN_BYTES + b"\x00" * 8)
        self.assertEqual(decode_abi_value(t, enc), FN_BYTES)

    def test_encode_wrong_value(self):
        t = parse_abi_type("function")
        for value in (
            b"\x00" * 23, b"\x00" * 25, b"",
            bytearray(24), memoryview(b"\x00" * 24), 1, None, "00" * 24,
        ):
            with self.subTest(value=repr(value)[:30]):
                with self.assertRaises(ABIValueError):
                    encode_abi_value(t, value)

    def test_decode_nonzero_padding(self):
        t = parse_abi_type("function")
        bad = b"\x00" * 24 + b"\x01" + b"\x00" * 7
        with self.assertRaises(ABIValueError):
            decode_abi_value(t, bad)
        for data in (b"\x00" * 23, b"\x00" * 31, b"\x00" * 33):
            with self.subTest(len=len(data)):
                with self.assertRaises(ABIValueError):
                    decode_abi_value(t, data)


class ContainerAndStrictnessTests(unittest.TestCase):
    def test_static_arrays_and_tuples(self):
        t = parse_abi_type("fixed128x3[2]")
        enc = encode_abi_value(t, [Decimal("1.000"), Decimal("-2.250")])
        self.assertEqual(len(enc), 2 * W)
        self.assertEqual(
            decode_abi_value(t, enc), [Decimal("1.000"), Decimal("-2.250")]
        )

        t2 = parse_abi_type("(function,ufixed8x1)[]")
        enc2 = encode_abi_value(
            t2, [(FN_BYTES, Decimal("1.5")), (b"\x22" * 24, Decimal("0.0"))]
        )
        self.assertEqual(
            decode_abi_value(t2, enc2),
            [(FN_BYTES, Decimal("1.5")), (b"\x22" * 24, Decimal("0.0"))],
        )

    def test_mixed_dynamic_block(self):
        # 动态类型与新静态类型同块：偏移、长度与静态字布局必须自洽。
        t = parse_abi_type("(string,fixed128x3,function)")
        value = ("héllo", Decimal("-3.500"), FN_BYTES)
        self.assertEqual(decode_abi_value(t, encode_abi_value(t, value)), value)

    def test_short_and_trailing_data(self):
        for type_string, data in (
            ("fixed128x3", b"\x00" * 31),
            ("function", b"\x00" * 31),
            ("fixed128x3", b"\x00" * 33),
            ("function", b"\x00" * 33),
            ("fixed128x3[2]", b"\x00" * 63),
        ):
            with self.subTest(s=type_string, n=len(data)):
                with self.assertRaises(ABIValueError):
                    decode_abi_value(parse_abi_type(type_string), data)

    def test_container_member_failure_is_atomic(self):
        t = parse_abi_type("(bool,fixed128x3)")
        good = encode_abi_value(t, (True, Decimal("1.000")))
        bad = good[:W] + (2**127).to_bytes(W, "big", signed=False)
        with self.assertRaises(ABIValueError):
            decode_abi_value(t, bad)

        ta = parse_abi_type("ufixed8x1[3]")
        bad_array = (
            word_int(10) + word_int(260) + word_int(0)
        )
        with self.assertRaises(ABIValueError):
            decode_abi_value(ta, bad_array)


class PathTests(unittest.TestCase):
    TYPE = "(fixed128x3 price,function target,ufixed64x2[] scales)"

    def setUp(self):
        self.parsed = _NamedParser(self.TYPE).parse()
        self.data = encode_abi_value(
            self.parsed,
            (Decimal("1.250"), FN_BYTES, [Decimal("0.10"), Decimal("2.00")]),
        )

    def test_read_paths(self):
        self.assertEqual(
            get_abi_value_at_path(self.TYPE, self.data, "price"),
            Decimal("1.250"),
        )
        self.assertEqual(
            get_abi_value_at_path(self.TYPE, self.data, "target"), FN_BYTES
        )
        self.assertEqual(
            get_abi_value_at_path(self.TYPE, self.data, "scales[1]"),
            Decimal("2.00"),
        )

    def test_replace_fixed_and_function(self):
        new = replace_abi_value_at_path(
            self.TYPE, self.data, "scales[0]", Decimal("9.99")
        )
        self.assertEqual(
            get_abi_value_at_path(self.TYPE, new, "scales[0]"), Decimal("9.99")
        )
        # 未选中的兄弟节点不变。
        self.assertEqual(
            get_abi_value_at_path(self.TYPE, new, "price"), Decimal("1.250")
        )
        new2 = replace_abi_value_at_path(
            self.TYPE, self.data, "target", b"\xab" * 24
        )
        self.assertEqual(
            get_abi_value_at_path(self.TYPE, new2, "target"), b"\xab" * 24
        )

    def test_replace_wrong_value(self):
        from abi_kit import AbiPathError

        with self.assertRaises(AbiPathError) as ctx:
            replace_abi_value_at_path(self.TYPE, self.data, "price", 1)
        self.assertEqual(ctx.exception.code, "PATH_VALUE_MISMATCH")
        with self.assertRaises(AbiPathError) as ctx:
            replace_abi_value_at_path(self.TYPE, self.data, "target", b"\x00" * 23)
        self.assertEqual(ctx.exception.code, "PATH_VALUE_MISMATCH")


class EventIndexedTests(unittest.TestCase):
    EVENT = {
        "type": "event",
        "name": "Tick",
        "inputs": [
            {"type": "fixed128x3", "name": "p", "indexed": True},
            {"type": "function", "name": "fn", "indexed": True},
            {"type": "ufixed256x2", "name": "q", "indexed": False},
        ],
    }

    def test_indexed_roundtrip(self):
        from abi_kit import match_event_log_values

        event = parse_event_abi(self.EVENT)
        values = (Decimal("-12.500"), FN_BYTES, Decimal("1.50"))
        enc = encode_event_log(event, list(values))
        self.assertEqual(len(enc.topics), 3)
        self.assertEqual(enc.topics[1], word_int(-12500, signed=True))
        self.assertEqual(enc.topics[2], FN_BYTES + b"\x00" * 8)
        decoded = decode_event_log(event, enc.topics, enc.data)
        self.assertEqual(decoded, values)
        self.assertEqual(decoded[0].as_tuple().exponent, -3)
        self.assertEqual(
            match_event_log_values(event, enc.topics, enc.data, list(values)),
            values,
        )

    def test_match_mismatch_returns_none(self):
        from abi_kit import match_event_log_values

        event = parse_event_abi(self.EVENT)
        enc = encode_event_log(event, [Decimal("-12.500"), FN_BYTES, Decimal("1.50")])
        self.assertIsNone(
            match_event_log_values(
                event, enc.topics, enc.data,
                [Decimal("-12.500"), FN_BYTES, Decimal("1.60")],
            )
        )

    def test_bad_indexed_topic(self):
        event = parse_event_abi(self.EVENT)
        enc = encode_event_log(event, [Decimal("-12.500"), FN_BYTES, Decimal("1.50")])
        # function topic 的填充字节非零。
        topics = list(enc.topics[:2]) + [b"\x00" * 24 + b"\x01" + b"\x00" * 7]
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(event, topics, enc.data)
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")
        # fixed128x3 缩放整数越界。
        topics2 = [enc.topics[0], word_int(2**127, signed=False), enc.topics[2]]
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(event, topics2, enc.data)
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_bad_indexed_value_on_encode(self):
        event = parse_event_abi(self.EVENT)
        with self.assertRaises(AbiEventError) as ctx:
            encode_event_log(event, [Decimal("NaN"), FN_BYTES, Decimal("1.50")])
        self.assertEqual(ctx.exception.code, "EVENT_VALUE_INVALID")
        with self.assertRaises(AbiEventError) as ctx:
            encode_event_log(event, [Decimal("-12.500"), b"\x00" * 20, Decimal("1.50")])
        self.assertEqual(ctx.exception.code, "EVENT_VALUE_INVALID")

    def test_hashed_tuple_containing_new_types(self):
        from abi_kit import match_event_log_values

        event_json = {
            "type": "event",
            "name": "E",
            "inputs": [
                {
                    "type": "tuple",
                    "indexed": True,
                    "components": [
                        {"type": "fixed128x18"},
                        {"type": "function"},
                    ],
                }
            ],
        }
        event = parse_event_abi(event_json)
        value = (Decimal("3.14"), FN_BYTES)
        enc = encode_event_log(event, [value])
        # tuple 不可逆：原样得到 32 字节 topic。
        decoded = decode_event_log(event, enc.topics, b"")
        self.assertEqual(decoded, (enc.topics[1],))
        expected_preimage = word_int(314 * 10**16, signed=True) + (
            FN_BYTES + b"\x00" * 8
        )
        self.assertEqual(enc.topics[1], keccak_256(expected_preimage))
        self.assertEqual(
            match_event_log_values(event, enc.topics, b"", [value]), (value,)
        )
        self.assertIsNone(
            match_event_log_values(
                event, enc.topics, b"", [(Decimal("3.15"), FN_BYTES)]
            )
        )


class AbiEntryTests(unittest.TestCase):
    def test_function_signature_selector_and_call(self):
        from abi_kit import (
            canonical_function_signature,
            decode_function_call,
            encode_function_call,
            parse_function_abi,
        )

        abi = (
            '[{"type":"function","name":"set",'
            '"inputs":[{"type":"fixed128x3"},{"type":"function"}],'
            '"outputs":[{"type":"ufixed8x1"}]}]'
        )
        fd = parse_function_abi(abi)[0]
        self.assertEqual(
            canonical_function_signature(fd), "set(fixed128x3,function)"
        )
        from abi_kit import function_selector

        self.assertEqual(
            function_selector(fd),
            keccak_256(b"set(fixed128x3,function)")[:4],
        )
        call = encode_function_call(abi, "set", [Decimal("7.777"), FN_BYTES])
        decoded = decode_function_call(abi, call.calldata)
        self.assertEqual(decoded.values, (Decimal("7.777"), FN_BYTES))
        self.assertEqual(decoded.args[0].type, "fixed128x3")
        self.assertEqual(decoded.args[1].type, "function")
        with self.assertRaises(ABIValueError):
            encode_function_call(abi, "set", [7, FN_BYTES])
        with self.assertRaises(ABIValueError):
            encode_function_call(abi, "set", [Decimal("7.777"), b"\x00" * 23])

    def test_error_entry(self):
        abi = '[{"type":"error","name":"Bad","inputs":[{"type":"ufixed128x10"}]}]'
        ed = parse_error_abi(abi)[0]
        from abi_kit import canonical_error_signature

        self.assertEqual(canonical_error_signature(ed), "Bad(ufixed128x10)")
        encoded = encode_error_data(abi, "Bad", [Decimal("0.0000000001")])
        self.assertEqual(
            decode_error_data(abi, encoded.data).values,
            (Decimal("0.0000000001"),),
        )
        with self.assertRaises(ABIValueError):
            encode_error_data(abi, "Bad", [Decimal("NaN")])

    def test_constructor_entry(self):
        abi = '[{"type":"constructor","inputs":[{"type":"fixed256x80"}]}]'
        parse_constructor_abi(abi)
        value = Decimal("-0.0001000000")
        encoded = encode_constructor_data(abi, b"BYTECODE", [value])
        self.assertTrue(encoded.data.startswith(b"BYTECODE"))
        decoded = decode_constructor_data(abi, b"BYTECODE", encoded.data)
        self.assertEqual(decoded.values, (value,))
        self.assertEqual(decoded.values[0].as_tuple().exponent, -80)
        with self.assertRaises(ABIValueError):
            encode_constructor_data(abi, b"BC", [1])

    def test_contract_abi_listing(self):
        abi = (
            '[{"type":"function","name":"set",'
            '"inputs":[{"type":"fixed128x3"},{"type":"function"}],'
            '"outputs":[]}]'
        )
        listing = parse_contract_abi(abi)
        self.assertEqual(
            listing.function_signatures, ("set(fixed128x3,function)",)
        )
        for bad in (
            "fixed7x1", "function24", "fixed128x81", "ufixed08x3",
            "fixed256x0", "fixed8x080",
        ):
            with self.subTest(bad=bad):
                entry = json.dumps(
                    [
                        {
                            "type": "function",
                            "name": "x",
                            "inputs": [{"type": bad}],
                        }
                    ]
                )
                with self.assertRaises(AbiContractAbiError) as ctx:
                    parse_contract_abi(entry)
                self.assertEqual(ctx.exception.code, "ABI_ENTRY_INVALID")

    def test_contract_abi_listing_accepts_aliases(self):
        abi = (
            '[{"type":"function","name":"set",'
            '"inputs":[{"type":"fixed"},{"type":"ufixed"},{"type":"uint"}],'
            '"outputs":[]}]'
        )
        listing = parse_contract_abi(abi)
        self.assertEqual(
            listing.function_signatures,
            ("set(fixed128x18,ufixed128x18,uint256)",),
        )


if __name__ == "__main__":
    unittest.main()
