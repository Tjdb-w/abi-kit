"""parse_event_registry / decode_contract_event_log /
decode_contract_event_logs 与 AbiLogDispatchError 的行为测试。

仅使用标准库 unittest，无第三方依赖。topic0 向量与 test_event.py 一致
（Transfer / Approval 取自链上常见事件）。
"""

import json
import unittest

from abi_kit import (
    AbiEventError,
    AbiLogDispatchError,
    EventDefinition,
    EventRegistry,
    decode_contract_event_log,
    decode_contract_event_logs,
    event_topic0,
    parse_event_registry,
)

W = 32


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def addr_word(addr: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(addr[2:])


ADDR_A = "0x" + "11" * 20
ADDR_B = "0x" + "22" * 20

TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
APPROVAL_TOPIC0 = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)

TRANSFER_ENTRY = {
    "type": "event",
    "name": "Transfer",
    "inputs": [
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to", "type": "address"},
        {"indexed": False, "name": "value", "type": "uint256"},
    ],
}
APPROVAL_ENTRY = {
    "type": "event",
    "name": "Approval",
    "inputs": [
        {"indexed": True, "name": "owner", "type": "address"},
        {"indexed": True, "name": "spender", "type": "address"},
        {"indexed": False, "name": "value", "type": "uint256"},
    ],
}
MARKER_ENTRY = {
    "type": "event",
    "name": "Marker",
    "anonymous": True,
    "inputs": [{"indexed": True, "name": "mark", "type": "uint256"}],
}
NAMED_ENTRY = {
    "type": "event",
    "name": "Named",
    "inputs": [
        {"indexed": True, "name": "tag", "type": "string"},
        {"indexed": False, "name": "value", "type": "uint256"},
    ],
}
LOGGED_UINT_ENTRY = {
    "type": "event",
    "name": "Logged",
    "inputs": [{"indexed": False, "name": "n", "type": "uint256"}],
}
LOGGED_ADDR_ENTRY = {
    "type": "event",
    "name": "Logged",
    "inputs": [{"indexed": False, "name": "who", "type": "address"}],
}
TUPLE_ENTRY = {
    "type": "event",
    "name": "Pair",
    "inputs": [
        {
            "indexed": False,
            "name": "pair",
            "type": "tuple",
            "components": [
                {"name": "token", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
        }
    ],
}

FULL_ABI = [
    {"type": "function", "name": "transfer", "inputs": [], "outputs": []},
    {"type": "constructor", "inputs": []},
    TRANSFER_ENTRY,
    APPROVAL_ENTRY,
    MARKER_ENTRY,
    NAMED_ENTRY,
    LOGGED_UINT_ENTRY,
    LOGGED_ADDR_ENTRY,
    TUPLE_ENTRY,
]


def transfer_log(**over):
    log = {
        "topics": [TRANSFER_TOPIC0, addr_word(ADDR_A), addr_word(ADDR_B)],
        "data": word(1000),
    }
    log.update(over)
    return log


class ParseEventRegistryTests(unittest.TestCase):
    def test_accepts_json_string_and_entry_array(self):
        from_json = parse_event_registry(json.dumps(FULL_ABI))
        from_list = parse_event_registry(FULL_ABI)
        self.assertIsInstance(from_json, EventRegistry)
        self.assertEqual(from_json, from_list)
        self.assertEqual(
            [event.name for event in from_list.events],
            ["Transfer", "Approval", "Marker", "Named", "Logged", "Logged", "Pair"],
        )

    def test_skips_non_event_entries(self):
        abi = [
            {"type": "function", "name": "f", "inputs": []},
            {"type": "constructor", "inputs": []},
            {"type": "error", "name": "Bad", "inputs": []},
            {"type": "receive"},
            {"type": "fallback"},
            {"type": "unknown-thing"},
            {"name": "no-type"},
            42,
            "garbage",
            TRANSFER_ENTRY,
        ]
        registry = parse_event_registry(abi)
        self.assertEqual([event.name for event in registry.events], ["Transfer"])

    def test_empty_registry(self):
        registry = parse_event_registry([])
        self.assertEqual(registry.events, ())
        self.assertEqual(len(registry), 0)

    def test_registry_is_immutable(self):
        registry = parse_event_registry([TRANSFER_ENTRY])
        with self.assertRaises(Exception):
            registry.events = ()

    def test_event_metadata_preserved(self):
        registry = parse_event_registry([MARKER_ENTRY, TUPLE_ENTRY])
        marker, pair = registry.events
        self.assertTrue(marker.anonymous)
        self.assertTrue(marker.inputs[0].indexed)
        self.assertEqual(pair.inputs[0].name, "pair")
        self.assertEqual(
            event_topic0(registry.events[1]),
            event_topic0(EventDefinition("Pair", pair.inputs)),
        )

    def test_root_must_be_array(self):
        for bad in ("{}", "42", '"text"', {"type": "event"}, 42, None):
            with self.assertRaises(AbiLogDispatchError) as ctx:
                parse_event_registry(bad)
            self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_malformed_json_string(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            parse_event_registry("{not json")
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_invalid_event_entry(self):
        bad_entries = [
            {"type": "event"},
            {"type": "event", "name": "not a name"},
            {"type": "event", "name": "E", "inputs": [{"type": "uint7"}]},
            {"type": "event", "name": "E", "anonymous": "yes"},
        ]
        for entry in bad_entries:
            with self.assertRaises(AbiLogDispatchError) as ctx:
                parse_event_registry([entry])
            self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_duplicate_canonical_signature_rejected(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            parse_event_registry([TRANSFER_ENTRY, TRANSFER_ENTRY])
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_anonymous_flag_does_not_escape_duplicate_check(self):
        twin = dict(TRANSFER_ENTRY, anonymous=True)
        with self.assertRaises(AbiLogDispatchError) as ctx:
            parse_event_registry([TRANSFER_ENTRY, twin])
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_same_name_different_signature_allowed(self):
        registry = parse_event_registry([LOGGED_UINT_ENTRY, LOGGED_ADDR_ENTRY])
        self.assertEqual(len(registry), 2)


class DecodeContractEventLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = parse_event_registry(FULL_ABI)

    def test_dispatch_by_topic0(self):
        event, topics, data, values = decode_contract_event_log(
            self.registry, transfer_log()
        )
        self.assertIsInstance(event, EventDefinition)
        self.assertEqual(event.name, "Transfer")
        self.assertEqual(
            topics,
            (
                bytes.fromhex(TRANSFER_TOPIC0[2:]),
                addr_word(ADDR_A),
                addr_word(ADDR_B),
            ),
        )
        self.assertEqual(data, word(1000))
        self.assertEqual(values, (ADDR_A, ADDR_B, 1000))

    def test_hex_string_topics_and_data(self):
        log = {
            "topics": [
                TRANSFER_TOPIC0,
                "0x" + addr_word(ADDR_A).hex(),
                addr_word(ADDR_B).hex(),  # 无 0x 前缀同样接受
            ],
            "data": "0x" + word(5).hex(),
        }
        _, topics, data, values = decode_contract_event_log(self.registry, log)
        self.assertEqual(topics[1], addr_word(ADDR_A))
        self.assertEqual(data, word(5))
        self.assertEqual(values, (ADDR_A, ADDR_B, 5))

    def test_explicit_event_by_name(self):
        log = transfer_log(event="Transfer")
        event, _, _, values = decode_contract_event_log(self.registry, log)
        self.assertEqual(event.name, "Transfer")
        self.assertEqual(values, (ADDR_A, ADDR_B, 1000))

    def test_explicit_event_by_signature(self):
        log = transfer_log(event="Transfer(address,address,uint256)")
        event, _, _, _ = decode_contract_event_log(self.registry, log)
        self.assertEqual(event.name, "Transfer")

    def test_explicit_event_topic0_mismatch(self):
        log = transfer_log(event="Approval")
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "LOG_TOPIC_MISMATCH")

    def test_explicit_event_empty_topics_mismatch(self):
        log = transfer_log(topics=[], data="0x", event="Transfer")
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "LOG_TOPIC_MISMATCH")

    def test_anonymous_event_requires_explicit_selection(self):
        log = {"topics": [word(7)], "data": b""}
        # 匿名事件没有 topic0，topics 首项匹配不到任何非匿名事件。
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

    def test_anonymous_event_explicit_by_name_and_signature(self):
        for selector in ("Marker", "Marker(uint256)"):
            log = {"topics": [word(7)], "data": "0x", "event": selector}
            event, topics, data, values = decode_contract_event_log(
                self.registry, log
            )
            self.assertEqual(event.name, "Marker")
            self.assertTrue(event.anonymous)
            self.assertEqual(topics, (word(7),))
            self.assertEqual(data, b"")
            self.assertEqual(values, (7,))

    def test_empty_topics_with_anonymous_event_in_registry(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.registry, {"topics": [], "data": "0x"})
        self.assertEqual(ctx.exception.code, "LOG_EVENT_REQUIRED")

    def test_empty_topics_without_anonymous_event(self):
        registry = parse_event_registry([TRANSFER_ENTRY])
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(registry, {"topics": [], "data": b""})
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

    def test_unknown_topic0(self):
        log = transfer_log(topics=[word(1), addr_word(ADDR_A), addr_word(ADDR_B)])
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

    def test_unknown_event_name_and_signature(self):
        for selector in ("Nope", "Nope(uint256)"):
            log = transfer_log(event=selector)
            with self.assertRaises(AbiLogDispatchError) as ctx:
                decode_contract_event_log(self.registry, log)
            self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

    def test_ambiguous_event_name(self):
        log = {"topics": [], "data": "0x", "event": "Logged"}
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_AMBIGUOUS")

    def test_overloaded_event_selected_by_signature(self):
        logged_uint_topic0 = event_topic0(
            parse_event_registry([LOGGED_UINT_ENTRY]).events[0]
        )
        log = {
            "topics": [logged_uint_topic0],
            "data": word(9),
            "event": "Logged(uint256)",
        }
        event, _, _, values = decode_contract_event_log(self.registry, log)
        self.assertEqual(event.name, "Logged")
        self.assertEqual(values, (9,))

    def test_indexed_dynamic_value_yields_topic_bytes(self):
        topic0 = event_topic0(parse_event_registry([NAMED_ENTRY]).events[0])
        tag_topic = bytes.fromhex("ab" * 32)
        log = {"topics": [topic0, tag_topic], "data": word(3)}
        _, _, _, values = decode_contract_event_log(self.registry, log)
        self.assertEqual(values, (tag_topic, 3))

    def test_tuple_event(self):
        topic0 = event_topic0(parse_event_registry([TUPLE_ENTRY]).events[0])
        data = addr_word(ADDR_A) + word(77)
        log = {"topics": [topic0], "data": data}
        _, _, _, values = decode_contract_event_log(self.registry, log)
        self.assertEqual(values, ((ADDR_A, 77),))

    def test_log_must_be_mapping(self):
        for bad in (42, "log", [TRANSFER_TOPIC0], b"", None):
            with self.assertRaises(AbiLogDispatchError) as ctx:
                decode_contract_event_log(self.registry, bad)
            self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_missing_required_fields(self):
        for log in ({}, {"topics": []}, {"data": "0x"}):
            with self.assertRaises(AbiLogDispatchError) as ctx:
                decode_contract_event_log(self.registry, log)
            self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_extra_keys_ignored(self):
        log = transfer_log(address="0x" + "00" * 20, logIndex=0)
        event, _, _, _ = decode_contract_event_log(self.registry, log)
        self.assertEqual(event.name, "Transfer")

    def test_invalid_topics_field(self):
        bad_logs = [
            transfer_log(topics=TRANSFER_TOPIC0),
            transfer_log(topics=[TRANSFER_TOPIC0, b"\x00" * 31, addr_word(ADDR_B)]),
            transfer_log(topics=[TRANSFER_TOPIC0, "0xzz" + "00" * 31, addr_word(ADDR_B)]),
            transfer_log(topics=[TRANSFER_TOPIC0, 7, addr_word(ADDR_B)]),
        ]
        for log in bad_logs:
            with self.assertRaises(AbiLogDispatchError) as ctx:
                decode_contract_event_log(self.registry, log)
            self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_invalid_data_field(self):
        bad_logs = [
            transfer_log(data=7),
            transfer_log(data="0xabc"),
            transfer_log(data="0xzz"),
        ]
        for log in bad_logs:
            with self.assertRaises(AbiLogDispatchError) as ctx:
                decode_contract_event_log(self.registry, log)
            self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_invalid_event_field(self):
        for bad_selector in (7, "", ["Transfer"]):
            log = transfer_log(event=bad_selector)
            with self.assertRaises(AbiLogDispatchError) as ctx:
                decode_contract_event_log(self.registry, log)
            self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_registry_type_checked(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_log([TRANSFER_ENTRY], transfer_log())
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")

    def test_decode_layer_errors_keep_event_codes(self):
        # topics 数量与声明不符：分派成功后仍抛 AbiEventError。
        log = transfer_log(topics=[TRANSFER_TOPIC0, addr_word(ADDR_A)])
        with self.assertRaises(AbiEventError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

        # data 不能按非 indexed 参数严格解码。
        log = transfer_log(data=word(1) + b"\x00")
        with self.assertRaises(AbiEventError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

        # indexed address 高位非零。
        bad_topic = b"\x01" + b"\x00" * 11 + bytes.fromhex(ADDR_A[2:])
        log = transfer_log(topics=[TRANSFER_TOPIC0, bad_topic, addr_word(ADDR_B)])
        with self.assertRaises(AbiEventError) as ctx:
            decode_contract_event_log(self.registry, log)
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")


class DecodeContractEventLogsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = parse_event_registry(FULL_ABI)

    def test_batch_preserves_input_order(self):
        approval_log = {
            "topics": [APPROVAL_TOPIC0, addr_word(ADDR_B), addr_word(ADDR_A)],
            "data": word(4),
        }
        logs = [transfer_log(), approval_log, transfer_log(data=word(6))]
        results = decode_contract_event_logs(self.registry, logs)
        self.assertIsInstance(results, tuple)
        self.assertEqual(len(results), 3)
        self.assertEqual(
            [result[0].name for result in results],
            ["Transfer", "Approval", "Transfer"],
        )
        self.assertEqual(results[0][3], (ADDR_A, ADDR_B, 1000))
        self.assertEqual(results[1][3], (ADDR_B, ADDR_A, 4))
        self.assertEqual(results[2][3], (ADDR_A, ADDR_B, 6))

    def test_batch_empty(self):
        self.assertEqual(decode_contract_event_logs(self.registry, []), ())

    def test_batch_failure_is_atomic(self):
        logs = [transfer_log(), {"topics": [word(1)], "data": "0x"}]
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_logs(self.registry, logs)
        self.assertEqual(ctx.exception.code, "LOG_EVENT_NOT_FOUND")

        logs = [transfer_log(), transfer_log(topics=[TRANSFER_TOPIC0])]
        with self.assertRaises(AbiEventError) as ctx:
            decode_contract_event_logs(self.registry, logs)
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_logs_must_be_sequence(self):
        for bad in ("logs", b"logs", 42, None):
            with self.assertRaises(AbiLogDispatchError) as ctx:
                decode_contract_event_logs(self.registry, bad)
            self.assertEqual(ctx.exception.code, "LOG_ENTRY_INVALID")

    def test_registry_type_checked(self):
        with self.assertRaises(AbiLogDispatchError) as ctx:
            decode_contract_event_logs(FULL_ABI, [])
        self.assertEqual(ctx.exception.code, "LOG_ABI_INVALID")


if __name__ == "__main__":
    unittest.main()
