"""parse_function_abi / function_selector / encode_function_call /
decode_function_call 的行为测试。

仅使用标准库 unittest，无第三方依赖。selector 向量取自链上常见函数
（ERC-20 transfer / approve / balanceOf / transferFrom）。
"""

import json
import unittest

from abi_kit import (
    AbiCalldataLengthError,
    AbiFunctionNotFoundError,
    AbiMetadataError,
    AbiOverloadError,
    AbiSelectorError,
    AbiTrailingDataError,
    AbiValueError,
    ABIValueError,
    ArrayType,
    DecodedFunctionCall,
    EncodedFunctionCall,
    FunctionArgument,
    FunctionDefinition,
    canonical_function_signature,
    decode_event_log,
    decode_function_call,
    decodeFunctionCall,
    encode_function_call,
    encodeFunctionCall,
    format_abi_type,
    function_selector,
    parse_event_abi,
    parse_function_abi,
    event_topic0,
)
from abi_kit._keccak import keccak_256

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def addr_word(addr: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(addr[2:])


ADDR_A = "0x" + "11" * 20
ADDR_B = "0x" + "22" * 20

TRANSFER_SELECTOR = "0xa9059cbb"
APPROVE_SELECTOR = "0x095ea7b3"
BALANCE_OF_SELECTOR = "0x70a08231"
TRANSFER_FROM_SELECTOR = "0x23b872dd"


def fn(name, inputs, **extra):
    entry = {"type": "function", "name": name, "inputs": inputs}
    entry.update(extra)
    return entry


def transfer_fn(**over):
    entry = fn(
        "transfer",
        [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        outputs=[],
        stateMutability="nonpayable",
    )
    entry.update(over)
    return entry


def transfer_event():
    return {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
    }


ERC20_ABI = [
    transfer_fn(),
    fn(
        "approve",
        [
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
    ),
    fn("balanceOf", [{"name": "owner", "type": "address"}], outputs=[{"type": "uint256"}]),
    fn(
        "transferFrom",
        [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
    ),
    transfer_event(),
]


class ParseFunctionAbiTests(unittest.TestCase):
    def test_minimal_function(self):
        functions = parse_function_abi([fn("ping", [])])
        self.assertEqual(len(functions), 1)
        self.assertIsInstance(functions[0], FunctionDefinition)
        self.assertEqual(functions[0].name, "ping")
        self.assertEqual(functions[0].inputs, ())

    def test_inputs_required_but_may_be_empty(self):
        functions = parse_function_abi([fn("a", [])])
        self.assertEqual(functions[0].inputs, ())

    def test_param_name_defaults_empty(self):
        functions = parse_function_abi(
            [fn("f", [{"type": "uint256"}, {"name": "x", "type": "bool"}])]
        )
        self.assertEqual(functions[0].inputs[0].name, "")
        self.assertEqual(functions[0].inputs[1].name, "x")

    def test_outputs_and_mutability_ignored(self):
        functions = parse_function_abi(
            [fn("f", [{"type": "uint256"}], outputs=[{"type": "bool"}],
                stateMutability="view")]
        )
        self.assertEqual(len(functions[0].inputs), 1)

    def test_tuple_components_recursive_with_names(self):
        functions = parse_function_abi(
            [
                fn(
                    "f",
                    [
                        {
                            "name": "p",
                            "type": "tuple",
                            "components": [
                                {"name": "to", "type": "address"},
                                {
                                    "name": "inner",
                                    "type": "tuple",
                                    "components": [
                                        {"name": "x", "type": "uint256"},
                                        {"name": "s", "type": "string"},
                                    ],
                                },
                            ],
                        }
                    ],
                )
            ]
        )
        t = functions[0].inputs[0].abi_type
        self.assertEqual(format_abi_type(t), "(address,(uint256,string))")
        self.assertEqual(t.names, ("to", "inner"))
        self.assertEqual(t.components[1].names, ("x", "s"))

    def test_tuple_array_suffixes(self):
        functions = parse_function_abi(
            [
                fn(
                    "f",
                    [
                        {
                            "name": "items",
                            "type": "tuple[2][]",
                            "components": [
                                {"name": "a", "type": "uint128"},
                                {"name": "b", "type": "bool"},
                            ],
                        }
                    ],
                )
            ]
        )
        t = functions[0].inputs[0].abi_type
        self.assertIsInstance(t, ArrayType)
        self.assertIsNone(t.length)
        self.assertEqual(format_abi_type(t), "(uint128,bool)[2][]")

    def test_only_function_entries_consumed(self):
        abi = [
            fn("a", []),
            transfer_event(),
            {"type": "constructor", "inputs": [{"type": "uint256"}]},
            {"type": "error", "name": "Bad", "inputs": []},
            {"type": "receive", "stateMutability": "payable"},
            {"type": "fallback", "stateMutability": "payable"},
            fn("b", [{"type": "bool"}]),
        ]
        functions = parse_function_abi(abi)
        self.assertEqual([f.name for f in functions], ["a", "b"])

    def test_non_function_entries_not_validated(self):
        # 事件/构造/错误条目的合法性由各自入口负责；即使结构不完整，
        # 函数路径也只跳过，不抛错。
        functions = parse_function_abi(
            [fn("a", []), {"type": "event"}, {"type": "constructor"},
             {"type": "error"}]
        )
        self.assertEqual([f.name for f in functions], ["a"])

    def test_accepts_json_string(self):
        functions = parse_function_abi(json.dumps(ERC20_ABI))
        self.assertEqual(
            [f.name for f in functions],
            ["transfer", "approve", "balanceOf", "transferFrom"],
        )

    def test_accepts_tuple_input(self):
        functions = parse_function_abi(tuple(ERC20_ABI))
        self.assertEqual(len(functions), 4)

    def test_immutability(self):
        functions = parse_function_abi([fn("a", [])])
        with self.assertRaises(Exception):
            functions[0].name = "b"
        with self.assertRaises(Exception):
            functions[0].inputs = ()

    def _invalid(self, abi):
        with self.assertRaises(AbiMetadataError):
            parse_function_abi(abi)

    def test_invalid_root(self):
        self._invalid(None)
        self._invalid(42)
        self._invalid({})
        self._invalid("not json")
        self._invalid(json.dumps({"name": "f"}))

    def test_invalid_entries(self):
        self._invalid([42])
        self._invalid(["x"])
        self._invalid([{"name": "f", "inputs": []}])  # 缺 type
        self._invalid([{"type": "widget", "name": "f", "inputs": []}])
        self._invalid([{"type": "function", "inputs": []}])  # 缺 name
        self._invalid([{"type": "function", "name": ""}])
        self._invalid([{"type": "function", "name": "9f"}])
        self._invalid([{"type": "function", "name": None}])
        self._invalid([{"type": "function", "name": 1}])
        self._invalid([{"type": "function", "name": "f"}])  # 缺 inputs
        self._invalid([{"type": "function", "name": "f", "inputs": {}}])
        self._invalid([{"type": "function", "name": "f", "inputs": "nope"}])
        self._invalid([fn("f", [42])])
        self._invalid([fn("f", [{"type": 7}])])
        self._invalid([fn("f", [{"type": ""}])])
        self._invalid([fn("f", [{"type": "uint"}])])
        self._invalid([fn("f", [{"type": "address", "name": 1}])])

    def test_invalid_tuple(self):
        self._invalid([fn("f", [{"type": "tuple"}])])
        self._invalid(
            [fn("f", [{"type": "tuple", "components": {}}])]
        )
        self._invalid(
            [fn("f", [{"type": "tuple[0]",
                       "components": [{"type": "uint256"}]}])]
        )
        self._invalid(
            [fn("f", [{"type": "tuple",
                       "components": [{"type": "uint"}]}])]
        )
        self._invalid([fn("f", [{"type": "uint256",
                                 "components": [{"type": "bool"}]}])])


class SignatureAndSelectorTests(unittest.TestCase):
    def test_known_selectors(self):
        functions = {f.name: f for f in parse_function_abi(ERC20_ABI)}
        self.assertEqual(function_selector(functions["transfer"]).hex(),
                         TRANSFER_SELECTOR[2:])
        self.assertEqual(function_selector(functions["approve"]).hex(),
                         APPROVE_SELECTOR[2:])
        self.assertEqual(function_selector(functions["balanceOf"]).hex(),
                         BALANCE_OF_SELECTOR[2:])
        self.assertEqual(function_selector(functions["transferFrom"]).hex(),
                         TRANSFER_FROM_SELECTOR[2:])

    def test_canonical_signatures(self):
        functions = {f.name: f for f in parse_function_abi(ERC20_ABI)}
        self.assertEqual(
            canonical_function_signature(functions["transfer"]),
            "transfer(address,uint256)",
        )
        self.assertEqual(
            canonical_function_signature(functions["transferFrom"]),
            "transferFrom(address,address,uint256)",
        )

    def test_param_names_ignored_by_selector(self):
        a = parse_function_abi(
            [fn("f", [{"name": "alpha", "type": "uint256"},
                      {"name": "beta", "type": "bool"}])]
        )[0]
        b = parse_function_abi(
            [fn("f", [{"name": "zzz", "type": "uint256"},
                      {"name": "", "type": "bool"}])]
        )[0]
        self.assertEqual(function_selector(a), function_selector(b))
        self.assertEqual(
            function_selector(a),
            keccak_256(b"f(uint256,bool)")[:4],
        )

    def test_canonical_tuple_and_array(self):
        f = parse_function_abi(
            [
                fn(
                    "f",
                    [
                        {
                            "type": "tuple[]",
                            "components": [
                                {"name": "a", "type": "address"},
                                {"name": "b", "type": "uint8[2]"},
                            ],
                        },
                        {"type": "bytes"},
                    ],
                )
            ]
        )[0]
        self.assertEqual(
            canonical_function_signature(f),
            "f((address,uint8[2])[],bytes)",
        )

    def test_no_arg_signature(self):
        f = parse_function_abi([fn("a", [])])[0]
        self.assertEqual(canonical_function_signature(f), "a()")
        self.assertEqual(function_selector(f).hex(), "0dbe671f")

    def test_selector_is_four_bytes(self):
        f = parse_function_abi(ERC20_ABI)[0]
        self.assertIsInstance(function_selector(f), bytes)
        self.assertEqual(len(function_selector(f)), 4)


class EncodeFunctionCallTests(unittest.TestCase):
    def test_encode_transfer_layout(self):
        result = encode_function_call(ERC20_ABI, "transfer", [ADDR_A, 1000])
        self.assertIsInstance(result, EncodedFunctionCall)
        self.assertEqual(result.selector.hex(), TRANSFER_SELECTOR[2:])
        expected_payload = addr_word(ADDR_A) + word(1000)
        self.assertEqual(result.calldata, result.selector + expected_payload)
        self.assertEqual(len(result.calldata), 4 + 64)

    def test_result_identity_properties(self):
        result = encode_function_call(ERC20_ABI, "transfer", [ADDR_A, 1])
        self.assertEqual(result.function_name, "transfer")
        self.assertEqual(result.signature, "transfer(address,uint256)")
        self.assertEqual(result.selector_hex, TRANSFER_SELECTOR)
        self.assertTrue(result.calldata_hex.startswith("0x"))
        self.assertEqual(result.calldata_hex, "0x" + result.calldata.hex())

    def test_json_string_abi(self):
        result = encode_function_call(
            json.dumps(ERC20_ABI), "balanceOf", [ADDR_A]
        )
        self.assertEqual(result.selector_hex, BALANCE_OF_SELECTOR)
        self.assertEqual(result.calldata,
                         result.selector + addr_word(ADDR_A))

    def test_no_args_omitted_or_empty(self):
        abi = [fn("ping", [])]
        r1 = encode_function_call(abi, "ping")
        r2 = encode_function_call(abi, "ping", [])
        r3 = encode_function_call(abi, "ping", ())
        self.assertEqual(r1.calldata, r1.selector)
        self.assertEqual(r1.calldata, r2.calldata)
        self.assertEqual(r1.calldata, r3.calldata)

    def test_dynamic_and_static_types(self):
        abi = [
            fn(
                "mix",
                [
                    {"name": "s", "type": "string"},
                    {"name": "raw", "type": "bytes"},
                    {"name": "arr", "type": "uint32[]"},
                    {"name": "flag", "type": "bool"},
                ],
            )
        ]
        result = encode_function_call(
            abi, "mix", ["hi", b"\xde\xad", [1, 2], True]
        )
        decoded = decode_function_call(abi, result.calldata)
        self.assertEqual(decoded.values, ("hi", b"\xde\xad", [1, 2], True))

    def test_nested_tuple_and_arrays(self):
        abi = [
            fn(
                "complex",
                [
                    {"name": "id", "type": "uint64"},
                    {
                        "name": "p",
                        "type": "tuple",
                        "components": [
                            {"name": "to", "type": "address"},
                            {"name": "amounts", "type": "uint128[2]"},
                            {
                                "name": "items",
                                "type": "tuple[]",
                                "components": [
                                    {"name": "x", "type": "bool"},
                                    {"name": "data", "type": "bytes"},
                                ],
                            },
                        ],
                    },
                    {"name": "tags", "type": "string[]"},
                    {"name": "fixed", "type": "bytes4"},
                ],
            )
        ]
        args = [
            7,
            (ADDR_A, [10, 20], [(True, b"\x01"), (False, b"")]),
            ["a", "bb", ""],
            b"abcd",
        ]
        result = encode_function_call(abi, "complex", args)
        decoded = decode_function_call(abi, result.calldata)
        self.assertEqual(decoded.values, tuple(args))
        self.assertEqual(
            decoded.args[1].type,
            "(address,uint128[2],(bool,bytes)[])",
        )

    def test_select_by_canonical_signature(self):
        abi = [
            fn("f", [{"type": "uint256"}]),
            fn("f", [{"type": "address"}]),
        ]
        result = encode_function_call(abi, "f(uint256)", [5])
        self.assertEqual(result.signature, "f(uint256)")
        self.assertEqual(
            result.calldata, result.selector + word(5)
        )
        result_addr = encode_function_call(abi, "f(address)", [ADDR_B])
        self.assertEqual(
            result_addr.calldata,
            result_addr.selector + addr_word(ADDR_B),
        )
        self.assertNotEqual(result.selector, result_addr.selector)

    def test_deterministic_bytes(self):
        args = [ADDR_A, 1000]
        r1 = encode_function_call(ERC20_ABI, "transfer", args)
        r2 = encode_function_call(json.dumps(ERC20_ABI), "transfer", args)
        self.assertEqual(r1.calldata, r2.calldata)
        self.assertEqual(
            encode_function_call(ERC20_ABI, "transfer(address,uint256)", args).calldata,
            r1.calldata,
        )

    def test_wrong_args_container_type(self):
        with self.assertRaises(ABIValueError):
            encode_function_call(ERC20_ABI, "transfer", ADDR_A)


class DecodeFunctionCallTests(unittest.TestCase):
    def test_decode_transfer(self):
        encoded = encode_function_call(ERC20_ABI, "transfer", [ADDR_A, 1000])
        decoded = decode_function_call(ERC20_ABI, encoded.calldata)
        self.assertIsInstance(decoded, DecodedFunctionCall)
        self.assertEqual(decoded.function_name, "transfer")
        self.assertEqual(decoded.signature, "transfer(address,uint256)")
        self.assertEqual(decoded.selector, encoded.selector)
        self.assertEqual(decoded.selector_hex, TRANSFER_SELECTOR)
        self.assertEqual(
            decoded.args,
            (
                FunctionArgument("to", "address", ADDR_A),
                FunctionArgument("value", "uint256", 1000),
            ),
        )
        self.assertEqual(decoded.values, (ADDR_A, 1000))

    def test_decode_hex_inputs(self):
        encoded = encode_function_call(ERC20_ABI, "balanceOf", [ADDR_A])
        d1 = decode_function_call(ERC20_ABI, encoded.calldata.hex())
        d2 = decode_function_call(ERC20_ABI, "0x" + encoded.calldata.hex())
        self.assertEqual(d1.values, (ADDR_A,))
        self.assertEqual(d2.values, (ADDR_A,))

    def test_decode_distinguishes_overloads_by_selector(self):
        abi = [
            fn("f", [{"type": "uint256"}]),
            fn("f", [{"type": "address"}]),
        ]
        e1 = encode_function_call(abi, "f(uint256)", [42])
        e2 = encode_function_call(abi, "f(address)", [ADDR_A])
        self.assertEqual(decode_function_call(abi, e1.calldata).signature,
                         "f(uint256)")
        self.assertEqual(decode_function_call(abi, e2.calldata).values,
                         (ADDR_A,))

    def test_decode_no_arg_function(self):
        abi = [fn("ping", [])]
        encoded = encode_function_call(abi, "ping")
        decoded = decode_function_call(abi, encoded.calldata)
        self.assertEqual(decoded.args, ())
        self.assertEqual(decoded.values, ())

    def test_decode_all_elementary_types_roundtrip(self):
        abi = [
            fn(
                "all",
                [
                    {"name": "u", "type": "uint8"},
                    {"name": "i", "type": "int128"},
                    {"name": "a", "type": "address"},
                    {"name": "b", "type": "bool"},
                    {"name": "s", "type": "string"},
                    {"name": "r", "type": "bytes"},
                    {"name": "m", "type": "bytes4"},
                    {"name": "m32", "type": "bytes32"},
                ],
            )
        ]
        values = (
            255, -12345, ADDR_B, False, "héllo",
            b"\x00\xff" * 10, b"wxyz", bytes(range(32)),
        )
        encoded = encode_function_call(abi, "all", list(values))
        decoded = decode_function_call(abi, encoded.calldata)
        self.assertEqual(decoded.values, values)
        self.assertEqual(
            [arg.type for arg in decoded.args],
            ["uint8", "int128", "address", "bool", "string", "bytes",
             "bytes4", "bytes32"],
        )

    def test_fixed_and_dynamic_array_roundtrip(self):
        abi = [
            fn(
                "arrays",
                [
                    {"name": "fixed", "type": "uint16[3]"},
                    {"name": "dyn", "type": "address[]"},
                    {"name": "nested", "type": "uint256[2][]"},
                    {"name": "empty", "type": "bytes[]"},
                ],
            )
        ]
        values = (
            [1, 2, 3],
            [ADDR_A, ADDR_B],
            [[1, 2], [3, 4], [5, 6]],
            [],
        )
        encoded = encode_function_call(abi, "arrays", list(values))
        decoded = decode_function_call(abi, encoded.calldata)
        self.assertEqual(decoded.values, values)

    def test_tuple_positional_and_named_roundtrip(self):
        abi = [
            fn(
                "order",
                [
                    {
                        "name": "order",
                        "type": "tuple",
                        "components": [
                            {"name": "maker", "type": "address"},
                            {"name": "nested", "type": "tuple",
                             "components": [
                                 {"name": "x", "type": "uint256"},
                                 {"name": "note", "type": "string"},
                             ]},
                            {"name": "tags", "type": "bytes32[2]"},
                        ],
                    }
                ],
            )
        ]
        value = (ADDR_A, (99, "note"), [b"\x01" * 32, b"\x02" * 32])
        encoded = encode_function_call(abi, "order", [value])
        decoded = decode_function_call(abi, encoded.calldata)
        self.assertEqual(decoded.values, (value,))
        # tuple 字段名与嵌套层级可从函数定义还原。
        t = decoded.function.inputs[0].abi_type
        self.assertEqual(t.names, ("maker", "nested", "tags"))
        self.assertEqual(t.components[1].names, ("x", "note"))

    def test_reencode_after_decode_is_identical(self):
        abi = [
            fn(
                "mix",
                [
                    {"type": "uint256"},
                    {
                        "type": "tuple[]",
                        "components": [
                            {"name": "a", "type": "address"},
                            {"name": "s", "type": "string"},
                        ],
                    },
                    {"type": "bool[2]"},
                ],
            )
        ]
        values = [
            2**256 - 1,
            [(ADDR_A, "x"), (ADDR_B, "yy")],
            [True, False],
        ]
        encoded = encode_function_call(abi, "mix", values)
        decoded = decode_function_call(abi, encoded.calldata)
        re_encoded = encode_function_call(
            abi, decoded.signature, list(decoded.values)
        )
        self.assertEqual(re_encoded.calldata, encoded.calldata)


class OverloadResolutionTests(unittest.TestCase):
    def setUp(self):
        self.abi = [
            fn("f", [{"type": "uint256"}]),
            fn("f", [{"type": "address"}]),
            fn("g", []),
        ]

    def test_bare_name_ambiguous_raises(self):
        with self.assertRaises(AbiOverloadError) as ctx:
            encode_function_call(self.abi, "f", [1])
        self.assertIn("f(uint256)", str(ctx.exception))
        self.assertIn("f(address)", str(ctx.exception))

    def test_bare_name_unique_succeeds(self):
        result = encode_function_call(self.abi, "g")
        self.assertEqual(result.function_name, "g")

    def test_canonical_signature_disambiguates(self):
        self.assertEqual(
            encode_function_call(self.abi, "f(uint256)", [1]).signature,
            "f(uint256)",
        )
        self.assertEqual(
            encode_function_call(self.abi, "f(address)", [ADDR_A]).signature,
            "f(address)",
        )

    def test_unknown_name_raises_not_found(self):
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_call(self.abi, "missing", [])

    def test_unknown_signature_raises_not_found(self):
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_call(self.abi, "f(bool)", [True])

    def test_non_signature_with_parens_raises_not_found(self):
        with self.assertRaises(AbiFunctionNotFoundError):
            encode_function_call(self.abi, "f(uint256", [1])

    def test_bare_name_non_identifier_is_metadata_error(self):
        with self.assertRaises(AbiMetadataError):
            encode_function_call(self.abi, "f f", [1])
        with self.assertRaises(AbiMetadataError):
            encode_function_call(self.abi, "", [])
        with self.assertRaises(AbiMetadataError):
            encode_function_call(self.abi, 9, [])


class DecodeErrorTests(unittest.TestCase):
    def setUp(self):
        self.abi = [
            fn("f", [{"name": "x", "type": "uint256"}]),
            fn("g", []),
        ]
        self.encoded = encode_function_call(self.abi, "f", [7])

    def test_empty_calldata(self):
        for empty in (b"", ""):
            with self.subTest(empty=empty):
                with self.assertRaises(AbiCalldataLengthError):
                    decode_function_call(self.abi, empty)

    def test_calldata_under_four_bytes(self):
        for short in (b"\x00", b"\x00\x01", b"\x00\x01\x02",
                      "0x", "00", "0x001122", "aabbcc"):
            with self.subTest(short=short):
                with self.assertRaises(AbiCalldataLengthError):
                    decode_function_call(self.abi, short)

    def test_unknown_selector(self):
        with self.assertRaises(AbiSelectorError):
            decode_function_call(self.abi, b"\xff\xff\xff\xff")
        with self.assertRaises(AbiSelectorError):
            decode_function_call(self.abi, "0x" + "ab" * 4)

    def test_trailing_bytes_after_args(self):
        with self.assertRaises(AbiTrailingDataError):
            decode_function_call(self.abi, self.encoded.calldata + b"\x00")
        with self.assertRaises(AbiTrailingDataError):
            decode_function_call(
                self.abi, self.encoded.calldata + b"\x00" * 32
            )

    def test_trailing_after_no_arg_function(self):
        g = encode_function_call(self.abi, "g")
        with self.assertRaises(AbiTrailingDataError):
            decode_function_call(self.abi, g.calldata + b"\x00")

    def test_payload_too_short_is_value_error(self):
        # selector 正确但参数区不完整（uint256 只有 31 字节）。
        with self.assertRaises(AbiValueError):
            decode_function_call(self.abi, self.encoded.selector + b"\x00" * 31)

    def test_invalid_hex_is_value_error(self):
        with self.assertRaises(AbiValueError):
            decode_function_call(self.abi, "0xzzzzzzzz")
        with self.assertRaises(AbiValueError):
            decode_function_call(self.abi, 1234)

    def test_dynamic_offset_violation_is_value_error(self):
        # string 参数区给一个非法偏移（指向 head 之外）。
        abi = [fn("s", [{"type": "string"}])]
        sel = encode_function_call(abi, "s", [""]).selector
        bad_payload = word(64) + word(0)
        with self.assertRaises(AbiValueError):
            decode_function_call(abi, sel + bad_payload)


class EncodeValueErrorTests(unittest.TestCase):
    def test_wrong_types(self):
        abi = [
            fn(
                "f",
                [
                    {"type": "uint8"},
                    {"type": "address"},
                    {"type": "bool"},
                    {"type": "bytes32"},
                ],
            )
        ]
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", ["1", ADDR_A, True, b"\x00" * 32])
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [1, 0x123, True, b"\x00" * 32])
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [1, ADDR_A, 1, b"\x00" * 32])
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [1, ADDR_A, True, b"\x00" * 31])

    def test_out_of_range(self):
        abi = [fn("f", [{"type": "uint8"}, {"type": "int8"}])]
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [256, 0])
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [0, 128])

    def test_arity_mismatch(self):
        abi = [fn("f", [{"type": "uint256"}, {"type": "bool"}])]
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [1])
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [1, True, 0])

    def test_fixed_array_length_mismatch(self):
        abi = [fn("f", [{"type": "uint16[2]"}])]
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [[1]])
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [(1, 2)])  # 元组不是数组

    def test_tuple_shape_mismatch(self):
        abi = [
            fn("f", [{"type": "tuple",
                      "components": [{"type": "uint256"},
                                     {"type": "bool"}]}])
        ]
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [[1, True]])  # list 不是 tuple
        with self.assertRaises(AbiValueError):
            encode_function_call(abi, "f", [(1,)])


class MixedAbiIntegrationTests(unittest.TestCase):
    def test_event_path_unchanged_alongside_functions(self):
        abi = [transfer_fn(), transfer_event()]
        event = parse_event_abi(transfer_event())
        topic0 = event_topic0(event)
        result_log = decode_event_log(
            event,
            [bytes.fromhex(topic0[2:]), addr_word(ADDR_A), addr_word(ADDR_B)],
            word(1000),
        )
        self.assertEqual(result_log, (ADDR_A, ADDR_B, 1000))

        call = encode_function_call(abi, "transfer", [ADDR_B, 1000])
        decoded = decode_function_call(abi, call.calldata)
        self.assertEqual(decoded.values, (ADDR_B, 1000))

    def test_value_error_alias_is_same_class(self):
        self.assertIs(AbiValueError, ABIValueError)

    def test_camel_case_aliases(self):
        self.assertIs(encodeFunctionCall, encode_function_call)
        self.assertIs(decodeFunctionCall, decode_function_call)

    def test_exception_hierarchy(self):
        for exc in (
            AbiMetadataError,
            AbiFunctionNotFoundError,
            AbiOverloadError,
            AbiSelectorError,
            AbiCalldataLengthError,
            AbiTrailingDataError,
        ):
            self.assertTrue(issubclass(exc, ValueError))


if __name__ == "__main__":
    unittest.main()
