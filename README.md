# ABI Kit

合约 ABI 编解码套件：类型解析、嵌套结构编解码、事件日志还原与校验、
嵌套值的路径化读取与定点替换、函数调用 calldata 编解码、error revert
data 编解码、合约部署 constructor 参数编解码。

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
- 已实现：合约级事件注册表与日志分派还原。
  - `parse_event_registry(abi)`：解析 ABI JSON 字符串或等价条目数组，
    只保留 `type == "event"` 的条目，按 ABI 声明顺序构建不可变
    `EventRegistry`；其余条目跳过不解析。事件名、规范类型、indexed、
    anonymous 与 tuple components 语义同 `parse_event_abi`；事件规范
    签名（anonymous 不参与）不得重复。
  - `decode_contract_event_log(registry, log)`：分派并还原单条日志。
    日志为映射，`topics` 与 `data` 必填，可选 `event` 指定规范签名或
    唯一事件名；缺省时非匿名事件按 topics 首项匹配注册表 topic0，匿名
    事件必须显式选择；显式事件与 topics[0] 的 topic0 不一致时不得解码。
    返回 `(event, topics, data, values)` 四元组：命中的
    `EventDefinition`、规范化的 32 字节 topics bytes tuple、data bytes
    与按声明顺序排列的 values tuple（口径同 `decode_event_log`）。
  - `decode_contract_event_logs(registry, logs)`：按输入顺序批量还原，
    返回等长 tuple；任一日志失败即整体抛出，不返回部分结果。
  - 分派失败抛 `AbiLogDispatchError`（见异常表）；分派成功后的日志还原
    委托 `decode_event_log`，`AbiEventError` 与错误码原样传播。
- 已实现：函数 ABI、selector 与函数调用 calldata 编解码。
  - `parse_function_abi(abi)`：解析 ABI JSON 字符串或等价条目数组，只消费
    `type == "function"` 条目，返回不可变 `FunctionDefinition`；event /
    constructor / error / receive / fallback 条目跳过不解析。
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
- 已实现：函数返回值（outputs）编解码。
  - `encode_function_result(abi, function_name, values=None)`（别名
    `encodeFunctionResult`）：按 outputs 声明顺序接受 list 或 tuple，
    返回不可变 `EncodedFunctionResult`（`function` / `data`，以及
    `function_name` / `signature` / `data_hex` 属性）；无 outputs 的
    函数只接受空序列，编码为 `b""`。
  - `decode_function_result(abi, function_name, data)`（别名
    `decodeFunctionResult`）：`data` 接受 `bytes` 或可选 `0x` 前缀的
    偶数位十六进制字符串，严格解码 outputs tuple，返回不可变
    `DecodedFunctionResult`（`function` / `outputs`，以及
    `function_name` / `signature` / `values` 属性）；`outputs` 每项为
    `FunctionArgument(name, type, value)`。解码后仍有尾随字节抛
    `AbiTrailingDataError`。
  - outputs 不参与规范签名、selector 与 calldata；函数选择、元数据与
    值层错误约定同 calldata 路径。
- 已实现：Solidity error ABI、selector 与 revert data 编解码。
  - `parse_error_abi(abi)`：解析 ABI JSON 字符串或等价条目数组，只消费
    `type == "error"` 条目，按声明顺序返回不可变 `ErrorDefinition`；
    function / event / constructor / receive / fallback 条目跳过不解析。
    签名只由 name 与 inputs 的规范类型构成，参数名不参与；inputs 支持
    基础类型、数组与 `components` 嵌套 tuple。规范签名重复抛
    `AbiMetadataError`。
  - `canonical_error_signature(error)`：返回 `Name(type1,type2,...)`
    规范签名；`error_selector(error)` 返回 `keccak256(签名)[:4]` 四字节
    selector（bytes）。
  - `encode_error_data(abi, error_name, args=None)`：`error_name` 接受
    错误名（无同名重载时）或规范错误签名；`args` 为按 inputs 声明顺序
    的实参 list/tuple。返回不可变 `EncodedErrorData`（`error` /
    `selector` / `data`，以及 `error_name` / `signature` /
    `selector_hex` / `data_hex` 属性），`data` 为
    `selector + 参数 tuple 的 ABI 编码` 的完整 revert data。
  - `decode_error_data(abi, data)`：`data` 接受 `bytes` 或可选 `0x`
    前缀的偶数位十六进制字符串；按前四字节 selector 定位 error，严格
    解码参数 tuple，返回不可变 `DecodedErrorData`（`error` / `selector`
    / `args`，以及 `error_name` / `signature` / `selector_hex` /
    `types` / `values` 属性）。`args` 每项为带类型标注的
    `ErrorArgument(name, abi_type, value)`，`type` 属性给出规范类型
    字符串；无参数 error 的 revert data 即四字节 selector，解码为空
    tuple。
  - 值口径与 `encode_abi_value` / `decode_abi_value` 完全一致；函数、
    事件路径行为不变。
- 已实现：合约部署 constructor ABI 与 deployment data 编解码。
  - `parse_constructor_abi(abi)`：解析 ABI JSON 字符串或等价条目数组，只
    消费 `type == "constructor"` 条目，返回不可变
    `ConstructorDefinition`；ABI 中没有 constructor 条目时返回零输入
    定义，出现多个 constructor 条目抛 `AbiMetadataError`；function /
    event / error / receive / fallback 条目跳过不解析。inputs 支持基础
    类型、数组与 `components` 嵌套 tuple；`payable` /
    `stateMutability` 等其余字段不参与解析。
  - `encode_constructor_data(abi, creation_bytecode, args=None)`：按
    inputs 声明顺序接受实参 list/tuple，参数 tuple 按 ABI 值规则编码后
    接在规范化 creation bytecode 之后，返回不可变
    `EncodedDeploymentData`（`constructor` / `args`（纯值 tuple）/
    `data`，以及 `data_hex` 属性）；零参数时 `data` 就是规范化后的
    creation bytecode。`creation_bytecode` 接受 `bytes` 或可选 `0x`
    前缀的偶数位十六进制字符串。
  - `decode_constructor_data(abi, creation_bytecode, deployment_data)`：
    `deployment_data` 接受 `bytes` 或可选 `0x` 前缀的偶数位十六进制
    字符串；先逐字节核对开头与 creation bytecode 一致，再严格解码剩余
    的完整参数 tuple，返回不可变 `DecodedDeploymentData`
    （`constructor` / `args` / `data`，以及 `values` / `data_hex`
    属性）。`args` 每项为带名称与规范类型标注的
    `ConstructorArgument(name, type, value)`；零参数 constructor 的
    deployment data 必须恰好等于 creation bytecode，解码为空 tuple。
  - creation bytecode / deployment data 类型或十六进制非法、数据短于
    creation bytecode 或前缀不一致抛 `AbiDeploymentDataError`；参数
    数量、类型或 ABI 编码布局不合法抛 `ABIValueError`；尾随字节抛
    `AbiTrailingDataError`。静态、动态与嵌套参数稳定往返；函数、事件、
    error 路径行为不变。
- 已实现：合约调用分派，统一处理 function、receive 与 fallback。
  - `parse_contract_call_registry(abi)`：解析 ABI JSON 字符串或等价
    条目数组，只登记 `type` 为 `function` / `receive` / `fallback`
    的条目，构建不可变 `ContractCallRegistry`（`functions` 按声明
    顺序保存，`receive` / `fallback` 为是否登记的布尔标记）；其余
    条目（event / constructor / error、缺 type、未知类型或非对象
    条目）跳过不解析。function 保留名称、inputs、outputs 与
    selector 语义（同 `parse_function_abi`），规范签名不得重复；
    receive 与 fallback 至多各一个且不接受 inputs。
  - `encode_contract_call(abi, target, args=None, data=None)`：
    `abi` 接受 ABI JSON 字符串、等价条目数组或已构建的注册表；
    `target` 接受无重载函数名、规范函数签名、`"receive"` 或
    `"fallback"`。函数按 inputs 顺序编码实参，返回
    `EncodedContractCall`（`bytes` 子类，值本身即完整 calldata
    `selector + 参数区`，另有 `kind` / `function` / `args` /
    `data` / `calldata` / `calldata_hex` / `selector` 属性）；
    receive 仅接受空实参与空 data，calldata 为 `b""`；fallback
    仅接受空实参，把 data 原样放入 calldata。
  - `decode_contract_call(abi, calldata)`：按完整 calldata 分派并
    严格解码，返回不可变 `DecodedContractCall`（`kind` /
    `function` / `args` / `data` / `calldata`，以及 `values` /
    `calldata_hex` / `selector` 属性）。`function` 为
    `FunctionDefinition` 或 `None`；`args` 为 `FunctionArgument`
    tuple；函数 `data` 为参数区，fallback `data` 为完整
    calldata，receive `data` 为 `b""`。空 calldata 优先选
    receive，否则选 fallback；未知 selector（含不足四字节）有
    fallback 时归入 fallback，否则报告目标不存在。
  - 值口径与 `encode_abi_value` / `decode_abi_value` 完全一致；值
    错误仍抛 `ABIValueError`，参数解码有尾随字节继续抛
    `AbiTrailingDataError`，调用层失败只抛异常、不返回部分结果。
    既有函数、error、constructor 与事件日志入口行为不变。

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

- `AbiLogDispatchError`：合约级事件注册表构建与日志分派六类失败，同样
  通过 `code` 携带错误码：

  | 错误码 | 触发情形 |
  | --- | --- |
  | `LOG_ABI_INVALID` | ABI 根非法、event 条目非法，或事件规范签名重复 |
  | `LOG_ENTRY_INVALID` | 日志非映射、缺 topics/data，或 topics/data/event 字段非法 |
  | `LOG_EVENT_NOT_FOUND` | 显式事件名/规范签名不存在，或无显式事件时 topic0 未知 |
  | `LOG_EVENT_AMBIGUOUS` | 只给事件名但同名事件不止一个，无法唯一选择 |
  | `LOG_EVENT_REQUIRED` | 匿名事件未显式选择（无显式事件且 topics 为空） |
  | `LOG_TOPIC_MISMATCH` | 显式指定的非匿名事件与 topics[0] 的 topic0 不一致 |

  分派成功后的日志还原失败仍抛 `AbiEventError`，错误码不变。

- 函数调用路径的六类失败（均为独立的 `ValueError` 子类；值不匹配仍抛
  既有 `ABIValueError`，别名 `AbiValueError`）：

  | 异常 | 触发情形 |
  | --- | --- |
  | `AbiMetadataError` | ABI 根非法、function 条目缺 name/type/inputs、标识符非法、参数类型字符串无法解析，或规范签名无法生成 selector |
  | `AbiFunctionNotFoundError` | 按函数名或规范签名找不到函数 |
  | `AbiOverloadError` | 只给函数名但同名重载不止一个，无法唯一选择 |
  | `AbiSelectorError` | calldata 的四字节 selector 在 ABI 中匹配不到函数 |
  | `AbiCalldataLengthError` | calldata 少于四字节 |
  | `AbiTrailingDataError` | 参数按声明类型消费完后仍有尾随字节 |

  实参不能按声明类型编码（含数量不符）、或参数区不能严格解码时抛
  `ABIValueError`（`AbiValueError` 为同一异常的别名）。空 calldata、
  未知 selector、错误函数名、歧义重载与尾随字节各自只得到上述唯一结果。
  ABI 同时包含函数与事件时，函数入口只消费 function 条目，事件 topic 与
  日志解码行为不变。

- error 路径的五类失败（均为独立的 `ValueError` 子类；元数据错误与
  函数路径共用 `AbiMetadataError`，值不匹配仍抛既有 `ABIValueError`）：

  | 异常 | 触发情形 |
  | --- | --- |
  | `AbiMetadataError` | ABI 根非法、error 条目缺 name/type/inputs、标识符非法、参数类型字符串无法解析，或规范签名重复 |
  | `AbiErrorNotFoundError` | 按错误名或规范签名找不到 error |
  | `AbiErrorOverloadError` | 只给错误名但同名重载不止一个，无法唯一选择 |
  | `AbiErrorSelectorError` | revert data 的四字节 selector 在 ABI 中匹配不到 error |
  | `AbiErrorDataLengthError` | revert data 少于四字节 |
  | `AbiErrorTrailingDataError` | 参数按声明类型消费完后仍有尾随字节 |

  revert data 不是 `bytes` 或可选 `0x` 前缀的偶数位十六进制字符串时抛
  `ABIValueError`。ABI 同时包含函数、事件与 error 时，各入口只消费
  各自条目，既有函数与事件行为不变。

- 部署（constructor）路径的失败（独立的 `ValueError` 子类；元数据错误
  与函数/error 路径共用 `AbiMetadataError`，尾随数据共用
  `AbiTrailingDataError`，值不匹配仍抛既有 `ABIValueError`）：

  | 异常 | 触发情形 |
  | --- | --- |
  | `AbiMetadataError` | ABI 根非法、constructor 条目缺 inputs、参数描述非法、类型字符串无法解析，或出现多个 constructor 条目 |
  | `AbiDeploymentDataError` | creation bytecode / deployment data 不是 `bytes` 或可选 `0x` 前缀的偶数位十六进制字符串，或 deployment data 短于 creation bytecode、开头逐字节核对不一致 |
  | `AbiTrailingDataError` | constructor 参数按声明类型消费完后仍有尾随字节 |

  实参数量或类型与 inputs 声明不符、参数区 ABI 编码布局不合法时抛
  `ABIValueError`。ABI 同时包含函数、事件、error 与 constructor 时，
  各入口只消费各自条目，既有行为不变。

- 合约调用分派（function / receive / fallback）路径的失败统一抛
  `AbiContractCallError`（`ValueError` 子类），通过 `code` 携带唯一
  错误码：

  | 错误码 | 触发情形 |
  | --- | --- |
  | `CALL_ENTRY_INVALID` | ABI 根非法、function / receive / fallback 条目非法（含 receive / fallback 带 inputs），或函数规范签名重复、receive / fallback 重复登记 |
  | `CALL_TARGET_NOT_FOUND` | 函数名 / 规范签名 / receive / fallback 目标不存在，或 calldata 无法分派（空 calldata 无 receive / fallback、未知 selector 无 fallback） |
  | `CALL_TARGET_AMBIGUOUS` | 只给函数名但同名重载不止一个，无法唯一选择 |
  | `CALL_DATA_INVALID` | calldata 或 data 不是 `bytes` 或可选 `0x` 前缀的偶数位十六进制字符串，或 function 调用携带额外 data |
  | `CALL_RECEIVE_NONEMPTY` | receive 调用携带非空实参或非空 data |
  | `CALL_FALLBACK_ARGS` | fallback 调用携带实参 |

  实参值与声明类型不匹配（含数量不符）、参数区 ABI 编码布局不合法
  仍抛 `ABIValueError`；函数参数区解码后的尾随字节仍抛
  `AbiTrailingDataError`。调用层失败只抛异常，不返回部分结果；既有
  函数、error、constructor 与事件日志入口的输入输出和异常不变。

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
