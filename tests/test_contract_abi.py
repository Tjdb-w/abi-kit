"""parse_contract_abi 与 AbiContractAbiError 的行为测试。

覆盖：
- 六类条目按声明顺序登记，functions/events/errors 为现有不可变定义 tuple，
  constructor 为现有定义或 None，receive/fallback 为登记 bool；
- function/event/error 签名、function/error selector、非匿名事件 topic0
  按声明顺序派生，匿名事件不进入 event_topic0s；
- 空 ABI、相同输入重复解析稳定、结果不可变；
- ABI_ENTRY_INVALID / ABI_ROOT_INVALID / ABI_ENTRY_DUPLICATE /
  ABI_SIGNATURE_COLLISION / ABI_TOPIC0_COLLISION 五类错误码边界，以及
  function 与 error selector 相同不冲突；
- 元数据/事件/类型层既有异常不泄漏，统一转译为 AbiContractAbiError。

仅使用标准库 unittest，无第三方依赖。
"""

import json
import unittest
from unittest import mock

from abi_kit import (
    AbiContractAbiError,
    AbiEventError,
    AbiMetadataError,
    ConstructorDefinition,
    ContractAbiDefinition,
    ErrorDefinition,
    EventDefinition,
    FunctionDefinition,
    canonical_error_signature,
    canonical_function_signature,
    error_selector,
    event_topic0,
    format_abi_type,
    function_selector,
    parse_contract_abi,
)
import abi_kit._contract_abi as contract_abi_mod

FUNCTION_F = {
    "type": "function",
    "name": "f",
    "inputs": [{"name": "a", "type": "uint256"}],
    "outputs": [],
}
FUNCTION_G = {
    "type": "function",
    "name": "g",
    "inputs": [
        {
            "name": "item",
            "type": "tuple",
            "components": [
                {"name": "x", "type": "address"},
                {"name": "ys", "type": "uint256[]"},
            ],
        }
    ],
    "outputs": [{"name": "", "type": "bool"}],
}
FUNCTION_H = {"type": "function", "name": "h", "inputs": [], "outputs": []}
EVENT_E1 = {
    "type": "event",
    "name": "E1",
    "inputs": [
        {"name": "from", "type": "address", "indexed": True},
        {"name": "v", "type": "uint256", "indexed": False},
    ],
}
EVENT_E2 = {
    "type": "event",
    "name": "E2",
    "inputs": [{"name": "s", "type": "string", "indexed": True}],
}
EVENT_ANON = {
    "type": "event",
    "name": "Anon",
    "inputs": [{"name": "v", "type": "uint256"}],
    "anonymous": True,
}
ERROR_BAD = {
    "type": "error",
    "name": "Bad",
    "inputs": [{"name": "code", "type": "uint8"}],
}
ERROR_EMPTY = {"type": "error", "name": "Boom", "inputs": []}
CONSTRUCTOR_ENTRY = {
    "type": "constructor",
    "inputs": [{"name": "owner", "type": "address"}],
}
RECEIVE_ENTRY = {"type": "receive", "stateMutability": "payable"}
FALLBACK_ENTRY = {"type": "fallback"}


def build(*entries):
    return parse_contract_abi(list(entries))


class ParsingAndOrderTests(unittest.TestCase):
    def test_registers_all_six_kinds_in_declaration_order(self):
        contract = build(
            FUNCTION_F,
            EVENT_E1,
            ERROR_BAD,
            CONSTRUCTOR_ENTRY,
            FUNCTION_G,
            RECEIVE_ENTRY,
            EVENT_ANON,
            FALLBACK_ENTRY,
            ERROR_EMPTY,
            FUNCTION_H,
            EVENT_E2,
        )
        self.assertIsInstance(contract, ContractAbiDefinition)

        self.assertEqual(
            [f.name for f in contract.functions], ["f", "g", "h"]
        )
        self.assertEqual(
            [e.name for e in contract.events], ["E1", "Anon", "E2"]
        )
        self.assertEqual(
            [e.name for e in contract.errors], ["Bad", "Boom"]
        )
        self.assertIsInstance(contract.constructor, ConstructorDefinition)
        self.assertEqual(len(contract.constructor.inputs), 1)
        self.assertTrue(contract.receive)
        self.assertTrue(contract.fallback)

        self.assertTrue(
            all(isinstance(f, FunctionDefinition) for f in contract.functions)
        )
        self.assertTrue(
            all(isinstance(e, EventDefinition) for e in contract.events)
        )
        self.assertTrue(
            all(isinstance(e, ErrorDefinition) for e in contract.errors)
        )

    def test_definitions_use_existing_single_entry_semantics(self):
        contract = build(FUNCTION_G, EVENT_E1, ERROR_BAD)
        g = contract.functions[0]
        tuple_type = g.inputs[0].abi_type
        self.assertEqual(tuple_type.names, ("x", "ys"))
        self.assertEqual(tuple_type.components[0].kind, "address")
        self.assertEqual(g.outputs[0].abi_type.kind, "bool")
        event = contract.events[0]
        self.assertTrue(event.inputs[0].indexed)
        self.assertFalse(event.inputs[1].indexed)
        self.assertEqual(contract.errors[0].inputs[0].abi_type.bit_size, 8)

    def test_accepts_json_string_tuple_and_missing_optional_fields(self):
        abi = json.dumps([FUNCTION_H, EVENT_E2, {"type": "receive"}])
        contract = parse_contract_abi(abi)
        self.assertEqual(len(contract.functions), 1)
        self.assertEqual(len(contract.events), 1)
        self.assertTrue(contract.receive)
        contract2 = parse_contract_abi((FUNCTION_H,))
        self.assertEqual(contract2.functions[0].name, "h")
        # receive/fallback 缺省 inputs 或空 inputs 均合法。
        contract3 = build({"type": "fallback", "inputs": []})
        self.assertTrue(contract3.fallback)


class EmptyAndStabilityTests(unittest.TestCase):
    def test_empty_abi(self):
        for empty in ([], (), "[]"):
            contract = parse_contract_abi(empty)
            self.assertEqual(contract.functions, ())
            self.assertEqual(contract.events, ())
            self.assertEqual(contract.errors, ())
            self.assertIsNone(contract.constructor)
            self.assertFalse(contract.receive)
            self.assertFalse(contract.fallback)
            self.assertEqual(contract.function_signatures, ())
            self.assertEqual(contract.function_selectors, ())
            self.assertEqual(contract.event_signatures, ())
            self.assertEqual(contract.error_signatures, ())
            self.assertEqual(contract.error_selectors, ())
            self.assertEqual(contract.event_topic0s, ())

    def test_repeated_parsing_is_stable_and_equal(self):
        abi = [
            FUNCTION_F,
            EVENT_E1,
            ERROR_BAD,
            CONSTRUCTOR_ENTRY,
            RECEIVE_ENTRY,
            FALLBACK_ENTRY,
        ]
        first = parse_contract_abi(json.dumps(abi))
        second = parse_contract_abi(tuple(abi))
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(first.function_selectors, second.function_selectors)
        self.assertEqual(first.event_topic0s, second.event_topic0s)
        # 派生序列重复访问同一对象。
        self.assertIs(first.function_signatures, first.function_signatures)


class DerivedSequenceTests(unittest.TestCase):
    def setUp(self):
        self.contract = build(
            FUNCTION_F,
            FUNCTION_G,
            FUNCTION_H,
            EVENT_E1,
            EVENT_ANON,
            EVENT_E2,
            ERROR_BAD,
            ERROR_EMPTY,
        )

    def test_function_signatures_and_selectors_align_by_index(self):
        functions = self.contract.functions
        self.assertEqual(
            self.contract.function_signatures,
            tuple(canonical_function_signature(f) for f in functions),
        )
        self.assertEqual(
            self.contract.function_selectors,
            tuple("0x" + function_selector(f).hex() for f in functions),
        )
        for selector in self.contract.function_selectors:
            self.assertTrue(selector.startswith("0x"))
            self.assertEqual(len(selector), 10)
            self.assertEqual(selector, selector.lower())

    def test_error_signatures_and_selectors_align_by_index(self):
        errors = self.contract.errors
        self.assertEqual(
            self.contract.error_signatures,
            tuple(canonical_error_signature(e) for e in errors),
        )
        self.assertEqual(
            self.contract.error_selectors,
            tuple("0x" + error_selector(e).hex() for e in errors),
        )
        for selector in self.contract.error_selectors:
            self.assertEqual(len(selector), 10)
            self.assertEqual(selector, selector.lower())

    def test_event_signatures_include_anonymous_but_topic0s_do_not(self):
        events = self.contract.events
        self.assertEqual(
            self.contract.event_signatures,
            tuple(
                f"{e.name}("
                + ",".join(format_abi_type(p.abi_type) for p in e.inputs)
                + ")"
                for e in events
            ),
        )
        expected_topic0s = tuple(
            topic0
            for event in events
            if (topic0 := event_topic0(event)) is not None
        )
        self.assertEqual(self.contract.event_topic0s, expected_topic0s)
        # 两个非匿名事件、一个匿名事件：topic0 序列长度为 2 且保持相对顺序。
        self.assertEqual(len(self.contract.event_topic0s), 2)
        self.assertEqual(
            self.contract.event_topic0s[0], event_topic0(events[0])
        )
        self.assertEqual(
            self.contract.event_topic0s[1], event_topic0(events[2])
        )
        for topic0 in self.contract.event_topic0s:
            self.assertTrue(topic0.startswith("0x"))
            self.assertEqual(len(topic0), 66)
            self.assertEqual(topic0, topic0.lower())

    def test_function_and_error_selectors_may_coincide(self):
        # function 与 error 同类之外 selector 相同不构成冲突。白盒方式把
        # 两侧 selector 固定为同一常量：单个 function 与单个 error 仍可解析。
        constant = b"\xab\xcd\x12\x34"
        with mock.patch.object(
            contract_abi_mod, "function_selector", return_value=constant
        ), mock.patch.object(
            contract_abi_mod, "error_selector", return_value=constant
        ):
            contract = build(FUNCTION_F, ERROR_BAD)
        self.assertEqual(contract.function_selectors, ("0xabcd1234",))
        self.assertEqual(contract.error_selectors, ("0xabcd1234",))


class ImmutabilityTests(unittest.TestCase):
    def test_contract_definition_is_frozen(self):
        contract = build(FUNCTION_F, EVENT_E1)
        with self.assertRaises(AttributeError):
            contract.functions = ()
        with self.assertRaises(AttributeError):
            contract.receive = True
        with self.assertRaises(AttributeError):
            contract.function_signatures = ()


class RootInvalidTests(unittest.TestCase):
    def _assert_code(self, abi):
        with self.assertRaises(AbiContractAbiError) as caught:
            parse_contract_abi(abi)
        self.assertEqual(caught.exception.code, "ABI_ROOT_INVALID")

    def test_input_type_errors(self):
        self._assert_code(None)
        self._assert_code(123)
        self._assert_code({})
        self._assert_code(b"[]")
        self._assert_code({"type": "function"})

    def test_malformed_json(self):
        self._assert_code("[")
        self._assert_code('{"type": "function"}')
        self._assert_code("not json")

    def test_json_root_must_be_array(self):
        self._assert_code(json.dumps({"type": "function"}))
        self._assert_code(json.dumps({"a": 1}))
        self._assert_code("null")
        self._assert_code("\"x\"")
        self._assert_code("1")

    def test_only_abi_contract_abi_error_escapes(self):
        for abi in (
            123,
            "{",
            json.dumps({"a": 1}),
        ):
            try:
                parse_contract_abi(abi)
            except AbiContractAbiError:
                pass
            else:
                self.fail("应当抛出 AbiContractAbiError")


class EntryInvalidTests(unittest.TestCase):
    def _assert_code(self, abi):
        with self.assertRaises(AbiContractAbiError) as caught:
            parse_contract_abi(abi)
        self.assertEqual(caught.exception.code, "ABI_ENTRY_INVALID")

    def test_entry_must_be_json_object(self):
        self._assert_code([1])
        self._assert_code(["x"])
        self._assert_code([None])
        self._assert_code([[]])
        self._assert_code([FUNCTION_F, 42])

    def test_unknown_or_missing_entry_type(self):
        self._assert_code([{"name": "f", "inputs": []}])
        self._assert_code([{"type": "wat", "name": "f"}])
        self._assert_code([{"type": "FUNCTION", "name": "f"}])
        self._assert_code([{"type": None, "name": "f"}])
        self._assert_code([{"type": 7, "name": "f"}])

    def test_function_entry_invalid(self):
        # 缺 name / inputs、名称非法、inputs 非数组、参数类型无法形成 ABI。
        self._assert_code([{"type": "function", "inputs": []}])
        self._assert_code([{"type": "function", "name": "f"}])
        self._assert_code([{"type": "function", "name": "1f", "inputs": []}])
        self._assert_code(
            [{"type": "function", "name": "f", "inputs": {}}]
        )
        self._assert_code(
            [{"type": "function", "name": "f", "inputs": [42]}]
        )
        self._assert_code(
            [
                {
                    "type": "function",
                    "name": "f",
                    "inputs": [{"name": "a", "type": "uint9"}],
                }
            ]
        )
        self._assert_code(
            [
                {
                    "type": "function",
                    "name": "f",
                    "inputs": [
                        {"name": "a", "type": "tuple"}
                    ],  # tuple 缺 components
                }
            ]
        )
        self._assert_code(
            [
                {
                    "type": "function",
                    "name": "f",
                    "inputs": [{"name": "a", "type": "uint256"}],
                    "outputs": "nope",
                }
            ]
        )

    def test_error_entry_invalid(self):
        self._assert_code([{"type": "error", "inputs": []}])
        self._assert_code([{"type": "error", "name": "Bad"}])
        self._assert_code(
            [{"type": "error", "name": "Bad", "inputs": [{"type": "??"}]}]
        )

    def test_event_entry_invalid(self):
        self._assert_code([{"type": "event", "inputs": []}])
        self._assert_code(
            [{"type": "event", "name": "E", "inputs": [], "anonymous": 1}]
        )
        self._assert_code(
            [
                {
                    "type": "event",
                    "name": "E",
                    "inputs": [{"name": "v", "type": "bool", "indexed": "y"}],
                }
            ]
        )
        self._assert_code(
            [
                {
                    "type": "event",
                    "name": "E",
                    "inputs": [{"name": "v", "type": "uint257"}],
                }
            ]
        )

    def test_constructor_entry_invalid(self):
        self._assert_code([{"type": "constructor"}])
        self._assert_code(
            [{"type": "constructor", "inputs": [{"type": "addressx"}]}]
        )

    def test_underlying_exceptions_do_not_escape(self):
        # 元数据/事件/类型层异常一律转译，不泄漏到 parse_contract_abi 之外。
        bad_abis = [
            # function 缺 name -> AbiMetadataError
            [{"type": "function", "inputs": []}],
            # event 缺 name -> AbiEventError(EVENT_ABI_INVALID)
            [{"type": "event", "inputs": []}],
            # constructor 参数类型无法解析 -> 转译自类型层错误
            [{"type": "constructor", "inputs": [{"type": "??"}]}],
        ]
        for abi in bad_abis:
            try:
                parse_contract_abi(abi)
            except AbiContractAbiError as exc:
                self.assertNotIsInstance(exc, AbiMetadataError)
                self.assertNotIsInstance(exc, AbiEventError)
            else:
                self.fail("应当抛出 AbiContractAbiError")


class EntryDuplicateTests(unittest.TestCase):
    def _assert_code(self, abi):
        with self.assertRaises(AbiContractAbiError) as caught:
            parse_contract_abi(abi)
        self.assertEqual(caught.exception.code, "ABI_ENTRY_DUPLICATE")

    def test_duplicate_constructor(self):
        self._assert_code(
            [
                {"type": "constructor", "inputs": []},
                {"type": "constructor", "inputs": [{"type": "uint256"}]},
            ]
        )

    def test_duplicate_receive_and_fallback(self):
        self._assert_code([RECEIVE_ENTRY, RECEIVE_ENTRY])
        self._assert_code([FALLBACK_ENTRY, FALLBACK_ENTRY])
        self._assert_code(
            [RECEIVE_ENTRY, FUNCTION_H, RECEIVE_ENTRY]
        )

    def test_receive_fallback_nonempty_or_invalid_inputs(self):
        self._assert_code(
            [{"type": "receive", "inputs": [{"type": "uint256"}]}]
        )
        self._assert_code(
            [{"type": "fallback", "inputs": [{"type": "uint256"}]}]
        )
        # inputs 不是数组（如对象）同样按非空/非法处理。
        self._assert_code([{"type": "receive", "inputs": {}}])
        self._assert_code([{"type": "fallback", "inputs": "x"}])

    def test_empty_or_missing_inputs_still_allowed(self):
        contract = build(
            {"type": "receive"},
            {"type": "fallback", "inputs": []},
        )
        self.assertTrue(contract.receive)
        self.assertTrue(contract.fallback)

    def test_single_constructor_receive_fallback_allowed(self):
        contract = build(
            CONSTRUCTOR_ENTRY, RECEIVE_ENTRY, FALLBACK_ENTRY
        )
        self.assertIsNotNone(contract.constructor)
        self.assertTrue(contract.receive)
        self.assertTrue(contract.fallback)


class SignatureCollisionTests(unittest.TestCase):
    def _assert_code(self, abi):
        with self.assertRaises(AbiContractAbiError) as caught:
            parse_contract_abi(abi)
        self.assertEqual(caught.exception.code, "ABI_SIGNATURE_COLLISION")

    def test_duplicate_function_signature(self):
        self._assert_code([FUNCTION_F, FUNCTION_F])
        # 同名不同参是合法重载；同签名（同名同参类型）才冲突。
        self._assert_code(
            [
                {"type": "function", "name": "f", "inputs": []},
                {"type": "function", "name": "f", "inputs": []},
            ]
        )

    def test_duplicate_error_signature(self):
        self._assert_code([ERROR_EMPTY, ERROR_EMPTY])

    def test_duplicate_event_signature_including_anonymous(self):
        self._assert_code([EVENT_E1, EVENT_E1])
        anon = {"type": "event", "name": "A", "inputs": [], "anonymous": True}
        self._assert_code([anon, dict(anon)])

    def test_overloads_with_distinct_signatures_are_allowed(self):
        f_uint = {
            "type": "function",
            "name": "f",
            "inputs": [{"type": "uint256"}],
        }
        f_addr = {
            "type": "function",
            "name": "f",
            "inputs": [{"type": "address"}],
        }
        contract = build(f_uint, f_addr)
        self.assertEqual(len(contract.functions), 2)
        self.assertEqual(len(contract.function_selectors), 2)
        self.assertNotEqual(
            contract.function_selectors[0], contract.function_selectors[1]
        )

    def test_function_selector_collision_between_distinct_signatures(self):
        # 不同函数签名的四字节 selector 实际相同（哈希碰撞）属
        # ABI_SIGNATURE_COLLISION；白盒固定 selector 触发该分支。
        constant = b"\x00\x11\x22\x33"
        with mock.patch.object(
            contract_abi_mod, "function_selector", return_value=constant
        ):
            self._assert_code([FUNCTION_F, FUNCTION_G])

    def test_error_selector_collision_between_distinct_signatures(self):
        constant = b"\x44\x55\x66\x77"
        with mock.patch.object(
            contract_abi_mod, "error_selector", return_value=constant
        ):
            self._assert_code([ERROR_BAD, ERROR_EMPTY])

    def test_same_named_function_and_error_do_not_collide_by_name(self):
        contract = build(
            {"type": "function", "name": "X", "inputs": []},
            {"type": "error", "name": "X", "inputs": []},
        )
        self.assertEqual(len(contract.functions), 1)
        self.assertEqual(len(contract.errors), 1)


class Topic0CollisionTests(unittest.TestCase):
    def test_distinct_non_anonymous_events_with_same_topic0_collide(self):
        # 不同事件规范签名的 topic0 实际相同需要哈希碰撞；白盒固定
        # event_topic0 触发 ABI_TOPIC0_COLLISION 分支。
        constant = "0x" + "ab" * 32
        with mock.patch.object(
            contract_abi_mod, "event_topic0", return_value=constant
        ):
            with self.assertRaises(AbiContractAbiError) as caught:
                parse_contract_abi([EVENT_E1, EVENT_E2])
        self.assertEqual(caught.exception.code, "ABI_TOPIC0_COLLISION")

    def test_anonymous_events_never_participate_in_topic0_collision(self):
        # 非匿名与匿名事件即使 topic0 计算一致也不冲突：匿名事件直接跳过。
        constant = "0x" + "cd" * 32

        def fake_topic0(event):
            return None if event.anonymous else constant

        with mock.patch.object(
            contract_abi_mod, "event_topic0", side_effect=fake_topic0
        ):
            contract = build(EVENT_E1, EVENT_ANON)
        self.assertEqual(contract.event_topic0s, (constant,))

    def test_identical_signature_reports_signature_collision_first(self):
        # 同签名先命中签名冲突，不会归类为 topic0 冲突。
        with self.assertRaises(AbiContractAbiError) as caught:
            parse_contract_abi([EVENT_E2, EVENT_E2])
        self.assertEqual(caught.exception.code, "ABI_SIGNATURE_COLLISION")

    def test_legit_distinct_events_have_distinct_topic0s(self):
        contract = build(EVENT_E1, EVENT_E2)
        self.assertEqual(len(contract.event_topic0s), 2)
        self.assertNotEqual(
            contract.event_topic0s[0], contract.event_topic0s[1]
        )


class OtherEntryPointsUnaffectedTests(unittest.TestCase):
    def test_existing_parsers_keep_their_own_errors(self):
        # parse_contract_abi 不改变其他入口：未知条目在函数路径下本就抛
        # AbiMetadataError，这里确认既有行为未变。
        from abi_kit import parse_function_abi

        with self.assertRaises(AbiMetadataError):
            parse_function_abi([{"type": "wat"}])
        with self.assertRaises(AbiMetadataError):
            parse_function_abi([{"type": "function", "inputs": []}])


if __name__ == "__main__":
    unittest.main()
