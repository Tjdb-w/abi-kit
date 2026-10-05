"""parse_contract_call_registry / encode_contract_call /
decode_contract_call 与 AbiContractCallError 的行为测试。

覆盖：
- 注册表只登记 function / receive / fallback，receive/fallback 至多各一个
  且不接受 inputs，函数规范签名不得重复，其余条目跳过；
- 函数按 inputs 顺序编解码实参，selector 与既有 function 路径一致；
- receive 仅接受空实参/空 data，空 calldata 优先分派 receive；
- fallback 仅接受空实参且 data 原样透传，未知 selector 退回 fallback；
- 六类 AbiContractCallError 错误码、ABIValueError 与 AbiTrailingDataError
  的边界，以及调用层失败不返回部分结果。

仅使用标准库 unittest，无第三方依赖。
"""

import json
import unittest

from abi_kit import (
    AbiContractCallError,
    AbiTrailingDataError,
    ContractCallRegistry,
    DecodedContractCall,
    FunctionArgument,
    ABIValueError,
    decode_contract_call,
    encode_contract_call,
    function_selector,
    parse_contract_call_registry,
    parse_function_abi,
)

TRANSFER_ENTRY = {
    "type": "function",
    "name": "transfer",
    "inputs": [
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
    ],
    "outputs": [{"name": "", "type": "bool"}],
}
TRANSFER_128_ENTRY = {
    "type": "function",
    "name": "transfer",
    "inputs": [
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint128"},
    ],
    "outputs": [],
}
PING_ENTRY = {
    "type": "function",
    "name": "ping",
    "inputs": [],
    "outputs": [{"name": "", "type": "string"}],
}
DEPOSIT_ENTRY = {
    "type": "function",
    "name": "deposit",
    "inputs": [
        {"name": "who", "type": "address"},
        {
            "name": "note",
            "type": "tuple",
            "components": [
                {"name": "tag", "type": "string"},
                {"name": "amounts", "type": "uint256[]"},
            ],
        },
    ],
    "outputs": [],
}
RECEIVE_ENTRY = {"type": "receive", "stateMutability": "payable"}
FALLBACK_ENTRY = {"type": "fallback", "stateMutability": "payable"}
EVENT_ENTRY = {
    "type": "event",
    "name": "Transfer",
    "inputs": [{"name": "v", "type": "uint256"}],
}
CONSTRUCTOR_ENTRY = {"type": "constructor", "inputs": []}
ERROR_ENTRY = {"type": "error", "name": "Bad", "inputs": []}

ADDR = "0x" + "11" * 20


def build_registry(*entries):
    return parse_contract_call_registry(list(entries))


class RegistryParsingTests(unittest.TestCase):
    def test_registers_only_three_kinds_in_declaration_order(self):
        registry = build_registry(
            TRANSFER_ENTRY,
            RECEIVE_ENTRY,
            FALLBACK_ENTRY,
            EVENT_ENTRY,
            CONSTRUCTOR_ENTRY,
            ERROR_ENTRY,
        )
        self.assertIsInstance(registry, ContractCallRegistry)
        self.assertEqual(len(registry.functions), 1)
        self.assertTrue(registry.receive)
        self.assertTrue(registry.fallback)
        self.assertEqual(len(registry), 3)
        function = registry.functions[0]
        self.assertEqual(function.name, "transfer")
        self.assertEqual(len(function.inputs), 2)
        self.assertEqual(len(function.outputs), 1)

    def test_accepts_json_string_and_tuple(self):
        abi = json.dumps([TRANSFER_ENTRY, RECEIVE_ENTRY])
        registry = parse_contract_call_registry(abi)
        self.assertEqual(len(registry.functions), 1)
        self.assertTrue(registry.receive)
        registry2 = parse_contract_call_registry((TRANSFER_ENTRY,))
        self.assertEqual(len(registry2.functions), 1)

    def test_passthrough_registry_instance(self):
        registry = build_registry(TRANSFER_ENTRY)
        self.assertIs(parse_contract_call_registry(registry), registry)

    def test_selector_semantics_match_function_path(self):
        registry = build_registry(TRANSFER_ENTRY)
        standalone = parse_function_abi([TRANSFER_ENTRY])[0]
        self.assertEqual(
            function_selector(registry.functions[0]),
            function_selector(standalone),
        )

    def test_receive_fallback_default_absent(self):
        registry = build_registry(TRANSFER_ENTRY)
        self.assertFalse(registry.receive)
        self.assertFalse(registry.fallback)

    def test_receive_fallback_empty_inputs_allowed(self):
        registry = build_registry(
            {"type": "receive", "inputs": []},
            {"type": "fallback", "inputs": []},
        )
        self.assertTrue(registry.receive)
        self.assertTrue(registry.fallback)

    def test_unknown_missing_type_and_non_object_entries_skipped(self):
        registry = parse_contract_call_registry(
            [
                {"type": "constructor"},
                {"x": 1},
                "str-entry",
                4,
                None,
                {"type": "fictional"},
                PING_ENTRY,
            ]
        )
        self.assertEqual(len(registry.functions), 1)
        self.assertFalse(registry.receive)
        self.assertFalse(registry.fallback)

    def test_invalid_abi_root(self):
        for bad in ({}, 42, object(), json.dumps({}), "{bad json"):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry(bad)
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_ABI_INVALID"
            )

    def test_invalid_function_entry(self):
        for bad in (
            {"type": "function", "inputs": []},
            {"type": "function", "name": "f"},
            {"type": "function", "name": "1f", "inputs": []},
            {"type": "function", "name": "f", "inputs": [{"type": "uint"}]},
        ):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry([bad])
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_ABI_INVALID"
            )

    def test_duplicate_function_signature(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            parse_contract_call_registry([TRANSFER_ENTRY, TRANSFER_ENTRY])
        self.assertEqual(ctx.exception.code, "CONTRACT_CALL_ABI_INVALID")

    def test_receive_fallback_with_inputs_invalid(self):
        for kind in ("receive", "fallback"):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry(
                    [{"type": kind, "inputs": [{"type": "uint256"}]}]
                )
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_ABI_INVALID"
            )

    def test_duplicate_receive_and_fallback(self):
        for kind in ("receive", "fallback"):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry(
                    [{"type": kind}, {"type": kind}]
                )
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_ABI_INVALID"
            )


class EncodeFunctionTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_registry(TRANSFER_ENTRY, RECEIVE_ENTRY, FALLBACK_ENTRY)

    def test_encode_by_name_and_signature_matches_selector_path(self):
        calldata = encode_contract_call(
            self.registry, "transfer", [ADDR, 7]
        )
        self.assertEqual(calldata[:4], function_selector(self.registry.functions[0]))
        self.assertEqual(len(calldata), 4 + 64)
        by_signature = encode_contract_call(
            self.registry, "transfer(address,uint256)", [ADDR, 7]
        )
        self.assertEqual(by_signature, calldata)
        self.assertEqual(calldata[16:36], bytes.fromhex("11" * 20))
        self.assertEqual(calldata[-32:], (7).to_bytes(32, "big"))

    def test_encode_no_arg_function_is_selector_only(self):
        registry = build_registry(PING_ENTRY)
        calldata = encode_contract_call(registry, "ping")
        self.assertEqual(calldata, function_selector(registry.functions[0]))
        self.assertEqual(len(calldata), 4)

    def test_encode_nested_tuple_and_array_args(self):
        registry = build_registry(DEPOSIT_ENTRY)
        calldata = encode_contract_call(
            registry,
            "deposit",
            [ADDR, ("hello", [1, 2, 3])],
        )
        result = decode_contract_call(registry, calldata)
        self.assertEqual(result.kind, "function")
        self.assertEqual(
            result.values, (ADDR, ("hello", [1, 2, 3]))
        )

    def test_encode_accepts_raw_abi_json(self):
        calldata = encode_contract_call(
            json.dumps([TRANSFER_ENTRY]), "transfer", [ADDR, 0]
        )
        self.assertEqual(calldata[:4], function_selector(parse_function_abi([TRANSFER_ENTRY])[0]))

    def test_ambiguous_name(self):
        registry = build_registry(TRANSFER_ENTRY, TRANSFER_128_ENTRY)
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call(registry, "transfer", [ADDR, 1])
        self.assertEqual(ctx.exception.code, "CONTRACT_CALL_AMBIGUOUS")
        # 规范签名可唯一选择，两个重载均可用。
        cd256 = encode_contract_call(
            registry, "transfer(address,uint256)", [ADDR, 1]
        )
        cd128 = encode_contract_call(
            registry, "transfer(address,uint128)", [ADDR, 1]
        )
        self.assertNotEqual(cd256, cd128)

    def test_target_not_found(self):
        for target in ("missing", "missing()", "nope(uint256)"):
            with self.assertRaises(AbiContractCallError) as ctx:
                encode_contract_call(self.registry, target)
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_TARGET_NOT_FOUND"
            )

    def test_value_errors_propagate(self):
        with self.assertRaises(ABIValueError):
            encode_contract_call(self.registry, "transfer", [ADDR])
        with self.assertRaises(ABIValueError):
            encode_contract_call(self.registry, "transfer", [ADDR, "x"])
        with self.assertRaises(ABIValueError):
            encode_contract_call(
                self.registry, "transfer", [ADDR, 1, 2]
            )
        with self.assertRaises(ABIValueError):
            encode_contract_call(self.registry, "transfer", 7)

    def test_function_target_rejects_data(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call(
                self.registry, "transfer", [ADDR, 1], data=b"\x00"
            )
        self.assertEqual(ctx.exception.code, "CONTRACT_CALL_DATA_INVALID")


class EncodeReceiveFallbackTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_registry(RECEIVE_ENTRY, FALLBACK_ENTRY, PING_ENTRY)

    def test_receive_requires_empty_args_and_data(self):
        self.assertEqual(encode_contract_call(self.registry, "receive"), b"")
        self.assertEqual(
            encode_contract_call(self.registry, "receive", None, b""), b""
        )
        self.assertEqual(
            encode_contract_call(self.registry, "receive", data="0x"), b""
        )

    def test_receive_nonempty_args(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call(self.registry, "receive", [1])
        self.assertEqual(
            ctx.exception.code, "CONTRACT_CALL_RECEIVE_NONEMPTY"
        )

    def test_receive_nonempty_data(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call(self.registry, "receive", data=b"\x00")
        self.assertEqual(
            ctx.exception.code, "CONTRACT_CALL_RECEIVE_NONEMPTY"
        )

    def test_receive_invalid_hex_data(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call(self.registry, "receive", data="0xzz")
        self.assertEqual(ctx.exception.code, "CONTRACT_CALL_DATA_INVALID")

    def test_fallback_passes_data_through_verbatim(self):
        raw = bytes.fromhex("1234abcd")
        self.assertEqual(
            encode_contract_call(self.registry, "fallback"), b""
        )
        self.assertEqual(
            encode_contract_call(self.registry, "fallback", data=raw), raw
        )
        self.assertEqual(
            encode_contract_call(
                self.registry, "fallback", None, "0x1234abcd"
            ),
            raw,
        )

    def test_fallback_with_args_rejected(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call(
                self.registry, "fallback", [1], data=b"\xaa"
            )
        self.assertEqual(ctx.exception.code, "CONTRACT_CALL_FALLBACK_ARGS")

    def test_fallback_invalid_data(self):
        for bad in (3, "0xzz", "0xf0f"):
            with self.assertRaises(AbiContractCallError) as ctx:
                encode_contract_call(self.registry, "fallback", data=bad)
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_DATA_INVALID"
            )

    def test_missing_receive_fallback_targets(self):
        registry = build_registry(PING_ENTRY)
        for target in ("receive", "fallback"):
            with self.assertRaises(AbiContractCallError) as ctx:
                encode_contract_call(registry, target)
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_TARGET_NOT_FOUND"
            )


class DecodeDispatchTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_registry(
            TRANSFER_ENTRY, PING_ENTRY, RECEIVE_ENTRY, FALLBACK_ENTRY
        )

    def test_decode_function_strict_args(self):
        calldata = encode_contract_call(
            self.registry, "transfer", [ADDR, 9]
        )
        result = decode_contract_call(self.registry, calldata)
        self.assertIsInstance(result, DecodedContractCall)
        self.assertEqual(result.kind, "function")
        self.assertEqual(result.function, self.registry.functions[0])
        self.assertEqual(result.function_name, "transfer")
        self.assertEqual(
            result.signature, "transfer(address,uint256)"
        )
        self.assertEqual(result.calldata, calldata)
        self.assertEqual(result.data, calldata[4:])
        self.assertEqual(
            result.data_hex, "0x" + calldata[4:].hex()
        )
        self.assertEqual(result.calldata_hex, "0x" + calldata.hex())
        self.assertEqual(len(result.args), 2)
        self.assertIsInstance(result.args[0], FunctionArgument)
        self.assertEqual(result.args[0].name, "to")
        self.assertEqual(result.args[0].type, "address")
        self.assertEqual(result.args[0].value, ADDR)
        self.assertEqual(result.args[1].name, "value")
        self.assertEqual(result.args[1].type, "uint256")
        self.assertEqual(result.args[1].value, 9)
        self.assertEqual(result.values, (ADDR, 9))

    def test_decode_function_from_hex_string(self):
        calldata = encode_contract_call(self.registry, "ping")
        result = decode_contract_call(self.registry, "0x" + calldata.hex())
        self.assertEqual(result.kind, "function")
        self.assertEqual(result.function_name, "ping")
        self.assertEqual(result.args, ())
        self.assertEqual(result.data, b"")

    def test_decode_no_arg_function_is_selector_only(self):
        calldata = encode_contract_call(self.registry, "ping")
        self.assertEqual(len(calldata), 4)
        result = decode_contract_call(self.registry, calldata)
        self.assertEqual(result.kind, "function")
        self.assertEqual(result.data, b"")

    def test_empty_calldata_prefers_receive(self):
        for empty in (b"", "0x", "0X"):
            result = decode_contract_call(self.registry, empty)
            self.assertEqual(result.kind, "receive")
            self.assertIsNone(result.function)
            self.assertEqual(result.function_name, None)
            self.assertIsNone(result.signature)
            self.assertEqual(result.args, ())
            self.assertEqual(result.values, ())
            self.assertEqual(result.data, b"")
            self.assertEqual(result.calldata, b"")

    def test_empty_calldata_falls_back_to_fallback_without_receive(self):
        registry = build_registry(FALLBACK_ENTRY, PING_ENTRY)
        result = decode_contract_call(registry, b"")
        self.assertEqual(result.kind, "fallback")
        self.assertEqual(result.data, b"")
        self.assertEqual(result.calldata, b"")

    def test_empty_calldata_without_receive_or_fallback_fails(self):
        registry = build_registry(PING_ENTRY)
        with self.assertRaises(AbiContractCallError) as ctx:
            decode_contract_call(registry, b"")
        self.assertEqual(
            ctx.exception.code, "CONTRACT_CALL_TARGET_NOT_FOUND"
        )

    def test_unknown_selector_goes_to_fallback_verbatim(self):
        raw = bytes.fromhex("12345678aabbccdd")
        result = decode_contract_call(self.registry, raw)
        self.assertEqual(result.kind, "fallback")
        self.assertIsNone(result.function)
        self.assertEqual(result.args, ())
        self.assertEqual(result.data, raw)
        self.assertEqual(result.calldata, raw)

    def test_short_nonempty_calldata_goes_to_fallback(self):
        raw = b"\x01\x02"
        result = decode_contract_call(self.registry, raw)
        self.assertEqual(result.kind, "fallback")
        self.assertEqual(result.data, raw)

    def test_unknown_selector_without_fallback_fails(self):
        registry = build_registry(TRANSFER_ENTRY, RECEIVE_ENTRY)
        with self.assertRaises(AbiContractCallError) as ctx:
            decode_contract_call(registry, bytes.fromhex("12345678"))
        self.assertEqual(
            ctx.exception.code, "CONTRACT_CALL_TARGET_NOT_FOUND"
        )

    def test_function_selector_but_bad_payload_raises_value_error(self):
        calldata = encode_contract_call(
            self.registry, "transfer", [ADDR, 1]
        )
        with self.assertRaises(ABIValueError):
            decode_contract_call(self.registry, calldata[:-1])

    def test_trailing_bytes_raise_trailing_data_error(self):
        calldata = encode_contract_call(self.registry, "ping") + b"\x00"
        with self.assertRaises(AbiTrailingDataError):
            decode_contract_call(self.registry, calldata)

    def test_invalid_calldata(self):
        for bad in (3, "0xzz", "0xf0f", object()):
            with self.assertRaises(AbiContractCallError) as ctx:
                decode_contract_call(self.registry, bad)
            self.assertEqual(
                ctx.exception.code, "CONTRACT_CALL_DATA_INVALID"
            )

    def test_round_trip_with_dynamic_values(self):
        registry = build_registry(DEPOSIT_ENTRY, FALLBACK_ENTRY)
        values = (ADDR, ("标签", [42, 0, 7]))
        calldata = encode_contract_call(
            registry, "deposit", list(values)
        )
        result = decode_contract_call(registry, calldata)
        self.assertEqual(result.kind, "function")
        self.assertEqual(
            result.args[1].type, "(string,uint256[])"
        )
        self.assertEqual(result.values, values)
        # 解码后重编码得到相同 calldata。
        self.assertEqual(
            encode_contract_call(
                registry, result.signature, list(result.values)
            ),
            calldata,
        )

    def test_failure_returns_no_partial_result(self):
        # 无效 calldata 与尾随字节均必须整体抛出，而不是返回结果对象。
        with self.assertRaises(AbiContractCallError):
            decode_contract_call(self.registry, "not-hex")
        with self.assertRaises(AbiTrailingDataError):
            calldata = encode_contract_call(
                self.registry, "transfer", [ADDR, 1]
            )
            decode_contract_call(self.registry, calldata + b"\xff")


class ExistingEntryPointsUnchangedTests(unittest.TestCase):
    """混合 ABI 下既有 function/event/error/constructor 入口行为不变。"""

    ABI = [
        TRANSFER_ENTRY,
        TRANSFER_128_ENTRY,
        RECEIVE_ENTRY,
        FALLBACK_ENTRY,
        EVENT_ENTRY,
        ERROR_ENTRY,
        CONSTRUCTOR_ENTRY,
    ]

    def test_function_entry_points_skip_non_function_entries(self):
        from abi_kit import (
            decode_function_call,
            encode_function_call,
        )

        functions = parse_function_abi(self.ABI)
        self.assertEqual(len(functions), 2)
        encoded = encode_function_call(
            self.ABI, "transfer(address,uint256)", [ADDR, 1]
        )
        decoded = decode_function_call(self.ABI, encoded.calldata)
        self.assertEqual(decoded.values, (ADDR, 1))

    def test_call_registry_ignores_event_error_constructor(self):
        registry = parse_contract_call_registry(self.ABI)
        self.assertEqual(len(registry.functions), 2)
        self.assertTrue(registry.receive)
        self.assertTrue(registry.fallback)
        # 构造器/事件/错误不会影响分派。
        result = decode_contract_call(registry, b"")
        self.assertEqual(result.kind, "receive")


if __name__ == "__main__":
    unittest.main()
