"""parse_constructor_abi / encode_constructor_data /
decode_constructor_data 的行为测试。

仅使用标准库 unittest，无第三方依赖。
"""

import json
import unittest
from dataclasses import FrozenInstanceError

from abi_kit import (
    AbiDeploymentDataError,
    AbiMetadataError,
    AbiTrailingDataError,
    ABIValueError,
    ConstructorArgument,
    ConstructorDefinition,
    DecodedDeploymentData,
    EncodedDeploymentData,
    decode_constructor_data,
    encode_constructor_data,
    parse_constructor_abi,
    parse_function_abi,
    parse_error_abi,
    parse_event_abi,
)

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


ADDR_A = "0x" + "11" * 20
BYTECODE = bytes.fromhex("6080604052" + "ab" * 16)


def ctor(inputs, **extra):
    entry = {"type": "constructor", "inputs": inputs}
    entry.update(extra)
    return entry


def func(name, inputs=None):
    return {"type": "function", "name": name, "inputs": inputs or [], "outputs": []}


def event(name, inputs=None):
    return {"type": "event", "name": name, "inputs": inputs or []}


def err(name, inputs):
    return {"type": "error", "name": name, "inputs": inputs}


ABI_ENTRIES = [
    func("balanceOf", [{"name": "owner", "type": "address"}]),
    event("Transfer", []),
    err("Unauthorized", []),
    ctor(
        [
            {"name": "owner", "type": "address"},
            {"name": "supply", "type": "uint256"},
        ],
        stateMutability="nonpayable",
        payable=False,
    ),
]
ABI_JSON = json.dumps(ABI_ENTRIES)

NO_CTOR_ABI = [
    func("balanceOf", []),
    event("Transfer", []),
]

NESTED_ABI = [
    ctor(
        [
            {"name": "members", "type": "address[]"},
            {
                "name": "config",
                "type": "tuple",
                "components": [
                    {"name": "flag", "type": "bool"},
                    {"name": "label", "type": "string"},
                    {"name": "fixed", "type": "uint128[2]"},
                ],
            },
            {"name": "blob", "type": "bytes"},
        ]
    )
]


class ParseConstructorAbiTest(unittest.TestCase):
    def test_parses_constructor_entry_and_skips_others(self):
        definition = parse_constructor_abi(ABI_JSON)
        self.assertIsInstance(definition, ConstructorDefinition)
        self.assertEqual(
            [parameter.name for parameter in definition.inputs],
            ["owner", "supply"],
        )
        self.assertEqual(definition.inputs[0].abi_type.kind, "address")

    def test_accepts_entry_array_and_json_string(self):
        self.assertEqual(
            parse_constructor_abi(ABI_ENTRIES),
            parse_constructor_abi(ABI_JSON),
        )

    def test_missing_constructor_means_zero_inputs(self):
        definition = parse_constructor_abi(NO_CTOR_ABI)
        self.assertIsInstance(definition, ConstructorDefinition)
        self.assertEqual(definition.inputs, ())
        # 空 ABI 同样得到零输入定义。
        self.assertEqual(parse_constructor_abi([]), ConstructorDefinition())

    def test_explicit_zero_argument_constructor(self):
        definition = parse_constructor_abi([ctor([], payable=True)])
        self.assertEqual(definition.inputs, ())

    def test_multiple_constructors_rejected(self):
        abi = [
            ctor([{"name": "a", "type": "uint256"}]),
            ctor([{"name": "b", "type": "address"}]),
        ]
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi(abi)
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi(json.dumps(abi))

    def test_result_is_immutable(self):
        definition = parse_constructor_abi(ABI_JSON)
        with self.assertRaises(FrozenInstanceError):
            definition.inputs = ()
        self.assertIsInstance(definition.inputs, tuple)

    def test_unnamed_parameter_defaults_to_empty_name(self):
        definition = parse_constructor_abi([ctor([{"type": "uint8"}])])
        self.assertEqual(definition.inputs[0].name, "")

    def test_tuple_components_expand_to_canonical_type(self):
        from abi_kit import format_abi_type

        definition = parse_constructor_abi(NESTED_ABI)
        self.assertEqual(format_abi_type(definition.inputs[0].abi_type), "address[]")
        self.assertEqual(
            format_abi_type(definition.inputs[1].abi_type),
            "(bool,string,uint128[2])",
        )
        self.assertEqual(format_abi_type(definition.inputs[2].abi_type), "bytes")

    def test_metadata_errors(self):
        bad_abis = [
            "{not json",
            json.dumps({"type": "constructor"}),
            42,
            [42],
            [{"name": "C", "inputs": []}],  # 缺 type
            [{"type": "unknown", "name": "C", "inputs": []}],
            [{"type": "constructor"}],  # 缺 inputs
            [{"type": "constructor", "inputs": "nope"}],
            [{"type": "constructor", "inputs": [42]}],
            [
                {
                    "type": "constructor",
                    "inputs": [{"name": 1, "type": "uint8"}],
                }
            ],
            [
                {
                    "type": "constructor",
                    "inputs": [{"name": "x", "type": "uint7"}],
                }
            ],
        ]
        for bad in bad_abis:
            with self.subTest(abi=bad):
                with self.assertRaises(AbiMetadataError):
                    parse_constructor_abi(bad)


class EncodeConstructorDataTest(unittest.TestCase):
    def test_encode_appends_encoded_tuple_to_bytecode(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        self.assertIsInstance(encoded, EncodedDeploymentData)
        self.assertIsInstance(encoded.constructor, ConstructorDefinition)
        self.assertEqual(encoded.args, (ADDR_A, 7))
        expected_payload = (
            b"\x00" * 12 + bytes.fromhex(ADDR_A[2:]) + word(7)
        )
        self.assertEqual(encoded.data, BYTECODE + expected_payload)
        self.assertEqual(encoded.data_hex, "0x" + encoded.data.hex())

    def test_encode_accepts_hex_bytecode_with_and_without_prefix(self):
        from_bytes = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        from_prefixed = encode_constructor_data(
            ABI_JSON, "0x" + BYTECODE.hex(), [ADDR_A, 7]
        )
        from_plain = encode_constructor_data(
            ABI_JSON, BYTECODE.hex(), [ADDR_A, 7]
        )
        self.assertEqual(from_bytes.data, from_prefixed.data)
        self.assertEqual(from_bytes.data, from_plain.data)

    def test_encode_zero_arguments_data_equals_bytecode(self):
        # ABI 中没有 constructor 条目：零输入。
        encoded = encode_constructor_data(NO_CTOR_ABI, BYTECODE)
        self.assertEqual(encoded.args, ())
        self.assertEqual(encoded.data, BYTECODE)
        # 显式零参 constructor 与空 args 同样如此。
        explicit = encode_constructor_data([ctor([])], BYTECODE, [])
        self.assertEqual(explicit.data, BYTECODE)

    def test_encode_accepts_tuple_args(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, (ADDR_A, 7))
        self.assertEqual(
            encoded.data[len(BYTECODE):],
            b"\x00" * 12 + bytes.fromhex(ADDR_A[2:]) + word(7),
        )

    def test_encode_nested_dynamic_and_arrays(self):
        members = [ADDR_A, "0x" + "22" * 20]
        config = (True, "hello", [3, 4])
        blob = b"\xde\xad\xbe\xef"
        encoded = encode_constructor_data(
            NESTED_ABI, BYTECODE, [members, config, blob]
        )
        decoded = decode_constructor_data(NESTED_ABI, BYTECODE, encoded.data)
        self.assertEqual(decoded.values, (members, config, blob))

    def test_result_is_immutable(self):
        encoded = encode_constructor_data(NO_CTOR_ABI, BYTECODE)
        with self.assertRaises(FrozenInstanceError):
            encoded.data = b""

    def test_rejects_bad_args(self):
        with self.assertRaises(ABIValueError):
            encode_constructor_data(ABI_JSON, BYTECODE, (ADDR_A,))  # 数量少
        with self.assertRaises(ABIValueError):
            encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7, 1])  # 多
        with self.assertRaises(ABIValueError):
            encode_constructor_data(ABI_JSON, BYTECODE, [7, ADDR_A])  # 类型不符
        with self.assertRaises(ABIValueError):
            encode_constructor_data(ABI_JSON, BYTECODE, {ADDR_A, 7})  # 非序列
        with self.assertRaises(ABIValueError):
            # 零输入 constructor 不接受任何实参。
            encode_constructor_data(NO_CTOR_ABI, BYTECODE, [1])

    def test_rejects_bad_bytecode(self):
        for bad in (42, None, [1, 2], "0x123", "0xzz", "abc"):
            with self.subTest(bytecode=bad):
                with self.assertRaises(AbiDeploymentDataError):
                    encode_constructor_data(NO_CTOR_ABI, bad)

    def test_empty_bytecode_is_allowed(self):
        encoded = encode_constructor_data(NO_CTOR_ABI, b"")
        self.assertEqual(encoded.data, b"")


class DecodeConstructorDataTest(unittest.TestCase):
    def test_roundtrip(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        decoded = decode_constructor_data(ABI_JSON, BYTECODE, encoded.data)
        self.assertIsInstance(decoded, DecodedDeploymentData)
        self.assertEqual(decoded.data, encoded.data)
        self.assertEqual(decoded.data_hex, encoded.data_hex)
        self.assertEqual(decoded.values, (ADDR_A, 7))
        self.assertEqual(len(decoded.args), 2)
        self.assertEqual(
            decoded.args[0],
            ConstructorArgument("owner", "address", ADDR_A),
        )
        self.assertEqual(
            decoded.args[1],
            ConstructorArgument("supply", "uint256", 7),
        )

    def test_decode_accepts_hex_inputs(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        decoded = decode_constructor_data(
            ABI_JSON, "0x" + BYTECODE.hex(), encoded.data_hex
        )
        self.assertEqual(decoded.values, (ADDR_A, 7))
        # 无 0x 前缀同样接受。
        decoded_plain = decode_constructor_data(
            ABI_JSON, BYTECODE.hex(), encoded.data_hex[2:]
        )
        self.assertEqual(decoded_plain.values, (ADDR_A, 7))

    def test_decode_zero_arguments_requires_exact_bytecode(self):
        encoded = encode_constructor_data(NO_CTOR_ABI, BYTECODE)
        decoded = decode_constructor_data(NO_CTOR_ABI, BYTECODE, encoded.data)
        self.assertEqual(decoded.args, ())
        self.assertEqual(decoded.values, ())
        self.assertEqual(decoded.data, BYTECODE)

    def test_result_is_immutable(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        decoded = decode_constructor_data(ABI_JSON, BYTECODE, encoded.data)
        with self.assertRaises(FrozenInstanceError):
            decoded.args = ()

    def test_rejects_data_shorter_than_bytecode(self):
        with self.assertRaises(AbiDeploymentDataError):
            decode_constructor_data(NO_CTOR_ABI, BYTECODE, BYTECODE[:-1])
        with self.assertRaises(AbiDeploymentDataError):
            decode_constructor_data(
                NO_CTOR_ABI, "0x" + BYTECODE.hex(), "0x" + BYTECODE.hex()[:-2]
            )

    def test_rejects_prefix_mismatch(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        other_bytecode = b"\xff" * len(BYTECODE)
        with self.assertRaises(AbiDeploymentDataError):
            decode_constructor_data(ABI_JSON, other_bytecode, encoded.data)
        # 等长但首字节不同。
        flipped = bytes([BYTECODE[0] ^ 0xFF]) + BYTECODE[1:]
        with self.assertRaises(AbiDeploymentDataError):
            decode_constructor_data(NO_CTOR_ABI, flipped, BYTECODE)

    def test_rejects_bad_data_type_or_hex(self):
        for bad in (42, None, [1], "0x123", "0xzz", "xyz"):
            with self.subTest(data=bad):
                with self.assertRaises(AbiDeploymentDataError):
                    decode_constructor_data(NO_CTOR_ABI, BYTECODE, bad)
        for bad in (42, None, [1], "0x123", "0xzz"):
            with self.subTest(bytecode=bad):
                with self.assertRaises(AbiDeploymentDataError):
                    decode_constructor_data(NO_CTOR_ABI, bad, BYTECODE)

    def test_rejects_truncated_payload(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        with self.assertRaises(ABIValueError):
            decode_constructor_data(
                ABI_JSON, BYTECODE, encoded.data[:-W]
            )

    def test_rejects_trailing_bytes(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        with self.assertRaises(AbiTrailingDataError):
            decode_constructor_data(
                ABI_JSON, BYTECODE, encoded.data + word(0)
            )
        # 零参数 constructor 也不允许多余字节。
        with self.assertRaises(AbiTrailingDataError):
            decode_constructor_data(
                NO_CTOR_ABI, BYTECODE, BYTECODE + word(0)
            )

    def test_rejects_wrong_value_layout(self):
        encoded = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 7])
        payload = encoded.data[len(BYTECODE):]
        # 把 address 字改成高 12 字节非零的值，严格解码应报高位填充非法。
        broken = (
            encoded.data[: len(BYTECODE)]
            + word(1 << 160)
            + payload[W:]
        )
        with self.assertRaises(ABIValueError):
            decode_constructor_data(ABI_JSON, BYTECODE, broken)

        # 动态参数的非法偏移/长度同样按 ABIValueError 拒绝。
        dynamic = encode_constructor_data(
            NESTED_ABI,
            BYTECODE,
            [["0x" + "22" * 20], (True, "x", [1, 2]), b"k"],
        )
        head = len(BYTECODE)
        tampered = bytearray(dynamic.data)
        # 首个成员 address[] 的 head 槽存放 tail 偏移，篡改成越界值。
        tampered[head:head + W] = word(10_000)
        with self.assertRaises(ABIValueError):
            decode_constructor_data(NESTED_ABI, BYTECODE, bytes(tampered))

    def test_static_dynamic_nested_roundtrip_is_stable(self):
        members = ["0x" + f"{i:040x}" for i in range(1, 4)]
        config = (False, "标签", [2**127 - 1, 0])
        blob = b"\x00" * 33
        encoded = encode_constructor_data(
            NESTED_ABI, BYTECODE, [members, config, blob]
        )
        decoded = decode_constructor_data(NESTED_ABI, BYTECODE, encoded.data)
        self.assertEqual(
            [arg.name for arg in decoded.args], ["members", "config", "blob"]
        )
        self.assertEqual(
            [arg.type for arg in decoded.args],
            ["address[]", "(bool,string,uint128[2])", "bytes"],
        )
        self.assertEqual(decoded.values, (members, config, blob))
        #重编码得到完全相同的部署数据。
        re_encoded = encode_constructor_data(
            NESTED_ABI, BYTECODE, decoded.values
        )
        self.assertEqual(re_encoded.data, encoded.data)


class InteractionTest(unittest.TestCase):
    def test_other_paths_still_skip_constructor_entry(self):
        self.assertEqual(
            [function.name for function in parse_function_abi(ABI_JSON)],
            ["balanceOf"],
        )
        self.assertEqual(
            [error.name for error in parse_error_abi(ABI_JSON)],
            ["Unauthorized"],
        )
        events = parse_event_abi(
            next(entry for entry in ABI_ENTRIES if entry["type"] == "event")
        )
        self.assertEqual(events.name, "Transfer")

    def test_deployment_path_shares_abi_with_call_path(self):
        deployed = encode_constructor_data(ABI_JSON, BYTECODE, [ADDR_A, 1])
        call = encode_constructor_data  # 仅引用确认入口共存
        self.assertTrue(deployed.data.startswith(BYTECODE))
        self.assertIs(call, encode_constructor_data)


if __name__ == "__main__":
    unittest.main()
