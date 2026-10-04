"""encode_function_result / decode_function_result 的行为测试。

仅使用标准库 unittest，无第三方依赖。覆盖静态/动态基础类型、定长与
动态数组、嵌套数组、带 components 的元组及嵌套元组的返回值编解码，
以及函数选择、元数据、值层与尾随数据错误约定。
"""

import json
import unittest

from abi_kit import (
    AbiFunctionNotFoundError,
    AbiMetadataError,
    AbiOverloadError,
    AbiTrailingDataError,
    AbiValueError,
    ABIValueError,
    DecodedFunctionResult,
    EncodedFunctionResult,
    FunctionArgument,
    FunctionDefinition,
    decode_function_result,
    decodeFunctionResult,
    encode_function_call,
    encode_function_result,
    encodeFunctionResult,
    function_selector,
    parse_function_abi,
)

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def addr_word(addr: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(addr[2:])


ADDR_A = "0x" + "11" * 20
ADDR_B = "0x" + "22" * 20


def fn(name, inputs, outputs=None, **extra):
    entry = {"type": "function", "name": name, "inputs": inputs}
    if outputs is not None:
        entry["outputs"] = outputs
    entry.update(extra)
    return entry


def param(type_, name="", **extra):
    node = {"type": type_, "name": name}
    node.update(extra)
    return node


BALANCE_OF = fn(
    "balanceOf",
    [param("address", "owner")],
    [param("uint256", "balance")],
)

NO_OUTPUTS = fn("ping", [], [])

NO_OUTPUTS_KEY = fn("pong", [])  # outputs 缺省视为无返回值

GET_PAIR = fn(
    "getPair",
    [],
    [param("address", "token0"), param("address", "token1")],
)

GET_STRING = fn("name", [], [param("string", "")])

GET_DYNAMIC = fn(
    "getDynamic",
    [],
    [
        param("uint256", "count"),
        param("string", "label"),
        param("bytes", "blob"),
    ],
)

GET_ARRAYS = fn(
    "getArrays",
    [],
    [
        param("uint8[3]", "fixed"),
        param("bool[]", "flags"),
        param("string[][]", "grid"),
    ],
)

GET_TUPLE = fn(
    "getConfig",
    [],
    [
        param(
            "tuple",
            "config",
            components=[
                param("address", "owner"),
                param("uint256[]", "weights"),
                param(
                    "tuple",
                    "meta",
                    components=[
                        param("string", "label"),
                        param("bytes2[2]", "tags"),
                    ],
                ),
            ],
        )
    ],
)

OVERLOAD_A = fn("foo", [param("uint256", "x")], [param("uint256", "")])
OVERLOAD_B = fn("foo", [param("address", "a")], [param("bool", "")])

# 同名同参、仅 outputs 不同的两个函数：selector 必须一致。
SELECTOR_A = fn("bar", [param("uint256", "x")], [param("uint256", "")])
SELECTOR_B = fn("bar", [param("uint256", "x")], [param("bool", ""), param("bool", "")])

ALL_FUNCTIONS = [
    BALANCE_OF,
    NO_OUTPUTS,
    NO_OUTPUTS_KEY,
    GET_PAIR,
    GET_STRING,
    GET_DYNAMIC,
    GET_ARRAYS,
    GET_TUPLE,
    OVERLOAD_A,
    OVERLOAD_B,
]

ABI_JSON = json.dumps(ALL_FUNCTIONS)


class ParseOutputsTest(unittest.TestCase):
    def test_outputs_parsed_in_declaration_order(self):
        functions = parse_function_abi(ABI_JSON)
        balance_of = next(f for f in functions if f.name == "balanceOf")
        self.assertEqual(len(balance_of.outputs), 1)
        self.assertEqual(balance_of.outputs[0].name, "balance")
        self.assertEqual(
            format_of(balance_of.outputs[0]), "uint256"
        )

    def test_missing_outputs_defaults_to_empty(self):
        functions = parse_function_abi([NO_OUTPUTS_KEY])
        self.assertEqual(functions[0].outputs, ())

    def test_outputs_do_not_change_signature_or_selector(self):
        functions = parse_function_abi([SELECTOR_A, SELECTOR_B])
        self.assertEqual(
            function_selector(functions[0]), function_selector(functions[1])
        )
        # calldata 编码也不受 outputs 影响。
        call_a = encode_function_call([SELECTOR_A], "bar", [7])
        call_b = encode_function_call([SELECTOR_B], "bar", [7])
        self.assertEqual(call_a.calldata, call_b.calldata)

    def test_outputs_not_a_list_raises_metadata(self):
        abi = [fn("f", [], outputs={"type": "uint256"})]
        with self.assertRaises(AbiMetadataError):
            parse_function_abi(abi)

    def test_outputs_entry_not_object_raises_metadata(self):
        abi = [fn("f", [], outputs=["uint256"])]
        with self.assertRaises(AbiMetadataError):
            parse_function_abi(abi)

    def test_outputs_bad_type_string_raises_metadata(self):
        abi = [fn("f", [], outputs=[param("uint257", "")])]
        with self.assertRaises(AbiMetadataError):
            parse_function_abi(abi)

    def test_outputs_bad_name_raises_metadata(self):
        abi = [fn("f", [], outputs=[param("uint256", 1)])]
        with self.assertRaises(AbiMetadataError):
            parse_function_abi(abi)

    def test_abi_root_invalid_raises_metadata(self):
        with self.assertRaises(AbiMetadataError):
            encode_function_result('{"type":"function"}', "f", [])
        with self.assertRaises(AbiMetadataError):
            decode_function_result(12345, "f", b"")


def format_of(parameter):
    from abi_kit import format_abi_type

    return format_abi_type(parameter.abi_type)


class EncodeFunctionResultTest(unittest.TestCase):
    def test_single_static_output(self):
        result = encode_function_result(ABI_JSON, "balanceOf", [1000])
        self.assertIsInstance(result, EncodedFunctionResult)
        self.assertEqual(result.data, word(1000))
        self.assertEqual(result.data_hex, "0x" + word(1000).hex())
        self.assertEqual(result.function_name, "balanceOf")
        self.assertEqual(result.signature, "balanceOf(address)")

    def test_multiple_static_outputs(self):
        result = encode_function_result(ABI_JSON, "getPair", [ADDR_A, ADDR_B])
        self.assertEqual(result.data, addr_word(ADDR_A) + addr_word(ADDR_B))

    def test_tuple_values_accepted(self):
        result = encode_function_result(ABI_JSON, "balanceOf", (5,))
        self.assertEqual(result.data, word(5))

    def test_no_outputs_encodes_empty(self):
        for abi_key in ("ping", "pong"):
            for values in (None, [], ()):
                result = encode_function_result(ABI_JSON, abi_key, values)
                self.assertEqual(result.data, b"")
                self.assertEqual(result.data_hex, "0x")

    def test_no_outputs_rejects_nonempty_values(self):
        with self.assertRaises(ABIValueError):
            encode_function_result(ABI_JSON, "ping", [1])

    def test_count_mismatch_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            encode_function_result(ABI_JSON, "balanceOf", [])
        with self.assertRaises(ABIValueError):
            encode_function_result(ABI_JSON, "getPair", [ADDR_A])

    def test_type_mismatch_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            encode_function_result(ABI_JSON, "balanceOf", ["not-an-int"])
        with self.assertRaises(ABIValueError):
            encode_function_result(ABI_JSON, "getPair", [ADDR_A, 1])

    def test_values_not_sequence_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            encode_function_result(ABI_JSON, "balanceOf", 1000)

    def test_dynamic_outputs_encoding(self):
        result = encode_function_result(
            ABI_JSON, "getDynamic", [2, "hi", b"\x01\x02"]
        )
        expected_head = word(2) + word(3 * W) + word(3 * W + 2 * W)
        string_tail = word(2) + b"hi" + b"\x00" * 30
        bytes_tail = word(2) + b"\x01\x02" + b"\x00" * 30
        self.assertEqual(result.data, expected_head + string_tail + bytes_tail)

    def test_result_is_immutable(self):
        result = encode_function_result(ABI_JSON, "balanceOf", [1])
        with self.assertRaises(Exception):
            result.data = b""


class DecodeFunctionResultTest(unittest.TestCase):
    def test_decode_bytes_and_hex_forms(self):
        data = word(1000)
        for payload in (data, "0x" + data.hex(), data.hex(), "0X" + data.hex()):
            result = decode_function_result(ABI_JSON, "balanceOf", payload)
            self.assertIsInstance(result, DecodedFunctionResult)
            self.assertEqual(result.values, (1000,))
            self.assertEqual(result.function_name, "balanceOf")
            self.assertEqual(result.signature, "balanceOf(address)")

    def test_decode_outputs_carry_names_and_canonical_types(self):
        data = addr_word(ADDR_A) + addr_word(ADDR_B)
        result = decode_function_result(ABI_JSON, "getPair", data)
        self.assertEqual(len(result.outputs), 2)
        self.assertEqual(
            result.outputs[0], FunctionArgument("token0", "address", ADDR_A)
        )
        self.assertEqual(
            result.outputs[1], FunctionArgument("token1", "address", ADDR_B)
        )
        self.assertEqual(result.values, (ADDR_A, ADDR_B))

    def test_empty_outputs_roundtrip(self):
        encoded = encode_function_result(ABI_JSON, "ping", [])
        decoded = decode_function_result(ABI_JSON, "ping", encoded.data)
        self.assertEqual(decoded.outputs, ())
        self.assertEqual(decoded.values, ())

    def test_empty_outputs_reject_nonempty_data(self):
        with self.assertRaises(AbiTrailingDataError):
            decode_function_result(ABI_JSON, "ping", word(1))

    def test_odd_length_hex_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            decode_function_result(ABI_JSON, "balanceOf", "0xabc")

    def test_non_hex_characters_raise_value_error(self):
        with self.assertRaises(ABIValueError):
            decode_function_result(ABI_JSON, "balanceOf", "0x" + "zz" * 32)

    def test_non_bytes_non_str_input_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            decode_function_result(ABI_JSON, "balanceOf", 1000)
        with self.assertRaises(ABIValueError):
            decode_function_result(ABI_JSON, "balanceOf", [word(1)])

    def test_truncated_data_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            decode_function_result(ABI_JSON, "balanceOf", word(1)[:16])

    def test_trailing_data_raises(self):
        with self.assertRaises(AbiTrailingDataError):
            decode_function_result(ABI_JSON, "balanceOf", word(1) + word(2))

    def test_bad_dynamic_offset_raises_value_error(self):
        # 单个 string 输出：偏移指向界外。
        with self.assertRaises(ABIValueError):
            decode_function_result(ABI_JSON, "name", word(0xFFFF))

    def test_nonzero_padding_raises_value_error(self):
        # string 实体的补零区含非零字节。
        bad = word(0x20) + word(1) + b"a" + b"\x01" + b"\x00" * 30
        with self.assertRaises(ABIValueError):
            decode_function_result(ABI_JSON, "name", bad)

    def test_result_is_immutable(self):
        result = decode_function_result(ABI_JSON, "balanceOf", word(1))
        with self.assertRaises(Exception):
            result.outputs = ()


class FunctionSelectionTest(unittest.TestCase):
    def test_select_by_canonical_signature(self):
        result = encode_function_result(ABI_JSON, "foo(uint256)", [9])
        self.assertEqual(result.data, word(9))
        self.assertEqual(result.signature, "foo(uint256)")

    def test_overload_by_name_raises(self):
        with self.assertRaises(AbiOverloadError):
            encode_function_result(ABI_JSON, "foo", [9])
        with self.assertRaises(AbiOverloadError):
            decode_function_result(ABI_JSON, "foo", word(9))

    def test_unknown_function_raises_not_found(self):
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_result(ABI_JSON, "missing", [])
        with self.assertRaises(AbiFunctionNotFoundError):
            decode_function_result(ABI_JSON, "missing(uint8)", b"")

    def test_decode_by_signature(self):
        result = decode_function_result(ABI_JSON, "foo(address)", word(1))
        self.assertEqual(result.values, (True,))


class RoundTripTest(unittest.TestCase):
    def roundtrip(self, abi, name, values):
        encoded = encode_function_result(abi, name, values)
        decoded = decode_function_result(abi, name, encoded.data)
        self.assertEqual(decoded.values, tuple(values))
        # 十六进制形式同样可解码。
        decoded_hex = decode_function_result(abi, name, encoded.data_hex)
        self.assertEqual(decoded_hex.values, tuple(values))

    def test_single_output(self):
        self.roundtrip(ABI_JSON, "balanceOf", [12345])

    def test_multiple_static_outputs(self):
        self.roundtrip(ABI_JSON, "getPair", [ADDR_A, ADDR_B])

    def test_dynamic_outputs(self):
        self.roundtrip(ABI_JSON, "getDynamic", [7, "héllo", b"\x00\xff" * 40])

    def test_arrays_and_nested_arrays(self):
        self.roundtrip(
            ABI_JSON,
            "getArrays",
            [[1, 2, 255], [True, False, True], [["a", "bb"], [], ["ccc"]]],
        )

    def test_tuple_with_components(self):
        config = (
            ADDR_A,
            [1, 2, 3],
            ("meta-label", [b"\x01\x02", b"\x03\x04"]),
        )
        self.roundtrip(ABI_JSON, "getConfig", [config])

    def test_string_output(self):
        self.roundtrip(ABI_JSON, "name", ["Token 名字"])

    def test_address_value_format(self):
        result = decode_function_result(
            ABI_JSON, "getPair", addr_word(ADDR_A) + addr_word(ADDR_B)
        )
        for value in result.values:
            self.assertRegex(value, r"^0x[0-9a-f]{40}$")


class AliasTest(unittest.TestCase):
    def test_camel_case_aliases(self):
        self.assertIs(encodeFunctionResult, encode_function_result)
        self.assertIs(decodeFunctionResult, decode_function_result)

    def test_abi_accepts_list_and_json_string(self):
        from_json = encode_function_result(ABI_JSON, "balanceOf", [1])
        from_list = encode_function_result(ALL_FUNCTIONS, "balanceOf", [1])
        self.assertEqual(from_json.data, from_list.data)


if __name__ == "__main__":
    unittest.main()
