"""parse_event_abi / event_topic0 / decode_event_log 与 Keccak 的行为测试。

仅使用标准库 unittest，无第三方依赖。topic0 向量取自链上常见事件
（Transfer / Approval / OwnershipTransferred）。
"""

import unittest

from abi_kit import (
    ABIValueError,
    ABITypeError,
    AbiEventError,
    ArrayType,
    EncodedEventLog,
    EventDefinition,
    decode_event_log,
    encode_abi_value,
    encode_event_log,
    event_topic0,
    format_abi_type,
    parse_abi_type,
    parse_event_abi,
)
from abi_kit._keccak import keccak_256

W = 32
ZERO = b"\x00" * W


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
OWNERSHIP_TOPIC0 = (
    "0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0"
)


def transfer_event(**over):
    event = {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
    }
    event.update(over)
    return event


class KeccakTests(unittest.TestCase):
    def test_known_vectors(self):
        self.assertEqual(
            keccak_256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            keccak_256(b"abc").hex(),
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )
        self.assertEqual(
            keccak_256(b"Transfer(address,address,uint256)").hex(),
            TRANSFER_TOPIC0[2:],
        )

    def test_block_boundary_lengths(self):
        # 恰好一个位率块（136 字节）与临界前后长度，覆盖填充跨块路径。
        for length in (134, 135, 136, 137, 271, 272):
            self.assertEqual(len(keccak_256(b"a" * length)), 32)

    def test_multi_block_differential(self):
        # 与一份独立写法的扁平状态参考实现逐字节长度差分，覆盖多块吸收。
        mask = (1 << 64) - 1
        rc = [
            0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
            0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
            0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
            0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
            0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
            0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
            0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
            0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
        ]
        rot = [
            0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39,
            41, 45, 15, 21, 8, 18, 2, 61, 56, 14,
        ]

        def rol(v, n):
            return ((v << n) | (v >> (64 - n))) & mask if n else v

        def reference(msg):
            p = bytearray(msg)
            p.append(0x01)
            p.extend(b"\x00" * ((136 - len(p)) % 136))
            p[-1] ^= 0x80
            s = [0] * 25
            for off in range(0, len(p), 136):
                for i in range(17):
                    s[i] ^= int.from_bytes(p[off + i * 8:off + i * 8 + 8], "little")
                for r in rc:
                    c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20]
                         for x in range(5)]
                    d = [c[(x - 1) % 5] ^ rol(c[(x + 1) % 5], 1) for x in range(5)]
                    for x in range(5):
                        for y in range(5):
                            s[x + 5 * y] ^= d[x]
                    b = [0] * 25
                    for x in range(5):
                        for y in range(5):
                            b[y + 5 * ((2 * x + 3 * y) % 5)] = rol(
                                s[x + 5 * y], rot[x + 5 * y]
                            )
                    for x in range(5):
                        for y in range(5):
                            s[x + 5 * y] = (
                                b[x + 5 * y]
                                ^ ((~b[(x + 1) % 5 + 5 * y])
                                   & b[(x + 2) % 5 + 5 * y])
                            )
                    s[0] ^= r
            return b"".join(s[i].to_bytes(8, "little") for i in range(4))

        for length in list(range(0, 300)) + [1000, 4096]:
            msg = bytes((i * 31 + 7) % 256 for i in range(length))
            self.assertEqual(keccak_256(msg), reference(msg))


class ParseEventAbiTests(unittest.TestCase):
    def test_minimal_event_defaults(self):
        event = parse_event_abi({"type": "event", "name": "Ping", "inputs": []})
        self.assertIsInstance(event, EventDefinition)
        self.assertEqual(event.name, "Ping")
        self.assertEqual(event.inputs, ())
        self.assertFalse(event.anonymous)

    def test_inputs_default_to_empty_and_flags_default_false(self):
        event = parse_event_abi({"type": "event", "name": "E"})
        self.assertEqual(event.inputs, ())
        event2 = parse_event_abi(
            {"type": "event", "name": "E", "inputs": [{"name": "a", "type": "uint256"}]}
        )
        self.assertEqual(event2.inputs[0].name, "a")
        self.assertFalse(event2.inputs[0].indexed)

    def test_param_name_defaults_to_empty_string(self):
        event = parse_event_abi(
            {"type": "event", "name": "E", "inputs": [{"type": "bool"}]}
        )
        self.assertEqual(event.inputs[0].name, "")

    def test_identifier_rules(self):
        for name in ("a", "_a", "$a", "A1_b$", "x9"):
            event = parse_event_abi({"type": "event", "name": name, "inputs": []})
            self.assertEqual(event.name, name)

    def test_declaration_types_preserved(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "anonymous": True,
                "inputs": [
                    {"indexed": True, "name": "a", "type": "address"},
                    {"indexed": False, "name": "b", "type": "bytes32"},
                    {"indexed": True, "name": "c", "type": "int8"},
                ],
            }
        )
        self.assertTrue(event.anonymous)
        self.assertEqual(
            [p.indexed for p in event.inputs], [True, False, True]
        )
        self.assertEqual(format_abi_type(event.inputs[0].abi_type), "address")
        self.assertEqual(format_abi_type(event.inputs[1].abi_type), "bytes32")

    def test_tuple_components_recursive(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [
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
            }
        )
        self.assertEqual(
            format_abi_type(event.inputs[0].abi_type),
            "(address,(uint256,string))",
        )

    def test_tuple_array_suffixes(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {
                        "name": "items",
                        "type": "tuple[2][]",
                        "components": [
                            {"name": "a", "type": "uint128"},
                            {"name": "b", "type": "bool"},
                        ],
                    }
                ],
            }
        )
        t = event.inputs[0].abi_type
        self.assertIsInstance(t, ArrayType)
        self.assertIsNone(t.length)
        self.assertEqual(format_abi_type(t), "(uint128,bool)[2][]")

    def test_immutability(self):
        event = parse_event_abi(transfer_event())
        with self.assertRaises(Exception):
            event.name = "Other"
        with self.assertRaises(Exception):
            event.inputs = ()

    def _invalid(self, node):
        with self.assertRaises(AbiEventError) as ctx:
            parse_event_abi(node)
        self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")

    def test_invalid_json_shape(self):
        self._invalid(None)
        self._invalid([])
        self._invalid("event")
        self._invalid(42)

    def test_invalid_type_field(self):
        self._invalid({"name": "E", "inputs": []})
        self._invalid({"type": "function", "name": "E", "inputs": []})
        self._invalid({"type": "Event", "name": "E", "inputs": []})
        self._invalid({"type": True, "name": "E", "inputs": []})

    def test_invalid_name(self):
        self._invalid({"type": "event", "name": "", "inputs": []})
        self._invalid({"type": "event", "name": "9x", "inputs": []})
        self._invalid({"type": "event", "name": "a b", "inputs": []})
        self._invalid({"type": "event", "name": "a-b", "inputs": []})
        self._invalid({"type": "event", "name": "事", "inputs": []})
        self._invalid({"type": "event", "name": None, "inputs": []})
        self._invalid({"type": "event", "name": 1, "inputs": []})

    def test_invalid_flags(self):
        self._invalid(
            {"type": "event", "name": "E", "anonymous": 0, "inputs": []}
        )
        self._invalid(
            {"type": "event", "name": "E", "anonymous": "false", "inputs": []}
        )
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [{"type": "uint256", "indexed": 1}],
            }
        )

    def test_invalid_inputs(self):
        self._invalid({"type": "event", "name": "E", "inputs": {}})
        self._invalid({"type": "event", "name": "E", "inputs": "nope"})
        self._invalid({"type": "event", "name": "E", "inputs": [42]})
        self._invalid(
            {"type": "event", "name": "E", "inputs": [{"type": 7}]}
        )
        self._invalid(
            {"type": "event", "name": "E", "inputs": [{"type": ""}]}
        )
        self._invalid(
            {"type": "event", "name": "E", "inputs": [{"type": "uint"}]}
        )
        self._invalid(
            {"type": "event", "name": "E", "inputs": [{"type": "address", "name": 1}]}
        )

    def test_invalid_tuple_components(self):
        self._invalid(
            {"type": "event", "name": "E", "inputs": [{"type": "tuple"}]}
        )
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [{"type": "tuple", "components": {}}],
            }
        )
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [{"type": "tuple", "components": [{"type": "uint"}]}],
            }
        )
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {
                        "type": "tuple",
                        "components": [
                            {
                                "type": "tuple",
                                "components": [{"type": "tuple"}],
                            }
                        ],
                    }
                ],
            }
        )

    def test_invalid_tuple_type_string(self):
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {"type": "tuple[0]", "components": [{"type": "uint256"}]}
                ],
            }
        )
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {"type": "tuple[01]", "components": [{"type": "uint256"}]}
                ],
            }
        )
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {"type": "tuple[]x", "components": [{"type": "uint256"}]}
                ],
            }
        )

    def test_components_on_non_tuple(self):
        self._invalid(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {"type": "uint256", "components": [{"type": "bool"}]}
                ],
            }
        )

    def test_depth_limit_remains_abi_invalid(self):
        # 超过 128 层的语义深度或逼近 Python 递归上限的 components 嵌套，
        # 都必须统一为 EVENT_ABI_INVALID 而不是 RecursionError。
        def nested(n):
            node = {"name": "leaf", "type": "uint256"}
            for i in range(n):
                node = {
                    "name": f"t{i}",
                    "type": "tuple",
                    "components": [node],
                }
            return {"type": "event", "name": "Deep", "inputs": [node]}

        self._invalid(nested(128))
        self._invalid(nested(1000))


class EventTopic0Tests(unittest.TestCase):
    def test_known_topics(self):
        transfer = parse_event_abi(transfer_event())
        self.assertEqual(event_topic0(transfer), TRANSFER_TOPIC0)

        approval = parse_event_abi(
            {
                "type": "event",
                "name": "Approval",
                "inputs": [
                    {"indexed": True, "name": "owner", "type": "address"},
                    {"indexed": True, "name": "spender", "type": "address"},
                    {"indexed": False, "name": "value", "type": "uint256"},
                ],
            }
        )
        self.assertEqual(event_topic0(approval), APPROVAL_TOPIC0)

        ownership = parse_event_abi(
            {
                "type": "event",
                "name": "OwnershipTransferred",
                "inputs": [
                    {"indexed": True, "name": "previousOwner", "type": "address"},
                    {"indexed": True, "name": "newOwner", "type": "address"},
                ],
            }
        )
        self.assertEqual(event_topic0(ownership), OWNERSHIP_TOPIC0)

    def test_topic_format(self):
        event = parse_event_abi({"type": "event", "name": "E", "inputs": []})
        topic = event_topic0(event)
        self.assertTrue(topic.startswith("0x"))
        self.assertEqual(len(topic), 66)
        self.assertNotIn("A", topic)  # 全部小写
        self.assertEqual(topic, topic.lower())

    def test_param_names_and_indexed_ignored(self):
        a = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {"indexed": True, "name": "alpha", "type": "uint256"},
                    {"indexed": False, "name": "beta", "type": "bool"},
                ],
            }
        )
        b = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {"indexed": False, "name": "zzz", "type": "uint256"},
                    {"indexed": True, "name": "", "type": "bool"},
                ],
            }
        )
        self.assertEqual(event_topic0(a), event_topic0(b))
        self.assertEqual(
            event_topic0(a),
            "0x" + keccak_256(b"E(uint256,bool)").hex(),
        )

    def test_canonical_tuple_and_array_types(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {
                        "type": "tuple[]",
                        "components": [
                            {"name": "a", "type": "address"},
                            {"name": "b", "type": "uint8[2]"},
                        ],
                    },
                    {"type": "bytes"},
                ],
            }
        )
        self.assertEqual(
            event_topic0(event),
            "0x" + keccak_256(b"E((address,uint8[2])[],bytes)").hex(),
        )

    def test_anonymous_returns_none(self):
        event = parse_event_abi(
            {"type": "event", "name": "E", "anonymous": True, "inputs": []}
        )
        self.assertIsNone(event_topic0(event))


class DecodeEventLogBasicTests(unittest.TestCase):
    def setUp(self):
        self.event = parse_event_abi(transfer_event())
        self.topic0 = TRANSFER_TOPIC0
        self.data = word(1000)

    def test_decode_transfer_bytes_inputs(self):
        result = decode_event_log(
            self.event,
            [bytes.fromhex(self.topic0[2:]), addr_word(ADDR_A), addr_word(ADDR_B)],
            self.data,
        )
        self.assertEqual(result, (ADDR_A, ADDR_B, 1000))

    def test_decode_transfer_hex_inputs(self):
        result = decode_event_log(
            self.event,
            [
                self.topic0,
                "000000000000000000000000" + "11" * 20,
                "0x" + "00" * 12 + "22" * 20,
            ],
            self.data.hex(),
        )
        self.assertEqual(result, (ADDR_A, ADDR_B, 1000))

    def test_decode_data_0x_hex(self):
        result = decode_event_log(
            self.event,
            [self.topic0, addr_word(ADDR_A), addr_word(ADDR_B)],
            "0x" + self.data.hex(),
        )
        self.assertEqual(result, (ADDR_A, ADDR_B, 1000))

    def test_all_non_indexed(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Plain",
                "inputs": [
                    {"name": "a", "type": "uint32"},
                    {"name": "b", "type": "bool"},
                ],
            }
        )
        data = word(7) + (b"\x00" * 31 + b"\x01")
        self.assertEqual(
            decode_event_log(event, [bytes.fromhex(event_topic0(event)[2:])], data),
            (7, True),
        )

    def test_all_indexed_non_anonymous_requires_empty_data(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Only",
                "inputs": [{"indexed": True, "name": "a", "type": "address"}],
            }
        )
        result = decode_event_log(
            event, [bytes.fromhex(event_topic0(event)[2:]), addr_word(ADDR_A)], b""
        )
        self.assertEqual(result, (ADDR_A,))
        result2 = decode_event_log(
            event, [bytes.fromhex(event_topic0(event)[2:]), addr_word(ADDR_A)], ""
        )
        self.assertEqual(result2, (ADDR_A,))

    def test_merge_order_matches_declaration(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Mix",
                "inputs": [
                    {"indexed": False, "name": "a", "type": "uint256"},
                    {"indexed": True, "name": "b", "type": "address"},
                    {"indexed": False, "name": "c", "type": "bool"},
                    {"indexed": True, "name": "d", "type": "uint64"},
                ],
            }
        )
        topics = [
            bytes.fromhex(event_topic0(event)[2:]),
            addr_word(ADDR_A),
            word(99),
        ]
        data = word(5) + (b"\x00" * 31 + b"\x01")
        self.assertEqual(decode_event_log(event, topics, data), (5, ADDR_A, True, 99))

    def test_indexed_basic_types(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Prims",
                "inputs": [
                    {"indexed": True, "name": "u", "type": "uint8"},
                    {"indexed": True, "name": "i", "type": "int8"},
                    {"indexed": True, "name": "neg", "type": "int128"},
                    {"indexed": True, "name": "flag", "type": "bool"},
                    {"indexed": True, "name": "b4", "type": "bytes4"},
                    {"indexed": True, "name": "b32", "type": "bytes32"},
                ],
            }
        )
        topics = [
            bytes.fromhex(event_topic0(event)[2:]),
            word(255),
            word(127),
            (-1).to_bytes(W, "big", signed=True),
            b"\x00" * 31 + b"\x01",
            b"abcd" + b"\x00" * 28,
            bytes(range(32)),
        ]
        self.assertEqual(
            decode_event_log(event, topics, b""),
            (255, 127, -1, True, b"abcd", bytes(range(32))),
        )


class IndexedIrreversibleTests(unittest.TestCase):
    def _decode(self, type_string, topic):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [{"indexed": True, "name": "v", "type": type_string}],
            }
        )
        return decode_event_log(
            event, [bytes.fromhex(event_topic0(event)[2:]), topic], b""
        )[0]

    def test_indexed_string_returns_raw_topic(self):
        topic = keccak_256(b"hello")
        result = self._decode("string", topic)
        self.assertEqual(result, topic)
        self.assertIsInstance(result, bytes)

    def test_indexed_dynamic_bytes_returns_raw_topic(self):
        topic = keccak_256(b"\x01\x02")
        result = self._decode("bytes", topic)
        self.assertEqual(result, topic)

    def test_indexed_arrays_return_raw_topic(self):
        topic = b"\xab" * 32
        self.assertEqual(self._decode("uint256[]", topic), topic)
        self.assertEqual(self._decode("address[3]", topic), topic)
        self.assertEqual(self._decode("bytes4[]", topic), topic)

    def test_indexed_tuple_returns_raw_topic(self):
        topic = b"\xcd" * 32
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {
                        "indexed": True,
                        "name": "p",
                        "type": "tuple",
                        "components": [
                            {"name": "a", "type": "uint256"},
                            {"name": "b", "type": "address"},
                        ],
                    }
                ],
            }
        )
        result = decode_event_log(
            event, [bytes.fromhex(event_topic0(event)[2:]), topic], b""
        )
        self.assertEqual(result, (topic,))


class DecodeNonIndexedDataTests(unittest.TestCase):
    def test_dynamic_values_via_data(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Dyn",
                "inputs": [
                    {"indexed": False, "name": "s", "type": "string"},
                    {"indexed": False, "name": "raw", "type": "bytes"},
                    {"indexed": False, "name": "arr", "type": "uint32[]"},
                ],
            }
        )
        data = encode_abi_value(
            parse_abi_type("(string,bytes,uint32[])"),
            ("hi", b"\xde\xad", [1, 2]),
        )
        self.assertEqual(
            decode_event_log(
                event, [bytes.fromhex(event_topic0(event)[2:])], data
            ),
            ("hi", b"\xde\xad", [1, 2]),
        )

    def test_tuple_in_data(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "WithTuple",
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
                ],
            }
        )
        data = addr_word(ADDR_A) + word(7)
        tag = b"\x42" * 32
        result = decode_event_log(
            event, [bytes.fromhex(event_topic0(event)[2:]), tag], data
        )
        self.assertEqual(result, ((ADDR_A, 7), tag))

    def test_data_trailing_residue_invalid(self):
        # 非 indexed 只有一个 uint256，但 data 多了一个字 → 严格解码失败。
        event = parse_event_abi(transfer_event())
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [bytes.fromhex(TRANSFER_TOPIC0[2:]), addr_word(ADDR_A), addr_word(ADDR_B)],
                word(1) + word(2),
            )
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

    def test_data_short_invalid(self):
        event = parse_event_abi(transfer_event())
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [bytes.fromhex(TRANSFER_TOPIC0[2:]), addr_word(ADDR_A), addr_word(ADDR_B)],
                b"\x00" * 31,
            )
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

    def test_data_odd_hex_invalid(self):
        event = parse_event_abi(transfer_event())
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [TRANSFER_TOPIC0, addr_word(ADDR_A), addr_word(ADDR_B)],
                "0xabc",
            )
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

    def test_non_indexed_nonempty_but_data_empty(self):
        event = parse_event_abi(transfer_event())
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [bytes.fromhex(TRANSFER_TOPIC0[2:]), addr_word(ADDR_A), addr_word(ADDR_B)],
                b"",
            )
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

    def test_all_indexed_but_data_nonempty(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Only",
                "inputs": [{"indexed": True, "name": "a", "type": "address"}],
            }
        )
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [bytes.fromhex(event_topic0(event)[2:]), addr_word(ADDR_A)],
                b"\x00",
            )
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

    def test_data_value_out_of_range_invalid(self):
        # 声明 uint8，data 给 256 → 严格值口径拒绝。
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Small",
                "inputs": [{"indexed": False, "name": "v", "type": "uint8"}],
            }
        )
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event, [bytes.fromhex(event_topic0(event)[2:])], word(256)
            )
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")


class AnonymousEventTests(unittest.TestCase):
    def setUp(self):
        self.event = parse_event_abi(
            {
                "type": "event",
                "name": "Anon",
                "anonymous": True,
                "inputs": [
                    {"indexed": True, "name": "a", "type": "address"},
                    {"indexed": False, "name": "b", "type": "uint256"},
                ],
            }
        )

    def test_no_topic0(self):
        result = decode_event_log(self.event, [addr_word(ADDR_A)], word(3))
        self.assertEqual(result, (ADDR_A, 3))

    def test_extra_topic_count_error(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                self.event, [addr_word(ADDR_A), word(0)], word(3)
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_anonymous_allows_arbitrary_first_topic(self):
        # 匿名事件不做 topic0 校验：任意 32 字节都可作为第一个 indexed 值；
        # 该字若按 address 严格解码失败，仍属 EVENT_TOPIC_VALUE。
        weird_addr = b"\x00" * 12 + b"\xff" * 20
        self.assertEqual(
            decode_event_log(self.event, [weird_addr], word(1)),
            ("0x" + "ff" * 20, 1),
        )
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(self.event, [b"\xff" * 32], word(1))
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")


class TopicValidationTests(unittest.TestCase):
    def setUp(self):
        self.event = parse_event_abi(transfer_event())

    def test_topic_count_too_few(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                self.event,
                [bytes.fromhex(TRANSFER_TOPIC0[2:]), addr_word(ADDR_A)],
                word(1),
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_topic_count_too_many(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                self.event,
                [
                    bytes.fromhex(TRANSFER_TOPIC0[2:]),
                    addr_word(ADDR_A),
                    addr_word(ADDR_B),
                    word(0),
                ],
                word(1),
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_zero_indexed_needs_only_topic0(self):
        event = parse_event_abi(
            {"type": "event", "name": "None", "inputs": [{"type": "bool"}]}
        )
        self.assertEqual(
            decode_event_log(
                event,
                [bytes.fromhex(event_topic0(event)[2:])],
                b"\x00" * 31 + b"\x01",
            ),
            (True,),
        )
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(event, [], b"\x00" * 32)
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_topic0_mismatch(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                self.event,
                [b"\x00" * 32, addr_word(ADDR_A), addr_word(ADDR_B)],
                word(1),
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC0_MISMATCH")

    def test_topic0_mismatch_hex(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                self.event,
                [
                    "0x" + "ab" * 32,
                    "0x" + "00" * 12 + "11" * 20,
                    "0x" + "00" * 12 + "22" * 20,
                ],
                word(1),
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC0_MISMATCH")

    def test_topic_wrong_length(self):
        for bad in (b"\x00" * 31, b"\x00" * 33, "0x" + "ab" * 31, "0x" + "ab" * 33, "0x" + "zz" * 32):
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    decode_event_log(
                        self.event,
                        [bytes.fromhex(TRANSFER_TOPIC0[2:]), bad, addr_word(ADDR_B)],
                        word(1),
                    )
                self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_topic_wrong_python_type(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                self.event,
                [bytes.fromhex(TRANSFER_TOPIC0[2:]), 123, addr_word(ADDR_B)],
                word(1),
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_topics_container_shape(self):
        for bad in (b"\x00" * 96, "0x" + "ab" * 96, None, 7):
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    decode_event_log(self.event, bad, word(1))
                self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_indexed_address_high_bytes_nonzero(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                self.event,
                [
                    bytes.fromhex(TRANSFER_TOPIC0[2:]),
                    b"\x01" + b"\x00" * 31,
                    addr_word(ADDR_B),
                ],
                word(1),
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_indexed_bool_illegal_word(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "B",
                "inputs": [{"indexed": True, "name": "v", "type": "bool"}],
            }
        )
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [bytes.fromhex(event_topic0(event)[2:]), b"\x00" * 31 + b"\x02"],
                b"",
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_indexed_uint_out_of_range(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "U",
                "inputs": [{"indexed": True, "name": "v", "type": "uint8"}],
            }
        )
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [bytes.fromhex(event_topic0(event)[2:]), word(256)],
                b"",
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_indexed_int_out_of_range(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "I",
                "inputs": [{"indexed": True, "name": "v", "type": "int8"}],
            }
        )
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [bytes.fromhex(event_topic0(event)[2:]), word(128)],
                b"",
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_indexed_bytesM_trailing_nonzero(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "M",
                "inputs": [{"indexed": True, "name": "v", "type": "bytes4"}],
            }
        )
        raw = b"abcd" + b"\x00" * 27 + b"\x01"
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event, [bytes.fromhex(event_topic0(event)[2:]), raw], b""
            )
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")


class EncodeEventLogBasicTests(unittest.TestCase):
    def setUp(self):
        self.event = parse_event_abi(transfer_event())
        self.topic0 = bytes.fromhex(TRANSFER_TOPIC0[2:])

    def test_returns_immutable_encoded_event_log(self):
        result = encode_event_log(self.event, [ADDR_A, ADDR_B, 1000])
        self.assertIsInstance(result, EncodedEventLog)
        with self.assertRaises(Exception):
            result.data = b""
        with self.assertRaises(Exception):
            result.topics = ()

    def test_topics_layout_and_topic0(self):
        result = encode_event_log(self.event, [ADDR_A, ADDR_B, 1000])
        self.assertEqual(len(result.topics), 3)
        self.assertEqual(result.topics[0], self.topic0)
        self.assertEqual(result.topics[1], addr_word(ADDR_A))
        self.assertEqual(result.topics[2], addr_word(ADDR_B))
        for topic in result.topics:
            self.assertIsInstance(topic, bytes)
            self.assertEqual(len(topic), W)

    def test_topics_are_tuple_of_bytes(self):
        result = encode_event_log(self.event, [ADDR_A, ADDR_B, 1])
        self.assertIsInstance(result.topics, tuple)
        self.assertEqual(
            result.topics_hex, tuple("0x" + t.hex() for t in result.topics)
        )
        self.assertIsInstance(result.topics_hex, tuple)
        for item in result.topics_hex:
            self.assertTrue(item.startswith("0x"))
            self.assertEqual(len(item), 66)
            self.assertEqual(item, item.lower())

    def test_data_from_non_indexed_tuple(self):
        result = encode_event_log(self.event, [ADDR_A, ADDR_B, 1000])
        self.assertEqual(result.data, word(1000))
        self.assertEqual(result.data_hex, "0x" + word(1000).hex())

    def test_values_accepts_tuple(self):
        result = encode_event_log(self.event, (ADDR_A, ADDR_B, 3))
        self.assertEqual(result.data, word(3))
        self.assertEqual(result.topics[1:], (addr_word(ADDR_A), addr_word(ADDR_B)))

    def test_round_trip_with_decode_event_log(self):
        result = encode_event_log(self.event, [ADDR_A, ADDR_B, 12345])
        self.assertEqual(
            decode_event_log(self.event, result.topics, result.data),
            (ADDR_A, ADDR_B, 12345),
        )

    def test_deterministic(self):
        values = [ADDR_A, ADDR_B, 77]
        self.assertEqual(
            encode_event_log(self.event, values),
            encode_event_log(self.event, list(values)),
        )

    def test_all_indexed_gives_empty_data(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Only",
                "inputs": [{"indexed": True, "name": "a", "type": "address"}],
            }
        )
        result = encode_event_log(event, [ADDR_A])
        self.assertEqual(result.data, b"")
        self.assertEqual(result.data_hex, "0x")
        self.assertEqual(
            result.topics,
            (bytes.fromhex(event_topic0(event)[2:]), addr_word(ADDR_A)),
        )

    def test_no_inputs(self):
        event = parse_event_abi(
            {"type": "event", "name": "Empty", "inputs": []}
        )
        result = encode_event_log(event, [])
        self.assertEqual(
            result.topics, (bytes.fromhex(event_topic0(event)[2:]),)
        )
        self.assertEqual(result.data, b"")

    def test_merge_order_with_mixed_indexed(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Mix",
                "inputs": [
                    {"indexed": False, "name": "a", "type": "uint256"},
                    {"indexed": True, "name": "b", "type": "address"},
                    {"indexed": False, "name": "c", "type": "bool"},
                    {"indexed": True, "name": "d", "type": "uint64"},
                ],
            }
        )
        result = encode_event_log(event, [5, ADDR_A, True, 99])
        self.assertEqual(result.topics[1], addr_word(ADDR_A))
        self.assertEqual(result.topics[2], word(99))
        self.assertEqual(result.data, word(5) + (b"\x00" * 31 + b"\x01"))
        self.assertEqual(
            decode_event_log(event, result.topics, result.data),
            (5, ADDR_A, True, 99),
        )


class EncodeIndexedPrimitiveTests(unittest.TestCase):
    def test_indexed_static_primitives(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Prims",
                "inputs": [
                    {"indexed": True, "name": "u", "type": "uint8"},
                    {"indexed": True, "name": "i", "type": "int8"},
                    {"indexed": True, "name": "neg", "type": "int128"},
                    {"indexed": True, "name": "flag", "type": "bool"},
                    {"indexed": True, "name": "b4", "type": "bytes4"},
                    {"indexed": True, "name": "b32", "type": "bytes32"},
                ],
            }
        )
        result = encode_event_log(
            event, [255, 127, -1, True, b"abcd", bytes(range(32))]
        )
        self.assertEqual(
            result.topics[1:],
            (
                word(255),
                word(127),
                (-1).to_bytes(W, "big", signed=True),
                b"\x00" * 31 + b"\x01",
                b"abcd" + b"\x00" * 28,
                bytes(range(32)),
            ),
        )
        # 可静态还原的 indexed 值往返后与原值一致。
        self.assertEqual(
            decode_event_log(event, result.topics, b""),
            (255, 127, -1, True, b"abcd", bytes(range(32))),
        )

    def test_address_left_padded_and_bytesM_right_padded(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "P",
                "inputs": [
                    {"indexed": True, "name": "a", "type": "address"},
                    {"indexed": True, "name": "m", "type": "bytes4"},
                ],
            }
        )
        result = encode_event_log(event, ["0x" + "ee" * 20, b"abcd"])
        self.assertEqual(result.topics[1], b"\x00" * 12 + b"\xee" * 20)
        self.assertEqual(result.topics[2], b"abcd" + b"\x00" * 28)


class EncodeIndexedHashedTests(unittest.TestCase):
    def _encode_one(self, type_string, value, components=None):
        node = {
            "type": "event",
            "name": "H",
            "inputs": [{"indexed": True, "name": "v", "type": type_string}],
        }
        if components is not None:
            node["inputs"][0]["components"] = components
        event = parse_event_abi(node)
        return event, encode_event_log(event, [value])

    def test_indexed_string_is_keccak_of_utf8(self):
        event, result = self._encode_one("string", "hello")
        self.assertEqual(result.topics[1], keccak_256(b"hello"))
        self.assertEqual(result.data, b"")

    def test_indexed_dynamic_bytes_is_keccak_of_content(self):
        event, result = self._encode_one("bytes", b"\x01\x02")
        self.assertEqual(result.topics[1], keccak_256(b"\x01\x02"))

    def test_indexed_dynamic_array_omits_length(self):
        event, result = self._encode_one("uint256[]", [1, 2])
        self.assertEqual(result.topics[1], keccak_256(word(1) + word(2)))

    def test_indexed_fixed_array_concatenates_elements(self):
        event, result = self._encode_one("uint8[2]", [3, 4])
        self.assertEqual(result.topics[1], keccak_256(word(3) + word(4)))

    def test_indexed_tuple_concatenates_members_and_pads(self):
        components = [
            {"name": "a", "type": "uint256"},
            {"name": "b", "type": "address"},
        ]
        event, result = self._encode_one(
            "tuple", (5, ADDR_A), components=components
        )
        self.assertEqual(
            result.topics[1], keccak_256(word(5) + addr_word(ADDR_A))
        )

    def test_indexed_string_array_members_not_padded(self):
        event, result = self._encode_one("string[2]", ["x", "y"])
        # 每个字符串成员只取 UTF-8 内容；整段数组补齐到 32 字节倍数。
        self.assertEqual(
            result.topics[1], keccak_256(b"xy" + b"\x00" * 30)
        )

    def test_indexed_tuple_with_bytes_members_padded_whole(self):
        components = [
            {"name": "d", "type": "bytes"},
            {"name": "x", "type": "uint8"},
        ]
        event, result = self._encode_one(
            "tuple", (b"\xab\xcd", 9), components=components
        )
        # 动态 bytes 内容不补零，与下一成员直接连接，整段 tuple 补到 64 字节。
        payload = b"\xab\xcd" + word(9) + b"\x00" * 30
        self.assertEqual(result.topics[1], keccak_256(payload))

    def test_indexed_nested_tuple_array(self):
        components = [
            {"name": "n", "type": "uint16"},
            {"name": "s", "type": "string"},
        ]
        event, result = self._encode_one(
            "tuple[]", [(1, "ab"), (2, "c")], components=components
        )
        payload = (
            word(1) + b"ab" + b"\x00" * 30
            + word(2) + b"c" + b"\x00" * 31
        )
        self.assertEqual(result.topics[1], keccak_256(payload))

    def test_empty_containers_hash_empty_bytes(self):
        event1, r1 = self._encode_one("uint256[]", [])
        self.assertEqual(r1.topics[1], keccak_256(b""))
        event2, r2 = self._encode_one("tuple", (), components=[])
        self.assertEqual(r2.topics[1], keccak_256(b""))

    def test_decode_returns_raw_topic_bytes_for_hashed(self):
        event, result = self._encode_one("string", "hello")
        decoded = decode_event_log(event, result.topics, b"")
        self.assertEqual(decoded, (result.topics[1],))
        self.assertIsInstance(decoded[0], bytes)

        components = [
            {"name": "a", "type": "uint256"},
            {"name": "b", "type": "address"},
        ]
        event2, result2 = self._encode_one(
            "tuple", (1, ADDR_A), components=components
        )
        decoded2 = decode_event_log(event2, result2.topics, b"")
        self.assertEqual(decoded2, (result2.topics[1],))
        self.assertIsInstance(decoded2[0], bytes)


class EncodeNonIndexedDataTests(unittest.TestCase):
    def test_dynamic_non_indexed_values_in_data(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Dyn",
                "inputs": [
                    {"indexed": False, "name": "s", "type": "string"},
                    {"indexed": False, "name": "raw", "type": "bytes"},
                    {"indexed": False, "name": "arr", "type": "uint32[]"},
                ],
            }
        )
        values = ("hi", b"\xde\xad", [1, 2])
        result = encode_event_log(event, list(values))
        expected_data = encode_abi_value(
            parse_abi_type("(string,bytes,uint32[])"), values
        )
        self.assertEqual(result.data, expected_data)
        self.assertEqual(
            decode_event_log(event, result.topics, result.data),
            ("hi", b"\xde\xad", [1, 2]),
        )

    def test_non_indexed_tuple_in_data_with_indexed_tag(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "WithTuple",
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
                ],
            }
        )
        tag = b"\x42" * 32
        result = encode_event_log(event, [(ADDR_A, 7), tag])
        self.assertEqual(result.data, addr_word(ADDR_A) + word(7))
        self.assertEqual(result.topics[1], tag)
        self.assertEqual(
            decode_event_log(event, result.topics, result.data),
            ((ADDR_A, 7), tag),
        )


class EncodeAnonymousEventTests(unittest.TestCase):
    def setUp(self):
        self.event = parse_event_abi(
            {
                "type": "event",
                "name": "Anon",
                "anonymous": True,
                "inputs": [
                    {"indexed": True, "name": "a", "type": "address"},
                    {"indexed": False, "name": "b", "type": "uint256"},
                ],
            }
        )

    def test_no_topic0_in_topics(self):
        result = encode_event_log(self.event, [ADDR_A, 3])
        self.assertEqual(len(result.topics), 1)
        self.assertEqual(result.topics[0], addr_word(ADDR_A))
        self.assertEqual(result.data, word(3))
        self.assertEqual(len(result.topics_hex), 1)

    def test_anonymous_round_trip(self):
        result = encode_event_log(self.event, [ADDR_A, 9])
        self.assertEqual(
            decode_event_log(self.event, result.topics, result.data),
            (ADDR_A, 9),
        )


class EncodeEventLogErrorTests(unittest.TestCase):
    def setUp(self):
        self.event = parse_event_abi(transfer_event())

    def _invalid(self, event, values):
        with self.assertRaises(AbiEventError) as ctx:
            encode_event_log(event, values)
        self.assertEqual(ctx.exception.code, "EVENT_VALUE_INVALID")
        # ABIValueError / ABITypeError 不得从入口泄漏：既不是同一对象，
        # 调用方也无需感知值层异常类型。
        self.assertFalse(type(ctx.exception) is ABIValueError)
        self.assertFalse(type(ctx.exception) is ABITypeError)

    def test_event_not_definition(self):
        for bad in (None, 42, "event", [], {}):
            with self.subTest(bad=bad):
                self._invalid(bad, [])

    def test_values_not_list_or_tuple(self):
        for bad in (None, 7, "xx", b"", {}):
            with self.subTest(bad=bad):
                self._invalid(self.event, bad)

    def test_values_wrong_count(self):
        self._invalid(self.event, [ADDR_A, ADDR_B])
        self._invalid(self.event, [ADDR_A, ADDR_B, 1, 2])
        self._invalid(self.event, ())

    def test_non_indexed_value_type_mismatch(self):
        self._invalid(self.event, [ADDR_A, ADDR_B, "1000"])
        self._invalid(self.event, [ADDR_A, ADDR_B, True])
        self._invalid(self.event, [ADDR_A, ADDR_B, -1])

    def test_indexed_value_type_mismatch(self):
        self._invalid(self.event, [0x1111, ADDR_B, 1])
        self._invalid(self.event, [ADDR_A, "0x" + "22" * 19, 1])
        prims = parse_event_abi(
            {
                "type": "event",
                "name": "Prims",
                "inputs": [
                    {"indexed": True, "name": "u", "type": "uint8"},
                    {"indexed": True, "name": "i", "type": "int8"},
                    {"indexed": True, "name": "f", "type": "bool"},
                    {"indexed": True, "name": "m", "type": "bytes4"},
                ],
            }
        )
        self._invalid(prims, [256, 0, False, b"abcd"])
        self._invalid(prims, [0, 128, False, b"abcd"])
        self._invalid(prims, [0, 0, 1, b"abcd"])
        self._invalid(prims, [0, 0, False, b"abc"])
        self._invalid(prims, [0, 0, False, "abcd"])

    def test_hashed_indexed_value_type_mismatch(self):
        s_event = parse_event_abi(
            {
                "type": "event",
                "name": "S",
                "inputs": [
                    {"indexed": True, "name": "s", "type": "string"},
                    {"indexed": True, "name": "b", "type": "bytes"},
                ],
            }
        )
        self._invalid(s_event, [b"hello", b"x"])
        self._invalid(s_event, ["hello", "01"])

        arr_event = parse_event_abi(
            {
                "type": "event",
                "name": "A",
                "inputs": [
                    {"indexed": True, "name": "fix", "type": "uint8[2]"},
                    {"indexed": True, "name": "dyn", "type": "uint256[]"},
                ],
            }
        )
        self._invalid(arr_event, [(1, 2), []])
        self._invalid(arr_event, [[1, 2, 3], []])
        self._invalid(arr_event, [[1, 2], (1,)])
        self._invalid(arr_event, [[1, 2], [True]])

        tuple_event = parse_event_abi(
            {
                "type": "event",
                "name": "T",
                "inputs": [
                    {
                        "indexed": True,
                        "name": "p",
                        "type": "tuple",
                        "components": [
                            {"name": "a", "type": "uint256"},
                            {"name": "b", "type": "address"},
                        ],
                    }
                ],
            }
        )
        self._invalid(tuple_event, [[1, ADDR_A]])
        self._invalid(tuple_event, [(1,)])
        self._invalid(tuple_event, [("x", ADDR_A)])


class ExceptionContractTests(unittest.TestCase):
    def test_codes_and_hierarchy(self):
        self.assertTrue(issubclass(AbiEventError, ValueError))
        self.assertEqual(
            set(AbiEventError.CODES),
            {
                "EVENT_ABI_INVALID",
                "EVENT_TOPIC_COUNT",
                "EVENT_TOPIC0_MISMATCH",
                "EVENT_TOPIC_VALUE",
                "EVENT_DATA_INVALID",
                "EVENT_VALUE_INVALID",
            },
        )
        with self.assertRaises(ValueError):
            AbiEventError("NOT_A_CODE", "x")

    def test_decode_returns_tuple(self):
        event = parse_event_abi(
            {"type": "event", "name": "Empty", "inputs": []}
        )
        result = decode_event_log(
            event, [bytes.fromhex(event_topic0(event)[2:])], b""
        )
        self.assertEqual(result, ())
        self.assertIsInstance(result, tuple)


if __name__ == "__main__":
    unittest.main()
