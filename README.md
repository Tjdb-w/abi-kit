# ABI Kit

合约 ABI 编解码套件：类型解析、嵌套结构编解码、事件日志还原与校验、
嵌套值的路径化读取与定点替换。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- 已实现：ABI 类型解析与规范化。
  - `parse_abi_type(type_string)`：解析 ABI 类型字符串，返回不可变类型对象。
  - `format_abi_type(abi_type)`：返回不含空白的规范类型字符串。
  - 覆盖 `uintM`/`intM`（M 为 8–256 的 8 的倍数）、`address`、`bool`、
    `string`、`bytes`、`bytesM`（M 为 1–32）、`T[]`、`T[n]` 及任意深度元组，
    嵌套最多 128 层。
- 已实现：完整值编解码。
  - `encode_abi_value(abi_type, value)` / `decode_abi_value(abi_type, data)`，
    遵循 head/tail 布局，解码采用严格规范（紧密排列、填充必须为零）。
  - 值口径：uintM/intM 为 `int`，bool 为 `bool`，string 为 `str`，
    bytes/bytesM 为 `bytes`，address 为小写 `"0x"+40 个十六进制字符`，
    数组为 `list`，元组为 `tuple`。
- 已实现：嵌套值的路径化读取与定点替换。
  - `get_abi_value_at_path(abi_type, data, path)`：按路径只返回选中的子值。
  - `replace_abi_value_at_path(abi_type, data, path, value)`：返回替换后的
    完整新编码；原始字节不被就地修改，动态偏移与长度随结果重算。
  - 覆盖 tuple、动态/定长数组、嵌套数组以及 tuple 内继续嵌套数组。
- 已实现：事件 ABI 解析、签名 topic0 与日志还原校验。
  - `parse_event_abi(event_json)`：解析 ABI JSON 的 event 对象，返回不可变
    `EventDefinition`（含按声明顺序的不可变 `EventParameter`）。要求
    `type` 为 `"event"`、`name` 为非空 ASCII 标识符；`anonymous` 与
    `indexed` 缺失视为 `false`；tuple 参数由 `components` 递归展开，
    可带 `[]` / `[N]` 后缀。
  - `event_topic0(event)`：按 `事件名(规范参数类型,...)` 计算 Ethereum
    Keccak-256，返回小写 `"0x"` + 64 位十六进制；参数名与 `indexed`
    不参与签名，`anonymous` 事件返回 `None`。
  - `decode_event_log(event, topics, data)`：校验 topic 数量（非匿名为
    indexed 数加一且 topics[0] 为签名 topic0，匿名恰为 indexed 数），
    把 indexed / 非 indexed 参数按原声明顺序合并为 tuple 返回。
    - topics 项接受 32 字节 `bytes` 或可选 `0x` 前缀的 64 位十六进制
      `str`；data 接受 `bytes` 或可选 `0x` 前缀的偶数位十六进制 `str`。
    - indexed 的 `address`/`bool`/`intM`/`uintM`/`bytesM` 按字严格解码；
      indexed 的 `string`、动态 `bytes`、数组与 tuple 不可逆，返回原始
      32 字节 topic（`bytes` 口径）。
    - 非 indexed 参数组成 tuple 按值层严格规范解码；无此类参数时
      data 必须为空。

## 路径

- 点号连接 tuple 字段名：`inner.items[2].amount`。
- 方括号内为从 0 开始的非负十进制索引，用于数组元素，也可按位置选择
  tuple 元素：`items[0]`、`tuple[2]`。
- 空路径 `""` 表示整个根值。
- 字段名匹配类型中声明的名称。字段名通过两种方式提供：
  - 直接构造带 `names` 的 `TupleType`（与 components 等长，元素为
    字段名或 `None`）；
  - 向路径接口传入 Solidity 声明风格的类型字符串，组件类型后可空白跟
    字段名，例如 `(address to,uint256 value)[] items`。该带名语法仅用于
    路径接口，`parse_abi_type` 的严格规范语法不变。
- 语法只接受上述字段名与数组索引；空段、空字段名、负数、带符号或带
  空格索引、多余分隔符均为非法。

`data` 接受 `bytes` 或可选 `0x` 前缀的偶数位十六进制 `str`；`abi_type`
接受 `ABIType` 或上述类型字符串。

## 异常

- `ABITypeError`：类型层错误（既有语义不变）。
- `ABIValueError`：值与类型不匹配、编码数据非法（既有语义不变）。
- `AbiPathError`：路径层五类失败，统一通过异常的 `code` 属性携带唯一
  错误码：

  | 错误码 | 触发情形 |
  | --- | --- |
  | `PATH_SYNTAX` | 路径语法不合法 |
  | `PATH_OUT_OF_RANGE` | 索引超出数组实际长度或 tuple 位置范围 |
  | `PATH_NOT_FOUND` | tuple 中不存在该名称的字段 |
  | `PATH_TYPE_MISMATCH` | 对非容器值步进，或容器类型与步进方式不符 |
  | `PATH_VALUE_MISMATCH` | 替换值与路径所指的 ABI 类型不一致 |

  成功时只返回数据，不返回部分数据或错误对象；原始编码非法仍抛
  `ABIValueError`。
- `AbiEventError`：事件层五类失败，同样通过 `code` 属性携带唯一
  错误码：

  | 错误码 | 触发情形 |
  | --- | --- |
  | `EVENT_ABI_INVALID` | 事件 JSON 对象或 components 非法 |
  | `EVENT_TOPIC_COUNT` | topic 数量与 indexed 参数数量不符 |
  | `EVENT_TOPIC0_MISMATCH` | 非匿名事件 topics[0] 与签名 topic0 不符 |
  | `EVENT_TOPIC_VALUE` | topic 字节形式非法，或 indexed 基础值违反严格填充/越界 |
  | `EVENT_DATA_INVALID` | 非 indexed 参数 tuple 的 data 编码非法 |

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
