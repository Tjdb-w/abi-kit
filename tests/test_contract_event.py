"""parse_event_registry / decode_contract_event_log /
decode_contract_event_logs 与 AbiLogDispatchError 的行为测试。

仅使用标准库 unittest，无第三方依赖。日志向量由既有 encode_event_log
生成，保证与单事件入口口径一致。
"""

import json
import unittest

from abi_kit import (
    AbiEventError,
    AbiLogDispatchError,
    DecodedContractEventLog,
    EventRegistry,
    EventDefinition,
    decode_contract_event_log,
    decode_contract_event_logs,
    encode_event_log,
    event_topic0,
    parse_event_abi,
    parse_event_registry,
)

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def addr_word(addr: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(addr[2:])


ADDR_A = "0x" + "11" * 20
ADDR_B = "0x" + "22" * 20

TRANSFER = {
    "type": "event",
    "name": "Transfer",
    "inputs": [
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to", "type": "address"},
        {"indexed": False, "name": "value", "type": "uint256"},
    ],
}
APPROVAL = {
    "type": "event",
    "name": "Approval",
    "inputs": [
        {"indexed": True, "name": "owner", "type": "address"},
        {"indexed": True, "name": "spender", "type": "address"},
        {"indexed": False, "name": "value", "type": "uint256"},
    ],
}
PING_UINT = {
    "type": "event",
    "name": "Ping",
    "inputs": [{"indexed": True, "name": "n", "type": "uint256"}],
}
PING_ADDR = {
    "type": "event",
    "name": "Ping",
    "inputs": [{"indexed": True, "name": "a", "type": "address"}],
}
ANON = {
    "type": "event",
    "name": "Anon",
    "anonymous": True,
    "inputs": [
        {"indexed": True, "name": "a", "type": "address"},
        {"indexed": False, "name": "v", "type": "uint256"},
    ],
}
TUPLE_EVENT = {
    "type": "event",
    "name": "Structured",
    "inputs": [
        {
            "indexed": False,
            "name": "p",
            "type": "tuple",
            "components": [
                {"name": "to", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
        },
        {"indexed": True, "name": "tag", "type": "bytes32"},
        {"indexed": True, "name": "note", "type": "string"},
    ],
}


def abi(*entries):
    return list(entries)


def encoded(event_json, values):
    return encode_event_log(parse_event_abi(event_json), values)


def make_log(event_json, values, *, event=None):
    enc = encoded(event_json, values)
    log = {"topics": enc.topics, "data": enc.data}
    if event is not None:
        log["event"] = event
    return log, enc


FULL_ABI = abi(
    TRANSFER, APPROVAL, PING_UINT, ANON,
    {"type": "function", "name": "transfer", "inputs": [], "outputs": []},
    {"type": "constructor", "inputs": []},
    {"type": "error", "name": "Boom", "inputs": []},
    {"type": "receive", "stateMutability": "payable"},
    {"notatype": True},
)


class ParseEventRegistryTests(unittest.TestCase):
    def test_keeps_only_events_in_abi_order(self):
        reg = parse_event_registry(FULL_ABI)
        self.assertIsInstance(reg, EventRegistry)
        self.assertEqual(
            [e.name for e in reg.events],
            ["Transfer", "Approval", "Ping", "Anon"],
        )
        self.assertTrue(all(isinstance(e, EventDefinition) for e in reg.events))

    def test_accepts_json_string(self):
        reg = parse_event_registry(json.dumps(FULL_ABI))
        self.assertEqual([e.name for e in reg.events],
                         ["Transfer", "Approval", "Ping", "Anon"])

    def test_accepts_tuple_and_empty(self):
        self.assertEqual(parse_event_registry(()).events, ())
        reg = parse_event_registry(abi(TRANSFER, TUPLE_EVENT))
        self.assertEqual(
            [e.name for e in reg.events], ["Transfer", "Structured"]
        )

    def test_skips_unknown_typed_non_event_entries(self):
        reg = parse_event_registry(
            [
                {"type": "event", "name": "E", "inputs": []},
                {"type": "bogus", "name": "X"},
                {"name": "NoType"},
            ]
        )
        self.assertEqual([e.name for e in reg.events], ["E"])

    def test_registry_is_immutable(self):
        reg = parse_event_registry(FULL_ABI)
        with self.assertRaises(Exception):
            reg.events = ()

    def test_duplicate_canonical_signature_rejected(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            parse_event_registry(abi(TRANSFER, dict(TRANSFER)))
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_overloads_with_distinct_signatures_allowed(self):
        reg = parse_event_registry(abi(PING_UINT, PING_ADDR))
        self.assertEqual(len(reg.events), 2)
        self.assertEqual([e.name for e in reg.events], ["Ping", "Ping"])

    def test_duplicate_anonymous_signature_rejected(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            parse_event_registry(abi(ANON, dict(ANON)))
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def _abi_invalid(self, raw):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            parse_event_registry(raw)
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_invalid_root(self):
        self._abi_invalid({"type": "event"})
        self._abi_invalid(None)
        self._abi_invalid(42)
        self._abi_invalid("{not json")
        self._abi_invalid("{}")
        self._abi_invalid("42")

    def test_invalid_entries(self):
        self._abi_invalid([42])
        self._abi_invalid(["event"])
        self._abi_invalid([None])

    def test_invalid_event_entry(self):
        # event 条目的全部既有校验保持有效，仅错误码转译。
        self._abi_invalid([{"type": "event", "inputs": []}])
        self._abi_invalid([{"type": "event", "name": "9x", "inputs": []}])
        self._abi_invalid(
            [{"type": "event", "name": "E",
              "inputs": [{"type": "uint"}]}]
        )
        self._abi_invalid(
            [{"type": "event", "name": "E",
              "inputs": [{"type": "tuple"}]}]
        )

    def test_single_event_error_does_not_leak(self):
        # 非法 event（缺 name）必须转译为 LOG_ABI_INVALID。
        with self.assertRaises(AbiLogDispatchError):
            parse_event_registry(
                [{"type": "event", "inputs": []}]
            )
        try:
            parse_event_registry(
                [{"type": "event", "name": "E",
                  "inputs": [{"type": "bad"}]}]
            )
        except AbiLogDispatchError as exc:
            self.assertEqual(exc.code, "LOG_ABI_INVALID")
        except AbiEventError as exc:
            raise AssertionError(f"事件层异常泄漏：{exc!r}")
        else:
            raise AssertionError("非法 event 条目未抛错")


class ImplicitDispatchTests(unittest.TestCase):
    def setUp(self):
        self.reg = parse_event_registry(FULL_ABI)

    def test_dispatch_by_topic0_bytes(self):
        log, enc = make_log(TRANSFER, [ADDR_A, ADDR_B, 1000])
        result = decode_contract_event_log(self.reg, log)
        self._check_transfer(result, enc, 1000)

    def test_dispatch_by_topic0_hex_strings(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 7])
        log = {"topics": list(enc.topics_hex), "data": enc.data_hex}
        result = decode_contract_event_log(self.reg, log)
        self._check_transfer(result, enc, 7)

    def _check_transfer(self, result, enc, value):
        self.assertIsInstance(result, DecodedContractEventLog)
        self.assertEqual(result.event.name, "Transfer")
        self.assertEqual(result.event_name, "Transfer")
        self.assertEqual(
            result.signature, "Transfer(address,address,uint256)"
        )
        self.assertEqual(result.topics, enc.topics)
        self.assertIsInstance(result.topics, tuple)
        self.assertTrue(all(isinstance(t, bytes) for t in result.topics))
        self.assertEqual(result.data, enc.data)
        self.assertIsInstance(result.data, bytes)
        self.assertEqual(result.values, (ADDR_A, ADDR_B, value))
        self.assertEqual(result.topics_hex, enc.topics_hex)
        self.assertEqual(result.data_hex, enc.data_hex)

    def test_dispatch_picks_correct_event_among_many(self):
        log, _ = make_log(APPROVAL, [ADDR_A, ADDR_B, 5])
        result = decode_contract_event_log(self.reg, log)
        self.assertEqual(result.event.name, "Approval")
        self.assertEqual(result.values, (ADDR_A, ADDR_B, 5))

    def test_tuple_and_irreversible_indexed_values(self):
        tag = b"\x42" * 32
        from abi_kit._keccak import keccak_256

        note_topic = keccak_256(b"memo")
        log, _ = make_log(
            TUPLE_EVENT, ((ADDR_A, 9), tag, "memo"),
        )
        result = decode_contract_event_log(
            parse_event_registry(abi(TUPLE_EVENT)), log
        )
        # 非 indexed tuple 严格还原；indexed string 只得 32 字节 topic。
        self.assertEqual(result.values, ((ADDR_A, 9), tag, note_topic))
        self.assertIsInstance(result.values[2], bytes)

    def test_raw_abi_accepted_in_place_of_registry(self):
        log, _ = make_log(PING_UINT, [3])
        result = decode_contract_event_log(FULL_ABI, log)
        self.assertEqual(result.event.name, "Ping")

    def test_result_immutable(self):
        log, _ = make_log(PING_UINT, [1])
        result = decode_contract_event_log(self.reg, log)
        with self.assertRaises(Exception):
            result.values = ()
        with self.assertRaises(Exception):
            result.data = b""

    def test_unknown_topic0_not_found(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(
                self.reg, {"topics": [b"\x00" * 32], "data": b""}
            )
        self.assertEqual(ctx.exception.code, "LOG_EVENT_REQUIRED")

    def test_unknown_topic0_without_anonymous_event(self):
        reg = parse_event_registry(abi(TRANSFER, APPROVAL))
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(
                reg, {"topics": [b"\x00" * 32], "data": b""}
            )
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")


class ExplicitDispatchTests(unittest.TestCase):
    def setUp(self):
        self.reg = parse_event_registry(FULL_ABI)

    def test_explicit_unique_name(self):
        log, _ = make_log(TRANSFER, [ADDR_A, ADDR_B, 2])
        log["event"] = "Transfer"
        result = decode_contract_event_log(self.reg, log)
        self.assertEqual(result.event.name, "Transfer")

    def test_explicit_canonical_signature(self):
        log, _ = make_log(PING_UINT, [4])
        log["event"] = "Ping(uint256)"
        result = decode_contract_event_log(self.reg, log)
        self.assertEqual(result.signature, "Ping(uint256)")
        self.assertEqual(result.values, (4,))

    def test_explicit_signature_resolves_overload(self):
        reg = parse_event_registry(abi(PING_UINT, PING_ADDR))
        log, _ = make_log(PING_ADDR, [ADDR_A])
        log["event"] = "Ping(address)"
        result = decode_contract_event_log(reg, log)
        self.assertEqual(result.values, (ADDR_A,))

    def test_explicit_anonymous_event_required_and_works(self):
        enc = encoded(ANON, [ADDR_A, 11])
        log = {"topics": enc.topics, "data": enc.data, "event": "Anon"}
        result = decode_contract_event_log(self.reg, log)
        self.assertEqual(result.event.name, "Anon")
        self.assertEqual(result.topics, enc.topics)
        self.assertEqual(result.values, (ADDR_A, 11))

    def test_explicit_anonymous_via_signature(self):
        enc = encoded(ANON, [ADDR_A, 1])
        log = {
            "topics": enc.topics,
            "data": enc.data,
            "event": "Anon(address,uint256)",
        }
        result = decode_contract_event_log(self.reg, log)
        self.assertEqual(result.event.anonymous, True)

    def test_explicit_non_anonymous_name_ambiguous(self):
        reg = parse_event_registry(abi(PING_UINT, PING_ADDR))
        enc = encoded(PING_UINT, [1])
        log = {"topics": enc.topics, "data": b"", "event": "Ping"}
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(reg, log)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_AMBIGUOUS")

    def test_explicit_event_name_not_found(self):
        log, _ = make_log(PING_UINT, [1])
        log["event"] = "Nope"
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

    def test_explicit_signature_not_found(self):
        log, _ = make_log(PING_UINT, [1])
        log["event"] = "Ping(bytes32)"
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

    def test_explicit_event_topic0_mismatch(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 1])
        # topics[0] 是 Transfer 的 topic0，却显式选择 Approval。
        log = {"topics": enc.topics, "data": enc.data,
               "event": "Approval(address,address,uint256)"}
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_TOPIC_MISMATCH")

    def test_explicit_wrong_unique_name_mismatch(self):
        log, _ = make_log(TRANSFER, [ADDR_A, ADDR_B, 1])
        log["event"] = "Approval"
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_TOPIC_MISMATCH")

    def test_explicit_non_anonymous_with_empty_topics(self):
        log = {"topics": [], "data": b"", "event": "Ping(uint256)"}
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_explicit_event_field_invalid(self):
        log, _ = make_log(PING_UINT, [1])
        for bad in (123, b"Ping", "", True):
            log["event"] = bad
            with self.subTest(bad=bad):
                with self.assertRaises(AbiLogDispatchError) as ctx:
                    decode_contract_event_log(self.reg, log)
                self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_explicit_event_name_bad_identifier(self):
        log, _ = make_log(PING_UINT, [1])
        log["event"] = "9x"
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_explicit_signature_with_malformed_paren(self):
        # 含括号即按规范签名查找；不存在则 NOT_FOUND（而非 ENTRY_INVALID）。
        log, _ = make_log(PING_UINT, [1])
        log["event"] = "Ping(uint256"
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")


class AnonymousImplicitTests(unittest.TestCase):
    def setUp(self):
        self.reg = parse_event_registry(FULL_ABI)

    def test_anonymous_without_event_required(self):
        enc = encoded(ANON, [ADDR_A, 1])
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(
                self.reg, {"topics": enc.topics, "data": enc.data}
            )
        self.assertEqual(ctx.exception.code, "LOG_EVENT_REQUIRED")

    def test_empty_topics_with_anonymous_event_required(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, {"topics": [], "data": b""})
        self.assertEqual(ctx.exception.code, "LOG_EVENT_REQUIRED")

    def test_empty_topics_without_anonymous_event_not_found(self):
        reg = parse_event_registry(abi(TRANSFER))
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(reg, {"topics": [], "data": b""})
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

    def test_anonymous_explicit_does_not_check_topic0(self):
        # 匿名事件显式选择时，topics[0] 是 indexed 值，不做 topic0 校验，
        # 即使它恰好长得像某个非匿名事件的 topic0。
        enc = encoded(ANON, [ADDR_A, 1])
        log = {"topics": enc.topics, "data": enc.data, "event": "Anon"}
        result = decode_contract_event_log(self.reg, log)
        self.assertEqual(result.event.anonymous, True)


class EntryShapeTests(unittest.TestCase):
    def setUp(self):
        self.reg = parse_event_registry(FULL_ABI)

    def _entry_invalid(self, log):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_log_must_be_mapping(self):
        for bad in ([], "x", 42, None, (), [{"topics": [], "data": b""}]):
            with self.subTest(bad=bad):
                self._entry_invalid(bad)

    def test_missing_required_fields(self):
        good_topic = bytes.fromhex(event_topic0(
            parse_event_abi(PING_UINT))[2:])
        self._entry_invalid({"data": b""})
        self._entry_invalid({"topics": [good_topic]})
        self._entry_invalid({})

    def test_topics_container_shape(self):
        good_topic = bytes.fromhex(event_topic0(
            parse_event_abi(TRANSFER))[2:])
        self._entry_invalid({"topics": b"\x00" * 32, "data": b""})
        self._entry_invalid({"topics": "0x" + "ab" * 32, "data": b""})
        self._entry_invalid({"topics": None, "data": b""})
        self._entry_invalid({"topics": 7, "data": b""})
        self._entry_invalid({"topics": [b"\x00" * 31], "data": b""})
        self._entry_invalid({"topics": ["0xzz"], "data": b""})
        self._entry_invalid({"topics": [123], "data": b""})
        self._entry_invalid(
            {"topics": [good_topic, 9], "data": b"", "event": "Transfer"}
        )

    def test_data_shape(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 1])
        base = {"topics": list(enc.topics)}
        self._entry_invalid({**base, "data": "0xabc"})
        self._entry_invalid({**base, "data": "zz"})
        self._entry_invalid({**base, "data": 7})
        self._entry_invalid({**base, "data": None})

    def test_topic0_mismatch_takes_precedence_over_bad_data(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 1])
        log = {
            "topics": [b"\x00" * 32] + list(enc.topics[1:]),
            "data": "0xabc",
        }
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        # 注册表含匿名事件，topic0 未命中 -> REQUIRED，优先于 data 非法。
        self.assertEqual(ctx.exception.code, "LOG_EVENT_REQUIRED")

    def test_extra_fields_ignored(self):
        log, _ = make_log(PING_UINT, [1])
        log["address"] = "0x" + "ab" * 20
        log["blockNumber"] = 100
        result = decode_contract_event_log(self.reg, log)
        self.assertEqual(result.values, (1,))


class PostDispatchStrictnessTests(unittest.TestCase):
    def setUp(self):
        self.reg = parse_event_registry(FULL_ABI)

    def _entry_invalid(self, log):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.reg, log)
        self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_topic_count_mismatch(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 1])
        # 少一个 indexed topic。
        self._entry_invalid(
            {"topics": [enc.topics[0], enc.topics[1]], "data": enc.data}
        )
        # 多一个 topic。
        self._entry_invalid(
            {"topics": list(enc.topics) + [b"\x00" * 32],
             "data": enc.data}
        )

    def test_indexed_value_strict_decode_failure(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 1])
        # address indexed topic 高位 12 字节非零。
        bad_topics = [enc.topics[0], b"\x01" + b"\x00" * 31, enc.topics[2]]
        self._entry_invalid({"topics": bad_topics, "data": enc.data})

    def test_data_decoding_failure(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 1])
        self._entry_invalid({"topics": enc.topics, "data": b"\x00" * 31})
        self._entry_invalid({"topics": enc.topics, "data": b"\x00" * 64})

    def test_anonymous_post_dispatch_count_failure(self):
        enc = encoded(ANON, [ADDR_A, 1])
        log = {"topics": list(enc.topics) + [b"\x00" * 32],
               "data": enc.data, "event": "Anon"}
        self._entry_invalid(log)

    def test_no_event_layer_error_leaks(self):
        enc = encoded(TRANSFER, [ADDR_A, ADDR_B, 1])
        try:
            decode_contract_event_log(
                self.reg,
                {"topics": [enc.topics[0], enc.topics[1]],
                 "data": enc.data},
            )
        except AbiLogDispatchError as exc:
            self.assertEqual(exc.code, "LOG_ENTRY_INVALID")
        except AbiEventError as exc:
            raise AssertionError(f"事件层异常泄漏：{exc!r}")
        else:
            raise AssertionError("非法日志未抛错")


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.reg = parse_event_registry(FULL_ABI)

    def test_batch_preserves_order_and_tuple(self):
        l1, _ = make_log(TRANSFER, [ADDR_A, ADDR_B, 1])
        l2, _ = make_log(APPROVAL, [ADDR_A, ADDR_B, 2])
        l3, _ = make_log(PING_UINT, [3])
        results = decode_contract_event_logs(self.reg, [l1, l2, l3])
        self.assertIsInstance(results, tuple)
        self.assertEqual(len(results), 3)
        self.assertEqual(
            [r.event_name for r in results],
            ["Transfer", "Approval", "Ping"],
        )
        self.assertEqual(results[0].values, (ADDR_A, ADDR_B, 1))
        self.assertEqual(results[2].values, (3,))

    def test_empty_batch(self):
        self.assertEqual(decode_contract_event_logs(self.reg, []), ())
        self.assertEqual(decode_contract_event_logs(self.reg, ()), ())

    def test_batch_accepts_tuple_input(self):
        l1, _ = make_log(PING_UINT, [9])
        results = decode_contract_event_logs(self.reg, (l1,))
        self.assertEqual(results[0].values, (9,))

    def test_batch_rejects_non_sequence(self):
        for bad in ("x", b"", None, 7, {"topics": [], "data": b""}):
            with self.subTest(bad=bad):
                with self.assertRaises(AbiLogDispatchError) as ctx:
                    decode_contract_event_logs(self.reg, bad)
                self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_batch_failure_is_atomic_and_indexed(self):
        l1, _ = make_log(TRANSFER, [ADDR_A, ADDR_B, 1])
        bad = {"topics": [b"\x00" * 32], "data": b""}
        l3, _ = make_log(PING_UINT, [3])
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_logs(self.reg, [l1, bad, l3])
        self.assertEqual(ctx.exception.code, "LOG_EVENT_REQUIRED")
        self.assertIn("第 1 条", str(ctx.exception))

    def test_batch_first_failure_surfaces(self):
        good, _ = make_log(PING_UINT, [1])
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_logs(self.reg, [good, {"data": b""}])
        self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")
        self.assertIn("第 1 条", str(ctx.exception))

    def test_batch_parses_raw_abi_once_semantics(self):
        l1, _ = make_log(PING_UINT, [1])
        results = decode_contract_event_logs(FULL_ABI, [l1])
        self.assertEqual(results[0].event.name, "Ping")


class ExceptionContractTests(unittest.TestCase):
    def test_codes_and_hierarchy(self):
        self.assertTrue(issubclass(AbiLogDispatchError, ValueError))
        self.assertFalse(issubclass(AbiLogDispatchError, AbiEventError))
        self.assertEqual(
            set(AbiLogDispatchError.CODES),
            {
                "LOG_ABI_INVALID",
                "LOG_ENTRY_INVALID",
                "LOG_EVENT_NOT_FOUND",
                "LOG_EVENT_AMBIGUOUS",
                "LOG_EVENT_REQUIRED",
                "LOG_TOPIC_MISMATCH",
            },
        )
        with self.assertRaises(ValueError):
            AbiLogDispatchError("NOT_A_CODE", "x")

    def test_message_carried_separately(self):
        err = AbiLogDispatchError("LOG_EVENT_NOT_FOUND", "detail here")
        self.assertEqual(err.code, "LOG_EVENT_NOT_FOUND")
        self.assertEqual(err.message, "detail here")
        self.assertIn("LOG_EVENT_NOT_FOUND", str(err))
        self.assertIn("detail here", str(err))


class RegistryReuseTests(unittest.TestCase):
    def test_same_registry_object_reused_for_many_logs(self):
        reg = parse_event_registry(FULL_ABI)
        logs = [make_log(PING_UINT, [i])[0] for i in range(5)]
        results = decode_contract_event_logs(reg, logs)
        self.assertEqual([r.values[0] for r in results], [0, 1, 2, 3, 4])

    def test_invalid_registry_argument(self):
        log = {"topics": [], "data": b""}
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(42, log)
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_logs(None, [])
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")


if __name__ == "__main__":
    unittest.main()
