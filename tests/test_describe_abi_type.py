"""describeAbiType 的行为测试。

仅使用标准库 unittest，无第三方依赖。
"""

import copy
import unittest

from abi_kit import RangeError, describeAbiType

KEYS = {"canonical", "kind", "isDynamic", "arrayLength", "base", "components"}


class LeafTypeTests(unittest.TestCase):
    def test_uint_int(self):
        d = describeAbiType({"type": "uint256"})
        self.assertEqual(set(d), KEYS)
        self.assertEqual(d["canonical"], "uint256")
        self.assertEqual(d["kind"], "uint")
        self.assertFalse(d["isDynamic"])
        self.assertIsNone(d["arrayLength"])
        self.assertIsNone(d["base"])
        self.assertIsNone(d["components"])

        d = describeAbiType({"type": "int8"})
        self.assertEqual(d["canonical"], "int8")
        self.assertEqual(d["kind"], "int")
        self.assertFalse(d["isDynamic"])

    def test_address_bool_string(self):
        for type_string, kind, dynamic in (
            ("address", "address", False),
            ("bool", "bool", False),
            ("string", "string", True),
        ):
            with self.subTest(type_string=type_string):
                d = describeAbiType({"type": type_string})
                self.assertEqual(d["canonical"], type_string)
                self.assertEqual(d["kind"], kind)
                self.assertIs(d["isDynamic"], dynamic)
                self.assertIsNone(d["base"])
                self.assertIsNone(d["components"])

    def test_bytes_and_bytesN(self):
        d = describeAbiType({"type": "bytes"})
        self.assertEqual(d["canonical"], "bytes")
        self.assertEqual(d["kind"], "bytes")
        self.assertTrue(d["isDynamic"])

        d = describeAbiType({"type": "bytes32"})
        self.assertEqual(d["canonical"], "bytes32")
        self.assertEqual(d["kind"], "bytesN")
        self.assertFalse(d["isDynamic"])

        d = describeAbiType({"type": "bytes1"})
        self.assertEqual(d["kind"], "bytesN")
        self.assertFalse(d["isDynamic"])

    def test_fixed_ufixed(self):
        for type_string, kind in (
            ("fixed128x18", "fixed"),
            ("ufixed64x8", "ufixed"),
        ):
            with self.subTest(type_string=type_string):
                d = describeAbiType({"type": type_string})
                self.assertEqual(d["canonical"], type_string)
                self.assertEqual(d["kind"], kind)
                self.assertFalse(d["isDynamic"])

    def test_function_type(self):
        d = describeAbiType({"type": "function"})
        self.assertEqual(d["canonical"], "function")
        self.assertEqual(d["kind"], "function")
        self.assertFalse(d["isDynamic"])

    def test_aliases_normalized(self):
        cases = {
            "uint": ("uint256", "uint"),
            "int": ("int256", "int"),
            "byte": ("bytes1", "bytesN"),
            "fixed": ("fixed128x18", "fixed"),
            "ufixed": ("ufixed128x18", "ufixed"),
        }
        for raw, (canonical, kind) in cases.items():
            with self.subTest(raw=raw):
                d = describeAbiType({"type": raw})
                self.assertEqual(d["canonical"], canonical)
                self.assertEqual(d["kind"], kind)


class ArrayTests(unittest.TestCase):
    def test_dynamic_array_of_static(self):
        d = describeAbiType({"type": "address[]"})
        self.assertEqual(d["canonical"], "address[]")
        self.assertEqual(d["kind"], "array")
        self.assertTrue(d["isDynamic"])
        self.assertIsNone(d["arrayLength"])
        self.assertEqual(set(d["base"]), KEYS)
        self.assertEqual(d["base"]["canonical"], "address")
        self.assertEqual(d["base"]["kind"], "address")
        self.assertIsNone(d["base"]["base"])
        self.assertIsNone(d["components"])

    def test_fixed_array_of_static(self):
        d = describeAbiType({"type": "bytes32[2]"})
        self.assertEqual(d["canonical"], "bytes32[2]")
        self.assertEqual(d["kind"], "array")
        self.assertFalse(d["isDynamic"])
        self.assertEqual(d["arrayLength"], 2)
        self.assertEqual(d["base"]["canonical"], "bytes32")
        self.assertEqual(d["base"]["kind"], "bytesN")
        self.assertIsNone(d["components"])

    def test_fixed_array_of_dynamic_is_dynamic(self):
        d = describeAbiType({"type": "string[3]"})
        self.assertFalse(d["arrayLength"] is None)
        self.assertEqual(d["arrayLength"], 3)
        self.assertTrue(d["isDynamic"])
        self.assertEqual(d["base"]["kind"], "string")

    def test_nested_array_chain(self):
        d = describeAbiType({"type": "uint256[][3]"})
        self.assertEqual(d["canonical"], "uint256[][3]")
        self.assertEqual(d["kind"], "array")
        # 外层定长、内层动态元素 => 整体动态。
        self.assertTrue(d["isDynamic"])
        self.assertEqual(d["arrayLength"], 3)

        inner = d["base"]
        self.assertEqual(inner["kind"], "array")
        self.assertIsNone(inner["arrayLength"])
        self.assertTrue(inner["isDynamic"])
        self.assertEqual(inner["base"]["canonical"], "uint256")
        self.assertEqual(inner["base"]["kind"], "uint")

    def test_all_fixed_chain_static(self):
        d = describeAbiType({"type": "bool[2][3]"})
        self.assertFalse(d["isDynamic"])
        self.assertEqual(d["arrayLength"], 3)
        inner = d["base"]
        self.assertEqual(inner["arrayLength"], 2)
        self.assertFalse(inner["isDynamic"])
        self.assertEqual(inner["base"]["kind"], "bool")

    def test_array_of_non_tuple_has_no_components(self):
        d = describeAbiType({"type": "uint256[1][2]"})
        self.assertIsNone(d["components"])
        self.assertIsNone(d["base"]["components"])
        self.assertIsNone(d["base"]["base"]["components"])


class TupleTests(unittest.TestCase):
    def test_simple_tuple(self):
        node = {
            "type": "tuple",
            "components": [
                {"type": "bytes32", "name": "hash"},
                {"type": "uint256", "name": "amount"},
            ],
        }
        d = describeAbiType(node)
        self.assertEqual(set(d), KEYS)
        self.assertEqual(d["canonical"], "(bytes32,uint256)")
        self.assertEqual(d["kind"], "tuple")
        self.assertFalse(d["isDynamic"])
        self.assertIsNone(d["arrayLength"])
        self.assertIsNone(d["base"])
        self.assertEqual(len(d["components"]), 2)
        self.assertEqual(d["components"][0]["canonical"], "bytes32")
        self.assertEqual(d["components"][0]["kind"], "bytesN")
        self.assertIsNone(d["components"][0]["components"])
        self.assertEqual(d["components"][1]["canonical"], "uint256")

    def test_empty_tuple(self):
        d = describeAbiType({"type": "tuple", "components": []})
        self.assertEqual(d["canonical"], "()")
        self.assertEqual(d["kind"], "tuple")
        self.assertFalse(d["isDynamic"])
        self.assertEqual(d["components"], [])
        self.assertIsNone(d["base"])

    def test_tuple_with_dynamic_member(self):
        d = describeAbiType(
            {
                "type": "tuple",
                "components": [{"type": "address"}, {"type": "string"}],
            }
        )
        self.assertEqual(d["canonical"], "(address,string)")
        self.assertTrue(d["isDynamic"])
        self.assertFalse(d["components"][0]["isDynamic"])
        self.assertTrue(d["components"][1]["isDynamic"])

    def test_tuple_array_fixed_length(self):
        node = {
            "type": "tuple[2]",
            "components": [{"type": "bytes32"}, {"type": "uint256"}],
        }
        d = describeAbiType(node)
        self.assertEqual(d["canonical"], "(bytes32,uint256)[2]")
        self.assertEqual(d["kind"], "array")
        self.assertFalse(d["isDynamic"])
        self.assertEqual(d["arrayLength"], 2)
        # base 是最内层 tuple 诊断。
        self.assertEqual(d["base"]["kind"], "tuple")
        self.assertEqual(d["base"]["canonical"], "(bytes32,uint256)")
        self.assertEqual([c["canonical"] for c in d["base"]["components"]],
                         ["bytes32", "uint256"])
        # 每层数组都给出最内层 tuple 的子诊断。
        self.assertEqual(
            [c["canonical"] for c in d["components"]],
            ["bytes32", "uint256"],
        )
        self.assertIs(d["components"], d["base"]["components"])

    def test_tuple_dynamic_array(self):
        d = describeAbiType(
            {
                "type": "tuple[]",
                "components": [{"type": "bytes32"}, {"type": "uint256"}],
            }
        )
        self.assertEqual(d["canonical"], "(bytes32,uint256)[]")
        self.assertEqual(d["kind"], "array")
        self.assertTrue(d["isDynamic"])
        self.assertIsNone(d["arrayLength"])
        self.assertEqual(d["base"]["kind"], "tuple")
        self.assertEqual(len(d["components"]), 2)

    def test_tuple_array_chain_components_each_level(self):
        node = {
            # 从内到外：tuple 定长 2、再动态数组、再定长 4。
            "type": "tuple[2][][4]",
            "components": [
                {"type": "bytes32"},
                {"type": "uint256"},
            ],
        }
        d = describeAbiType(node)
        self.assertEqual(d["canonical"], "(bytes32,uint256)[2][][4]")
        self.assertEqual(d["arrayLength"], 4)
        # 外层定长，但元素（中层动态数组）动态 => 整体动态。
        self.assertTrue(d["isDynamic"])

        level1 = d["base"]
        self.assertEqual(level1["kind"], "array")
        self.assertIsNone(level1["arrayLength"])
        self.assertTrue(level1["isDynamic"])

        level2 = level1["base"]
        self.assertEqual(level2["kind"], "array")
        self.assertEqual(level2["arrayLength"], 2)
        self.assertFalse(level2["isDynamic"])

        innermost = level2["base"]
        self.assertEqual(innermost["kind"], "tuple")
        self.assertEqual(innermost["canonical"], "(bytes32,uint256)")

        # 每层数组的 components 都给出最内层 tuple 的子诊断。
        for level in (d, level1, level2):
            self.assertEqual(
                [c["canonical"] for c in level["components"]],
                ["bytes32", "uint256"],
            )
            self.assertIs(level["components"], innermost["components"])
        self.assertIsNone(innermost["base"])

    def test_tuple_with_static_member_array(self):
        d = describeAbiType(
            {
                "type": "tuple",
                "components": [
                    {"type": "address[2]"},
                    {"type": "bool"},
                ],
            }
        )
        self.assertEqual(d["canonical"], "(address[2],bool)")
        self.assertFalse(d["isDynamic"])
        member = d["components"][0]
        self.assertEqual(member["kind"], "array")
        self.assertEqual(member["arrayLength"], 2)
        # 非 tuple 数组叶子：components 为 None。
        self.assertIsNone(member["components"])
        self.assertEqual(member["base"]["kind"], "address")

    def test_nested_tuple_components(self):
        node = {
            "type": "tuple",
            "components": [
                {"type": "uint8"},
                {
                    "type": "tuple",
                    "components": [
                        {"type": "address"},
                        {"type": "string[]"},
                    ],
                },
            ],
        }
        d = describeAbiType(node)
        self.assertEqual(d["canonical"], "(uint8,(address,string[]))")
        self.assertTrue(d["isDynamic"])
        inner = d["components"][1]
        self.assertEqual(inner["kind"], "tuple")
        self.assertEqual(inner["canonical"], "(address,string[])")
        self.assertTrue(inner["isDynamic"])
        self.assertIsNone(d["base"])
        self.assertIsNone(inner["base"])
        # 内层数组成员的 components 为 None（元素是 string 叶子）。
        string_array = inner["components"][1]
        self.assertEqual(string_array["kind"], "array")
        self.assertIsNone(string_array["components"])

    def test_nested_tuple_inside_array(self):
        node = {
            "type": "tuple[]",
            "components": [
                {"type": "uint256"},
                {
                    "type": "tuple[3]",
                    "components": [{"type": "bytes32"}, {"type": "bool"}],
                },
            ],
        }
        d = describeAbiType(node)
        self.assertEqual(d["canonical"], "(uint256,(bytes32,bool)[3])[]")
        self.assertTrue(d["isDynamic"])
        outer_tuple = d["base"]
        self.assertEqual(outer_tuple["kind"], "tuple")
        inner_array = outer_tuple["components"][1]
        self.assertEqual(inner_array["kind"], "array")
        self.assertEqual(inner_array["arrayLength"], 3)
        self.assertEqual(inner_array["base"]["kind"], "tuple")
        self.assertEqual(
            [c["canonical"] for c in inner_array["components"]],
            ["bytes32", "bool"],
        )
        # 外层数组链透传的是最外层 tuple（自己的直接元素）诊断。
        self.assertEqual(
            [c["canonical"] for c in d["components"]],
            ["uint256", "(bytes32,bool)[3]"],
        )

    def test_empty_tuple_array(self):
        d = describeAbiType({"type": "tuple[2][]", "components": []})
        self.assertEqual(d["canonical"], "()[2][]")
        self.assertTrue(d["isDynamic"])  # 外层动态数组
        fixed_layer = d["base"]
        self.assertEqual(fixed_layer["arrayLength"], 2)
        self.assertFalse(fixed_layer["isDynamic"])
        self.assertEqual(fixed_layer["base"]["kind"], "tuple")
        self.assertEqual(d["components"], [])
        self.assertEqual(fixed_layer["components"], [])


class NameAndStabilityTests(unittest.TestCase):
    def test_names_do_not_enter_result(self):
        node = {
            "name": "root",
            "type": "tuple[]",
            "components": [
                {"name": "to", "type": "address", "indexed": True},
                {"name": "amount", "type": "uint256", "extra": 1},
            ],
            "internalType": "struct Payment",
        }
        d = describeAbiType(node)
        self.assertEqual(d["canonical"], "(address,uint256)[]")
        self.assertNotIn("name", d)
        self.assertNotIn("indexed", d["base"]["components"][0])

    def test_same_input_same_output(self):
        node = {
            "type": "tuple[2][]",
            "components": [
                {"type": "bytes32"},
                {"type": "tuple", "components": [{"type": "string"}]},
            ],
        }
        first = describeAbiType(copy.deepcopy(node))
        second = describeAbiType(copy.deepcopy(node))
        self.assertEqual(first, second)
        # 不修改输入。
        self.assertEqual(
            node,
            {
                "type": "tuple[2][]",
                "components": [
                    {"type": "bytes32"},
                    {"type": "tuple", "components": [{"type": "string"}]},
                ],
            },
        )

    def test_extra_fields_ignored(self):
        d = describeAbiType({"type": "uint256", "name": "x", "internalType": "uint256"})
        self.assertEqual(set(d), KEYS)
        self.assertEqual(d["canonical"], "uint256")


class TypeErrorTests(unittest.TestCase):
    def assertTypeError(self, node):
        with self.assertRaises(TypeError):
            describeAbiType(node)

    def test_input_not_dict(self):
        for bad in (None, 42, "uint256", ["uint256"], (), True):
            with self.subTest(bad=bad):
                self.assertTypeError(bad)

    def test_type_missing_or_not_string(self):
        for bad in ({}, {"type": None}, {"type": 1}, {"type": []},
                    {"type": True}, {"components": []}):
            with self.subTest(bad=bad):
                self.assertTypeError(bad)

    def test_tuple_missing_components(self):
        self.assertTypeError({"type": "tuple"})
        self.assertTypeError({"type": "tuple[]"})
        self.assertTypeError({"type": "tuple[2]"})

    def test_tuple_components_not_list(self):
        for bad in (None, (), "x", 3, {"type": "uint256"}, True):
            with self.subTest(bad=bad):
                self.assertTypeError({"type": "tuple", "components": bad})
                self.assertTypeError({"type": "tuple[]", "components": bad})

    def test_nested_component_not_dict(self):
        with self.assertRaises(TypeError) as ctx:
            describeAbiType(
                {"type": "tuple", "components": [{"type": "uint256"}, 7]}
            )
        self.assertIn("components[1]", str(ctx.exception))

    def test_nested_type_not_string_points_at_level(self):
        node = {
            "type": "tuple",
            "components": [
                {
                    "type": "tuple",
                    "components": [
                        {"type": "uint256"},
                        {"type": "tuple", "components": [{"type": 5}]},
                    ],
                }
            ],
        }
        with self.assertRaises(TypeError) as ctx:
            describeAbiType(node)
        message = str(ctx.exception)
        self.assertIn("components[0].components[1].components[0].type", message)

    def test_nested_tuple_missing_components_points_at_level(self):
        node = {
            "type": "tuple",
            "components": [{"type": "uint256"}, {"type": "tuple[]"}],
        }
        with self.assertRaises(TypeError) as ctx:
            describeAbiType(node)
        self.assertIn("components[1].components", str(ctx.exception))


class RangeErrorTests(unittest.TestCase):
    def assertRangeError(self, node):
        with self.assertRaises(RangeError):
            describeAbiType(node)

    def test_bad_elementary_syntax(self):
        for bad in ("", "uint0", "uint7", "uint264", "int9", "bytes0",
                    "bytes33", "hash", "address20", "boolean", "uint08",
                    "bytes03", "ui nt", "uint256 x", "fixed128",
                    "ufixed0x18", "fixed128x0", "fixed264x18"):
            with self.subTest(bad=bad):
                # 空字符串是类型语法非法（type 字段本身仍是字符串）。
                self.assertRangeError({"type": bad})

    def test_bad_array_syntax_and_length(self):
        for bad in ("address[0]", "address[01]", "address[001]",
                    "uint256[", "uint256[]]", "uint256[-1]", "uint256[1a]",
                    "uint256[1 []"):
            with self.subTest(bad=bad):
                self.assertRangeError({"type": bad})

    def test_bad_tuple_suffix(self):
        for bad in ("tuple[x]", "tuple[0]", "tuple[01]", "tuple[",
                    "tuple[]x", "tuple [2]"):
            with self.subTest(bad=bad):
                self.assertRangeError(
                    {"type": bad, "components": [{"type": "uint256"}]}
                )

    def test_non_tuple_with_components(self):
        for bad in ("uint256", "address[]", "bytes32[2]", "(uint256,uint256)"):
            with self.subTest(bad=bad):
                self.assertRangeError(
                    {"type": bad, "components": [{"type": "uint256"}]}
                )

    def test_nested_bad_type_points_at_level(self):
        node = {
            "type": "tuple[]",
            "components": [
                {"type": "uint256"},
                {
                    "type": "tuple",
                    "components": [{"type": "uint7"}],
                },
            ],
        }
        with self.assertRaises(RangeError) as ctx:
            describeAbiType(node)
        self.assertIn("components[1].components[0].type", str(ctx.exception))

    def test_nested_non_tuple_components_points_at_level(self):
        node = {
            "type": "tuple",
            "components": [
                {
                    "type": "tuple",
                    "components": [
                        {"type": "address", "components": [{"type": "bool"}]}
                    ],
                }
            ],
        }
        with self.assertRaises(RangeError) as ctx:
            describeAbiType(node)
        self.assertIn(
            "components[0].components[0].components", str(ctx.exception)
        )

    def test_nested_bad_array_length_in_tuple(self):
        node = {
            "type": "tuple[2]",
            "components": [
                {"type": "tuple[]",
                 "components": [{"type": "tuple[00]", "components": []}]},
            ],
        }
        with self.assertRaises(RangeError) as ctx:
            describeAbiType(node)
        self.assertIn("components[0].components[0].type", str(ctx.exception))

    def test_messages_contain_no_file_path(self):
        node = {"type": "tuple", "components": [{"type": "uint7"}]}
        try:
            describeAbiType(node)
        except RangeError as exc:
            message = str(exc)
        else:
            self.fail("应当抛出 RangeError")
        self.assertNotIn("/", message)
        self.assertNotIn(".py", message)

    def test_same_input_same_exception(self):
        node1 = {"type": "tuple", "components": [{"type": "uint7"}]}
        node2 = copy.deepcopy(node1)
        with self.assertRaises(RangeError) as ctx1:
            describeAbiType(node1)
        with self.assertRaises(RangeError) as ctx2:
            describeAbiType(node2)
        self.assertEqual(str(ctx1.exception), str(ctx2.exception))

        node3 = {"type": "tuple[]"}
        node4 = copy.deepcopy(node3)
        with self.assertRaises(TypeError) as ctx3:
            describeAbiType(node3)
        with self.assertRaises(TypeError) as ctx4:
            describeAbiType(node4)
        self.assertEqual(str(ctx3.exception), str(ctx4.exception))


class DynamicRulesTests(unittest.TestCase):
    def describe(self, type_string, components=None):
        node = {"type": type_string}
        if components is not None:
            node["components"] = components
        return describeAbiType(node)

    def test_static_kinds(self):
        for type_string in (
            "uint8", "uint256", "int256", "address", "bool", "bytes1",
            "bytes32", "fixed128x18", "ufixed128x18", "function",
            "uint256[3]", "address[2][4]", "bool[1]",
        ):
            with self.subTest(type_string=type_string):
                self.assertFalse(self.describe(type_string)["isDynamic"])

    def test_dynamic_kinds(self):
        for type_string in (
            "bytes", "string", "uint256[]", "address[2][]",
            "string[3]", "bytes[2]", "uint256[][3]",
        ):
            with self.subTest(type_string=type_string):
                self.assertTrue(self.describe(type_string)["isDynamic"])

    def test_tuple_dynamic_rules(self):
        static_components = [{"type": "uint256"}, {"type": "bytes32"}]
        dynamic_components = [{"type": "uint256"}, {"type": "bytes"}]
        self.assertFalse(
            self.describe("tuple", static_components)["isDynamic"]
        )
        self.assertTrue(
            self.describe("tuple", dynamic_components)["isDynamic"]
        )
        # tuple 本体静态、定长数组静态。
        self.assertFalse(
            self.describe("tuple[5]", static_components)["isDynamic"]
        )
        # 含动态成员的 tuple 即使放在定长数组中仍动态。
        self.assertTrue(
            self.describe("tuple[5]", dynamic_components)["isDynamic"]
        )
        # 动态数组恒动态。
        self.assertTrue(
            self.describe("tuple[]", static_components)["isDynamic"]
        )
        self.assertTrue(
            self.describe("tuple[]", dynamic_components)["isDynamic"]
        )
        # 空 tuple 静态。
        self.assertFalse(self.describe("tuple", [])["isDynamic"])


class DepthTests(unittest.TestCase):
    @staticmethod
    def _nested_tuples(levels, suffix=""):
        node = {"type": "uint8" + suffix}
        for _ in range(levels - 1):
            node = {"type": "tuple", "components": [node]}
        return node

    def test_128_tuple_layers_ok(self):
        d = describeAbiType(self._nested_tuples(128))
        self.assertEqual(d["canonical"].count("("), 127)
        self.assertEqual(d["kind"], "tuple")

    def test_129_tuple_layers_range_error(self):
        with self.assertRaises(RangeError):
            describeAbiType(self._nested_tuples(129))

    def test_array_layers_in_type_text_counted(self):
        d = describeAbiType({"type": "uint8" + "[]" * 127})
        self.assertEqual(d["kind"], "array")
        with self.assertRaises(RangeError):
            describeAbiType({"type": "uint8" + "[]" * 128})

    def test_tuple_suffix_layers_counted(self):
        # 127 层 tuple 组件 + 1 层 tuple[] 数组后缀 = 128，合法；
        # 再加一层数组后缀为 129，非法。
        node = self._nested_tuples(127)
        node["type"] = "tuple[]"
        d = describeAbiType(node)
        self.assertEqual(d["kind"], "array")
        node2 = self._nested_tuples(127)
        node2["type"] = "tuple[][]"
        with self.assertRaises(RangeError):
            describeAbiType(node2)


if __name__ == "__main__":
    unittest.main()
