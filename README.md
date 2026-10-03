# ABI Kit

合约 ABI 编解码套件：类型解析、嵌套结构编解码、事件日志还原与校验。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- 已实现：ABI 类型解析与规范化。
  - `parse_abi_type(type_string)`：解析 ABI 类型字符串，返回不可变类型对象。
  - `format_abi_type(abi_type)`：返回不含空白的规范类型字符串。
  - `ABITypeError`：类型解析/格式化层无效输入的唯一异常。
  - 覆盖 `uintM`/`intM`（M 为 8–256 的 8 的倍数）、`address`、`bool`、
    `string`、`bytes`、`bytesM`（M 为 1–32）、`T[]`、`T[n]` 及任意深度元组，
    嵌套最多 128 层。
  - 元组组成部分可带字段名（`"(uint256 amount, address to)"`），字段名仅供
    路径寻址使用，不参与规范类型字符串，也不影响类型相等性。
- 已实现：值层编解码（head/tail 规则）。
  - `encode_abi_value(abi_type, value) -> bytes`
  - `decode_abi_value(abi_type, data)`
  - `ABIValueError`：值与类型不匹配或编码字节非法时的唯一异常。
  - 值口径：整数为 `int`，`bool` 为 `bool`，`string` 为 `str`，
    `bytes`/`bytesM` 为 `bytes`，`address` 为小写 `0x` 加 40 位十六进制串，
    数组为 `list`，元组为 `tuple`。
- 已实现：嵌套值的路径化读取与定点替换。
  - `abi_get_at_path(abi_type, data, path)`：返回路径选中的子值。
  - `abi_replace_at_path(abi_type, data, path, replacement) -> bytes`：
    返回替换后的完整新编码，原始字节不被就地修改。
  - `AbiPathError`：路径相关失败的统一公开异常，错误码见下。
- 尚未实现：事件 topic、事件数据还原与日志校验。

## 路径语法

- 点号 `.` 连接 tuple 字段名：`inner.amount`。
- 方括号内非负十进制数字（从 0 开始）选择 tuple 位置或数组元素：
  `items[2]`、`matrix[0][1]`。数组下标可直接连写；进入字段必须用点号。
- 空路径表示整个根值：`""` 直接读取或整体替换根值。
- 字段名匹配 ABI 中声明的名称；不接受空段、负数、带符号数字、带空格索引或
  多余分隔符。

读取动态数组或嵌套结构时只返回选中的子结构。定点替换先解码、在新构造的值
树上替换、再整体重编码，因此动态偏移与长度会随结果重新计算；即便替换前后
动态数据长度不同，未选中兄弟节点的值也保持不变。

## 路径错误码

`AbiPathError.code` 取以下唯一值之一：

| 错误码 | 触发情形 |
| --- | --- |
| `PATH_SYNTAX` | 路径语法非法（空段、空字段名、非十进制或带符号索引、多余分隔符等） |
| `PATH_OUT_OF_RANGE` | 数组或 tuple 的数字索引越界 |
| `PATH_NOT_FOUND` | 路径指向不存在的字段（含在数组上按字段名寻址） |
| `PATH_TYPE_MISMATCH` | 在非容器（基础类型）值上继续步进 |
| `PATH_VALUE_MISMATCH` | 替换值与路径所指的 ABI 类型不一致 |

成功时直接返回子值或新编码 bytes，不返回部分数据或错误对象。编码字节本身
非法仍按值层约定抛出 `ABIValueError`，类型字符串非法抛出 `ABITypeError`。

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
