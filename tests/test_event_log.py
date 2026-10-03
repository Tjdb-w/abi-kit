"""parse_event_abi / event_topic0 / decode_event_log 的行为测试。

仅使用标准库 unittest，无第三方依赖；已知签名 topic 取自 Ethereum
链上通用向量（Transfer / Approval 等）。
"""

import unittest

from abi_kit import (
    AbiEventError,
    EventDefinition,
    decode_event_log,
    encode_abi_value,
    event_topic0,
    format_abi_type,
    parse_abi_type,
    parse_event_abi,
)
from abi_kit._keccak import keccak256

W = 32
ZERO = b"\x00" * W

TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
APPROVAL_TOPIC0 = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)


def word(value: int) -> bytes:
    return value.to_bytes(W, "big", signed=False)


def addr_word(address_hex: str) -> bytes:
    text = address_hex[2:] if address_hex.startswith("0x") else address_hex
    return b"\x00" * 12 + bytes.fromhex(text)


class ParseEventAbiTests(unittest.TestCase):
    def test_minimal_transfer(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Transfer",
                "inputs": [
                    {"name": "from", "type": "address", "indexed": True},
                    {"name": "to", "type": "address", "indexed": True},
                    {"name": "value", "type": "uint256"},
                ],
            }
        )
        self.assertIsInstance(event, EventDefinition)
        self.assertEqual(event.name, "Transfer")
        self.assertFalse(event.anonymous)
        self.assertEqual(len(event.inputs), 3)
        self.assertEqual(
            [p.name for p in event.inputs], ["from", "to", "value"]
        )
        self.assertEqual(
            [p.indexed for p in event.inputs], [True, True, False]
        )
        self.assertEqual(format_abi_type(event.inputs[0].abi_type), "address")
        self.assertEqual(format_abi_type(event.inputs[2].abi_type), "uint256")

    def test_inputs_and_flags_default(self):
        event = parse_event_abi({"type": "event", "name": "Ping"})
        self.assertEqual(event.inputs, ())
        self.assertFalse(event.anonymous)
        event2 = parse_event_abi(
            {
                "type": "event",
                "name": "Ping",
                "inputs": [{"type": "bool"}],
            }
        )
        self.assertEqual(event2.inputs[0].name, "")
        self.assertFalse(event2.inputs[0].indexed)

    def test_anonymous_explicit(self):
        event = parse_event_abi(
            {"type": "event", "name": "E", "anonymous": True, "inputs": []}
        )
        self.assertTrue(event.anonymous)
        event = parse_event_abi(
            {"type": "event", "name": "E", "anonymous": False, "inputs": []}
        )
        self.assertFalse(event.anonymous)

    def test_invalid_type_field(self):
        for bad in [{}, {"type": "function", "name": "E"},
                    {"type": "Event", "name": "E"}, {"type": None, "name": "E"}]:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    parse_event_abi(bad)
                self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")

    def test_invalid_name(self):
        for bad in [None, "", "a b", "1abc", "ab-c", "事件", 123, b"E"]:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    parse_event_abi({"type": "event", "name": bad})
                self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")

    def test_identifier_variants_accepted(self):
        for name in ["a", "_a", "$a", "A1", "_$abc_9", "z" * 100]:
            with self.subTest(name=name):
                event = parse_event_abi({"type": "event", "name": name})
                self.assertEqual(event.name, name)

    def test_event_json_must_be_object(self):
        for bad in [None, [], "event", 42]:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    parse_event_abi(bad)
                self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")

    def test_bad_inputs_and_parameters(self):
        cases = [
            {"type": "event", "name": "E", "inputs": {}},
            {"type": "event", "name": "E", "inputs": [None]},
            {"type": "event", "name": "E", "inputs": ["address"]},
            {"type": "event", "name": "E",
             "inputs": [{"name": "x"}]},
            {"type": "event", "name": "E",
             "inputs": [{"type": ""}]},
            {"type": "event", "name": "E",
             "inputs": [{"type": 7}]},
            {"type": "event", "name": "E",
             "inputs": [{"type": "uint7"}]},
            {"type": "event", "name": "E",
             "inputs": [{"type": "address", "name": 4}]},
            {"type": "event", "name": "E",
             "inputs": [{"type": "bool", "indexed": "true"}]},
            {"type": "event", "name": "E", "anonymous": 1},
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    parse_event_abi(bad)
                self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")

    def test_tuple_components_recursive(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Order",
                "inputs": [
                    {
                        "name": "detail",
                        "type": "tuple",
                        "components": [
                            {"name": "maker", "type": "address"},
                            {"name": "amount", "type": "uint128"},
                            {
                                "name": "nested",
                                "type": "tuple",
                                "components": [
                                    {"name": "flag", "type": "bool"}
                                ],
                            },
                        ],
                    }
                ],
            }
        )
        self.assertEqual(
            format_abi_type(event.inputs[0].abi_type),
            "(address,uint128,(bool))",
        )

    def test_tuple_array_suffixes(self):
        for suffix, canonical in [
            ("tuple[]", "(uint256,bool)[]"),
            ("tuple[3]", "(uint256,bool)[3]"),
            ("tuple[][]", "(uint256,bool)[][]"),
            ("tuple[2][3]", "(uint256,bool)[2][3]"),
        ]:
            with self.subTest(suffix=suffix):
                event = parse_event_abi(
                    {
                        "type": "event",
                        "name": "E",
                        "inputs": [
                            {
                                "name": "items",
                                "type": suffix,
                                "components": [
                                    {"name": "a", "type": "uint256"},
                                    {"name": "b", "type": "bool"},
                                ],
                            }
                        ],
                    }
                )
                self.assertEqual(
                    format_abi_type(event.inputs[0].abi_type), canonical
                )

    def test_tuple_components_invalid(self):
        cases = [
            {"name": "x", "type": "tuple"},
            {"name": "x", "type": "tuple[]"},
            {"name": "x", "type": "tuple", "components": {}},
            {"name": "x", "type": "tuple",
             "components": [{"type": "uint9"}]},
            {"name": "x", "type": "tuple[0]",
             "components": [{"type": "uint256"}]},
        ]
        for param in cases:
            with self.subTest(param=param):
                with self.assertRaises(AbiEventError) as ctx:
                    parse_event_abi(
                        {"type": "event", "name": "E", "inputs": [param]}
                    )
                self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")

    def test_components_on_non_tuple_rejected(self):
        with self.assertRaises(AbiEventError) as ctx:
            parse_event_abi(
                {
                    "type": "event",
                    "name": "E",
                    "inputs": [
                        {
                            "name": "x",
                            "type": "uint256",
                            "components": [{"type": "bool"}],
                        }
                    ],
                }
            )
        self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")

    def test_immutable_definition(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [{"name": "x", "type": "uint256"}],
            }
        )
        import dataclasses

        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.name = "F"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.inputs[0].indexed = True


class EventTopic0Tests(unittest.TestCase):
    def test_known_transfer_vector(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Transfer",
                "inputs": [
                    {"name": "from", "type": "address", "indexed": True},
                    {"name": "to", "type": "address", "indexed": True},
                    {"name": "value", "type": "uint256"},
                ],
            }
        )
        self.assertEqual(event_topic0(event), TRANSFER_TOPIC0)

    def test_known_approval_vector(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Approval",
                "inputs": [
                    {"name": "owner", "type": "address", "indexed": True},
                    {"name": "spender", "type": "address", "indexed": True},
                    {"name": "value", "type": "uint256"},
                ],
            }
        )
        self.assertEqual(event_topic0(event), APPROVAL_TOPIC0)

    def test_names_and_indexed_ignored(self):
        build = lambda indexed: parse_event_abi(
            {
                "type": "event",
                "name": "Transfer",
                "inputs": [
                    {"name": "a" if indexed else "from",
                     "type": "address", "indexed": indexed},
                    {"name": "b" if indexed else "to",
                     "type": "address", "indexed": indexed},
                    {"name": "value", "type": "uint256",
                     "indexed": indexed},
                ],
            }
        )
        self.assertEqual(event_topic0(build(True)), event_topic0(build(False)))
        self.assertEqual(event_topic0(build(True)), TRANSFER_TOPIC0)

    def test_canonical_types_in_signature(self):
        # uint/int 简写不合法（必须 uint256），但 bytesM、数组、tuple
        # 均按规范类型串参与签名。
        event = parse_event_abi(
            {
                "type": "event",
                "name": "F",
                "inputs": [
                    {"name": "a", "type": "uint8"},
                    {"name": "b", "type": "bytes4"},
                    {"name": "c", "type": "uint256[][3]"},
                    {
                        "name": "d",
                        "type": "tuple",
                        "components": [
                            {"name": "x", "type": "address"},
                            {"name": "y", "type": "string"},
                        ],
                    },
                ],
            }
        )
        signature = "F(uint8,bytes4,uint256[][3],(address,string))"
        self.assertEqual(
            event_topic0(event),
            "0x" + keccak256(signature.encode("ascii")).hex(),
        )

    def test_anonymous_returns_none(self):
        event = parse_event_abi(
            {"type": "event", "name": "E", "anonymous": True}
        )
        self.assertIsNone(event_topic0(event))

    def test_non_event_definition_rejected(self):
        with self.assertRaises(AbiEventError) as ctx:
            event_topic0({"type": "event", "name": "E"})
        self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")


def _transfer_event(anonymous=False):
    return parse_event_abi(
        {
            "type": "event",
            "name": "Transfer",
            "anonymous": anonymous,
            "inputs": [
                {"name": "from", "type": "address", "indexed": True},
                {"name": "to", "type": "address", "indexed": True},
                {"name": "value", "type": "uint256"},
            ],
        }
    )


class DecodeEventLogTests(unittest.TestCase):
    FROM = "0x" + "11" * 20
    TO = "0x" + "22" * 20

    def _transfer_topics(self, event):
        topics = []
        if not event.anonymous:
            topics.append(event_topic0(event))
        topics += [addr_word(self.FROM), addr_word(self.TO)]
        return topics

    def test_transfer_roundtrip_bytes(self):
        event = _transfer_event()
        topics = self._transfer_topics(event)
        data = word(12345)
        result = decode_event_log(event, topics, data)
        self.assertEqual(result, (self.FROM, self.TO, 12345))

    def test_transfer_roundtrip_hex(self):
        event = _transfer_event()
        topics = [
            event_topic0(event),
            "0x" + ("00" * 12 + "11" * 20),
            "00" * 12 + "22" * 20,
        ]
        result = decode_event_log(event, topics, "0x" + word(999).hex())
        self.assertEqual(result, (self.FROM, self.TO, 999))

    def test_topic_count_errors(self):
        event = _transfer_event()
        topics = self._transfer_topics(event)
        for bad_topics in [topics[:-1], topics + [ZERO], []]:
            with self.subTest(n=len(bad_topics)):
                with self.assertRaises(AbiEventError) as ctx:
                    decode_event_log(event, bad_topics, b"")
                self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_anonymous_topic_count(self):
        event = _transfer_event(anonymous=True)
        # 匿名事件只有两个 indexed topic，没有 topic0。
        topics = [addr_word(self.FROM), addr_word(self.TO)]
        result = decode_event_log(event, topics, word(7))
        self.assertEqual(result, (self.FROM, self.TO, 7))
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(event, topics[1:], word(7))
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_topic0_mismatch(self):
        event = _transfer_event()
        topics = [ZERO, addr_word(self.FROM), addr_word(self.TO)]
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(event, topics, word(1))
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC0_MISMATCH")

    def test_anonymous_skips_topic0_check(self):
        event = _transfer_event(anonymous=True)
        # 匿名事件没有 topic0：两个 topic 都按 indexed 参数解释，
        # 即使某字恰好等于某签名摘要也不做比对（这里用两个合法地址字）。
        topics = [addr_word(self.FROM), addr_word(self.TO)]
        result = decode_event_log(event, topics, word(1))
        self.assertEqual(result, (self.FROM, self.TO, 1))

    def test_topic_form_errors(self):
        event = _transfer_event()
        good = event_topic0(event)
        bad_topics_cases = [
            [good, b"\x00" * 31, addr_word(self.TO)],
            [good, "0x" + "ab" * 31, addr_word(self.TO)],
            [good, "0x" + "zz" * 32, addr_word(self.TO)],
            [good, "00" * 33, addr_word(self.TO)],
            [good, 123, addr_word(self.TO)],
            [good, None, addr_word(self.TO)],
        ]
        for bad in bad_topics_cases:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    decode_event_log(event, bad, word(1))
                self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_topics_must_be_array(self):
        event = _transfer_event()
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(event, event_topic0(event), b"")
        self.assertEqual(ctx.exception.code, "EVENT_TOPIC_COUNT")

    def test_indexed_strict_values(self):
        # bool 只能是 0/1；address 高 12 字节必须为零；uint8 不得越界；
        # int8 不得越界；bytes4 尾部 28 字节必须为零。
        specs = [
            ("bool", b"\x00" * 31 + b"\x02"),
            ("address", b"\x01" + b"\x00" * 31),
            ("uint8", word(256)),
            ("int8", (128).to_bytes(W, "big", signed=True)),
            ("bytes4", b"\x01\x02\x03\x04" + b"\x00" * 27 + b"\x01"),
        ]
        for type_string, bad_word in specs:
            event = parse_event_abi(
                {
                    "type": "event",
                    "name": "E",
                    "inputs": [
                        {"name": "x", "type": type_string, "indexed": True}
                    ],
                }
            )
            topics = [event_topic0(event), bad_word]
            with self.subTest(type_string=type_string):
                with self.assertRaises(AbiEventError) as ctx:
                    decode_event_log(event, topics, b"")
                self.assertEqual(ctx.exception.code, "EVENT_TOPIC_VALUE")

    def test_indexed_base_values_ok(self):
        specs = [
            ("bool", ZERO, False),
            ("bool", b"\x00" * 31 + b"\x01", True),
            ("uint8", word(255), 255),
            ("int8", (-128).to_bytes(W, "big", signed=True), -128),
            ("address", addr_word("0x" + "ab" * 20), "0x" + "ab" * 20),
            ("bytes4", b"\xde\xad\xbe\xef" + b"\x00" * 28,
             b"\xde\xad\xbe\xef"),
        ]
        for type_string, topic_word, expected in specs:
            event = parse_event_abi(
                {
                    "type": "event",
                    "name": "E",
                    "inputs": [
                        {"name": "x", "type": type_string, "indexed": True}
                    ],
                }
            )
            with self.subTest(type_string=type_string):
                result = decode_event_log(
                    event, [event_topic0(event), topic_word], b""
                )
                self.assertEqual(result, (expected,))

    def test_indexed_irreversible_types_return_raw_word(self):
        for type_string in [
            "string", "bytes", "uint256[]", "uint256[2]",
        ]:
            event = parse_event_abi(
                {
                    "type": "event",
                    "name": "E",
                    "inputs": [
                        {"name": "x", "type": type_string, "indexed": True}
                    ],
                }
            )
            raw = bytes(range(32))
            with self.subTest(type_string=type_string):
                result = decode_event_log(
                    event, [event_topic0(event), raw], b""
                )
                self.assertEqual(result, (raw,))
                self.assertIsInstance(result[0], bytes)

    def test_indexed_tuple_returns_raw_word(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "E",
                "inputs": [
                    {
                        "name": "p",
                        "type": "tuple",
                        "indexed": True,
                        "components": [
                            {"name": "a", "type": "uint256"},
                            {"name": "b", "type": "address"},
                        ],
                    }
                ],
            }
        )
        raw = b"\x99" * 32  # 非规范也原样返回：tuple indexed 不做解码
        result = decode_event_log(event, [event_topic0(event), raw], b"")
        self.assertEqual(result, (raw,))

    def test_data_is_non_indexed_tuple_and_order_merge(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Mixed",
                "inputs": [
                    {"name": "a", "type": "uint256", "indexed": True},
                    {"name": "b", "type": "address"},
                    {"name": "c", "type": "bool", "indexed": True},
                    {"name": "d", "type": "bytes4"},
                ],
            }
        )
        data = (
            addr_word("0x" + "33" * 20)
            + b"\x01\x02\x03\x04" + b"\x00" * 28
        )
        result = decode_event_log(
            event,
            [event_topic0(event), word(42), b"\x00" * 31 + b"\x01"],
            data,
        )
        self.assertEqual(
            result,
            (42, "0x" + "33" * 20, True, b"\x01\x02\x03\x04"),
        )

    def test_data_dynamic_non_indexed(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Msg",
                "inputs": [
                    {"name": "who", "type": "address", "indexed": True},
                    {"name": "text", "type": "string"},
                ],
            }
        )
        text = "hello"
        # data 是“非 indexed 参数 tuple”的编码，即 (string,) 的 head/tail。
        data = encode_abi_value(parse_abi_type("(string)"), (text,))
        result = decode_event_log(
            event,
            [event_topic0(event), addr_word("0x" + "44" * 20)],
            "0x" + data.hex(),
        )
        self.assertEqual(result, ("0x" + "44" * 20, text))

    def test_data_empty_when_no_non_indexed(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "OnlyTopics",
                "inputs": [
                    {"name": "a", "type": "uint256", "indexed": True},
                    {"name": "b", "type": "bool", "indexed": True},
                ],
            }
        )
        result = decode_event_log(
            event, [event_topic0(event), word(5), ZERO], b""
        )
        self.assertEqual(result, (5, False))
        # 多余 data 属于空 tuple 的尾部残留。
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                event,
                [event_topic0(event), word(5), ZERO],
                b"\x00",
            )
        self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

    def test_data_invalid_codes(self):
        event = _transfer_event()
        topics = self._transfer_topics(event)
        bad_data_cases = [
            word(1) + b"\x00",          # 尾部残留
            b"",                        # 缺少 uint256 字
            "0xabc",                    # 奇数位十六进制
            "0xzz",                     # 非法十六进制字符
            1234,                       # 错误类型
        ]
        for bad in bad_data_cases:
            with self.subTest(bad=bad):
                with self.assertRaises(AbiEventError) as ctx:
                    decode_event_log(event, topics, bad)
                self.assertEqual(ctx.exception.code, "EVENT_DATA_INVALID")

    def test_tuple_non_indexed_decoded(self):
        event = parse_event_abi(
            {
                "type": "event",
                "name": "Order",
                "inputs": [
                    {"name": "id", "type": "uint256", "indexed": True},
                    {
                        "name": "detail",
                        "type": "tuple",
                        "components": [
                            {"name": "maker", "type": "address"},
                            {"name": "amount", "type": "uint128"},
                        ],
                    },
                ],
            }
        )
        maker = "0x" + "55" * 20
        data = addr_word(maker) + word(0)[:16] + (7).to_bytes(16, "big")
        result = decode_event_log(
            event, [event_topic0(event), word(9)], data
        )
        self.assertEqual(result, (9, (maker, 7)))

    def test_non_event_definition_rejected(self):
        with self.assertRaises(AbiEventError) as ctx:
            decode_event_log(
                {"type": "event", "name": "E"}, [], b""
            )
        self.assertEqual(ctx.exception.code, "EVENT_ABI_INVALID")


if __name__ == "__main__":
    unittest.main()
