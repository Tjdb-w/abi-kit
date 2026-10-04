"""encode_function_result / decode_function_result 的行为测试。

仅使用标准库 unittest，无第三方依赖。返回值数据为纯 ABI tuple 编码，
不含 selector；空 outputs 与 b"" 往返得到空 tuple。
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
    canonical_function_signature,
    decode_function_call,
    decode_function_result,
    decodeFunctionResult,
    encode_function_call,
    encode_function_result,
    encodeFunctionResult,
    format_abi_type,
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
ADDR_UPPER = "0x" + "AA" * 20


def fn(name, inputs, outputs=None, **extra):
    entry = {"type": "function", "name": name, "inputs": inputs}
    if outputs is not None:
        entry["outputs"] = outputs
    entry.update(extra)
    return entry


def event(name):
    return {"type": "event", "name": name, "inputs": []}


# 带 outputs 的典型 ABI：balanceOf -> uint256；一个多输出 view 函数；
# 无 outputs 的写函数；重载对。
ABI = [
    fn("transfer",
       [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
       outputs=[{"name": "ok", "type": "bool"}]),
    fn("balanceOf", [{"name": "owner", "type": "address"}],
       outputs=[{"name": "balance", "type": "uint256"}]),
    fn("peek", [], outputs=[
        {"name": "who", "type": "address"},
        {"name": "amount", "type": "uint128"},
        {"name": "note", "type": "string"},
    ]),
    fn("write", [{"type": "uint256"}], outputs=[]),
    fn("implicit", [{"type": "bool"}]),
    fn("f", [{"type": "uint256"}], outputs=[{"type": "uint256"}]),
    fn("f", [{"type": "address"}], outputs=[{"type": "address"}]),
    event("Ping"),
]


class ParseOutputsTests(unittest.TestCase):
    def test_outputs_default_empty(self):
        f = parse_function_abi([fn("g", [])])[0]
        self.assertEqual(f.outputs, ())

    def test_explicit_empty_outputs(self):
        f = parse_function_abi([fn("g", [], outputs=[])])[0]
        self.assertEqual(f.outputs, ())

    def test_outputs_preserved_in_order(self):
        functions = {f.name: f for f in parse_function_abi(ABI)}
        types = [format_abi_type(p.abi_type) for p in functions["peek"].outputs]
        self.assertEqual(types, ["address", "uint128", "string"])
        self.assertEqual(
            [p.name for p in functions["peek"].outputs],
            ["who", "amount", "note"],
        )

    def test_output_tuple_components(self):
        f = parse_function_abi(
            [fn("g", [], outputs=[
                {"name": "p", "type": "tuple", "components": [
                    {"name": "a", "type": "address"},
                    {"name": "xs", "type": "uint256[]"},
                ]},
            ])]
        )[0]
        self.assertEqual(format_abi_type(f.outputs[0].abi_type), "(address,uint256[])")
        self.assertEqual(f.outputs[0].abi_type.names, ("a", "xs"))

    def test_outputs_do_not_change_signature_or_selector(self):
        with_out = fn("f", [{"type": "uint256"}],
                     outputs=[{"type": "bool"}, {"type": "string"}])
        without_out = fn("f", [{"type": "uint256"}])
        a = parse_function_abi([with_out])[0]
        b = parse_function_abi([without_out])[0]
        self.assertEqual(canonical_function_signature(a), "f(uint256)")
        self.assertEqual(canonical_function_signature(a),
                         canonical_function_signature(b))
        self.assertEqual(function_selector(a), function_selector(b))

    def test_outputs_absent_keeps_calldata_inputs_only(self):
        result = encode_function_call(ABI, "balanceOf", [ADDR_A])
        decoded = decode_function_call(ABI, result.calldata)
        self.assertEqual(decoded.values, (ADDR_A,))
        self.assertEqual(result.calldata, result.selector + addr_word(ADDR_A))

    def _invalid(self, entry):
        with self.assertRaises(AbiMetadataError):
            parse_function_abi([entry])

    def test_invalid_outputs_metadata(self):
        self._invalid(fn("g", [], outputs="nope"))
        self._invalid(fn("g", [], outputs={}))
        self._invalid(fn("g", [], outputs=[42]))
        self._invalid(fn("g", [], outputs=[{"type": 7}]))
        self._invalid(fn("g", [], outputs=[{"type": ""}]))
        self._invalid(fn("g", [], outputs=[{"type": "uint"}]))
        self._invalid(fn("g", [], outputs=[{"type": "address", "name": 1}]))
        self._invalid(fn("g", [], outputs=[{"type": "tuple"}]))
        self._invalid(fn("g", [], outputs=[
            {"type": "tuple", "components": {"a": 1}}]))
        self._invalid(fn("g", [], outputs=[
            {"type": "uint256", "components": [{"type": "bool"}]}]))


class EncodeFunctionResultTests(unittest.TestCase):
    def test_no_outputs_empty_only(self):
        for key in ("write", "implicit"):
            r1 = encode_function_result(ABI, key)
            r2 = encode_function_result(ABI, key, [])
            r3 = encode_function_result(ABI, key, ())
            self.assertIsInstance(r1, EncodedFunctionResult)
            self.assertEqual(r1.data, b"")
            self.assertEqual(r1.data, r2.data)
            self.assertEqual(r1.data, r3.data)
            self.assertEqual(r1.data_hex, "0x")
            self.assertEqual(r1.function_name, key)

    def test_single_static_output(self):
        result = encode_function_result(ABI, "balanceOf", [12345])
        self.assertEqual(result.data, word(12345))
        self.assertEqual(result.data_hex, "0x" + word(12345).hex())
        self.assertEqual(result.function_name, "balanceOf")
        self.assertEqual(result.signature, "balanceOf(address)")

    def test_multiple_outputs_layout(self):
        result = encode_function_result(ABI, "peek", [ADDR_A, 9, ""])
        # address + uint128 两个静态头，string 偏移=96 后接长度 0。
        expected = addr_word(ADDR_A) + word(9) + word(96) + word(0)
        self.assertEqual(result.data, expected)

    def test_values_accept_tuple(self):
        r_list = encode_function_result(ABI, "balanceOf", [7])
        r_tuple = encode_function_result(ABI, "balanceOf", (7,))
        self.assertEqual(r_list.data, r_tuple.data)

    def test_json_string_abi(self):
        result = encode_function_result(json.dumps(ABI), "balanceOf", [5])
        self.assertEqual(result.data, word(5))

    def test_select_by_canonical_signature(self):
        r_uint = encode_function_result(ABI, "f(uint256)", [42])
        r_addr = encode_function_result(ABI, "f(address)", [ADDR_A])
        self.assertEqual(r_uint.data, word(42))
        self.assertEqual(r_uint.signature, "f(uint256)")
        self.assertEqual(r_addr.data, addr_word(ADDR_A))
        self.assertEqual(r_addr.signature, "f(address)")

    def test_all_elementary_output_types_roundtrip(self):
        abi = [fn("all", [], outputs=[
            {"name": "u", "type": "uint8"},
            {"name": "i", "type": "int128"},
            {"name": "a", "type": "address"},
            {"name": "b", "type": "bool"},
            {"name": "s", "type": "string"},
            {"name": "r", "type": "bytes"},
            {"name": "m", "type": "bytes4"},
            {"name": "m32", "type": "bytes32"},
        ])]
        values = (255, -12345, ADDR_B, True, "héllo",
                  b"\x00\xff" * 10, b"wxyz", bytes(range(32)))
        encoded = encode_function_result(abi, "all", list(values))
        decoded = decode_function_result(abi, "all", encoded.data)
        self.assertEqual(decoded.values, values)

    def test_fixed_dynamic_nested_arrays_roundtrip(self):
        abi = [fn("arrays", [], outputs=[
            {"name": "fixed", "type": "uint16[3]"},
            {"name": "dyn", "type": "address[]"},
            {"name": "nested", "type": "uint256[2][]"},
            {"name": "empty", "type": "bytes[]"},
        ])]
        values = (
            [1, 2, 3],
            [ADDR_A, ADDR_B],
            [[1, 2], [3, 4], [5, 6]],
            [],
        )
        encoded = encode_function_result(abi, "arrays", list(values))
        decoded = decode_function_result(abi, "arrays", encoded.data)
        self.assertEqual(decoded.values, values)

    def test_tuple_and_nested_dynamic_roundtrip(self):
        abi = [fn("complex", [{"type": "uint256"}], outputs=[
            {"name": "id", "type": "uint64"},
            {"name": "p", "type": "tuple", "components": [
                {"name": "to", "type": "address"},
                {"name": "amounts", "type": "uint128[2]"},
                {"name": "items", "type": "tuple[]", "components": [
                    {"name": "x", "type": "bool"},
                    {"name": "data", "type": "bytes"},
                ]},
            ]},
            {"name": "tags", "type": "string[]"},
        ])]
        values = (
            7,
            (ADDR_A, [10, 20], [(True, b"\x01"), (False, b"")]),
            ["a", "bb", ""],
        )
        encoded = encode_function_result(abi, "complex", list(values))
        decoded = decode_function_result(abi, "complex", encoded.data)
        self.assertEqual(decoded.values, tuple(values))
        self.assertEqual(decoded.outputs[1].type,
                         "(address,uint128[2],(bool,bytes)[])")

    def test_deterministic_and_reencodable(self):
        values = ["hi", b"\xde\xad", [1, 2], True]
        abi = [fn("mix", [], outputs=[
            {"type": "string"}, {"type": "bytes"},
            {"type": "uint32[]"}, {"type": "bool"},
        ])]
        r1 = encode_function_result(abi, "mix", values)
        r2 = encode_function_result(json.dumps(abi), "mix", values)
        self.assertEqual(r1.data, r2.data)
        decoded = decode_function_result(abi, "mix", r1.data)
        self.assertEqual(
            encode_function_result(abi, "mix", list(decoded.values)).data,
            r1.data,
        )

    def test_address_normalized_to_lowercase(self):
        result = encode_function_result(ABI, "f(address)", [ADDR_UPPER])
        decoded = decode_function_result(ABI, "f(address)", result.data)
        expected_lower = "0x" + "aa" * 20
        self.assertEqual(decoded.values, (expected_lower,))
        self.assertEqual(decoded.outputs[0].value, expected_lower)

    def test_arity_mismatch_is_value_error(self):
        with self.assertRaises(AbiValueError):
            encode_function_result(ABI, "balanceOf", [])
        with self.assertRaises(AbiValueError):
            encode_function_result(ABI, "balanceOf", [1, 2])
        with self.assertRaises(AbiValueError):
            encode_function_result(ABI, "write", [1])

    def test_value_type_mismatch_is_value_error(self):
        with self.assertRaises(AbiValueError):
            encode_function_result(ABI, "balanceOf", ["1"])
        with self.assertRaises(AbiValueError):
            encode_function_result(ABI, "transfer", [ADDR_A, 1])  # bool 输出
        with self.assertRaises(AbiValueError):
            encode_function_result(ABI, "transfer", [[True]])

    def test_wrong_container_type_is_value_error(self):
        with self.assertRaises(AbiValueError):
            encode_function_result(ABI, "balanceOf", 5)

    def test_unknown_and_overload_resolution(self):
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_result(ABI, "missing", [])
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_result(ABI, "f(bool)", [True])
        with self.assertRaises(AbiOverloadError):
            encode_function_result(ABI, "f", [1])


class DecodeFunctionResultTests(unittest.TestCase):
    def test_empty_outputs_roundtrip(self):
        for empty in (b"", "", "0x", "0X"):
            with self.subTest(empty=empty):
                decoded = decode_function_result(ABI, "write", empty)
                self.assertIsInstance(decoded, DecodedFunctionResult)
                self.assertEqual(decoded.outputs, ())
                self.assertEqual(decoded.values, ())
                self.assertEqual(decoded.function_name, "write")
                self.assertEqual(decoded.signature, "write(uint256)")

    def test_decoded_arguments_carry_name_and_type(self):
        encoded = encode_function_result(ABI, "peek", [ADDR_B, 3, "x"])
        decoded = decode_function_result(ABI, "peek", encoded.data)
        self.assertEqual(
            decoded.outputs,
            (
                FunctionArgument("who", "address", ADDR_B),
                FunctionArgument("amount", "uint128", 3),
                FunctionArgument("note", "string", "x"),
            ),
        )
        self.assertEqual(decoded.values, (ADDR_B, 3, "x"))
        expected = parse_function_abi(ABI)
        self.assertIn(
            decoded.function,
            [f for f in expected if f.name == "peek"],
        )
        self.assertEqual(decoded.function_name, "peek")

    def test_hex_inputs_with_and_without_prefix(self):
        encoded = encode_function_result(ABI, "balanceOf", [99])
        d1 = decode_function_result(ABI, "balanceOf", encoded.data.hex())
        d2 = decode_function_result(ABI, "balanceOf", "0x" + encoded.data.hex())
        d3 = decode_function_result(ABI, "balanceOf", encoded.data)
        self.assertEqual(d1.values, (99,))
        self.assertEqual(d2.values, (99,))
        self.assertEqual(d3.values, (99,))

    def test_single_multiple_nested_dynamic_stable_roundtrip(self):
        abi = [fn("m", [], outputs=[
            {"name": "s", "type": "string"},
            {"name": "grp", "type": "tuple[]", "components": [
                {"name": "a", "type": "address"},
                {"name": "note", "type": "string"},
            ]},
            {"name": "matrix", "type": "uint256[2][]"},
        ])]
        values = ("top", [(ADDR_A, "one"), (ADDR_B, "")], [[1, 2], [3, 4]])
        encoded = encode_function_result(abi, "m", list(values))
        decoded = decode_function_result(abi, "m", encoded.data)
        self.assertEqual(decoded.values, values)

    def test_bool_output_roundtrip(self):
        decoded = decode_function_result(
            ABI, "transfer",
            encode_function_result(ABI, "transfer", [True]).data,
        )
        self.assertEqual(decoded.values, (True,))
        self.assertEqual(decoded.outputs[0].type, "bool")

    def test_invalid_input_type_is_value_error(self):
        with self.assertRaises(AbiValueError):
            decode_function_result(ABI, "balanceOf", 1234)
        with self.assertRaises(AbiValueError):
            decode_function_result(ABI, "balanceOf", None)
        with self.assertRaises(AbiValueError):
            decode_function_result(ABI, "balanceOf", [word(0)])

    def test_invalid_hex_is_value_error(self):
        with self.assertRaises(AbiValueError):
            decode_function_result(ABI, "balanceOf", "0xzz")
        with self.assertRaises(AbiValueError):
            decode_function_result(ABI, "balanceOf", "abc")  # 奇数位

    def test_too_short_is_value_error(self):
        # uint256 输出只有 31 字节。
        with self.assertRaises(AbiValueError):
            decode_function_result(ABI, "balanceOf", b"\x00" * 31)
        with self.assertRaises(AbiValueError):
            decode_function_result(ABI, "balanceOf", "00" * 31)

    def test_dynamic_offset_violation_is_value_error(self):
        abi = [fn("s", [], outputs=[{"type": "string"}])]
        # 偏移指向 head 之外。
        bad = word(64) + word(0)
        with self.assertRaises(AbiValueError):
            decode_function_result(abi, "s", bad)

    def test_nonzero_padding_is_value_error(self):
        abi = [fn("b", [], outputs=[{"type": "bytes4"}])]
        bad = b"abcd" + b"\x00" * 27 + b"\x01"
        with self.assertRaises(AbiValueError):
            decode_function_result(abi, "b", bad)

    def test_bad_utf8_is_value_error(self):
        abi = [fn("s", [], outputs=[{"type": "string"}])]
        payload = b"\xff\xfe"
        bad = word(32) + word(len(payload)) + payload + b"\x00" * 30
        with self.assertRaises(AbiValueError):
            decode_function_result(abi, "s", bad)

    def test_fixed_array_length_constraint_is_value_error(self):
        abi = [fn("a", [], outputs=[{"type": "uint256[2]"}])]
        # 编码只放一个元素，head 期望 64 字节静态块。
        bad = word(1)
        with self.assertRaises(AbiValueError):
            decode_function_result(abi, "a", bad)

    def test_address_high_padding_nonzero_is_value_error(self):
        abi = [fn("a", [], outputs=[{"type": "address"}])]
        bad = b"\x01" + b"\x00" * 31
        with self.assertRaises(AbiValueError):
            decode_function_result(abi, "a", bad)

    def test_trailing_bytes_is_trailing_error(self):
        encoded = encode_function_result(ABI, "balanceOf", [1])
        with self.assertRaises(AbiTrailingDataError):
            decode_function_result(ABI, "balanceOf", encoded.data + b"\x00")
        with self.assertRaises(AbiTrailingDataError):
            decode_function_result(
                ABI, "balanceOf", encoded.data + b"\x00" * 32
            )

    def test_nonempty_data_for_empty_outputs_is_trailing_error(self):
        with self.assertRaises(AbiTrailingDataError):
            decode_function_result(ABI, "write", b"\x00")
        with self.assertRaises(AbiTrailingDataError):
            decode_function_result(ABI, "write", "0x00")

    def test_resolution_errors(self):
        with self.assertRaises(AbiFunctionNotFoundError):
            decode_function_result(ABI, "missing", b"")
        with self.assertRaises(AbiOverloadError):
            decode_function_result(ABI, "f", word(1))
        with self.assertRaises(AbiMetadataError):
            decode_function_result(ABI, "f f", b"")

    def test_metadata_errors_propagate(self):
        with self.assertRaises(AbiMetadataError):
            decode_function_result("not json", "write", b"")
        with self.assertRaises(AbiMetadataError):
            decode_function_result(None, "write", b"")


class ResultObjectTests(unittest.TestCase):
    def test_immutability(self):
        encoded = encode_function_result(ABI, "balanceOf", [1])
        with self.assertRaises(Exception):
            encoded.data = b""
        decoded = decode_function_result(ABI, "balanceOf", encoded.data)
        with self.assertRaises(Exception):
            decoded.outputs = ()

    def test_aliases_are_same_objects(self):
        self.assertIs(encodeFunctionResult, encode_function_result)
        self.assertIs(decodeFunctionResult, decode_function_result)
        self.assertIs(AbiValueError, ABIValueError)

    def test_result_bytes_identity(self):
        encoded = encode_function_result(ABI, "balanceOf", [1])
        # 返回值数据不含 selector：恰好 32 字节。
        self.assertIsInstance(encoded.data, bytes)
        self.assertEqual(len(encoded.data), W)

    def test_function_path_unchanged_alongside_results(self):
        # 同一 ABI 上 calldata 与返回值路径互不干扰。
        call = encode_function_call(ABI, "balanceOf", [ADDR_A])
        result = encode_function_result(ABI, "balanceOf", [1000])
        decoded_call = decode_function_call(ABI, call.calldata)
        decoded_result = decode_function_result(ABI, "balanceOf", result.data)
        self.assertEqual(decoded_call.values, (ADDR_A,))
        self.assertEqual(decoded_result.values, (1000,))
        self.assertEqual(call.calldata[:4], function_selector(call.function))
        # 返回值数据是纯 tuple 编码，不带四字节 selector。
        self.assertEqual(result.data, word(1000))
        self.assertFalse(result.data.startswith(call.selector))


if __name__ == "__main__":
    unittest.main()
