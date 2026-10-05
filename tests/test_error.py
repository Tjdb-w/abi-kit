"""parse_error_abi / canonical_error_signature / error_selector /
encode_error_data / decode_error_data 的行为测试。

仅使用标准库 unittest，无第三方依赖。selector 向量取自 Solidity 内建
错误（Error(string) / Panic(uint256)）。
"""

import json
import unittest
from dataclasses import FrozenInstanceError

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

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


# keccak256("Error(string)")[:4] / keccak256("Panic(uint256)")[:4]
ERROR_STRING_SELECTOR = "0x08c379a0"
PANIC_SELECTOR = "0x4e487b71"

ADDR_A = "0x" + "11" * 20


def err(name, inputs):
    return {"type": "error", "name": name, "inputs": inputs}


ABI_ENTRIES = [
    {"type": "function", "name": "balanceOf", "inputs": [], "outputs": []},
    {
        "type": "event",
        "name": "Transfer",
        "inputs": [],
        "anonymous": False,
    },
    err("Unauthorized", []),
    err(
        "InsufficientBalance",
        [
            {"name": "account", "type": "address"},
            {"name": "required", "type": "uint256"},
        ],
    ),
    err("Failed", [{"name": "reason", "type": "string"}]),
    err(
        "Complex",
        [
            {"name": "values", "type": "uint256[]"},
            {
                "name": "meta",
                "type": "tuple",
                "components": [
                    {"name": "flag", "type": "bool"},
                    {"name": "tags", "type": "bytes32[2]"},
                ],
            },
        ],
    ),
]
ABI_JSON = json.dumps(ABI_ENTRIES)

OVERLOADED_ABI = [
    err("Failed", [{"name": "code", "type": "uint256"}]),
    err("Failed", [{"name": "reason", "type": "string"}]),
]


class ParseErrorAbiTest(unittest.TestCase):
    def test_parses_error_entries_in_order_and_skips_others(self):
        errors = parse_error_abi(ABI_JSON)
        self.assertEqual(
            [error.name for error in errors],
            ["Unauthorized", "InsufficientBalance", "Failed", "Complex"],
        )
        self.assertEqual(errors[1].inputs[0].name, "account")
        self.assertEqual(errors[1].inputs[1].type, "uint256")

    def test_accepts_entry_array_and_json_string(self):
        self.assertEqual(parse_error_abi(ABI_ENTRIES), parse_error_abi(ABI_JSON))

    def test_results_are_immutable(self):
        errors = parse_error_abi(ABI_JSON)
        for error in errors:
            self.assertIsInstance(error, ErrorDefinition)
            with self.assertRaises(FrozenInstanceError):
                error.name = "Other"
        self.assertIsInstance(errors, tuple)
        self.assertIsInstance(errors[0].inputs, tuple)

    def test_unnamed_parameter_defaults_to_empty_name(self):
        (error,) = parse_error_abi([err("E", [{"type": "uint8"}])])
        self.assertEqual(error.inputs[0].name, "")

    def test_tuple_components_expand_to_canonical_type(self):
        errors = parse_error_abi(ABI_JSON)
        complex_error = errors[3]
        self.assertEqual(complex_error.inputs[0].type, "uint256[]")
        self.assertEqual(complex_error.inputs[1].type, "(bool,bytes32[2])")

    def test_metadata_errors(self):
        bad_abis = [
            "{not json",
            json.dumps({"type": "error"}),
            42,
            [42],
            [{"name": "E", "inputs": []}],  # 缺 type
            [{"type": "unknown", "name": "E", "inputs": []}],
            [err("not a name!", [])],
            [{"type": "error", "name": "E"}],  # 缺 inputs
            [{"type": "error", "name": "E", "inputs": "nope"}],
            [{"type": "error", "name": "E", "inputs": [42]}],
            [
                {
                    "type": "error",
                    "name": "E",
                    "inputs": [{"name": 1, "type": "uint8"}],
                }
            ],
            [
                {
                    "type": "error",
                    "name": "E",
                    "inputs": [{"name": "x", "type": "uint7"}],
                }
            ],
        ]
        for bad in bad_abis:
            with self.subTest(abi=bad):
                with self.assertRaises(AbiMetadataError):
                    parse_error_abi(bad)

    def test_duplicate_canonical_signature_rejected(self):
        # 参数名不参与签名：同名同类型即重复。
        abi = [
            err("E", [{"name": "a", "type": "uint256"}]),
            err("E", [{"name": "b", "type": "uint256"}]),
        ]
        with self.assertRaises(AbiMetadataError):
            parse_error_abi(abi)

    def test_same_name_different_types_is_allowed(self):
        errors = parse_error_abi(OVERLOADED_ABI)
        self.assertEqual(len(errors), 2)


class SignatureAndSelectorTest(unittest.TestCase):
    def test_canonical_signature(self):
        errors = parse_error_abi(ABI_JSON)
        self.assertEqual(canonical_error_signature(errors[0]), "Unauthorized()")
        self.assertEqual(
            canonical_error_signature(errors[1]),
            "InsufficientBalance(address,uint256)",
        )
        self.assertEqual(
            canonical_error_signature(errors[3]),
            "Complex(uint256[],(bool,bytes32[2]))",
        )

    def test_canonical_signature_rejects_non_definition(self):
        with self.assertRaises(AbiMetadataError):
            canonical_error_signature("Unauthorized()")

    def test_builtin_error_selectors(self):
        (error_string,) = parse_error_abi(
            [err("Error", [{"name": "message", "type": "string"}])]
        )
        self.assertEqual(error_selector(error_string).hex(), ERROR_STRING_SELECTOR[2:])

        (panic,) = parse_error_abi(
            [err("Panic", [{"name": "code", "type": "uint256"}])]
        )
        self.assertEqual(error_selector(panic).hex(), PANIC_SELECTOR[2:])

    def test_selector_is_four_byte_bytes(self):
        errors = parse_error_abi(ABI_JSON)
        selector = error_selector(errors[0])
        self.assertIsInstance(selector, bytes)
        self.assertEqual(len(selector), 4)


class EncodeErrorDataTest(unittest.TestCase):
    def test_encode_by_name(self):
        encoded = encode_error_data(ABI_JSON, "InsufficientBalance", [ADDR_A, 7])
        self.assertIsInstance(encoded, EncodedErrorData)
        self.assertEqual(encoded.error_name, "InsufficientBalance")
        self.assertEqual(
            encoded.signature, "InsufficientBalance(address,uint256)"
        )
        self.assertEqual(encoded.selector_hex, "0x" + encoded.selector.hex())
        expected_payload = (
            b"\x00" * 12 + bytes.fromhex(ADDR_A[2:]) + word(7)
        )
        self.assertEqual(encoded.data, encoded.selector + expected_payload)
        self.assertEqual(encoded.data_hex, "0x" + encoded.data.hex())

    def test_encode_by_canonical_signature(self):
        by_name = encode_error_data(ABI_JSON, "Failed", ["boom"])
        by_signature = encode_error_data(ABI_JSON, "Failed(string)", ["boom"])
        self.assertEqual(by_name, by_signature)

    def test_encode_zero_argument_error(self):
        encoded = encode_error_data(ABI_JSON, "Unauthorized")
        self.assertEqual(encoded.data, encoded.selector)
        self.assertEqual(len(encoded.data), 4)

    def test_encode_accepts_tuple_args(self):
        encoded = encode_error_data(ABI_JSON, "InsufficientBalance", (ADDR_A, 7))
        self.assertEqual(encoded.data[4:], b"\x00" * 12 + bytes.fromhex(ADDR_A[2:]) + word(7))

    def test_encode_nested_tuple_and_arrays(self):
        tags = [b"\x01" * 32, b"\x02" * 32]
        encoded = encode_error_data(ABI_JSON, "Complex", [[1, 2, 3], (True, tags)])
        decoded = decode_error_data(ABI_JSON, encoded.data)
        self.assertEqual(decoded.values, ([1, 2, 3], (True, tags)))

    def test_encode_result_is_immutable(self):
        encoded = encode_error_data(ABI_JSON, "Unauthorized")
        with self.assertRaises(FrozenInstanceError):
            encoded.data = b""

    def test_encode_rejects_bad_args(self):
        with self.assertRaises(ABIValueError):
            encode_error_data(ABI_JSON, "Failed", "boom")  # 不是 list/tuple
        with self.assertRaises(ABIValueError):
            encode_error_data(ABI_JSON, "Failed", [])  # 数量不符
        with self.assertRaises(ABIValueError):
            encode_error_data(ABI_JSON, "Failed", [1])  # 类型不符
        with self.assertRaises(ABIValueError):
            encode_error_data(ABI_JSON, "InsufficientBalance", [ADDR_A, -1])

    def test_encode_unknown_error(self):
        with self.assertRaises(AbiErrorNotFoundError):
            encode_error_data(ABI_JSON, "Nope", [])
        with self.assertRaises(AbiErrorNotFoundError):
            encode_error_data(ABI_JSON, "Nope(uint256)", [1])

    def test_encode_overloaded_name_requires_signature(self):
        with self.assertRaises(AbiErrorOverloadError):
            encode_error_data(OVERLOADED_ABI, "Failed", [1])
        encoded = encode_error_data(OVERLOADED_ABI, "Failed(uint256)", [1])
        self.assertEqual(encoded.signature, "Failed(uint256)")


class DecodeErrorDataTest(unittest.TestCase):
    def test_roundtrip(self):
        encoded = encode_error_data(ABI_JSON, "InsufficientBalance", [ADDR_A, 7])
        decoded = decode_error_data(ABI_JSON, encoded.data)
        self.assertIsInstance(decoded, DecodedErrorData)
        self.assertEqual(decoded.error_name, "InsufficientBalance")
        self.assertEqual(
            decoded.signature, "InsufficientBalance(address,uint256)"
        )
        self.assertEqual(decoded.selector, encoded.selector)
        self.assertEqual(decoded.selector_hex, encoded.selector_hex)
        self.assertEqual(decoded.types, ("address", "uint256"))
        self.assertEqual(decoded.values, (ADDR_A, 7))
        self.assertEqual(
            [arg.name for arg in decoded.args], ["account", "required"]
        )
        self.assertEqual(decoded.args[0].type, "address")
        self.assertEqual(decoded.args[0].value, ADDR_A)

    def test_decode_accepts_hex_string(self):
        encoded = encode_error_data(ABI_JSON, "Failed", ["boom"])
        from_hex = decode_error_data(ABI_JSON, encoded.data_hex)
        self.assertEqual(from_hex.values, ("boom",))
        # 无 0x 前缀同样接受。
        self.assertEqual(
            decode_error_data(ABI_JSON, encoded.data_hex[2:]).values, ("boom",)
        )

    def test_decode_zero_argument_error(self):
        encoded = encode_error_data(ABI_JSON, "Unauthorized")
        decoded = decode_error_data(ABI_JSON, encoded.data)
        self.assertEqual(decoded.error_name, "Unauthorized")
        self.assertEqual(decoded.args, ())
        self.assertEqual(decoded.values, ())
        self.assertEqual(decoded.types, ())

    def test_decode_result_is_immutable(self):
        decoded = decode_error_data(ABI_JSON, encode_error_data(ABI_JSON, "Unauthorized").data)
        with self.assertRaises(FrozenInstanceError):
            decoded.selector = b""

    def test_decode_rejects_bad_data_type(self):
        for bad in (42, "0x123", "0xzz00", "not hex", [1, 2, 3]):
            with self.subTest(data=bad):
                with self.assertRaises(ABIValueError):
                    decode_error_data(ABI_JSON, bad)

    def test_decode_rejects_short_data(self):
        for short in (b"", b"\x08\xc3\x79", "0x", "0x08c379"):
            with self.subTest(data=short):
                with self.assertRaises(AbiErrorDataLengthError):
                    decode_error_data(ABI_JSON, short)

    def test_decode_rejects_unknown_selector(self):
        with self.assertRaises(AbiErrorSelectorError):
            decode_error_data(ABI_JSON, b"\xde\xad\xbe\xef" + word(1))

    def test_decode_rejects_truncated_payload(self):
        encoded = encode_error_data(ABI_JSON, "InsufficientBalance", [ADDR_A, 7])
        with self.assertRaises(ABIValueError):
            decode_error_data(ABI_JSON, encoded.data[:-W])

    def test_decode_rejects_trailing_data(self):
        encoded = encode_error_data(ABI_JSON, "InsufficientBalance", [ADDR_A, 7])
        with self.assertRaises(AbiErrorTrailingDataError):
            decode_error_data(ABI_JSON, encoded.data + word(0))
        # 无参 error 也不允许多余字节。
        encoded = encode_error_data(ABI_JSON, "Unauthorized")
        with self.assertRaises(AbiErrorTrailingDataError):
            decode_error_data(ABI_JSON, encoded.data + word(0))

    def test_decode_builtin_error_string(self):
        abi = [err("Error", [{"name": "message", "type": "string"}])]
        encoded = encode_error_data(abi, "Error", ["execution reverted"])
        self.assertEqual(encoded.selector_hex, ERROR_STRING_SELECTOR)
        decoded = decode_error_data(abi, encoded.data)
        self.assertEqual(decoded.values, ("execution reverted",))


class InteractionTest(unittest.TestCase):
    def test_function_path_still_skips_error_entries(self):
        from abi_kit import parse_function_abi

        functions = parse_function_abi(ABI_JSON)
        self.assertEqual([function.name for function in functions], ["balanceOf"])

    def test_error_and_function_paths_share_abi(self):
        # 同一份 ABI 可同时用于函数 calldata 与 error revert data。
        from abi_kit import encode_function_call

        call = encode_function_call(ABI_JSON, "balanceOf", [])
        encoded = encode_error_data(ABI_JSON, "Unauthorized")
        self.assertNotEqual(call.selector, encoded.selector)


if __name__ == "__main__":
    unittest.main()
