"""describeAbiType 的行为测试。

仅使用标准库 unittest，无第三方依赖。
"""

import copy
import unittest

from abi_kit import RangeError, describeAbiType, parse_abi_type, format_abi_type

KEYS = ("canonical", "kind", "isDynamic", "arrayLength", "base", "components")


def _leaf(canonical, kind, is_dynamic):
    return {
        "canonical": canonical,
        "kind": kind,
        "isDynamic": is_dynamic,
        "arrayLength": None,
        "base": None,
        "components": None,
    }


class LeafTypeTests(unittest.TestCase):
    def test_uint_int_alias_normalized(self):
        for node, canonical, kind in (
            ({"type": "uint256"}, "uint256", "uint"),
            ({"type": "uint"}, "uint256", "uint"),
            ({"type": "int"}, "int256", "int"),
            ({"type": "uint8"}, "uint8", "uint"),
            ({"type": "int248"}, "int248", "int"),
        ):
            with self.subTest(canonical=canonical):
                self.assertEqual(describeAbiType(node), _leaf(canonical, kind, False))

    def test_static_elementary_kinds(self):
        for type_text, kind in (
            ("address", "address"),
            ("bool", "bool"),
            ("bytes32", "bytesN"),
            ("bytes1", "bytesN"),
            ("function", "function"),
            ("ufixed128x18", "ufixed"),
            ("fixed128x18", "fixed"),
            ("fixed", "fixed"),
            ("ufixed", "ufixed"),
            ("byte", "bytesN"),
        ):
            with self.subTest(type_text=type_text):
                result = describeAbiType({"type": type_text})
                self.assertEqual(result["kind"], kind)
                self.assertFalse(result["isDynamic"])

    def test_dynamic_elementary(self):
        self.assertEqual(
            describeAbiType({"type": "string"}), _leaf("string", "string", True)
        )
        self.assertEqual(
            describeAbiType({"type": "bytes"}), _leaf("bytes", "bytes", True)
        )

    def test_byte_alias_canonical(self):
        self.assertEqual(describeAbiType({"type": "byte"})["canonical"], "bytes1")


class ArrayTests(unittest.TestCase):
    def test_dynamic_array(self):
        result = describeAbiType({"type": "address[]"})
        self.assertEqual(
            result,
            {
                "canonical": "address[]",
                "kind": "array",
                "isDynamic": True,
                "arrayLength": None,
                "base": _leaf("address", "address", False),
                "components": None,
            },
        )

    def test_fixed_array_static_element(self):
        result = describeAbiType({"type": "bytes32[2]"})
        self.assertEqual(result["canonical"], "bytes32[2]")
        self.assertEqual(result["kind"], "array")
        self.assertFalse(result["isDynamic"])
        self.assertEqual(result["arrayLength"], 2)
        self.assertEqual(result["base"], _leaf("bytes32", "bytesN", False))
        self.assertIsNone(result["components"])

    def test_fixed_array_dynamic_element_is_dynamic(self):
        result = describeAbiType({"type": "string[3]"})
        self.assertTrue(result["isDynamic"])
        self.assertEqual(result["arrayLength"], 3)
        self.assertEqual(result["base"]["canonical"], "string")

    def test_nested_arrays(self):
        result = describeAbiType({"type": "uint256[2][3]"})
        self.assertEqual(result["canonical"], "uint256[2][3]")
        self.assertFalse(result["isDynamic"])
        self.assertEqual(result["arrayLength"], 3)
        self.assertIsNone(result["components"])
        inner = result["base"]
        self.assertEqual(inner["canonical"], "uint256[2]")
        self.assertEqual(inner["kind"], "array")
        self.assertFalse(inner["isDynamic"])
        self.assertEqual(inner["arrayLength"], 2)
        self.assertEqual(inner["base"], _leaf("uint256", "uint", False))
        self.assertIsNone(inner["components"])

    def test_nested_arrays_dynamic_outer(self):
        result = describeAbiType({"type": "uint256[2][]"})
        self.assertTrue(result["isDynamic"])
        self.assertIsNone(result["arrayLength"])
        self.assertFalse(result["base"]["isDynamic"])

    def test_nested_arrays_dynamic_inner(self):
        # 定长外层、动态内层：元素动态，整体也动态。
        result = describeAbiType({"type": "uint256[][2]"})
        self.assertTrue(result["isDynamic"])
        self.assertEqual(result["arrayLength"], 2)
        self.assertTrue(result["base"]["isDynamic"])


class TupleTests(unittest.TestCase):
    def test_tuple_basic(self):
        node = {
            "type": "tuple",
            "components": [{"type": "bytes32"}, {"type": "uint256"}],
        }
        result = describeAbiType(node)
        self.assertEqual(
            result,
            {
                "canonical": "(bytes32,uint256)",
                "kind": "tuple",
                "isDynamic": False,
                "arrayLength": None,
                "base": None,
                "components": [
                    _leaf("bytes32", "bytesN", False),
                    _leaf("uint256", "uint", False),
                ],
            },
        )

    def test_empty_tuple(self):
        self.assertEqual(
            describeAbiType({"type": "tuple", "components": []}),
            {
                "canonical": "()",
                "kind": "tuple",
                "isDynamic": False,
                "arrayLength": None,
                "base": None,
                "components": [],
            },
        )

    def test_tuple_dynamic_member(self):
        result = describeAbiType(
            {
                "type": "tuple",
                "components": [{"type": "bytes32"}, {"type": "string"}],
            }
        )
        self.assertTrue(result["isDynamic"])
        self.assertEqual(result["canonical"], "(bytes32,string)")

    def test_tuple_fixed_array(self):
        node = {
            "type": "tuple[2]",
            "components": [{"type": "bytes32"}, {"type": "uint256"}],
        }
        result = describeAbiType(node)
        self.assertEqual(result["canonical"], "(bytes32,uint256)[2]")
        self.assertEqual(result["kind"], "array")
        self.assertFalse(result["isDynamic"])
        self.assertEqual(result["arrayLength"], 2)
        # 每层 components 给出最内层 tuple 的子诊断。
        self.assertEqual(
            result["components"],
            [
                _leaf("bytes32", "bytesN", False),
                _leaf("uint256", "uint", False),
            ],
        )
        base = result["base"]
        self.assertEqual(base["kind"], "tuple")
        self.assertEqual(base["canonical"], "(bytes32,uint256)")
        self.assertEqual(base["components"], result["components"])
        self.assertIsNone(base["base"])

    def test_tuple_dynamic_array(self):
        node = {
            "type": "tuple[]",
            "components": [{"type": "uint256"}],
        }
        result = describeAbiType(node)
        self.assertEqual(result["canonical"], "(uint256)[]")
        self.assertEqual(result["kind"], "array")
        self.assertTrue(result["isDynamic"])
        self.assertIsNone(result["arrayLength"])
        self.assertEqual(result["components"][0], _leaf("uint256", "uint", False))

    def test_tuple_nested_array_chain_components_each_level(self):
        node = {
            "type": "tuple[2][3]",
            "components": [{"type": "bytes32"}, {"type": "uint256"}],
        }
        result = describeAbiType(node)
        self.assertEqual(result["canonical"], "(bytes32,uint256)[2][3]")
        self.assertEqual(result["arrayLength"], 3)
        middle = result["base"]
        self.assertEqual(middle["kind"], "array")
        self.assertEqual(middle["arrayLength"], 2)
        inner = middle["base"]
        self.assertEqual(inner["kind"], "tuple")
        expected_components = [
            _leaf("bytes32", "bytesN", False),
            _leaf("uint256", "uint", False),
        ]
        self.assertEqual(result["components"], expected_components)
        self.assertEqual(middle["components"], expected_components)
        self.assertEqual(inner["components"], expected_components)

    def test_tuple_with_dynamic_member_in_fixed_array(self):
        node = {
            "type": "tuple[2]",
            "components": [{"type": "address"}, {"type": "bytes"}],
        }
        result = describeAbiType(node)
        self.assertTrue(result["isDynamic"])
        self.assertTrue(result["base"]["isDynamic"])

    def test_nested_tuple_components(self):
        node = {
            "type": "tuple",
            "components": [
                {
                    "type": "tuple",
                    "components": [{"type": "uint256"}, {"type": "address[]"}],
                },
                {"type": "bool"},
            ],
        }
        result = describeAbiType(node)
        self.assertEqual(result["canonical"], "((uint256,address[]),bool)")
        self.assertTrue(result["isDynamic"])
        outer_children = result["components"]
        self.assertEqual(len(outer_children), 2)
        nested = outer_children[0]
        self.assertEqual(nested["kind"], "tuple")
        self.assertTrue(nested["isDynamic"])
        self.assertEqual(nested["components"][0], _leaf("uint256", "uint", False))
        self.assertEqual(nested["components"][1]["kind"], "array")
        self.assertTrue(nested["components"][1]["isDynamic"])
        self.assertEqual(outer_children[1], _leaf("bool", "bool", False))

    def test_nested_tuple_array_component(self):
        node = {
            "type": "tuple[]",
            "components": [
                {"type": "tuple[2]", "components": [{"type": "uint256"}]},
            ],
        }
        result = describeAbiType(node)
        self.assertEqual(result["canonical"], "((uint256)[2])[]")
        nested = result["components"][0]
        self.assertEqual(nested["kind"], "array")
        self.assertEqual(nested["arrayLength"], 2)
        self.assertEqual(
            nested["components"], [_leaf("uint256", "uint", False)]
        )

    def test_array_of_tuple_via_text_syntax(self):
        # 类型文本直接写 tuple 语法时同样递归展开 components。
        result = describeAbiType({"type": "(uint256,bool)[2]"})
        self.assertEqual(result["canonical"], "(uint256,bool)[2]")
        self.assertEqual(result["kind"], "array")
        self.assertEqual(result["arrayLength"], 2)
        self.assertEqual(
            result["components"],
            [_leaf("uint256", "uint", False), _leaf("bool", "bool", False)],
        )


class CanonicalConsistencyTests(unittest.TestCase):
    def test_canonical_matches_parser_for_simple_types(self):
        for type_text in (
            "uint256", "uint", "int8", "address", "bool", "bytes", "bytes32",
            "string", "function", "fixed128x18", "ufixed80x10",
            "address[]", "uint256[3]", "bytes32[2][]", "(uint256,bool)",
            "(address,bytes32)[2]", "((uint256),string[])",
        ):
            with self.subTest(type_text=type_text):
                result = describeAbiType({"type": type_text})
                self.assertEqual(result["canonical"], format_abi_type(parse_abi_type(type_text)))

    def test_result_has_only_declared_keys_recursively(self):
        node = {
            "type": "tuple[2][]",
            "components": [
                {"type": "tuple", "components": [{"type": "uint256[]"}]},
                {"type": "bytes32"},
            ],
        }

        def walk(diagnostic, path):
            self.assertEqual(
                set(diagnostic.keys()), set(KEYS), msg=f"键集合不一致：{path}"
            )
            if diagnostic["base"] is not None:
                walk(diagnostic["base"], path + ".base")
            for index, child in enumerate(diagnostic["components"] or []):
                walk(child, f"{path}.components[{index}]")

        walk(describeAbiType(node), "root")

    def test_names_never_enter_result(self):
        node = {
            "type": "tuple",
            "name": "container",
            "components": [
                {"type": "uint256", "name": "amount"},
                {
                    "type": "tuple[]",
                    "name": "rows",
                    "components": [
                        {"type": "address", "name": "who"},
                    ],
                },
            ],
        }
        text = repr(describeAbiType(node))
        for forbidden in ("container", "amount", "rows", "who"):
            self.assertNotIn(forbidden, text)

    def test_deterministic_output(self):
        node = {
            "type": "tuple[2]",
            "components": [{"type": "bytes32"}, {"type": "uint256"}],
        }
        first = describeAbiType(copy.deepcopy(node))
        second = describeAbiType(copy.deepcopy(node))
        self.assertEqual(first, second)
        self.assertEqual(repr(first), repr(second))


class TypeErrorTests(unittest.TestCase):
    def assert_type_error(self, node):
        with self.assertRaises(TypeError) as ctx:
            describeAbiType(node)
        self.assertNotIn("/", str(ctx.exception))
        self.assertNotIn(".py", str(ctx.exception))

    def test_root_not_dict(self):
        for bad in (None, 42, "uint256", ["uint256"], (), True):
            with self.subTest(bad=bad):
                self.assert_type_error(bad)

    def test_type_missing_or_not_string(self):
        self.assert_type_error({})
        self.assert_type_error({"components": []})
        for bad_type in (None, 1, [], (), True, {}):
            self.assert_type_error({"type": bad_type})

    def test_tuple_missing_components(self):
        self.assert_type_error({"type": "tuple"})
        self.assert_type_error({"type": "tuple[]"})
        self.assert_type_error({"type": "tuple[2]"})

    def test_components_not_list(self):
        self.assert_type_error({"type": "tuple", "components": ()})
        self.assert_type_error({"type": "tuple", "components": {}})
        self.assert_type_error({"type": "tuple", "components": None})
        self.assert_type_error({"type": "tuple", "components": "nope"})

    def test_nested_component_not_dict(self):
        with self.assertRaises(TypeError) as ctx:
            describeAbiType(
                {"type": "tuple", "components": [{"type": "uint256"}, "uint256"]}
            )
        message = str(ctx.exception)
        self.assertIn("components[1]", message)

    def test_nested_tuple_missing_components_points_to_path(self):
        with self.assertRaises(TypeError) as ctx:
            describeAbiType(
                {
                    "type": "tuple",
                    "components": [
                        {"type": "tuple[2]"},
                    ],
                }
            )
        message = str(ctx.exception)
        self.assertIn("components[0]", message)


class RangeErrorTests(unittest.TestCase):
    def assert_range_error(self, node):
        with self.assertRaises(RangeError) as ctx:
            describeAbiType(node)
        message = str(ctx.exception)
        self.assertNotIn("/", message)
        self.assertNotIn(".py", message)
        return message

    def test_empty_type_text(self):
        self.assert_range_error({"type": ""})

    def test_bad_type_syntax(self):
        for bad in ("hash", "uint256,", "(uint256",
                    "uint256)", "uint[]256", "bool[]true", "addres",
                    "uint256[", "uint256[abc]", "uint256[0x1]", "tuple[",
                    "tuple[x]", "tuple[[]]", "tuple[]x", "tuple [2]"):
            with self.subTest(bad=bad):
                self.assert_range_error({"type": bad})

    def test_surrounding_whitespace_normalized_away(self):
        # 与 parse_abi_type 既有行为一致：两端空白被接受，canonical 无空白。
        for raw in ("uint256 ", " uint256", "  uint256  ", "(uint256, bool)[2]"):
            with self.subTest(raw=raw):
                result = describeAbiType({"type": raw})
                self.assertNotIn(" ", result["canonical"])
        self.assertEqual(
            describeAbiType({"type": " uint256 "})["canonical"], "uint256"
        )

    def test_bad_integer_width(self):
        for bad in ("uint0", "uint7", "uint9", "uint264", "int5", "int08",
                    "uint123"):
            with self.subTest(bad=bad):
                self.assert_range_error({"type": bad})

    def test_bad_bytes_n(self):
        for bad in ("bytes0", "bytes33", "bytes03", "bytes100"):
            with self.subTest(bad=bad):
                self.assert_range_error({"type": bad})

    def test_bad_fixed(self):
        for bad in ("fixed128", "fixed0x18", "fixed7x18", "fixed128x0",
                    "fixed128x81", "ufixed129x18", "fixed128x018"):
            with self.subTest(bad=bad):
                self.assert_range_error({"type": bad})

    def test_bad_array_length(self):
        for bad in ("uint256[0]", "uint256[01]", "uint256[-1]", "uint256[1.5]"):
            with self.subTest(bad=bad):
                self.assert_range_error({"type": bad})

    def test_tuple_bad_suffix(self):
        node = {"type": "tuple[0]", "components": []}
        self.assert_range_error(node)
        node = {"type": "tuple[01]", "components": []}
        self.assert_range_error(node)
        node = {"type": "tuple[x]", "components": []}
        self.assert_range_error(node)

    def test_components_on_non_tuple(self):
        message = self.assert_range_error(
            {"type": "uint256", "components": []}
        )
        self.assertIn("components", message)
        self.assert_range_error(
            {"type": "uint256[]", "components": [{"type": "uint256"}]}
        )

    def test_nested_bad_type_reports_level(self):
        node = {
            "type": "tuple[2]",
            "components": [
                {"type": "uint256"},
                {"type": "tuple", "components": [{"type": "uint7"}]},
            ],
        }
        with self.assertRaises(RangeError) as ctx:
            describeAbiType(node)
        message = str(ctx.exception)
        self.assertIn("components[1]", message)
        self.assertIn("components[0]", message)

    def test_deterministic_exception(self):
        node = {"type": "uint7"}
        with self.assertRaises(RangeError) as first:
            describeAbiType(copy.deepcopy(node))
        with self.assertRaises(RangeError) as second:
            describeAbiType(copy.deepcopy(node))
        self.assertEqual(str(first.exception), str(second.exception))

        bad = {"type": "tuple", "components": []}
        bad["components"] = None
        with self.assertRaises(TypeError) as first:
            describeAbiType(copy.deepcopy(bad))
        with self.assertRaises(TypeError) as second:
            describeAbiType(copy.deepcopy(bad))
        self.assertEqual(str(first.exception), str(second.exception))

    def test_range_error_is_value_error(self):
        with self.assertRaises(ValueError):
            describeAbiType({"type": "uint7"})


class ExistingTypesStillWorkTests(unittest.TestCase):
    def test_fixed_ufixed_function_empty_tuple(self):
        self.assertEqual(
            describeAbiType({"type": "fixed128x18"})["kind"], "fixed"
        )
        self.assertEqual(
            describeAbiType({"type": "ufixed80x11"})["kind"], "ufixed"
        )
        self.assertEqual(
            describeAbiType({"type": "function"})["canonical"], "function"
        )
        empty = describeAbiType({"type": "tuple", "components": []})
        self.assertEqual(empty["canonical"], "()")
        self.assertEqual(empty["components"], [])

    def test_function_in_array(self):
        result = describeAbiType({"type": "function[2]"})
        self.assertEqual(result["canonical"], "function[2]")
        self.assertFalse(result["isDynamic"])
        self.assertEqual(result["arrayLength"], 2)
        self.assertEqual(result["base"]["kind"], "function")
        self.assertIsNone(result["components"])


if __name__ == "__main__":
    unittest.main()
