"""parse_contract_abi / ContractAbiDefinition 与 AbiContractAbiError 的
行为测试。

覆盖：
- 空 ABI 得到全空集合、constructor 为 None、receive/fallback 为 False；
- 六类条目全部按现有语义解析，functions/events/errors 与派生签名、
  selector、topic0 序列按声明顺序排列，匿名事件不进入 event_topic0s；
- 与各单项入口（parse_function_abi / parse_error_abi /
  parse_constructor_abi / parse_event_abi）的定义对象一致；
- 相同输入重复解析稳定，定义对象不可变；
- 五类 AbiContractAbiError 错误码边界（根非法、条目非法、重复登记、
  签名/selector 冲突、topic0 冲突），且旧异常类型不泄漏；
- function 与 error selector 相同不冲突，匿名事件不参与 topic0 去重。

仅使用标准库 unittest，无第三方依赖。
"""

import json
import unittest
from unittest import mock

from abi_kit import (
    AbiContractAbiError,
    AbiEventError,
    AbiMetadataError,
    ContractAbiDefinition,
    ErrorDefinition,
    EventDefinition,
    FunctionDefinition,
    canonical_error_signature,
    canonical_function_signature,
    error_selector,
    event_topic0,
    function_selector,
    parse_constructor_abi,
    parse_contract_abi,
    parse_error_abi,
    parse_event_abi,
    parse_function_abi,
)

FUNC_TRANSFER = {
    "type": "function",
    "name": "transfer",
    "inputs": [
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
    ],
    "outputs": [{"name": "", "type": "bool"}],
}
FUNC_PING = {"type": "function", "name": "ping", "inputs": []}
FUNC_BAD_TYPE = {
    "type": "function",
    "name": "bad",
    "inputs": [{"name": "x", "type": "uint9"}],
}
FUNC_NO_INPUTS = {"type": "function", "name": "noInputs"}
FUNC_BAD_NAME = {"type": "function", "name": "1bad", "inputs": []}

EVENT_TRANSFER = {
    "type": "event",
    "name": "Transfer",
    "inputs": [
        {"name": "from", "type": "address", "indexed": True},
        {"name": "to", "type": "address", "indexed": True},
        {"name": "value", "type": "uint256", "indexed": False},
    ],
}
EVENT_APPROVAL = {
    "type": "event",
    "name": "Approval",
    "inputs": [
        {"name": "owner", "type": "address", "indexed": True},
        {"name": "spender", "type": "address", "indexed": True},
        {"name": "value", "type": "uint256"},
    ],
}
EVENT_ANON_PING = {
    "type": "event",
    "name": "Ping",
    "inputs": [{"name": "v", "type": "uint256"}],
    "anonymous": True,
}
EVENT_BAD_INDEXED = {
    "type": "event",
    "name": "Bad",
    "inputs": [{"name": "v", "type": "uint256", "indexed": "yes"}],
}

ERROR_BAD = {"type": "error", "name": "Bad", "inputs": [{"name": "c", "type": "uint256"}]}
ERROR_EMPTY = {"type": "error", "name": "Empty", "inputs": []}

CONSTRUCTOR_ENTRY = {
    "type": "constructor",
    "inputs": [{"name": "supply", "type": "uint256"}],
}
RECEIVE_ENTRY = {"type": "receive", "stateMutability": "payable"}
FALLBACK_ENTRY = {"type": "fallback", "stateMutability": "payable"}


def assert_code(testcase, code, abi):
    """断言 parse_contract_abi 抛且只抛 AbiContractAbiError 的指定 code。"""
    with testcase.assertRaises(AbiContractAbiError) as ctx:
        parse_contract_abi(abi)
    testcase.assertEqual(ctx.exception.code, code)
    # 旧的元数据/事件层异常不得泄漏到本入口之外。
    testcase.assertNotIsInstance(ctx.exception, AbiMetadataError)
    testcase.assertNotIsInstance(ctx.exception, AbiEventError)


class EmptyAbiTests(unittest.TestCase):
    def test_empty_json_string(self):
        definition = parse_contract_abi("[]")
        self._assert_empty(definition)

    def test_empty_list_and_tuple(self):
        self._assert_empty(parse_contract_abi([]))
        self._assert_empty(parse_contract_abi(()))

    def _assert_empty(self, definition):
        self.assertIsInstance(definition, ContractAbiDefinition)
        self.assertEqual(definition.functions, ())
        self.assertEqual(definition.events, ())
        self.assertEqual(definition.errors, ())
        self.assertIsNone(definition.constructor)
        self.assertFalse(definition.receive)
        self.assertFalse(definition.fallback)
        self.assertEqual(definition.function_signatures, ())
        self.assertEqual(definition.event_signatures, ())
        self.assertEqual(definition.error_signatures, ())
        self.assertEqual(definition.function_selectors, ())
        self.assertEqual(definition.error_selectors, ())
        self.assertEqual(definition.event_topic0s, ())


class ParsingTests(unittest.TestCase):
    def test_all_six_kinds_parsed_in_declaration_order(self):
        abi = [
            ERROR_BAD,
            FUNC_TRANSFER,
            EVENT_TRANSFER,
            CONSTRUCTOR_ENTRY,
            RECEIVE_ENTRY,
            FUNC_PING,
            FALLBACK_ENTRY,
            EVENT_ANON_PING,
            ERROR_EMPTY,
        ]
        definition = parse_contract_abi(json.dumps(abi))

        self.assertEqual(len(definition.functions), 2)
        self.assertEqual(
            [f.name for f in definition.functions], ["transfer", "ping"]
        )
        self.assertEqual(len(definition.events), 2)
        self.assertEqual(
            [e.name for e in definition.events], ["Transfer", "Ping"]
        )
        self.assertEqual(len(definition.errors), 2)
        self.assertEqual([e.name for e in definition.errors], ["Bad", "Empty"])

        self.assertIsNotNone(definition.constructor)
        self.assertEqual(len(definition.constructor.inputs), 1)
        self.assertTrue(definition.receive)
        self.assertTrue(definition.fallback)

    def test_definitions_match_single_entry_parsers(self):
        abi = [
            FUNC_TRANSFER,
            FUNC_PING,
            EVENT_TRANSFER,
            EVENT_APPROVAL,
            ERROR_BAD,
            CONSTRUCTOR_ENTRY,
        ]
        definition = parse_contract_abi(abi)
        self.assertEqual(
            definition.functions, parse_function_abi(json.dumps(abi))
        )
        self.assertEqual(definition.errors, parse_error_abi(abi))
        self.assertEqual(
            definition.constructor, parse_constructor_abi(json.dumps(abi))
        )
        self.assertEqual(
            definition.events[0], parse_event_abi(EVENT_TRANSFER)
        )
        self.assertEqual(
            definition.events[1], parse_event_abi(EVENT_APPROVAL)
        )
        self.assertIsInstance(definition.functions[0], FunctionDefinition)
        self.assertIsInstance(definition.events[0], EventDefinition)
        self.assertIsInstance(definition.errors[0], ErrorDefinition)

    def test_function_outputs_preserved(self):
        definition = parse_contract_abi([FUNC_TRANSFER])
        self.assertEqual(len(definition.functions[0].outputs), 1)
        self.assertEqual(
            definition.functions[0].outputs[0].abi_type.kind, "bool"
        )

    def test_accepts_json_string_list_and_tuple(self):
        abi = [FUNC_PING, RECEIVE_ENTRY]
        from_json = parse_contract_abi(json.dumps(abi))
        from_list = parse_contract_abi(list(abi))
        from_tuple = parse_contract_abi(tuple(abi))
        self.assertEqual(from_json, from_list)
        self.assertEqual(from_list, from_tuple)

    def test_constructor_none_when_absent(self):
        definition = parse_contract_abi([FUNC_PING])
        self.assertIsNone(definition.constructor)

    def test_receive_fallback_with_empty_or_missing_inputs(self):
        definition = parse_contract_abi(
            [
                {"type": "receive"},
                {"type": "fallback", "inputs": []},
            ]
        )
        self.assertTrue(definition.receive)
        self.assertTrue(definition.fallback)

    def test_tuple_components_parsed(self):
        entry = {
            "type": "function",
            "name": "f",
            "inputs": [
                {
                    "name": "item",
                    "type": "tuple",
                    "components": [
                        {"name": "a", "type": "uint256"},
                        {"name": "b", "type": "address"},
                    ],
                }
            ],
        }
        definition = parse_contract_abi([entry])
        abi_type = definition.functions[0].inputs[0].abi_type
        self.assertEqual(
            canonical_function_signature(definition.functions[0]),
            "f((uint256,address))",
        )
        self.assertEqual(len(abi_type.components), 2)


class SignatureSelectorTopic0Tests(unittest.TestCase):
    def setUp(self):
        self.abi = [
            FUNC_TRANSFER,
            EVENT_TRANSFER,
            ERROR_BAD,
            FUNC_PING,
            EVENT_APPROVAL,
            EVENT_ANON_PING,
            ERROR_EMPTY,
        ]
        self.definition = parse_contract_abi(self.abi)

    def test_function_signatures_in_declaration_order(self):
        functions = self.definition.functions
        expected = tuple(
            canonical_function_signature(f) for f in functions
        )
        self.assertEqual(self.definition.function_signatures, expected)
        self.assertEqual(
            self.definition.function_signatures,
            ("transfer(address,uint256)", "ping()"),
        )

    def test_error_signatures_in_declaration_order(self):
        errors = self.definition.errors
        expected = tuple(canonical_error_signature(e) for e in errors)
        self.assertEqual(self.definition.error_signatures, expected)
        self.assertEqual(
            self.definition.error_signatures, ("Bad(uint256)", "Empty()")
        )

    def test_event_signatures_include_anonymous(self):
        self.assertEqual(
            self.definition.event_signatures,
            (
                "Transfer(address,address,uint256)",
                "Approval(address,address,uint256)",
                "Ping(uint256)",
            ),
        )

    def test_function_selectors_lowercase_hex_in_order(self):
        expected = tuple(
            "0x" + function_selector(f).hex() for f in self.definition.functions
        )
        self.assertEqual(self.definition.function_selectors, expected)
        for selector in self.definition.function_selectors:
            self.assertTrue(selector.startswith("0x"))
            self.assertEqual(len(selector), 10)
            self.assertEqual(selector, selector.lower())
        self.assertEqual(
            self.definition.function_selectors[0], "0xa9059cbb"
        )

    def test_error_selectors_lowercase_hex_in_order(self):
        expected = tuple(
            "0x" + error_selector(e).hex() for e in self.definition.errors
        )
        self.assertEqual(self.definition.error_selectors, expected)
        for selector in self.definition.error_selectors:
            self.assertTrue(selector.startswith("0x"))
            self.assertEqual(len(selector), 10)
            self.assertEqual(selector, selector.lower())

    def test_event_topic0s_skip_anonymous_and_stay_in_order(self):
        non_anonymous = [e for e in self.definition.events if not e.anonymous]
        expected = tuple(event_topic0(e) for e in non_anonymous)
        self.assertEqual(self.definition.event_topic0s, expected)
        self.assertEqual(len(self.definition.event_topic0s), 2)
        for topic0 in self.definition.event_topic0s:
            self.assertTrue(topic0.startswith("0x"))
            self.assertEqual(len(topic0), 66)
            self.assertEqual(topic0, topic0.lower())
        self.assertEqual(
            self.definition.event_topic0s[0],
            event_topic0(parse_event_abi(EVENT_TRANSFER)),
        )
        self.assertEqual(
            self.definition.event_topic0s[1],
            event_topic0(parse_event_abi(EVENT_APPROVAL)),
        )

    def test_derived_sequences_are_tuples(self):
        self.assertIsInstance(self.definition.function_signatures, tuple)
        self.assertIsInstance(self.definition.event_signatures, tuple)
        self.assertIsInstance(self.definition.error_signatures, tuple)
        self.assertIsInstance(self.definition.function_selectors, tuple)
        self.assertIsInstance(self.definition.error_selectors, tuple)
        self.assertIsInstance(self.definition.event_topic0s, tuple)

    def test_function_and_error_may_share_selector(self):
        # 同名同参数类型的 function 与 error 具有完全相同的四字节 selector，
        # 跨种类不构成冲突。
        abi = [
            {"type": "function", "name": "Thing", "inputs": [{"type": "uint256"}]},
            {"type": "error", "name": "Thing", "inputs": [{"type": "uint256"}]},
        ]
        definition = parse_contract_abi(abi)
        self.assertEqual(
            definition.function_selectors[0], definition.error_selectors[0]
        )

    def test_anonymous_events_have_no_topic0_collisions(self):
        # 两个不同的匿名事件没有 topic0，可以共存。
        definition = parse_contract_abi(
            [
                {
                    "type": "event",
                    "name": "A",
                    "inputs": [],
                    "anonymous": True,
                },
                {
                    "type": "event",
                    "name": "B",
                    "inputs": [],
                    "anonymous": True,
                },
            ]
        )
        self.assertEqual(len(definition.events), 2)
        self.assertEqual(definition.event_topic0s, ())


class StabilityAndImmutabilityTests(unittest.TestCase):
    def test_repeated_parsing_stable(self):
        abi = [
            FUNC_TRANSFER,
            EVENT_TRANSFER,
            ERROR_BAD,
            CONSTRUCTOR_ENTRY,
            RECEIVE_ENTRY,
            FALLBACK_ENTRY,
        ]
        first = parse_contract_abi(json.dumps(abi))
        second = parse_contract_abi(json.dumps(abi))
        self.assertEqual(first, second)
        self.assertEqual(first.function_signatures, second.function_signatures)
        self.assertEqual(first.function_selectors, second.function_selectors)
        self.assertEqual(first.event_topic0s, second.event_topic0s)
        self.assertEqual(first.error_selectors, second.error_selectors)

    def test_definition_is_frozen(self):
        definition = parse_contract_abi([FUNC_PING])
        with self.assertRaises(AttributeError):
            definition.functions = ()
        with self.assertRaises(AttributeError):
            definition.receive = True


class RootInvalidTests(unittest.TestCase):
    def test_non_string_non_sequence_types(self):
        for bad in (None, 123, 12.5, object(), b"[]", {"x": 1}):
            assert_code(self, "ABI_ROOT_INVALID", bad)

    def test_malformed_json_string(self):
        assert_code(self, "ABI_ROOT_INVALID", "[{")

    def test_json_root_object(self):
        assert_code(self, "ABI_ROOT_INVALID", "{}")

    def test_json_root_scalar(self):
        assert_code(self, "ABI_ROOT_INVALID", '"hello"')


class EntryInvalidTests(unittest.TestCase):
    def test_non_object_entry(self):
        assert_code(self, "ABI_ENTRY_INVALID", [42])
        assert_code(self, "ABI_ENTRY_INVALID", ["function"])
        assert_code(self, "ABI_ENTRY_INVALID", [None])

    def test_missing_or_unknown_type(self):
        assert_code(self, "ABI_ENTRY_INVALID", [{"name": "f", "inputs": []}])
        assert_code(self, "ABI_ENTRY_INVALID", [{"type": "struct"}])
        assert_code(self, "ABI_ENTRY_INVALID", [{"type": 123}])

    def test_function_entry_missing_fields_or_bad_names(self):
        assert_code(self, "ABI_ENTRY_INVALID", [FUNC_NO_INPUTS])
        assert_code(self, "ABI_ENTRY_INVALID", [FUNC_BAD_NAME])
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "function", "name": "f", "inputs": "nope"}],
        )
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "function", "name": "f", "outputs": "nope"}],
        )

    def test_parameter_cannot_form_abi_type(self):
        assert_code(self, "ABI_ENTRY_INVALID", [FUNC_BAD_TYPE])
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [
                {
                    "type": "function",
                    "name": "f",
                    "inputs": [{"name": "x", "type": "tuple"}],
                }
            ],
        )

    def test_event_entry_invalid(self):
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "event", "inputs": []}],
        )
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "event", "name": "1bad", "inputs": []}],
        )
        assert_code(self, "ABI_ENTRY_INVALID", [EVENT_BAD_INDEXED])

    def test_error_entry_invalid(self):
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "error", "inputs": []}],
        )
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "error", "name": "Bad", "inputs": [{"type": "uint9"}]}],
        )

    def test_constructor_entry_invalid(self):
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "constructor", "inputs": [{"type": "uint9"}]}],
        )

    def test_receive_inputs_wrong_shape_is_entry_invalid(self):
        assert_code(
            self,
            "ABI_ENTRY_INVALID",
            [{"type": "receive", "inputs": "nope"}],
        )


class DuplicateTests(unittest.TestCase):
    def test_duplicate_constructor(self):
        assert_code(
            self,
            "ABI_ENTRY_DUPLICATE",
            [CONSTRUCTOR_ENTRY, {"type": "constructor", "inputs": []}],
        )

    def test_duplicate_receive_fallback(self):
        assert_code(
            self,
            "ABI_ENTRY_DUPLICATE",
            [RECEIVE_ENTRY, {"type": "receive"}],
        )
        assert_code(
            self,
            "ABI_ENTRY_DUPLICATE",
            [FALLBACK_ENTRY, {"type": "fallback"}],
        )

    def test_receive_fallback_nonempty_inputs(self):
        assert_code(
            self,
            "ABI_ENTRY_DUPLICATE",
            [
                {
                    "type": "receive",
                    "inputs": [{"name": "x", "type": "uint256"}],
                }
            ],
        )
        assert_code(
            self,
            "ABI_ENTRY_DUPLICATE",
            [
                {
                    "type": "fallback",
                    "inputs": [{"name": "x", "type": "uint256"}],
                }
            ],
        )


class SignatureCollisionTests(unittest.TestCase):
    def test_duplicate_function_signature(self):
        assert_code(
            self,
            "ABI_SIGNATURE_COLLISION",
            [FUNC_TRANSFER, dict(FUNC_TRANSFER)],
        )

    def test_duplicate_error_signature(self):
        assert_code(
            self,
            "ABI_SIGNATURE_COLLISION",
            [ERROR_BAD, dict(ERROR_BAD)],
        )

    def test_duplicate_event_signature(self):
        assert_code(
            self,
            "ABI_SIGNATURE_COLLISION",
            [EVENT_TRANSFER, dict(EVENT_TRANSFER)],
        )

    def test_duplicate_anonymous_event_signature(self):
        # 即使两个事件都是匿名的，规范签名仍不允许重复。
        assert_code(
            self,
            "ABI_SIGNATURE_COLLISION",
            [
                {
                    "type": "event",
                    "name": "Ping",
                    "inputs": [],
                    "anonymous": True,
                },
                {
                    "type": "event",
                    "name": "Ping",
                    "inputs": [],
                    "anonymous": True,
                },
            ],
        )

    def test_function_selector_collision_with_distinct_signatures(self):
        # 两个规范签名不同但四字节 selector 截位冲突的函数（此处用桩
        # 确定性地制造 selector 碰撞）只报 ABI_SIGNATURE_COLLISION。
        abi = [
            {"type": "function", "name": "alpha", "inputs": []},
            {"type": "function", "name": "beta", "inputs": []},
        ]
        with mock.patch(
            "abi_kit._contract_abi.function_selector",
            return_value=b"\x12\x34\x56\x78",
        ):
            assert_code(self, "ABI_SIGNATURE_COLLISION", abi)

    def test_error_selector_collision_with_distinct_signatures(self):
        abi = [
            {"type": "error", "name": "Alpha", "inputs": []},
            {"type": "error", "name": "Beta", "inputs": []},
        ]
        with mock.patch(
            "abi_kit._contract_abi.error_selector",
            return_value=b"\x12\x34\x56\x78",
        ):
            assert_code(self, "ABI_SIGNATURE_COLLISION", abi)

    def test_cross_kind_selector_collision_is_allowed(self):
        # function 之间与 error 之间各自去重，跨种类相同 selector 不冲突；
        # 桩让所有 selector 相同：两个 function 会先冲突，换成各一个则通过。
        abi = [
            {"type": "function", "name": "alpha", "inputs": []},
            {"type": "error", "name": "beta", "inputs": []},
        ]
        with mock.patch(
            "abi_kit._contract_abi.function_selector",
            return_value=b"\x12\x34\x56\x78",
        ), mock.patch(
            "abi_kit._contract_abi.error_selector",
            return_value=b"\x12\x34\x56\x78",
        ):
            definition = parse_contract_abi(abi)
        self.assertEqual(
            definition.function_selectors, definition.error_selectors
        )


class Topic0CollisionTests(unittest.TestCase):
    def test_distinct_events_with_same_topic0(self):
        # 规范签名不同但 topic0 相同的两个非匿名事件（用桩确定性制造
        # Keccak 截位冲突）报 ABI_TOPIC0_COLLISION，而不是签名冲突。
        abi = [
            {"type": "event", "name": "Alpha", "inputs": []},
            {"type": "event", "name": "Beta", "inputs": []},
        ]

        def fake_topic0(event):
            if event.anonymous:
                return None
            return "0x" + "ab" * 32

        with mock.patch(
            "abi_kit._contract_abi.event_topic0", side_effect=fake_topic0
        ):
            assert_code(self, "ABI_TOPIC0_COLLISION", abi)

    def test_same_topic0_from_anonymous_event_ignored(self):
        abi = [
            {"type": "event", "name": "Alpha", "inputs": []},
            {
                "type": "event",
                "name": "Beta",
                "inputs": [],
                "anonymous": True,
            },
        ]

        def fake_topic0(event):
            if event.anonymous:
                return None
            return "0x" + "ab" * 32

        with mock.patch(
            "abi_kit._contract_abi.event_topic0", side_effect=fake_topic0
        ):
            definition = parse_contract_abi(abi)
        self.assertEqual(len(definition.events), 2)
        self.assertEqual(len(definition.event_topic0s), 1)


if __name__ == "__main__":
    unittest.main()
