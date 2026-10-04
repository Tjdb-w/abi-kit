# ABI Kit

合约 ABI 编解码套件：类型解析、嵌套结构编解码、事件日志还原与校验、
嵌套值的路径化读取与定点替换、函数调用 calldata 与函数返回值编解码。

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
- 已实现：事件 ABI、签名 topic0 与日志还原校验。
  - `parse_event_abi(event_json)`：解析 ABI JSON 的 event 对象，返回不可变
    `EventDefinition`（按声明顺序保存参数名、规范类型、indexed 与
    anonymous 元数据）；tuple 参数由 `components` 递归展开。
  - `event_topic0(event)`：按 `keccak256("Name(type1,type2,...)")` 计算
    签名 topic0，返回小写 `"0x"` + 64 位十六进制；参数名与 indexed 不参与
    签名，anonymous 事件返回 `None`。
  - `decode_event_log(event, topics, data)`：校验 topics 数量与 topic0，
    严格解码 data 为非 indexed 参数 tuple，按声明顺序与 indexed 值合并
    返回一个 tuple。indexed 的 `address`/`bool`/`intM`/`uintM`/`bytesM`
    从 32 字节 topic 严格解码；indexed 的 `string`、动态 `bytes`、数组与
    tuple 不可逆，原样返回该 32 字节 topic（口径 `bytes`）。
  - `encode_event_log(event, values)`：按声明值编码日志，返回不可变
    `EncodedEventLog`（`event` / `topics`（bytes tuple）/ `data`，以及
    `topics_hex` / `data_hex` 属性）。`values` 按声明顺序接受 list 或
    tuple；非 indexed 参数组成 tuple 按 ABI 值编码生成 data，无此类参数
    时 `data` 为 `b""`；非匿名事件 topics 首项为签名 topic0 的 32 字节，
    匿名事件不放，再按声明顺序放 indexed 主题，数量口径与
    `decode_event_log` 一致。indexed 的 `address`/`bool`/`intM`/
    `uintM`/`bytesM` 生成 32 字节 ABI 规范字（address 左补零、`bytesM`
    右补零）；indexed 的 `string`、动态 `bytes`、数组与 tuple 取事件索引
    特殊编码的 Keccak-256 主题——`string` 用 UTF-8 字节、动态 `bytes` 用
    内容、数组省略长度递归连接元素、tuple 递归连接成员，递归成员与整段
    结果补齐到 32 字节倍数，`string` 与动态 `bytes` 内容不补零。动态
    indexed 参数经 `decode_event_log` 仍得 32 字节 topic bytes，不伪装成
    可逆值。
  - `topics` 每项接受 32 字节 `bytes` 或可选 `0x` 前缀的 64 位十六进制
    字符串；`data` 接受 `bytes` 或可选 `0x` 前缀的偶数位十六进制字符串。
  - Keccak-256 为仓库内纯 Python 实现（Ethereum 口径，域后缀 `0x01`，
    非 NIST SHA3），无第三方依赖。
- 已实现：函数 ABI、selector 与函数调用 calldata 编解码。
  - `parse_function_abi(abi)`：解析 ABI JSON 字符串或等价条目数组，只消费
    `type == "function"` 条目，返回不可变 `FunctionDefinition`（按声明顺序
    保存 `inputs` 与 `outputs` 参数）；event / constructor / error /
    receive / fallback 条目跳过不解析。`outputs` 元数据与 `inputs` 同口径
    严格校验，但不参与规范签名与 selector。
  - `canonical_function_signature(function)`：返回
    `name(type1,type2,...)` 规范签名；`function_selector(function)` 返回
    `keccak256(签名)[:4]` 四字节 selector。
  - `encode_function_call(abi, function_name, args=None)`（别名
    `encodeFunctionCall`）：`function_name` 接受函数名（无同名重载时）或
    规范函数签名；`args` 为按公开参数顺序的实参列表。返回
    `EncodedFunctionCall`（`function` / `selector` / `calldata`，以及
    `function_name` / `signature` / `selector_hex` / `calldata_hex`
    属性）。
  - `decode_function_call(abi, calldata)`（别名 `decodeFunctionCall`）：
    `calldata` 接受 `bytes` 或可选 `0x` 前缀的十六进制字符串；按 selector
    还原函数，严格解码参数 tuple，返回 `DecodedFunctionCall`（`function` /
    `selector` / `args`，以及 `function_name` / `signature` /
    `selector_hex` / `values` 属性）。`args` 每项为带类型标注的
    `FunctionArgument(name, type, value)`，`type` 是不含空白的规范类型
    字符串；tuple 参数名随类型对象保留，可用于字段寻址。
  - 值口径与 `encode_abi_value` / `decode_abi_value` 完全一致；相同输入
    得到相同字节，解码后按原声明类型重编码得到相同 calldata，动态值与
    静态值均可稳定往返。
  - `encode_function_result(abi, function_name, values=None)`（别名
    `encodeFunctionResult`）：`function_name` 接受函数名（无同名重载时）
    或规范函数签名；`values` 为按 `outputs` 声明顺序排列的返回值序列
    （list/tuple）。返回不可变 `EncodedFunctionResult`（`function` /
    `data`，以及 `function_name` / `signature` / `data_hex` 属性），
    `data` 是返回值 tuple 的标准 ABI 编码，不含 selector；无 `outputs`
    的函数只接受空序列并生成 `b""`。
  - `decode_function_result(abi, function_name, data)`（别名
    `decodeFunctionResult`）：`data` 接受 `bytes` 或可选 `0x` 前缀的偶数
    位十六进制字符串；按函数名或规范签名选定函数后严格解码其 `outputs`
    tuple，返回不可变 `DecodedFunctionResult`（`function` / `outputs`，
    以及 `function_name` / `signature` / `values` 属性）。`outputs`
    每项为带类型标注的 `FunctionArgument(name, type, value)`，`values`
    是与之等价的纯值 tuple。空 `outputs` 与 `b""` 往返得到空 tuple；
    单个、多个与嵌套动态输出均可稳定往返。
  - 返回值值口径与值层编解码完全一致（address 仍为小写 `"0x"` + 40 个
    十六进制字符）；`outputs` 不参与 selector 与 calldata，既有
    selector/calldata 结果不受影响。

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
- `AbiEventError`：事件层六类失败，同样通过 `code` 携带错误码：

  | 错误码 | 触发情形 |
  | --- | --- |
  | `EVENT_ABI_INVALID` | 事件 JSON 或 components 不合法 |
  | `EVENT_TOPIC_COUNT` | topics 数量与 indexed 参数数量不符 |
  | `EVENT_TOPIC0_MISMATCH` | 非匿名事件 topics[0] 与签名 topic0 不一致 |
  | `EVENT_TOPIC_VALUE` | topic 非 32 字节，或 indexed 基础值无法严格解码 |
  | `EVENT_DATA_INVALID` | data 十六进制/字节非法，或非 indexed tuple 解码失败 |
  | `EVENT_VALUE_INVALID` | `encode_event_log` 的 event 非 EventDefinition、values 非 list/tuple、数量不符，或任一值与声明类型不匹配 |

- 函数调用与返回值路径的失败（相关异常均为独立的 `ValueError` 子类；
  值不匹配仍抛既有 `ABIValueError`，别名 `AbiValueError`）：

  | 异常 | 触发情形 |
  | --- | --- |
  | `AbiMetadataError` | ABI 根非法、function 条目缺 name/type/inputs、outputs 元数据非法、标识符非法、参数/返回值类型字符串无法解析，或规范签名无法生成 selector |
  | `AbiFunctionNotFoundError` | 按函数名或规范签名找不到函数 |
  | `AbiOverloadError` | 只给函数名但同名重载不止一个，无法唯一选择 |
  | `AbiSelectorError` | calldata 的四字节 selector 在 ABI 中匹配不到函数 |
  | `AbiCalldataLengthError` | calldata 少于四字节 |
  | `AbiTrailingDataError` | 参数或返回值按声明类型消费完后仍有尾随字节 |

  实参或返回值不能按声明类型编码（含数量不符）、参数区或返回值区不能
  严格解码时抛 `ABIValueError`（`AbiValueError` 为同一异常的别名）。
  空 calldata、未知 selector、错误函数名、歧义重载与尾随字节各自只得到
  上述唯一结果。无 `outputs` 的函数，其返回值编码只接受空序列（生成
  `b""`）、解码只接受空数据（返回空 tuple）。ABI 同时包含函数与事件时，
  函数入口只消费 function 条目，事件 topic 与日志解码行为不变。

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
