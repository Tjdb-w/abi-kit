"""parse_contract_call_registry / encode_contract_call /
decode_contract_call 的行为测试。

仅使用标准库 unittest，无第三方依赖。selector 向量取自链上常见函数
（ERC-20 transfer / balanceOf）。
"""

import json
import unittest

from abi_kit import (
    AbiContractCallError,
    AbiTrailingDataError,
    AbiValueError,
    ABIValueError,
    ContractCallRegistry,
    DecodedContractCall,
    EncodedContractCall,
    FunctionArgument,
    FunctionDefinition,
    decode_contract_call,
    encode_contract_call,
    parse_contract_call_registry,
)

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def addr_word(addr: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(addr[2:])


ADDR_A = "0x" + "11" * 20

TRANSFER_SELECTOR = bytes.fromhex("a9059cbb")
BALANCE_OF_SELECTOR = bytes.fromhex("70a08231")

TRANSFER = {
    "type": "function",
    "name": "transfer",
    "inputs": [
        {"name": "to", "type": "address"},
        {"name": "amount", "type": "uint256"},
    ],
    "outputs": [{"name": "", "type": "bool"}],
}
BALANCE_OF = {
    "type": "function",
    "name": "balanceOf",
    "inputs": [{"name": "owner", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
}
OVERLOAD_A = {
    "type": "function",
    "name": "foo",
    "inputs": [{"name": "x", "type": "uint256"}],
}
OVERLOAD_B = {
    "type": "function",
    "name": "foo",
    "inputs": [{"name": "x", "type": "address"}],
}
RECEIVE = {"type": "receive"}
FALLBACK = {"type": "fallback"}
EVENT = {
    "type": "event",
    "name": "Transfer",
    "inputs": [{"name": "v", "type": "uint256", "indexed": False}],
}
CONSTRUCTOR = {"type": "constructor", "inputs": []}
ERROR = {"type": "error", "name": "Denied", "inputs": []}


def transfer_calldata(to: str, amount: int) -> bytes:
    return TRANSFER_SELECTOR + addr_word(to) + word(amount)


class RegistryTest(unittest.TestCase):
    def test_functions_receive_fallback_registered(self):
        registry = parse_contract_call_registry(
            [TRANSFER, BALANCE_OF, RECEIVE, FALLBACK, EVENT, CONSTRUCTOR, ERROR]
        )
        self.assertIsInstance(registry, ContractCallRegistry)
        self.assertEqual(len(registry), 2)
        self.assertEqual(
            [f.name for f in registry.functions], ["transfer", "balanceOf"]
        )
        self.assertTrue(registry.receive)
        self.assertTrue(registry.fallback)
        # outputs 语义保留。
        self.assertEqual(len(registry.functions[0].outputs), 1)

    def test_accepts_json_string(self):
        registry = parse_contract_call_registry(json.dumps([TRANSFER, RECEIVE]))
        self.assertEqual(len(registry.functions), 1)
        self.assertTrue(registry.receive)
        self.assertFalse(registry.fallback)

    def test_empty_abi(self):
        registry = parse_contract_call_registry("[]")
        self.assertEqual(registry.functions, ())
        self.assertFalse(registry.receive)
        self.assertFalse(registry.fallback)

    def test_unknown_and_malformed_entries_skipped(self):
        registry = parse_contract_call_registry(
            [TRANSFER, {"type": "event"}, {"type": "constructor"},
             {"type": "error"}, {"type": "unknown"}, {"name": "x"}, 42, "s"]
        )
        self.assertEqual(len(registry.functions), 1)

    def test_bad_root(self):
        for bad in ("{}", "not json", 42, None):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry(bad)
            self.assertEqual(ctx.exception.code, "CALL_ENTRY_INVALID")

    def test_invalid_function_entry(self):
        for entry in (
            {"type": "function", "name": "", "inputs": []},
            {"type": "function", "name": "f"},
            {"type": "function", "name": "f", "inputs": [{"type": "uint7"}]},
        ):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry([entry])
            self.assertEqual(ctx.exception.code, "CALL_ENTRY_INVALID")

    def test_duplicate_function_signature(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            parse_contract_call_registry([TRANSFER, dict(TRANSFER)])
        self.assertEqual(ctx.exception.code, "CALL_ENTRY_INVALID")

    def test_duplicate_receive_fallback(self):
        for entry in (RECEIVE, FALLBACK):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry([entry, dict(entry)])
            self.assertEqual(ctx.exception.code, "CALL_ENTRY_INVALID")

    def test_receive_fallback_reject_inputs(self):
        for entry in (
            {"type": "receive", "inputs": [{"name": "x", "type": "uint256"}]},
            {"type": "fallback", "inputs": [{"name": "x", "type": "uint256"}]},
            {"type": "receive", "inputs": "nope"},
        ):
            with self.assertRaises(AbiContractCallError) as ctx:
                parse_contract_call_registry([entry])
            self.assertEqual(ctx.exception.code, "CALL_ENTRY_INVALID")
        # 空 inputs 数组视为无 inputs。
        registry = parse_contract_call_registry(
            [{"type": "receive", "inputs": []}, {"type": "fallback", "inputs": []}]
        )
        self.assertTrue(registry.receive)
        self.assertTrue(registry.fallback)


class EncodeTest(unittest.TestCase):
    def test_encode_function_by_name(self):
        result = encode_contract_call(
            [TRANSFER, RECEIVE], "transfer", [ADDR_A, 5]
        )
        expected = transfer_calldata(ADDR_A, 5)
        self.assertIsInstance(result, EncodedContractCall)
        self.assertEqual(result, expected)
        self.assertEqual(result.calldata, expected)
        self.assertEqual(result.calldata_hex, "0x" + expected.hex())
        self.assertEqual(result.kind, "function")
        self.assertIsInstance(result.function, FunctionDefinition)
        self.assertEqual(result.function.name, "transfer")
        self.assertEqual(result.args, (ADDR_A, 5))
        self.assertEqual(result.data, expected[4:])
        self.assertEqual(result.selector, TRANSFER_SELECTOR)

    def test_encode_function_by_signature(self):
        abi = [OVERLOAD_A, OVERLOAD_B]
        result = encode_contract_call(abi, "foo(address)", [ADDR_A])
        self.assertEqual(result.args, (ADDR_A,))
        result2 = encode_contract_call(abi, "foo(uint256)", [7])
        self.assertEqual(result2.args, (7,))
        self.assertNotEqual(result[:4], result2[:4])

    def test_encode_accepts_json_string_and_registry(self):
        abi_json = json.dumps([TRANSFER])
        expected = transfer_calldata(ADDR_A, 1)
        self.assertEqual(
            encode_contract_call(abi_json, "transfer", [ADDR_A, 1]), expected
        )
        registry = parse_contract_call_registry([TRANSFER])
        self.assertEqual(
            encode_contract_call(registry, "transfer", [ADDR_A, 1]), expected
        )

    def test_encode_no_args_function(self):
        abi = [{"type": "function", "name": "ping", "inputs": []}]
        result = encode_contract_call(abi, "ping")
        self.assertEqual(len(result), 4)

    def test_encode_target_not_found(self):
        abi = [TRANSFER]
        for target in ("missing", "transfer(address)", "receive", "fallback",
                       "", None, 42):
            with self.assertRaises(AbiContractCallError) as ctx:
                encode_contract_call(abi, target)
            self.assertEqual(ctx.exception.code, "CALL_TARGET_NOT_FOUND")

    def test_encode_ambiguous_name(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call([OVERLOAD_A, OVERLOAD_B], "foo", [1])
        self.assertEqual(ctx.exception.code, "CALL_TARGET_AMBIGUOUS")

    def test_encode_arg_value_errors(self):
        with self.assertRaises(ABIValueError):
            encode_contract_call([TRANSFER], "transfer", [ADDR_A])
        with self.assertRaises(ABIValueError):
            encode_contract_call([TRANSFER], "transfer", [ADDR_A, "x"])
        with self.assertRaises(ABIValueError):
            encode_contract_call([TRANSFER], "transfer", "not-a-list")
        # AbiValueError 是同一异常的别名。
        self.assertIs(AbiValueError, ABIValueError)

    def test_encode_function_rejects_data(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call([TRANSFER], "transfer", [ADDR_A, 1], b"\x00")
        self.assertEqual(ctx.exception.code, "CALL_DATA_INVALID")

    def test_encode_receive(self):
        abi = [RECEIVE]
        for args, data in ((None, None), ((), None), ([], b""), (None, "0x")):
            result = encode_contract_call(abi, "receive", args, data)
            self.assertEqual(result, b"")
            self.assertEqual(result.kind, "receive")
            self.assertIsNone(result.function)
            self.assertEqual(result.args, ())
            self.assertEqual(result.data, b"")
            self.assertEqual(result.calldata, b"")
            self.assertIsNone(result.selector)

    def test_encode_receive_nonempty(self):
        abi = [RECEIVE]
        for args, data in (([1], None), (None, b"\x00"), ([1], "0x00")):
            with self.assertRaises(AbiContractCallError) as ctx:
                encode_contract_call(abi, "receive", args, data)
            self.assertEqual(ctx.exception.code, "CALL_RECEIVE_NONEMPTY")

    def test_encode_fallback(self):
        abi = [FALLBACK]
        result = encode_contract_call(abi, "fallback", data=b"\x01\x02")
        self.assertEqual(result, b"\x01\x02")
        self.assertEqual(result.kind, "fallback")
        self.assertIsNone(result.function)
        self.assertEqual(result.data, b"\x01\x02")
        self.assertEqual(result.calldata, b"\x01\x02")
        # 十六进制字符串与空 data。
        self.assertEqual(encode_contract_call(abi, "fallback", data="0xdead"),
                         b"\xde\xad")
        self.assertEqual(encode_contract_call(abi, "fallback"), b"")

    def test_encode_fallback_with_args(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            encode_contract_call([FALLBACK], "fallback", [1])
        self.assertEqual(ctx.exception.code, "CALL_FALLBACK_ARGS")

    def test_encode_bad_data(self):
        for data in ("0x0", "0xzz", 42):
            with self.assertRaises(AbiContractCallError) as ctx:
                encode_contract_call([FALLBACK], "fallback", data=data)
            self.assertEqual(ctx.exception.code, "CALL_DATA_INVALID")


class DecodeTest(unittest.TestCase):
    def test_decode_function(self):
        abi = [TRANSFER, BALANCE_OF, RECEIVE, FALLBACK]
        raw = transfer_calldata(ADDR_A, 5)
        result = decode_contract_call(abi, raw)
        self.assertIsInstance(result, DecodedContractCall)
        self.assertEqual(result.kind, "function")
        self.assertEqual(result.function.name, "transfer")
        self.assertEqual(result.calldata, raw)
        self.assertEqual(result.data, raw[4:])
        self.assertEqual(result.selector, TRANSFER_SELECTOR)
        self.assertEqual(len(result.args), 2)
        self.assertTrue(
            all(isinstance(a, FunctionArgument) for a in result.args)
        )
        self.assertEqual(result.args[0].name, "to")
        self.assertEqual(result.args[0].type, "address")
        self.assertEqual(result.args[0].value, ADDR_A)
        self.assertEqual(result.args[1].name, "amount")
        self.assertEqual(result.args[1].type, "uint256")
        self.assertEqual(result.args[1].value, 5)
        self.assertEqual(result.values, (ADDR_A, 5))

    def test_decode_function_hex_string(self):
        raw = transfer_calldata(ADDR_A, 5)
        result = decode_contract_call([TRANSFER], "0x" + raw.hex())
        self.assertEqual(result.values, (ADDR_A, 5))

    def test_decode_roundtrip(self):
        abi = [TRANSFER, BALANCE_OF]
        encoded = encode_contract_call(abi, "balanceOf", [ADDR_A])
        decoded = decode_contract_call(abi, encoded)
        self.assertEqual(decoded.kind, "function")
        self.assertEqual(decoded.function.name, "balanceOf")
        self.assertEqual(decoded.values, (ADDR_A,))

    def test_decode_empty_prefers_receive(self):
        result = decode_contract_call([RECEIVE, FALLBACK], b"")
        self.assertEqual(result.kind, "receive")
        self.assertIsNone(result.function)
        self.assertEqual(result.args, ())
        self.assertEqual(result.data, b"")
        self.assertEqual(result.calldata, b"")
        # "0x" 与 "" 同样是空 calldata。
        self.assertEqual(decode_contract_call([RECEIVE], "0x").kind, "receive")
        self.assertEqual(decode_contract_call([RECEIVE], "").kind, "receive")

    def test_decode_empty_falls_back(self):
        result = decode_contract_call([FALLBACK], b"")
        self.assertEqual(result.kind, "fallback")
        self.assertEqual(result.data, b"")

    def test_decode_empty_no_target(self):
        for abi in ([], [TRANSFER]):
            with self.assertRaises(AbiContractCallError) as ctx:
                decode_contract_call(abi, b"")
            self.assertEqual(ctx.exception.code, "CALL_TARGET_NOT_FOUND")

    def test_decode_unknown_selector_with_fallback(self):
        raw = b"\xde\xad\xbe\xef" + word(1)
        result = decode_contract_call([TRANSFER, FALLBACK], raw)
        self.assertEqual(result.kind, "fallback")
        self.assertIsNone(result.function)
        self.assertEqual(result.args, ())
        self.assertEqual(result.data, raw)
        self.assertEqual(result.calldata, raw)

    def test_decode_unknown_selector_without_fallback(self):
        with self.assertRaises(AbiContractCallError) as ctx:
            decode_contract_call([TRANSFER], b"\xde\xad\xbe\xef" + word(1))
        self.assertEqual(ctx.exception.code, "CALL_TARGET_NOT_FOUND")

    def test_decode_short_calldata(self):
        raw = b"\x01\x02"
        result = decode_contract_call([FALLBACK], raw)
        self.assertEqual(result.kind, "fallback")
        self.assertEqual(result.data, raw)
        # receive 只接空 calldata，短 calldata 无 fallback 时报未找到。
        with self.assertRaises(AbiContractCallError) as ctx:
            decode_contract_call([RECEIVE], raw)
        self.assertEqual(ctx.exception.code, "CALL_TARGET_NOT_FOUND")

    def test_decode_trailing_data(self):
        raw = transfer_calldata(ADDR_A, 5) + word(9)
        with self.assertRaises(AbiTrailingDataError):
            decode_contract_call([TRANSFER], raw)

    def test_decode_bad_payload(self):
        # 参数区少一个 word，无法严格解码。
        raw = TRANSFER_SELECTOR + addr_word(ADDR_A)
        with self.assertRaises(ABIValueError):
            decode_contract_call([TRANSFER], raw)

    def test_decode_bad_calldata(self):
        for bad in ("0x0", "0xzz", 42, None):
            with self.assertRaises(AbiContractCallError) as ctx:
                decode_contract_call([TRANSFER], bad)
            self.assertEqual(ctx.exception.code, "CALL_DATA_INVALID")

    def test_decode_accepts_registry(self):
        registry = parse_contract_call_registry([TRANSFER])
        result = decode_contract_call(registry, transfer_calldata(ADDR_A, 2))
        self.assertEqual(result.values, (ADDR_A, 2))


class ErrorCodeTest(unittest.TestCase):
    def test_codes_unique_and_declared(self):
        self.assertEqual(
            len(AbiContractCallError.CODES), len(set(AbiContractCallError.CODES))
        )
        self.assertEqual(len(AbiContractCallError.CODES), 6)

    def test_is_value_error(self):
        try:
            parse_contract_call_registry(42)
        except AbiContractCallError as exc:
            self.assertIsInstance(exc, ValueError)
            self.assertIn(exc.code, str(exc))


if __name__ == "__main__":
    unittest.main()
