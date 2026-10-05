"""parse_error_abi / canonical_error_signature / error_selector /
encode_error_data / decode_error_data 的行为测试。

仅使用标准库 unittest，无第三方依赖。selector 向量取 Solidity 内建
Error(string) = 0x08c379a0 与 Panic(uint256) = 0x4e487b71。
"""

import json
import unittest

from abi_kit import (
    AbiErrorDataLengthError,
    AbiErrorNotFoundError,
    AbiErrorOverloadError,
    AbiErrorSelectorError,
    AbiErrorTrailingDataError,
    AbiMetadataError,
    ABIValueError,
    DecodedErrorData,
    EncodedErrorData,
    ErrorArgument,
    ErrorDefinition,
    canonical_error_signature,
    decode_error_data,
    encode_error_data,
    error_selector,
    parse_error_abi,
)
from abi_kit._keccak import keccak_256

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def err(name, inputs, **extra):
    entry = {"type": "error", "name": name, "inputs": inputs}
    entry.update(extra)
    return entry


def param(type_, name="", **extra):
    node = {"type": type_, "name": name}
    node.update(extra)
    return node


ERROR_BUILTIN = err("Error", [param("string", "message")])
PANIC_BUILTIN = err("Panic", [param("uint256", "reason")])
UNAUTHORIZED = err(
    "Unauthorized", [param("address", "caller"), param("uint256", "code")]
)
NO_ARGS = err("Nothing", [])
TUPLE_ERROR = err(
    "BadOrder",
    [
        param(
            "tuple",
            "order",
            components=[
                param("address", "maker"),
                param("uint256", "amount"),
            ],
        ),
        param("bool", "flag"),
    ],
)

ERROR_SELECTOR = "0x08c379a0"
PANIC_SELECTOR = "0x4e487b71"


class ParseErrorAbiTests(unittest.TestCase):
    def test_only_error_entries_consumed_in_order(self):
        abi = [
            {"type": "function", "name": "f", "inputs": []},
            ERROR_BUILTIN,
            {"type": "event", "name": "E", "inputs": []},
            PANIC_BUILTIN,
            {"type": "constructor", "inputs": []},
            {"type": "receive"},
            {"type": "fallback"},
        ]
        errors = parse_error_abi(abi)
        self.assertEqual(
            [canonical_error_signature(e) for e in errors],
            ["Error(string)", "Panic(uint256)"],
        )
        self.assertIsInstance(errors[0], ErrorDefinition)
        self.assertEqual(errors[0].name, "Error")
        self.assertEqual(errors[0].inputs[0].name, "message")

    def test_accepts_json_string(self):
        errors = parse_error_abi(json.dumps([ERROR_BUILTIN, NO_ARGS]))
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[1].inputs, ())

    def test_tuple_components_expanded(self):
        (error,) = parse_error_abi([TUPLE_ERROR])
        self.assertEqual(
            canonical_error_signature(error), "BadOrder((address,uint256),bool)"
        )

    def test_array_inputs(self):
        (error,) = parse_error_abi(
            [err("BatchFailed", [param("uint256[]", "ids"), param("bytes", "blob")])]
        )
        self.assertEqual(
            canonical_error_signature(error), "BatchFailed(uint256[],bytes)"
        )

    def test_param_names_do_not_participate(self):
        a = parse_error_abi([err("E", [param("uint256", "a")])])[0]
        b = parse_error_abi([err("E", [param("uint256", "b")])])[0]
        self.assertEqual(
            canonical_error_signature(a), canonical_error_signature(b)
        )

    def test_invalid_roots(self):
        with self.assertRaises(AbiMetadataError):
            parse_error_abi({"type": "error", "name": "E", "inputs": []})
        with self.assertRaises(AbiMetadataError):
            parse_error_abi("not json")
        with self.assertRaises(AbiMetadataError):
            parse_error_abi(42)
        with self.assertRaises(AbiMetadataError):
            parse_error_abi([42])
        with self.assertRaises(AbiMetadataError):
            parse_error_abi([{"type": "error", "inputs": []}])
        with self.assertRaises(AbiMetadataError):
            parse_error_abi([{"type": "error", "name": "E"}])  # 缺 inputs
        with self.assertRaises(AbiMetadataError):
            parse_error_abi([{"type": "error", "name": "E", "inputs": {}}])
        with self.assertRaises(AbiMetadataError):
            parse_error_abi([err("E", [param("uint7", "x")])])
        with self.assertRaises(AbiMetadataError):
            parse_error_abi([{"type": "bogus", "name": "E", "inputs": []}])
        with self.assertRaises(AbiMetadataError):
            parse_error_abi(
                [err("E", [param("tuple", "x")])]  # tuple 缺 components
            )

    def test_duplicate_canonical_signature(self):
        with self.assertRaises(AbiMetadataError):
            parse_error_abi(
                [
                    err("E", [param("uint256", "a")]),
                    err("E", [param("uint256", "b")]),
                ]
            )

    def test_definitions_are_immutable(self):
        error = parse_error_abi([UNAUTHORIZED])[0]
        with self.assertRaises(Exception):
            error.name = "Other"


class SignatureAndSelectorTests(unittest.TestCase):
    def test_builtin_selectors(self):
        error_sig, panic_sig = parse_error_abi([ERROR_BUILTIN, PANIC_BUILTIN])
        self.assertEqual(canonical_error_signature(error_sig), "Error(string)")
        self.assertEqual(canonical_error_signature(panic_sig), "Panic(uint256)")
        self.assertEqual(error_selector(error_sig), bytes.fromhex("08c379a0"))
        self.assertEqual(error_selector(panic_sig), bytes.fromhex("4e487b71"))
        self.assertEqual(
            error_selector(error_sig), keccak_256(b"Error(string)")[:4]
        )

    def test_no_args_signature(self):
        (error,) = parse_error_abi([NO_ARGS])
        self.assertEqual(canonical_error_signature(error), "Nothing()")

    def test_requires_error_definition(self):
        with self.assertRaises(AbiMetadataError):
            canonical_error_signature("Error(string)")


class EncodeErrorDataTests(unittest.TestCase):
    def test_encode_string_error_vector(self):
        result = encode_error_data([ERROR_BUILTIN], "Error", ["boom"])
        self.assertIsInstance(result, EncodedErrorData)
        self.assertEqual(result.selector, bytes.fromhex("08c379a0"))
        self.assertEqual(result.selector_hex, ERROR_SELECTOR)
        self.assertEqual(result.error_name, "Error")
        self.assertEqual(result.signature, "Error(string)")
        # offset 32, length 4, "boom" 右补零。
        payload = word(32) + word(4) + b"boom" + b"\x00" * 28
        self.assertEqual(result.data, result.selector + payload)
        self.assertEqual(result.data_hex, "0x" + result.data.hex())

    def test_encode_panic_vector(self):
        result = encode_error_data([PANIC_BUILTIN], "Panic(uint256)", [0x11])
        self.assertEqual(result.selector.hex(), "4e487b71")
        self.assertEqual(result.data, bytes.fromhex("4e487b71") + word(0x11))

    def test_no_args_error_is_four_bytes(self):
        result = encode_error_data([NO_ARGS], "Nothing")
        self.assertEqual(result.data, result.selector)
        self.assertEqual(len(result.data), 4)
        result2 = encode_error_data([NO_ARGS], "Nothing()", [])
        self.assertEqual(result2.data, result.data)

    def test_static_and_tuple_values(self):
        addr = "0x" + "33" * 20
        result = encode_error_data(
            [UNAUTHORIZED], "Unauthorized", [addr, 7]
        )
        self.assertEqual(
            result.data,
            result.selector
            + b"\x00" * 12
            + bytes.fromhex("33" * 20)
            + word(7),
        )

        order = ("0x" + "44" * 20, 99)
        tup_result = encode_error_data([TUPLE_ERROR], "BadOrder", [order, True])
        # 静态 tuple + bool：payload 为 3 个 32 字节字。
        self.assertEqual(
            tup_result.data,
            tup_result.selector
            + b"\x00" * 12
            + bytes.fromhex("44" * 20)
            + word(99)
            + word(1),
        )

    def test_encode_result_immutable(self):
        result = encode_error_data([NO_ARGS], "Nothing")
        with self.assertRaises(Exception):
            result.data = b""

    def test_not_found_and_overload(self):
        with self.assertRaises(AbiErrorNotFoundError):
            encode_error_data([NO_ARGS], "Missing")
        with self.assertRaises(AbiErrorNotFoundError):
            encode_error_data([NO_ARGS], "Missing()")
        overloaded = [
            err("E", [param("uint256", "a")]),
            err("E", [param("address", "a")]),
        ]
        with self.assertRaises(AbiErrorOverloadError):
            encode_error_data(overloaded, "E", [1])
        # 给规范签名仍可唯一定位。
        result = encode_error_data(overloaded, "E(uint256)", [1])
        self.assertEqual(result.signature, "E(uint256)")

    def test_bad_name_metadata(self):
        with self.assertRaises(AbiMetadataError):
            encode_error_data([NO_ARGS], "not an ident")
        with self.assertRaises(AbiMetadataError):
            encode_error_data([NO_ARGS], "")

    def test_value_errors(self):
        with self.assertRaises(ABIValueError):
            encode_error_data([PANIC_BUILTIN], "Panic", ["17"])
        with self.assertRaises(ABIValueError):
            encode_error_data([PANIC_BUILTIN], "Panic", [1, 2])
        with self.assertRaises(ABIValueError):
            encode_error_data([NO_ARGS], "Nothing", [1])
        with self.assertRaises(ABIValueError):
            encode_error_data([ERROR_BUILTIN], "Error", (42,))
        with self.assertRaises(ABIValueError):
            encode_error_data([ERROR_BUILTIN], "Error", {"x": 1})


class DecodeErrorDataTests(unittest.TestCase):
    def test_decode_panic_bytes_and_hex(self):
        raw = bytes.fromhex("4e487b71") + word(0x11)
        for data in (raw, raw.hex(), "0x" + raw.hex()):
            result = decode_error_data([PANIC_BUILTIN], data)
            self.assertIsInstance(result, DecodedErrorData)
            self.assertEqual(result.error.name, "Panic")
            self.assertEqual(result.name, "Panic")
            self.assertEqual(result.error_name, "Panic")
            self.assertEqual(result.signature, "Panic(uint256)")
            self.assertEqual(result.selector, bytes.fromhex("4e487b71"))
            self.assertEqual(result.selector_hex, PANIC_SELECTOR)
            self.assertEqual(result.values, (17,))
            self.assertEqual(
                result.args,
                (ErrorArgument(name="reason", type="uint256", value=17),),
            )

    def test_decode_string_error(self):
        encoded = encode_error_data([ERROR_BUILTIN], "Error", ["boom"])
        result = decode_error_data([ERROR_BUILTIN], encoded.data)
        self.assertEqual(result.values, ("boom",))
        self.assertEqual(
            result.args,
            (ErrorArgument(name="message", type="string", value="boom"),),
        )

    def test_decode_no_args_error(self):
        encoded = encode_error_data([NO_ARGS], "Nothing")
        result = decode_error_data([NO_ARGS], encoded.data)
        self.assertEqual(result.args, ())
        self.assertEqual(result.values, ())
        self.assertEqual(len(encoded.data), 4)

    def test_decode_dynamic_round_trips(self):
        abi = [
            err(
                "Complex",
                [
                    param("string", "s"),
                    param("uint256[]", "ids"),
                    param("bytes", "blob"),
                    param(
                        "tuple",
                        "nested",
                        components=[
                            param("address", "to"),
                            param("bool[]", "flags"),
                        ],
                    ),
                ],
            )
        ]
        values = (
            "héllo",
            [1, 2, 3],
            b"\xde\xad\xbe\xef",
            ("0x" + "ab" * 20, [True, False]),
        )
        encoded = encode_error_data(abi, "Complex", values)
        decoded = decode_error_data(abi, encoded.data)
        self.assertEqual(decoded.values, values)
        # 再编码得到相同字节。
        self.assertEqual(
            encode_error_data(abi, "Complex", list(decoded.values)).data,
            encoded.data,
        )
        self.assertEqual(
            [arg.type for arg in decoded.args],
            ["string", "uint256[]", "bytes", "(address,bool[])"],
        )

    def test_data_too_short(self):
        for bad in (b"", b"\x08", b"\x08\xc3", "0x08c3", b"\x08" * 3):
            with self.assertRaises(AbiErrorDataLengthError):
                decode_error_data([ERROR_BUILTIN], bad)

    def test_unknown_selector(self):
        raw = bytes.fromhex("ffffffff") + word(1)
        with self.assertRaises(AbiErrorSelectorError):
            decode_error_data([PANIC_BUILTIN], raw)

    def test_trailing_data(self):
        # Panic(uint256) 只消费一个字，多给一个字即尾随。
        raw = bytes.fromhex("4e487b71") + word(1) + word(0)
        with self.assertRaises(AbiErrorTrailingDataError):
            decode_error_data([PANIC_BUILTIN], raw)

    def test_invalid_hex_and_type(self):
        with self.assertRaises(ABIValueError):
            decode_error_data([PANIC_BUILTIN], "4e487b7" + "0" * 64)  # 奇数位
        with self.assertRaises(ABIValueError):
            decode_error_data([PANIC_BUILTIN], "0xzz" + "00" * 32)
        with self.assertRaises(ABIValueError):
            decode_error_data([PANIC_BUILTIN], 1234)

    def test_strict_decode_failure(self):
        # bool 参数的值字既非 0 也非 1，严格解码失败抛 ABIValueError。
        raw = error_selector(parse_error_abi([err("Bad", [param("bool")])])[0]) + word(2)
        with self.assertRaises(ABIValueError):
            decode_error_data([err("Bad", [param("bool")])], raw)

    def test_decoded_result_immutable(self):
        raw = bytes.fromhex("4e487b71") + word(1)
        result = decode_error_data([PANIC_BUILTIN], raw)
        with self.assertRaises(Exception):
            result.args = ()


class MixedAbiTests(unittest.TestCase):
    def test_error_entries_skipped_by_function_path_and_vice_versa(self):
        abi = [
            {"type": "function", "name": "transfer", "inputs": [
                param("address", "to"), param("uint256", "value")
            ]},
            {"type": "event", "name": "Transfer", "inputs": []},
            ERROR_BUILTIN,
            UNAUTHORIZED,
        ]
        errors = parse_error_abi(abi)
        self.assertEqual(len(errors), 2)

        from abi_kit import decode_function_call, encode_function_call

        call = encode_function_call(abi, "transfer", ["0x" + "11" * 20, 5])
        decoded_call = decode_function_call(abi, call.calldata)
        self.assertEqual(decoded_call.function_name, "transfer")

        error_data = encode_error_data(abi, "Unauthorized", ["0x" + "22" * 20, 3])
        decoded_error = decode_error_data(abi, error_data.data)
        self.assertEqual(decoded_error.signature, "Unauthorized(address,uint256)")
        self.assertEqual(decoded_error.values, ("0x" + "22" * 20, 3))

    def test_error_exception_hierarchy(self):
        for exc_type in (
            AbiErrorNotFoundError,
            AbiErrorOverloadError,
            AbiErrorSelectorError,
            AbiErrorDataLengthError,
            AbiErrorTrailingDataError,
            AbiMetadataError,
        ):
            self.assertTrue(issubclass(exc_type, ValueError))
        # 五类 error 异常彼此独立。
        self.assertIsNot(AbiErrorSelectorError, AbiErrorNotFoundError)


if __name__ == "__main__":
    unittest.main()
