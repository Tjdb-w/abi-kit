"""parse_constructor_abi / encode_constructor_data / decode_constructor_data 测试。

仅使用标准库 unittest，无第三方依赖。覆盖零输入与多参数 constructor、
静态/动态/嵌套参数的部署数据往返、bytecode 与 deployment_data 的
类型/十六进制/长度/前缀校验，以及元数据、值层与尾随数据错误约定。
"""

import json
import unittest

from abi_kit import (
    AbiDeploymentDataError,
    AbiMetadataError,
    AbiTrailingDataError,
    AbiValueError,
    ABIValueError,
    ConstructorArgument,
    ConstructorDefinition,
    ConstructorParameter,
    DecodedDeploymentData,
    EncodedDeploymentData,
    decode_constructor_data,
    encode_constructor_data,
    parse_constructor_abi,
)

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


ADDR_A = "0x" + "11" * 20
ADDR_B = "0x" + "22" * 20

BYTECODE = bytes.fromhex("6080604052348015600e575f5ffd5b50")
BYTECODE_HEX = "0x" + BYTECODE.hex()


def param(type_, name="", **extra):
    node = {"type": type_, "name": name}
    node.update(extra)
    return node


def constructor(inputs, **extra):
    entry = {"type": "constructor", "inputs": inputs}
    entry.update(extra)
    return entry


def fn(name, inputs):
    return {"type": "function", "name": name, "inputs": inputs}


SIMPLE_ABI = [
    constructor([
        param("address", "owner"),
        param("uint256", "supply"),
        param("string", "greeting"),
    ]),
    fn("transfer", [param("address", "to"), param("uint256", "amount")]),
    {"type": "event", "name": "Transfer", "inputs": []},
]

NESTED_ABI = [
    constructor([
        param("bool", "flag"),
        param("uint256[]", "scores"),
        param("bytes", "blob"),
        {
            "type": "tuple",
            "name": "config",
            "components": [
                param("address", "admin"),
                param("string", "label"),
                {
                    "type": "tuple",
                    "name": "limits",
                    "components": [
                        param("uint8", "low"),
                        param("uint8[2]", "high"),
                    ],
                },
            ],
        },
        param("string[2]", "tags"),
    ]),
]

SIMPLE_VALUES = (ADDR_A, 1000, "hello")
NESTED_VALUES = (
    True,
    [1, 2, 3],
    b"\xde\xad",
    (ADDR_B, "cfg", (7, [8, 9])),
    ["aa", "bb"],
)


class ParseConstructorAbiTests(unittest.TestCase):
    def test_no_constructor_means_zero_inputs(self):
        abi = [fn("transfer", [])]
        definition = parse_constructor_abi(abi)
        self.assertIsInstance(definition, ConstructorDefinition)
        self.assertEqual(definition.inputs, ())

    def test_empty_abi_means_zero_inputs(self):
        for abi in ("[]", [], ()):
            self.assertEqual(parse_constructor_abi(abi).inputs, ())

    def test_parses_inputs_in_declaration_order(self):
        definition = parse_constructor_abi(json.dumps(SIMPLE_ABI))
        self.assertEqual(
            [p.name for p in definition.inputs], ["owner", "supply", "greeting"]
        )
        self.assertIsInstance(definition.inputs[0], ConstructorParameter)

    def test_accepts_entry_list_and_json_string(self):
        from_list = parse_constructor_abi(SIMPLE_ABI)
        from_json = parse_constructor_abi(json.dumps(SIMPLE_ABI))
        self.assertEqual(from_list, from_json)

    def test_skips_other_entries(self):
        abi = [
            fn("f", []),
            {"type": "event", "name": "E", "inputs": []},
            {"type": "error", "name": "Err", "inputs": []},
            {"type": "receive"},
            {"type": "fallback"},
            constructor([param("uint256", "x")]),
        ]
        definition = parse_constructor_abi(abi)
        self.assertEqual(len(definition.inputs), 1)

    def test_multiple_constructors_raise_metadata_error(self):
        abi = [constructor([]), constructor([param("uint256")])]
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi(abi)

    def test_invalid_root_raises_metadata_error(self):
        for abi in (123, None, {"type": "constructor"}, "not json", "{}"):
            with self.assertRaises(AbiMetadataError, msg=repr(abi)):
                parse_constructor_abi(abi)

    def test_non_object_entry_raises_metadata_error(self):
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi(["constructor"])

    def test_constructor_missing_inputs_raises_metadata_error(self):
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi([{"type": "constructor"}])

    def test_constructor_inputs_not_array_raises_metadata_error(self):
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi([constructor({})])

    def test_bad_param_type_raises_metadata_error(self):
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi([constructor([param("uint7", "x")])])

    def test_definition_is_immutable(self):
        definition = parse_constructor_abi(SIMPLE_ABI)
        with self.assertRaises(Exception):
            definition.inputs = ()


class EncodeConstructorDataTests(unittest.TestCase):
    def test_zero_args_data_is_bytecode(self):
        result = encode_constructor_data([], BYTECODE)
        self.assertIsInstance(result, EncodedDeploymentData)
        self.assertEqual(result.data, BYTECODE)
        self.assertEqual(result.args, ())
        self.assertEqual(result.data_hex, BYTECODE_HEX)

    def test_bytecode_accepts_hex_with_and_without_prefix(self):
        for bytecode in (BYTECODE, BYTECODE_HEX, BYTECODE.hex(), "0X" + BYTECODE.hex()):
            result = encode_constructor_data([], bytecode)
            self.assertEqual(result.data, BYTECODE, msg=repr(bytecode))

    def test_encodes_args_after_bytecode(self):
        result = encode_constructor_data(SIMPLE_ABI, BYTECODE, SIMPLE_VALUES)
        expected_tail = (
            bytes(12) + bytes.fromhex("11" * 20)
            + word(1000)
            + word(96)
            + word(5) + b"hello" + bytes(27)
        )
        self.assertEqual(result.data, BYTECODE + expected_tail)
        self.assertEqual(result.data_hex, "0x" + result.data.hex())

    def test_args_keep_names_and_canonical_types(self):
        result = encode_constructor_data(SIMPLE_ABI, BYTECODE, SIMPLE_VALUES)
        self.assertEqual(
            [(a.name, a.type, a.value) for a in result.args],
            [
                ("owner", "address", ADDR_A),
                ("supply", "uint256", 1000),
                ("greeting", "string", "hello"),
            ],
        )
        self.assertEqual(result.values, SIMPLE_VALUES)

    def test_constructor_definition_exposed(self):
        result = encode_constructor_data(SIMPLE_ABI, BYTECODE, SIMPLE_VALUES)
        self.assertEqual(result.constructor, parse_constructor_abi(SIMPLE_ABI))

    def test_nested_dynamic_round_trip_encoding(self):
        result = encode_constructor_data(NESTED_ABI, BYTECODE, NESTED_VALUES)
        self.assertTrue(result.data.startswith(BYTECODE))
        self.assertEqual(result.values, NESTED_VALUES)

    def test_abi_may_be_json_string(self):
        result = encode_constructor_data(
            json.dumps(SIMPLE_ABI), BYTECODE_HEX, list(SIMPLE_VALUES)
        )
        self.assertEqual(result.values, SIMPLE_VALUES)

    def test_bad_bytecode_type_raises_deployment_error(self):
        for bytecode in (123, None, ["00"]):
            with self.assertRaises(AbiDeploymentDataError, msg=repr(bytecode)):
                encode_constructor_data([], bytecode)

    def test_bad_bytecode_hex_raises_deployment_error(self):
        for bytecode in ("0x0", "0xzz", "abc", "0x 12"):
            with self.assertRaises(AbiDeploymentDataError, msg=repr(bytecode)):
                encode_constructor_data([], bytecode)

    def test_args_must_be_list_or_tuple(self):
        with self.assertRaises(ABIValueError):
            encode_constructor_data(SIMPLE_ABI, BYTECODE, "oops")

    def test_arg_count_mismatch_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            encode_constructor_data(SIMPLE_ABI, BYTECODE, (ADDR_A, 1000))
        with self.assertRaises(AbiValueError):
            encode_constructor_data([], BYTECODE, (1,))

    def test_arg_type_mismatch_raises_value_error(self):
        with self.assertRaises(ABIValueError):
            encode_constructor_data(SIMPLE_ABI, BYTECODE, (ADDR_A, "x", "hello"))

    def test_metadata_error_propagates(self):
        with self.assertRaises(AbiMetadataError):
            encode_constructor_data([constructor([]), constructor([])], BYTECODE)


class DecodeConstructorDataTests(unittest.TestCase):
    def test_zero_args_round_trip(self):
        encoded = encode_constructor_data([], BYTECODE)
        decoded = decode_constructor_data([], BYTECODE, encoded.data)
        self.assertIsInstance(decoded, DecodedDeploymentData)
        self.assertEqual(decoded.args, ())
        self.assertEqual(decoded.data, BYTECODE)
        self.assertEqual(decoded.data_hex, BYTECODE_HEX)

    def test_simple_round_trip(self):
        encoded = encode_constructor_data(SIMPLE_ABI, BYTECODE, SIMPLE_VALUES)
        decoded = decode_constructor_data(SIMPLE_ABI, BYTECODE, encoded.data)
        self.assertEqual(decoded.values, SIMPLE_VALUES)
        self.assertEqual(decoded.args, encoded.args)
        self.assertEqual(decoded.constructor, encoded.constructor)
        self.assertEqual(decoded.data, encoded.data)

    def test_nested_round_trip(self):
        encoded = encode_constructor_data(NESTED_ABI, BYTECODE, NESTED_VALUES)
        decoded = decode_constructor_data(NESTED_ABI, BYTECODE, encoded.data)
        self.assertEqual(decoded.values, NESTED_VALUES)
        self.assertEqual(
            [a.name for a in decoded.args],
            ["flag", "scores", "blob", "config", "tags"],
        )
        self.assertEqual(
            [a.type for a in decoded.args],
            ["bool", "uint256[]", "bytes", "(address,string,(uint8,uint8[2]))", "string[2]"],
        )
        for arg in decoded.args:
            self.assertIsInstance(arg, ConstructorArgument)

    def test_accepts_hex_strings(self):
        encoded = encode_constructor_data(SIMPLE_ABI, BYTECODE, SIMPLE_VALUES)
        decoded = decode_constructor_data(
            json.dumps(SIMPLE_ABI), BYTECODE_HEX, encoded.data_hex
        )
        self.assertEqual(decoded.values, SIMPLE_VALUES)

    def test_deployment_data_shorter_than_bytecode_raises(self):
        with self.assertRaises(AbiDeploymentDataError):
            decode_constructor_data([], BYTECODE, BYTECODE[:-1])

    def test_prefix_mismatch_raises(self):
        bad = b"\xff" + BYTECODE[1:]
        with self.assertRaises(AbiDeploymentDataError):
            decode_constructor_data([], BYTECODE, bad)

    def test_bad_deployment_data_type_or_hex_raises(self):
        for data in (123, None, "0x0", "0xzz"):
            with self.assertRaises(AbiDeploymentDataError, msg=repr(data)):
                decode_constructor_data([], BYTECODE, data)

    def test_bad_bytecode_type_or_hex_raises(self):
        for bytecode in (123, "0x0", "0xzz"):
            with self.assertRaises(AbiDeploymentDataError, msg=repr(bytecode)):
                decode_constructor_data([], bytecode, BYTECODE)

    def test_trailing_bytes_raise(self):
        encoded = encode_constructor_data(SIMPLE_ABI, BYTECODE, SIMPLE_VALUES)
        with self.assertRaises(AbiTrailingDataError):
            decode_constructor_data(
                SIMPLE_ABI, BYTECODE, encoded.data + bytes(32)
            )

    def test_zero_args_with_trailing_bytes_raise(self):
        with self.assertRaises(AbiTrailingDataError):
            decode_constructor_data([], BYTECODE, BYTECODE + bytes(32))

    def test_truncated_args_raise_value_error(self):
        encoded = encode_constructor_data(SIMPLE_ABI, BYTECODE, SIMPLE_VALUES)
        with self.assertRaises(ABIValueError):
            decode_constructor_data(
                SIMPLE_ABI, BYTECODE, encoded.data[:-32]
            )

    def test_metadata_error_propagates(self):
        with self.assertRaises(AbiMetadataError):
            decode_constructor_data("not json", BYTECODE, BYTECODE)


if __name__ == "__main__":
    unittest.main()
