"""Solidity 常用类型别名（uint/int/fixed/ufixed/byte）统一支持的测试。

别名只在解析层规范化为显式规范类型：

- uint    -> uint256
- int     -> int256
- fixed   -> fixed128x18
- ufixed  -> ufixed128x18
- byte    -> bytes1

别名可出现在基础位置、任意深度元组与数组元素中，也可出现在 ABI JSON
参数的 type 与 components 递归声明中；format_abi_type 一律输出展开后的
规范形式。本文件同时锁定：既有显式形式不变、近似拼写继续报错、各元数据
入口沿用各自唯一异常类型/错误码，以及 selector、topic0、路径类型与严格
编解码布局全部以展开后的规范类型计算。
"""

import json
import unittest
from decimal import Decimal

from abi_kit import (
    ABITypeError,
    AbiContractAbiError,
    AbiContractCallError,
    AbiEventError,
    AbiLogDispatchError,
    AbiMetadataError,
    decode_constructor_data,
    decode_contract_call,
    decode_contract_event_log,
    decode_error_data,
    decode_event_log,
    decode_function_call,
    decode_function_result,
    encode_constructor_data,
    encode_contract_call,
    encode_error_data,
    encode_event_log,
    encode_function_call,
    encode_function_result,
    error_selector,
    event_topic0,
    format_abi_type,
    function_selector,
    match_contract_event_log_values,
    match_event_log_values,
    parse_abi_type,
    parse_constructor_abi,
    parse_contract_abi,
    parse_contract_call_registry,
    parse_error_abi,
    parse_event_abi,
    parse_event_registry,
    parse_function_abi,
    canonical_error_signature,
    canonical_function_signature,
)
from abi_kit._keccak import keccak_256

_ALIAS_TO_CANONICAL = {
    "uint": "uint256",
    "int": "int256",
    "fixed": "fixed128x18",
    "ufixed": "ufixed128x18",
    "byte": "bytes1",
}

#: 既非别名也非既有合法形式，必须继续抛 ABITypeError。
_INVALID_NEAR_MISSES = [
    "uint0",
    "byte2",
    "bytes0",
    "fixed128",
    "fixed128x",
    "fixedx18",
    "ufixed0x18",
    "fixed0x18",
    "ufixed128x0",
]


class AliasParsingTests(unittest.TestCase):
    def test_bare_aliases_expand(self):
        for alias, canonical in _ALIAS_TO_CANONICAL.items():
            with self.subTest(alias=alias):
                parsed = parse_abi_type(alias)
                self.assertEqual(format_abi_type(parsed), canonical)
                # 别名对象与显式规范形式完全等价，且不保留别名拼写。
                self.assertEqual(parsed, parse_abi_type(canonical))
                self.assertEqual(
                    format_abi_type(parsed),
                    format_abi_type(parse_abi_type(canonical)),
                )

    def test_alias_in_arrays_and_tuples(self):
        cases = {
            "uint[]": "uint256[]",
            "int[2]": "int256[2]",
            "byte[3]": "bytes1[3]",
            "fixed[][]": "fixed128x18[][]",
            "(uint,int)": "(uint256,int256)",
            "((byte,ufixed),fixed)": (
                "((bytes1,ufixed128x18),fixed128x18)"
            ),
            "(uint[],(byte,int)[2])": "(uint256[],(bytes1,int256)[2])",
        }
        for raw, canonical in cases.items():
            with self.subTest(raw=raw):
                parsed = parse_abi_type(raw)
                self.assertEqual(format_abi_type(parsed), canonical)
                self.assertEqual(parsed, parse_abi_type(canonical))

    def test_near_miss_spellings_still_raise(self):
        for raw in _INVALID_NEAR_MISSES:
            with self.subTest(raw=raw):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(raw)

    def test_near_miss_nested_still_raises(self):
        for raw in ("(uint0)", "(uint,byte2)[]", "fixed128[1]", "ufixed0x18[]"):
            with self.subTest(raw=raw):
                with self.assertRaises(ABITypeError):
                    parse_abi_type(raw)

    def test_explicit_forms_unchanged(self):
        for raw in (
            "uint8",
            "uint256",
            "int256",
            "bytes1",
            "bytes32",
            "bytes",
            "fixed128x18",
            "ufixed128x18",
            "ufixed8x1",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(format_abi_type(parse_abi_type(raw)), raw)

    def test_whitespace_rules_unchanged(self):
        # 两端空白依旧允许；展开后仍是规范形式。
        self.assertEqual(
            format_abi_type(parse_abi_type("  uint\t")), "uint256"
        )
        self.assertEqual(
            format_abi_type(parse_abi_type("(uint, int)")),
            "(uint256,int256)",
        )
        # 类型词内部空白依旧非法（既不是别名也不是合法形式）。
        with self.assertRaises(ABITypeError):
            parse_abi_type("uin t")


class FunctionAliasTests(unittest.TestCase):
    def _alias_abi(self):
        return [
            {
                "type": "function",
                "name": "f",
                "inputs": [
                    {"name": "a", "type": "uint"},
                    {"name": "b", "type": "int"},
                    {"name": "c", "type": "byte"},
                    {"name": "d", "type": "fixed"},
                    {"name": "e", "type": "ufixed"},
                    {
                        "name": "t",
                        "type": "tuple",
                        "components": [
                            {"name": "x", "type": "uint[]"},
                            {"name": "y", "type": "byte[2]"},
                        ],
                    },
                ],
                "outputs": [{"name": "r", "type": "uint"}],
            }
        ]

    def _explicit_abi(self):
        return json.loads(
            json.dumps(self._alias_abi())
            .replace('"uint"', '"uint256"')
            .replace('"int"', '"int256"')
            .replace('"byte[2]"', '"bytes1[2]"')
            .replace('"byte"', '"bytes1"')
            .replace('"fixed"', '"fixed128x18"')
            .replace('"ufixed"', '"ufixed128x18"')
        )

    def test_canonical_signature_and_selector(self):
        alias = parse_function_abi(self._alias_abi())[0]
        explicit = parse_function_abi(self._explicit_abi())[0]
        expected = (
            "f(uint256,int256,bytes1,fixed128x18,ufixed128x18,"
            "(uint256[],bytes1[2]))"
        )
        self.assertEqual(canonical_function_signature(alias), expected)
        self.assertEqual(
            canonical_function_signature(alias),
            canonical_function_signature(explicit),
        )
        self.assertEqual(
            function_selector(alias), function_selector(explicit)
        )

    def test_calldata_roundtrip_and_canonical_arg_types(self):
        abi = self._alias_abi()
        args = [
            2 ** 256 - 1,
            -5,
            b"z",
            Decimal("1.5"),
            Decimal("2.25"),
            ([1, 2], [b"a", b"b"]),
        ]
        encoded = encode_function_call(abi, "f", args)
        decoded = decode_function_call(abi, encoded.calldata)
        self.assertEqual(decoded.values, tuple(args))
        self.assertEqual(
            [arg.type for arg in decoded.args],
            [
                "uint256",
                "int256",
                "bytes1",
                "fixed128x18",
                "ufixed128x18",
                "(uint256[],bytes1[2])",
            ],
        )
        # 显式宽度声明解码同一份 calldata，结果一致。
        decoded_explicit = decode_function_call(
            self._explicit_abi(), encoded.calldata
        )
        self.assertEqual(decoded_explicit.values, tuple(args))

    def test_result_roundtrip(self):
        abi = self._alias_abi()
        encoded = encode_function_result(abi, "f", [7])
        decoded = decode_function_result(abi, "f", encoded.data)
        self.assertEqual(decoded.values, (7,))
        self.assertEqual(decoded.outputs[0].type, "uint256")

    def test_bad_alias_raises_metadata_error(self):
        for bad in _INVALID_NEAR_MISSES:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiMetadataError):
                    parse_function_abi(
                        [
                            {
                                "type": "function",
                                "name": "g",
                                "inputs": [{"type": bad}],
                            }
                        ]
                    )


class ErrorAliasTests(unittest.TestCase):
    def test_signature_selector_and_data(self):
        alias_abi = [
            {
                "type": "error",
                "name": "E",
                "inputs": [
                    {"name": "", "type": "uint"},
                    {"name": "", "type": "fixed"},
                ],
            }
        ]
        explicit_abi = [
            {
                "type": "error",
                "name": "E",
                "inputs": [
                    {"name": "", "type": "uint256"},
                    {"name": "", "type": "fixed128x18"},
                ],
            }
        ]
        alias = parse_error_abi(alias_abi)[0]
        explicit = parse_error_abi(explicit_abi)[0]
        self.assertEqual(
            canonical_error_signature(alias), "E(uint256,fixed128x18)"
        )
        self.assertEqual(
            canonical_error_signature(alias),
            canonical_error_signature(explicit),
        )
        self.assertEqual(error_selector(alias), error_selector(explicit))

        encoded = encode_error_data(alias_abi, "E", [9, Decimal("0.5")])
        decoded = decode_error_data(alias_abi, encoded.data)
        self.assertEqual(decoded.values, (9, Decimal("0.5")))
        self.assertEqual(
            [arg.type for arg in decoded.args], ["uint256", "fixed128x18"]
        )

    def test_bad_alias_raises_metadata_error(self):
        with self.assertRaises(AbiMetadataError):
            parse_error_abi(
                [
                    {
                        "type": "error",
                        "name": "X",
                        "inputs": [{"type": "uint0"}],
                    }
                ]
            )


class ConstructorAliasTests(unittest.TestCase):
    def test_deployment_data_roundtrip(self):
        alias_abi = [
            {
                "type": "constructor",
                "inputs": [
                    {"name": "", "type": "uint"},
                    {"name": "", "type": "byte"},
                ],
            }
        ]
        bytecode = "0x608060"
        encoded = encode_constructor_data(
            alias_abi, bytecode, [42, b"\xab"]
        )
        decoded = decode_constructor_data(
            alias_abi, bytecode, encoded.data
        )
        self.assertEqual(decoded.values, (42, b"\xab"))
        self.assertEqual(
            [arg.type for arg in decoded.args], ["uint256", "bytes1"]
        )

    def test_bad_alias_raises_metadata_error(self):
        with self.assertRaises(AbiMetadataError):
            parse_constructor_abi(
                [
                    {
                        "type": "constructor",
                        "inputs": [{"type": "fixed128"}],
                    }
                ]
            )


class ContractCallAliasTests(unittest.TestCase):
    def test_dispatch_uses_canonical_signature(self):
        abi = [
            {
                "type": "function",
                "name": "transfer",
                "inputs": [
                    {"name": "", "type": "address"},
                    {"name": "", "type": "uint"},
                ],
                "outputs": [],
            },
            {"type": "receive"},
            {"type": "fallback"},
        ]
        calldata = encode_contract_call(
            abi, "transfer", ["0x" + "11" * 20, 5]
        )
        decoded = decode_contract_call(abi, calldata)
        self.assertEqual(decoded.kind, "function")
        self.assertEqual(
            decoded.signature, "transfer(address,uint256)"
        )
        self.assertEqual(decoded.values, ("0x" + "11" * 20, 5))
        self.assertEqual(decoded.args[1].type, "uint256")

    def test_bad_alias_error_code(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            parse_contract_call_registry(
                [
                    {
                        "type": "function",
                        "name": "z",
                        "inputs": [{"type": "byte2"}],
                    }
                ]
            )
        self.assertEqual(
            ctx.exception.code, "CONTRACT_CALL_ABI_INVALID"
        )


class ContractAbiListingAliasTests(unittest.TestCase):
    def test_listing_expands_all_entry_kinds(self):
        abi = [
            {
                "type": "function",
                "name": "f",
                "inputs": [{"name": "", "type": "uint"}],
                "outputs": [],
            },
            {
                "type": "event",
                "name": "Ev",
                "inputs": [
                    {"name": "", "type": "int", "indexed": True},
                    {"name": "", "type": "string", "indexed": False},
                ],
            },
            {
                "type": "error",
                "name": "Err",
                "inputs": [{"name": "", "type": "fixed"}],
            },
            {
                "type": "constructor",
                "inputs": [{"name": "", "type": "ufixed"}],
            },
        ]
        listing = parse_contract_abi(abi)
        self.assertEqual(
            listing.function_signatures, ("f(uint256)",)
        )
        self.assertEqual(listing.event_signatures, ("Ev(int256,string)",))
        self.assertEqual(
            listing.error_signatures, ("Err(fixed128x18)",)
        )
        self.assertIsNotNone(listing.constructor)
        self.assertEqual(
            format_abi_type(listing.constructor.inputs[0].abi_type),
            "ufixed128x18",
        )
        self.assertEqual(
            listing.function_selectors,
            ("0x" + function_selector(listing.functions[0]).hex(),),
        )
        self.assertEqual(
            listing.event_topic0s, (event_topic0(listing.events[0]),)
        )

    def test_bad_alias_error_code(self):
        with self.assertRaises(AbiContractAbiError) as ctx:
            parse_contract_abi(
                [
                    {
                        "type": "function",
                        "name": "f",
                        "inputs": [{"type": "uint0"}],
                        "outputs": [],
                    }
                ]
            )
        self.assertEqual(ctx.exception.code, "ABI_ENTRY_INVALID")


class EventAliasTests(unittest.TestCase):
    def _event(self, type_overrides=None):
        inputs = [
            {"name": "a", "type": "uint", "indexed": True},
            {"name": "b", "type": "int", "indexed": False},
            {"name": "c", "type": "byte", "indexed": True},
            {"name": "d", "type": "fixed", "indexed": False},
            {"name": "e", "type": "ufixed", "indexed": True},
        ]
        return {"type": "event", "name": "Log", "inputs": inputs}

    def test_signature_and_topic0_canonical(self):
        alias = parse_event_abi(self._event())
        explicit = parse_event_abi(
            {
                "type": "event",
                "name": "Log",
                "inputs": [
                    {"name": "a", "type": "uint256", "indexed": True},
                    {"name": "b", "type": "int256", "indexed": False},
                    {"name": "c", "type": "bytes1", "indexed": True},
                    {"name": "d", "type": "fixed128x18", "indexed": False},
                    {"name": "e", "type": "ufixed128x18", "indexed": True},
                ],
            }
        )
        self.assertEqual(alias, explicit)
        signature = (
            "Log(uint256,int256,bytes1,fixed128x18,ufixed128x18)"
        )
        expected_topic0 = "0x" + keccak_256(signature.encode()).hex()
        self.assertEqual(event_topic0(alias), expected_topic0)
        self.assertEqual(event_topic0(alias), event_topic0(explicit))

    def test_topics_data_layout_and_candidate_match(self):
        event = parse_event_abi(self._event())
        values = [123, -4, b"\x09", Decimal("3.14"), Decimal("2.5")]
        encoded = encode_event_log(event, values)
        self.assertEqual(
            decode_event_log(event, encoded.topics, encoded.data),
            tuple(values),
        )
        self.assertEqual(
            match_event_log_values(
                event, encoded.topics, encoded.data, values
            ),
            tuple(values),
        )
        # 任一候选不符返回 None，而不是抛错。
        self.assertIsNone(
            match_event_log_values(
                event,
                encoded.topics,
                encoded.data,
                [123, -4, b"\x09", Decimal("3.14"), Decimal("9.9")],
            )
        )

    def test_anonymous_event(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "An",
                "anonymous": True,
                "inputs": [
                    {"name": "", "type": "uint", "indexed": True}
                ],
            }
        )
        self.assertIsNone(event_topic0(event))
        encoded = encode_event_log(event, [7])
        self.assertEqual(len(encoded.topics), 1)
        self.assertEqual(
            decode_event_log(event, encoded.topics, encoded.data), (7,)
        )

    def test_dynamic_indexed_aliases_in_components(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "D",
                "inputs": [
                    {"name": "s", "type": "string", "indexed": True},
                    {"name": "arr", "type": "uint[]", "indexed": True},
                    {
                        "name": "t",
                        "type": "tuple",
                        "indexed": True,
                        "components": [
                            {"name": "x", "type": "int"},
                            {"name": "y", "type": "byte"},
                        ],
                    },
                ],
            }
        )
        self.assertEqual(
            format_abi_type(event.inputs[2].abi_type),
            "(int256,bytes1)",
        )
        values = ["hi", [1, 2], (-1, b"\x05")]
        encoded = encode_event_log(event, values)
        # 三个 indexed 均为动态类型：topic0 + 3 个哈希主题，data 为空。
        self.assertEqual(len(encoded.topics), 4)
        self.assertEqual(encoded.data, b"")
        self.assertEqual(
            match_event_log_values(
                event, encoded.topics, encoded.data, values
            ),
            tuple(values),
        )
        self.assertIsNone(
            match_event_log_values(
                event,
                encoded.topics,
                encoded.data,
                ["no", [1, 2], (-1, b"\x05")],
            )
        )

    def test_arbitrarily_deep_component_alias(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "N",
                "inputs": [
                    {
                        "type": "tuple",
                        "components": [
                            {
                                "name": "inner",
                                "type": "tuple",
                                "components": [
                                    {"name": "z", "type": "uint[]"}
                                ],
                            },
                            {"name": "b", "type": "byte"},
                        ],
                    }
                ],
            }
        )
        self.assertEqual(
            format_abi_type(event.inputs[0].abi_type),
            "((uint256[]),bytes1)",
        )

    def test_bad_alias_raises_event_abi_invalid(self):
        for bad in _INVALID_NEAR_MISSES:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    parse_event_abi(
                        {
                            "type": "event",
                            "name": "B",
                            "inputs": [{"type": bad}],
                        }
                    )
                self.assertEqual(
                    ctx.exception.code, "EVENT_ABI_INVALID"
                )

    def test_bad_alias_in_nested_component(self):
        with self.assertRaises(AbiEventError) as ctx:
            parse_event_abi(
                {
                    "type": "event",
                    "name": "B",
                    "inputs": [
                        {
                            "type": "tuple",
                            "components": [{"type": "byte2"}],
                        }
                    ],
                }
            )
        self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")


class EventRegistryAliasTests(unittest.TestCase):
    def test_contract_level_dispatch_and_match(self):
        registry = parse_event_registry(
            [
                {
                    "type": "event",
                    "name": "Log",
                    "inputs": [
                        {"name": "", "type": "uint", "indexed": False}
                    ],
                }
            ]
        )
        encoded = encode_event_log(
            parse_event_abi(
                {
                    "type": "event",
                    "name": "Log",
                    "inputs": [
                        {"name": "", "type": "uint256", "indexed": False}
                    ],
                }
            ),
            [55],
        )
        log = {"topics": list(encoded.topics_hex), "data": encoded.data_hex}
        event, _topics, _data, values = decode_contract_event_log(
            registry, log
        )
        self.assertEqual(event.name, "Log")
        self.assertEqual(values, (55,))
        self.assertEqual(
            match_contract_event_log_values(registry, log, [55]), (55,)
        )

    def test_bad_alias_error_code(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            parse_event_registry(
                [
                    {
                        "type": "event",
                        "name": "B",
                        "inputs": [{"type": "byte2"}],
                    }
                ]
            )
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")


if __name__ == "__main__":
    unittest.main()
