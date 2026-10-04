"""parse_function_abi / function_selector / encode_function_call /
decode_function_call 的行为测试。

仅使用标准库 unittest，无第三方依赖。selector 向量为 ERC-20 等常见
函数的链上值。
"""

import json
import unittest

from abi_kit import (
    AbiCalldataLengthError,
    AbiEventError,
    AbiFunctionNotFoundError,
    AbiMetadataError,
    AbiOverloadError,
    AbiSelectorError,
    AbiTrailingDataError,
    AbiValueError,
    ArrayType,
    FunctionCallEncoding,
    FunctionCallResult,
    TupleType,
    decode_event_log,
    decode_function_call,
    decodeFunctionCall,
    encode_function_call,
    encodeFunctionCall,
    event_topic0,
    format_abi_type,
    parse_event_abi,
    parse_function_abi,
    parse_functions,
)

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


ADDR_A = "0x" + "11" * 20
ADDR_B = "0x" + "22" * 20


def addr_word(addr: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(addr[2:])


TRANSFER_SELECTOR = bytes.fromhex("a9059cbb")
APPROVE_SELECTOR = bytes.fromhex("095ea7b3")
TRANSFER_FROM_SELECTOR = bytes.fromhex("23b872dd")
BALANCE_OF_SELECTOR = bytes.fromhex("70a08231")


def transfer_entry():
    return {
        "type": "function",
        "name": "transfer",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
    }


ERC20_ABI = [
    transfer_entry(),
    {
        "type": "function",
        "name": "approve",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "transferFrom",
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "owner", "type": "address"}],
    },
    {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
    },
    {"type": "constructor", "inputs": [{"name": "supply", "type": "uint256"}]},
]
ERC20_JSON = json.dumps(ERC20_ABI)


class ParseFunctionAbiTests(unittest.TestCase):
    def test_minimal_defaults(self):
        fn = parse_function_abi({"type": "function", "name": "ping", "inputs": []})
        self.assertEqual(fn.name, "ping")
        self.assertEqual(fn.inputs, ())
        self.assertEqual(fn.signature, "ping()")
        self.assertEqual(len(fn.selector), 4)

    def test_inputs_preserved_in_order_with_names(self):
        fn = parse_function_abi(transfer_entry())
        self.assertEqual(fn.signature, "transfer(address,uint256)")
        self.assertEqual(fn.selector, TRANSFER_SELECTOR)
        self.assertEqual([p.name for p in fn.inputs], ["to", "value"])
        self.assertEqual(format_abi_type(fn.inputs[0].abi_type), "address")
        self.assertEqual(format_abi_type(fn.inputs[1].abi_type), "uint256")

    def test_param_name_defaults_empty(self):
        fn = parse_function_abi(
            {"type": "function", "name": "f", "inputs": [{"type": "bool"}]}
        )
        self.assertEqual(fn.inputs[0].name, "")

    def test_tuple_and_array_inputs(self):
        fn = parse_function_abi(
            {
                "type": "function",
                "name": "complex",
                "inputs": [
                    {
                        "name": "p",
                        "type": "tuple",
                        "components": [
                            {"name": "to", "type": "address"},
                            {
                                "name": "amounts",
                                "type": "uint128[2][]",
                            },
                            {
                                "name": "inner",
                                "type": "tuple",
                                "components": [
                                    {"name": "x", "type": "uint256"},
                                    {"name": "s", "type": "string"},
                                ],
                            },
                        ],
                    },
                    {"name": "tag", "type": "bytes32"},
                ],
            }
        )
        self.assertEqual(
            fn.signature,
            "complex((address,uint128[2][],(uint256,string)),bytes32)",
        )
        tuple_type = fn.inputs[0].abi_type
        self.assertIsInstance(tuple_type, TupleType)
        self.assertEqual(tuple_type.names, ("to", "amounts", "inner"))
        self.assertIsInstance(tuple_type.components[1], ArrayType)

    def test_immutability(self):
        fn = parse_function_abi(transfer_entry())
        with self.assertRaises(Exception):
            fn.name = "other"
        with self.assertRaises(Exception):
            fn.selector = b"\x00\x00\x00\x00"

    def _metadata_error(self, entry):
        with self.assertRaises(AbiMetadataError):
            parse_function_abi(entry)

    def test_invalid_entries(self):
        self._metadata_error(None)
        self._metadata_error([])
        self._metadata_error("function")
        self._metadata_error(42)
        self._metadata_error({"name": "f", "inputs": []})
        self._metadata_error({"type": "event", "name": "f", "inputs": []})
        self._metadata_error({"type": "function", "inputs": []})
        self._metadata_error({"type": "function", "name": "", "inputs": []})
        self._metadata_error({"type": "function", "name": "9f", "inputs": []})
        self._metadata_error({"type": "function", "name": "f"})
        self._metadata_error({"type": "function", "name": "f", "inputs": {}})
        self._metadata_error(
            {"type": "function", "name": "f", "inputs": [42]}
        )
        self._metadata_error(
            {"type": "function", "name": "f", "inputs": [{"type": "uint"}]}
        )
        self._metadata_error(
            {
                "type": "function",
                "name": "f",
                "inputs": [{"type": "tuple", "components": [{"type": "uint"}]}],
            }
        )
        self._metadata_error(
            {
                "type": "function",
                "name": "f",
                "inputs": [
                    {"type": "uint256", "components": [{"type": "bool"}]}
                ],
            }
        )

    def test_depth_limit_is_metadata_error(self):
        node = {"name": "leaf", "type": "uint256"}
        for _ in range(128):
            node = {
                "name": "t",
                "type": "tuple",
                "components": [node],
            }
        self._metadata_error(
            {"type": "function", "name": "deep", "inputs": [node]}
        )


class ParseFunctionsTests(unittest.TestCase):
    def test_extracts_only_functions_in_order(self):
        fns = parse_functions(ERC20_ABI)
        self.assertEqual(
            [f.name for f in fns],
            ["transfer", "approve", "transferFrom", "balanceOf"],
        )

    def test_accepts_json_string(self):
        fns = parse_functions(ERC20_JSON)
        self.assertEqual(len(fns), 4)
        self.assertEqual(fns[0].signature, "transfer(address,uint256)")

    def test_constructor_error_receive_fallback_skipped(self):
        abi = [
            {"type": "constructor", "inputs": []},
            {"type": "receive", "name": ""},
            {"type": "fallback", "name": ""},
            {
                "type": "error",
                "name": "Bad",
                "inputs": [{"name": "c", "type": "uint256"}],
            },
            {"type": "function", "name": "ok", "inputs": []},
        ]
        fns = parse_functions(abi)
        self.assertEqual([f.name for f in fns], ["ok"])

    def test_bad_abi_metadata_errors(self):
        with self.assertRaises(AbiMetadataError):
            parse_functions("not json")
        with self.assertRaises(AbiMetadataError):
            parse_functions("{}")
        with self.assertRaises(AbiMetadataError):
            parse_functions(123)
        with self.assertRaises(AbiMetadataError):
            parse_functions([42])
        with self.assertRaises(AbiMetadataError):
            parse_functions([{"name": "f", "inputs": []}])
        with self.assertRaises(AbiMetadataError):
            parse_functions([{"type": "struct", "name": "S", "inputs": []}])

    def test_mixed_abi_does_not_change_event_behavior(self):
        # 同一 ABI 中既有函数也有事件：函数入口与事件入口互不影响。
        event = parse_event_abi(ERC20_ABI[4])
        self.assertEqual(
            event_topic0(event),
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
        )
        result = decode_event_log(
            event,
            [
                bytes.fromhex(event_topic0(event)[2:]),
                addr_word(ADDR_A),
                addr_word(ADDR_B),
            ],
            word(1000),
        )
        self.assertEqual(result, (ADDR_A, ADDR_B, 1000))


class SelectorVectorTests(unittest.TestCase):
    def test_known_selectors(self):
        fns = {f.name: f for f in parse_functions(ERC20_ABI)}
        self.assertEqual(fns["transfer"].selector, TRANSFER_SELECTOR)
        self.assertEqual(fns["approve"].selector, APPROVE_SELECTOR)
        self.assertEqual(fns["transferFrom"].selector, TRANSFER_FROM_SELECTOR)
        self.assertEqual(fns["balanceOf"].selector, BALANCE_OF_SELECTOR)

    def test_selector_requires_definition(self):
        with self.assertRaises(AbiMetadataError):
            from abi_kit import function_selector

            function_selector({"name": "f"})


class EncodeFunctionCallTests(unittest.TestCase):
    def test_static_args_bytes_and_hex_abi_equivalent(self):
        args = [ADDR_B, 12345]
        from_list = encode_function_call(ERC20_ABI, "transfer", args)
        from_json = encode_function_call(ERC20_JSON, "transfer", tuple(args))
        self.assertEqual(from_list.selector, TRANSFER_SELECTOR)
        self.assertEqual(from_list.calldata[:4], TRANSFER_SELECTOR)
        expected_body = addr_word(ADDR_B) + word(12345)
        self.assertEqual(from_list.calldata, TRANSFER_SELECTOR + expected_body)
        self.assertEqual(from_list.calldata, from_json.calldata)
        self.assertIsInstance(from_list, FunctionCallEncoding)

    def test_deterministic(self):
        a = encode_function_call(ERC20_JSON, "approve", [ADDR_A, 7])
        b = encode_function_call(ERC20_JSON, "approve", [ADDR_A, 7])
        self.assertEqual(a.calldata, b.calldata)

    def test_no_args(self):
        abi = [{"type": "function", "name": "ping", "inputs": []}]
        result = encode_function_call(json.dumps(abi), "ping", [])
        self.assertEqual(result.calldata, result.selector)
        self.assertEqual(len(result.calldata), 4)
        result2 = encode_function_call(json.dumps(abi), "ping()")
        self.assertEqual(result2.calldata, result.calldata)

    def test_select_by_canonical_signature(self):
        abi = [
            {
                "type": "function",
                "name": "f",
                "inputs": [{"name": "x", "type": "uint256"}],
            },
            {
                "type": "function",
                "name": "f",
                "inputs": [{"name": "x", "type": "address"}],
            },
        ]
        result = encode_function_call(abi, "f(uint256)", [5])
        self.assertEqual(result.selector, bytes.fromhex("b3de648b"))
        self.assertEqual(result.calldata, result.selector + word(5))

    def test_overloaded_name_requires_signature(self):
        abi = [
            {
                "type": "function",
                "name": "f",
                "inputs": [{"name": "x", "type": "uint256"}],
            },
            {
                "type": "function",
                "name": "f",
                "inputs": [{"name": "x", "type": "address"}],
            },
        ]
        with self.assertRaises(AbiOverloadError):
            encode_function_call(abi, "f", [1])
        with self.assertRaises(AbiOverloadError):
            encode_function_call(abi, "f")
        # 签名中的类型串非法属元数据错误，而不是悄悄选中。
        with self.assertRaises(AbiMetadataError):
            encode_function_call(abi, "f(uint 256)", [1])

    def test_function_not_found(self):
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_call(ERC20_ABI, "mint", [])
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_call(ERC20_ABI, "transfer(address,uint128)", [ADDR_A, 1])
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_call(ERC20_JSON, "nope()")

    def test_bad_signature_is_metadata_error(self):
        # 签名本身无法解析/无法生成 selector → AbiMetadataError。
        with self.assertRaises(AbiMetadataError):
            encode_function_call(ERC20_ABI, "transfer(uint)", [ADDR_A, 1])
        with self.assertRaises(AbiMetadataError):
            encode_function_call(ERC20_ABI, "transfer(address,uint256", [ADDR_A, 1])
        with self.assertRaises(AbiMetadataError):
            encode_function_call(ERC20_ABI, "(address)", [ADDR_A])
        with self.assertRaises(AbiMetadataError):
            encode_function_call(ERC20_ABI, "", [])
        with self.assertRaises(AbiMetadataError):
            encode_function_call(ERC20_ABI, 123, [])

    def test_value_mismatch_raises_abi_value_error(self):
        with self.assertRaises(AbiValueError):
            encode_function_call(ERC20_ABI, "transfer", [ADDR_B, -1])
        with self.assertRaises(AbiValueError):
            encode_function_call(ERC20_ABI, "transfer", [ADDR_B, "1000"])
        with self.assertRaises(AbiValueError):
            encode_function_call(ERC20_ABI, "balanceOf", [12345])
        with self.assertRaises(AbiValueError):
            encode_function_call(ERC20_ABI, "transfer", [ADDR_B])
        with self.assertRaises(AbiValueError):
            encode_function_call(ERC20_ABI, "transfer", [ADDR_B, 1, 2])

    def test_nested_tuple_and_arrays(self):
        abi = [
            {
                "type": "function",
                "name": "foo",
                "inputs": [
                    {
                        "name": "p",
                        "type": "tuple",
                        "components": [
                            {"name": "n", "type": "uint256"},
                            {"name": "who", "type": "address"},
                        ],
                    },
                    {"name": "arr", "type": "uint32[]"},
                    {"name": "fixed", "type": "bytes2[2]"},
                ],
            }
        ]
        result = encode_function_call(
            abi, "foo", [(5, ADDR_A), [1, 2, 3], [b"ab", b"cd"]]
        )
        self.assertEqual(len(result.selector), 4)
        self.assertEqual(result.calldata[:4], result.selector)

    def test_camelcase_alias(self):
        a = encodeFunctionCall(ERC20_ABI, "transfer", [ADDR_B, 9])
        b = encode_function_call(ERC20_ABI, "transfer", [ADDR_B, 9])
        self.assertEqual(a.calldata, b.calldata)
        self.assertIs(encodeFunctionCall, encode_function_call)


class DecodeFunctionCallTests(unittest.TestCase):
    def test_roundtrip_static(self):
        calldata = encode_function_call(
            ERC20_ABI, "transferFrom", [ADDR_A, ADDR_B, 777]
        ).calldata
        result = decode_function_call(ERC20_ABI, calldata)
        self.assertIsInstance(result, FunctionCallResult)
        self.assertEqual(result.name, "transferFrom")
        self.assertEqual(result.signature, "transferFrom(address,address,uint256)")
        self.assertEqual(result.selector, TRANSFER_FROM_SELECTOR)
        self.assertEqual(result.args, (ADDR_A, ADDR_B, 777))
        self.assertEqual(
            [p.name for p in result.inputs], ["from", "to", "value"]
        )
        self.assertEqual(format_abi_type(result.inputs[2].abi_type), "uint256")

    def test_roundtrip_hex_input(self):
        encoded = encode_function_call(ERC20_ABI, "balanceOf", [ADDR_A])
        result = decode_function_call(ERC20_JSON, "0x" + encoded.calldata.hex())
        self.assertEqual(result.args, (ADDR_A,))
        result2 = decode_function_call(ERC20_JSON, encoded.calldata.hex())
        self.assertEqual(result2.args, (ADDR_A,))

    def test_roundtrip_no_args(self):
        abi = [{"type": "function", "name": "ping", "inputs": []}]
        encoded = encode_function_call(abi, "ping", []).calldata
        result = decode_function_call(abi, encoded)
        self.assertEqual(result.name, "ping")
        self.assertEqual(result.signature, "ping()")
        self.assertEqual(result.args, ())
        self.assertEqual(result.inputs, ())

    def test_roundtrip_dynamic_and_nested(self):
        abi = [
            {
                "type": "function",
                "name": "mix",
                "inputs": [
                    {"name": "s", "type": "string"},
                    {"name": "raw", "type": "bytes"},
                    {"name": "nums", "type": "int64[]"},
                    {
                        "name": "p",
                        "type": "tuple",
                        "components": [
                            {"name": "to", "type": "address"},
                            {"name": "amount", "type": "uint256"},
                            {
                                "name": "tags",
                                "type": "tuple[]",
                                "components": [
                                    {"name": "k", "type": "uint8"},
                                    {"name": "v", "type": "bool"},
                                ],
                            },
                        ],
                    },
                    {"name": "fixed", "type": "bytes3[3]"},
                ],
            }
        ]
        args = [
            "hello 世界",
            b"\xde\xad\xbe\xef",
            [-3, 0, 9],
            (
                ADDR_A,
                42,
                [(1, True), (2, False)],
            ),
            [b"abc", b"def", b"ghi"],
        ]
        calldata = encode_function_call(abi, "mix", args).calldata
        result = decode_function_call(abi, calldata)
        self.assertEqual(result.args, tuple(args))
        # 嵌套 tuple 字段名与数组层级可还原。
        p_type = result.inputs[3].abi_type
        self.assertEqual(p_type.names, ("to", "amount", "tags"))
        tags_type = p_type.components[2]
        self.assertIsInstance(tags_type, ArrayType)
        self.assertEqual(
            format_abi_type(tags_type.element_type), "(uint8,bool)"
        )

    def test_reencode_matches_original_calldata(self):
        abi = [
            {
                "type": "function",
                "name": "mix",
                "inputs": [
                    {"name": "a", "type": "uint256"},
                    {"name": "s", "type": "string"},
                    {"name": "arr", "type": "address[]"},
                    {"name": "raw", "type": "bytes"},
                ],
            }
        ]
        args = [3, "xyz", [ADDR_A, ADDR_B], b"\x01\x02"]
        original = encode_function_call(abi, "mix", args).calldata
        decoded = decode_function_call(abi, original)
        reencoded = encode_function_call(
            abi, decoded.signature, list(decoded.args)
        ).calldata
        self.assertEqual(reencoded, original)

    def test_unknown_selector(self):
        with self.assertRaises(AbiSelectorError):
            decode_function_call(ERC20_ABI, b"\xaa\xbb\xcc\xdd")
        with self.assertRaises(AbiSelectorError):
            decode_function_call(
                ERC20_ABI, "0xaabbccdd" + "00" * 64
            )

    def test_short_calldata(self):
        for bad in (b"", b"\x00", b"\x00\x01", b"\x00\x01\x02", "", "0x", "0011"):
            with self.subTest(bad=bad):
                with self.assertRaises(AbiCalldataLengthError):
                    decode_function_call(ERC20_ABI, bad)

    def test_trailing_bytes(self):
        # uint256 参数主体应为 32 字节，多给一个字 → 尾随。
        bad = TRANSFER_SELECTOR + addr_word(ADDR_A) + word(1) + word(2)
        with self.assertRaises(AbiTrailingDataError):
            decode_function_call(ERC20_ABI, bad)
        # 无参函数后多一个字节也是尾随。
        abi = [{"type": "function", "name": "ping", "inputs": []}]
        selector = parse_functions(abi)[0].selector
        with self.assertRaises(AbiTrailingDataError):
            decode_function_call(abi, selector + b"\x00")

    def test_invalid_body_is_value_error(self):
        # string 参数的偏移字指向 head 内部（0），严格布局拒绝。
        abi = [
            {
                "type": "function",
                "name": "s",
                "inputs": [{"name": "x", "type": "string"}],
            }
        ]
        selector = parse_functions(abi)[0].selector
        with self.assertRaises(AbiValueError):
            decode_function_call(abi, selector + b"\x00" * 32)
        # uint8 给越界值 256。
        abi2 = [
            {
                "type": "function",
                "name": "u",
                "inputs": [{"name": "x", "type": "uint8"}],
            }
        ]
        selector2 = parse_functions(abi2)[0].selector
        with self.assertRaises(AbiValueError):
            decode_function_call(abi2, selector2 + word(256))

    def test_bad_hex_is_value_error(self):
        with self.assertRaises(AbiValueError):
            decode_function_call(ERC20_ABI, "0xabc")
        with self.assertRaises(AbiValueError):
            decode_function_call(ERC20_ABI, 1234)

    def test_overloads_decode_by_selector(self):
        abi = [
            {
                "type": "function",
                "name": "f",
                "inputs": [{"name": "x", "type": "uint256"}],
            },
            {
                "type": "function",
                "name": "f",
                "inputs": [{"name": "x", "type": "address"}],
            },
        ]
        uint_call = encode_function_call(abi, "f(uint256)", [42]).calldata
        addr_call = encode_function_call(abi, "f(address)", [ADDR_B]).calldata
        self.assertNotEqual(uint_call[:4], addr_call[:4])
        self.assertEqual(decode_function_call(abi, uint_call).args, (42,))
        self.assertEqual(
            decode_function_call(abi, addr_call).signature, "f(address)"
        )

    def test_camelcase_alias(self):
        encoded = encode_function_call(ERC20_ABI, "transfer", [ADDR_B, 5])
        result = decodeFunctionCall(ERC20_ABI, encoded.calldata)
        self.assertEqual(result.args, (ADDR_B, 5))
        self.assertIs(decodeFunctionCall, decode_function_call)


class ExceptionContractTests(unittest.TestCase):
    def test_hierarchy_and_independence(self):
        for exc in (
            AbiMetadataError,
            AbiFunctionNotFoundError,
            AbiOverloadError,
            AbiSelectorError,
            AbiCalldataLengthError,
            AbiValueError,
            AbiTrailingDataError,
        ):
            self.assertTrue(issubclass(exc, ValueError))
        # 函数层 AbiValueError 与值层 ABIValueError 相互独立。
        from abi_kit import ABIValueError

        self.assertFalse(issubclass(AbiValueError, ABIValueError))
        self.assertFalse(issubclass(ABIValueError, AbiValueError))
        # 不与事件层错误混用。
        self.assertFalse(issubclass(AbiValueError, AbiEventError))


if __name__ == "__main__":
    unittest.main()
