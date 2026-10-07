"""异常类型。

- ABITypeError：无效的 ABI 类型字符串或类型对象（类型层）。
- ABIValueError：值与类型不匹配、编码数据非法或类型对象不适用于值层
  操作（值层）；函数调用实参不能按声明类型编码或解码时同样抛出。
- AbiPathError：路径语法非法或按路径读取/定点替换失败（路径层）。
- AbiEventError：事件 ABI 非法或日志（topics/data）还原、校验失败
  （事件层）。
- AbiLogDispatchError：合约级事件注册表构建或日志分派失败（分派层）；
  分派成功后的单条日志还原仍抛 AbiEventError。
- AbiMetadataError：函数 ABI 元数据非法（条目缺 name/type/inputs、
  类型字符串无法解析、规范签名无法生成 selector 等）。
- AbiFunctionNotFoundError：按函数名或规范签名找不到函数条目。
- AbiOverloadError：只给函数名但存在多个同名重载，无法唯一选择。
- AbiSelectorError：calldata 的四字节 selector 在 ABI 中匹配不到函数。
- AbiCalldataLengthError：calldata 少于四字节，无法读取 selector。
- AbiTrailingDataError：参数按声明类型解码完成后仍有尾随字节。
- AbiErrorNotFoundError：按错误名或规范签名找不到 error 条目。
- AbiErrorOverloadError：只给错误名但同名重载不止一个，无法唯一选择。
- AbiErrorSelectorError：revert data 的四字节 selector 在 ABI 中匹配不到
  任何 error。
- AbiErrorDataLengthError：revert data 少于四字节，无法读取 selector。
- AbiErrorTrailingDataError：error 参数解码完成后仍有尾随字节。
- AbiDeploymentDataError：部署数据（creation bytecode / deployment data）
  类型或十六进制非法、deployment data 短于 creation bytecode，或开头
  与 creation bytecode 逐字节不一致。
- AbiContractCallError：合约调用分派路径（function / receive /
  fallback）的注册表构建、目标选择与数据非法失败，以 ``code`` 唯一区分。
- AbiContractAbiError：整份合约 ABI 只读清单（parse_contract_abi）的根、
  条目、重复登记与签名/selector/topic0 冲突失败，以 ``code`` 唯一区分。

所有异常各自独立，均为 ValueError 子类。
"""


class ABITypeError(ValueError):
    """无效的 ABI 类型字符串或类型对象。"""


class RangeError(ValueError):
    """ABI 类型声明的值域非法。

    用于 :func:`abi_kit.describeAbiType`：类型文本语法无法识别、整数位宽
    越界、bytesM/数组长度非法、fixedMxN 的 M/N 越界，或非 tuple 声明
    带有 components 等——即声明结构正确但类型取值不合法的情形。它与
    :class:`TypeError`（输入本身不是字典、type 不是字符串、tuple 缺
    components 等结构/类别错误）互斥。
    """


class ABIValueError(ValueError):
    """值与类型不匹配、非法 ABI 编码，或类型对象无法进行值编解码。"""


class AbiPathError(ValueError):
    """路径非法或按路径读取/替换失败。

    ``code`` 取下列唯一错误码之一：

    - ``PATH_SYNTAX``：路径语法不合法（空段、空字段名、负数、带符号或
      带空格索引、多余分隔符等）；
    - ``PATH_OUT_OF_RANGE``：索引超出当前数组实际长度；
    - ``PATH_NOT_FOUND``：tuple 中不存在该名称的字段；
    - ``PATH_TYPE_MISMATCH``：对非容器值继续步进，或对 tuple 使用索引、
      对数组使用字段名；
    - ``PATH_VALUE_MISMATCH``：替换值与路径所指的 ABI 类型不一致。
    - ``PATH_REPLACEMENTS_INVALID``：多路径替换的替换序列不是 list/tuple，
      或其中元素不是恰好含路径与新值的二元 list/tuple。
    - ``PATH_CONFLICT``：多路径替换中同一路径出现两次，或两条路径互为
      祖先与后代，存在重叠写入。
    """

    #: 全部公开错误码。
    CODES = (
        "PATH_SYNTAX",
        "PATH_OUT_OF_RANGE",
        "PATH_NOT_FOUND",
        "PATH_TYPE_MISMATCH",
        "PATH_VALUE_MISMATCH",
        "PATH_REPLACEMENTS_INVALID",
        "PATH_CONFLICT",
    )

    def __init__(self, code: str, message: str):
        if code not in self.CODES:
            raise ValueError(f"未知的路径错误码：{code!r}")
        self.code = code
        super().__init__(f"{code}: {message}")


class AbiEventError(ValueError):
    """事件 ABI 非法或日志还原/校验失败。

    ``code`` 取下列唯一错误码之一：

    - ``EVENT_ABI_INVALID``：事件 JSON 对象或其中的 components 不合法
      （type 非 ``"event"``、name 非法、参数描述非法等）；
    - ``EVENT_TOPIC_COUNT``：topics 数量与 indexed 参数数量不符；
    - ``EVENT_TOPIC0_MISMATCH``：非匿名事件的 topics[0] 与签名 topic0
      不一致；
    - ``EVENT_TOPIC_VALUE``：某个 topic 不是 32 字节数据，或可还原的
      indexed 基础值（address/bool/intM/uintM/bytesM）无法严格解码；
    - ``EVENT_DATA_INVALID``：data 不是合法的偶数位十六进制/字节串，或
      非 indexed 参数 tuple 无法严格解码（长度、偏移、填充、残留等）。
    - ``EVENT_VALUE_INVALID``：事件日志编码入参不合法——event 不是
      EventDefinition、values 不是 list/tuple、数量与声明不符，或任一值
      与其声明类型不匹配；亦用于事件候选值核验（match_event_log_values）
      的 values 不是 list/tuple、数量与声明不符，或动态 indexed 候选
      （string、动态 bytes、数组、tuple）无法按声明类型生成索引值。
    """

    #: 全部公开错误码。
    CODES = (
        "EVENT_ABI_INVALID",
        "EVENT_TOPIC_COUNT",
        "EVENT_TOPIC0_MISMATCH",
        "EVENT_TOPIC_VALUE",
        "EVENT_DATA_INVALID",
        "EVENT_VALUE_INVALID",
    )

    def __init__(self, code: str, message: str):
        if code not in self.CODES:
            raise ValueError(f"未知的事件错误码：{code!r}")
        self.code = code
        super().__init__(f"{code}: {message}")


class AbiLogDispatchError(ValueError):
    """合约级事件注册表构建或日志分派失败。

    ``code`` 取下列唯一错误码之一：

    - ``LOG_ABI_INVALID``：ABI 根非法（不是 JSON 字符串/条目数组、根不是
      数组）、event 条目非法，或事件规范签名重复；
    - ``LOG_ENTRY_INVALID``：日志不是映射、缺 topics/data 字段，或
      topics、data、event 字段非法（类型、十六进制、长度等）；
    - ``LOG_EVENT_NOT_FOUND``：显式事件名/规范签名在注册表中不存在，或
      无显式事件时 topics 首项的 topic0 未知；
    - ``LOG_EVENT_AMBIGUOUS``：只给事件名但同名事件不止一个，无法唯一
      选择；
    - ``LOG_EVENT_REQUIRED``：匿名事件未显式选择（无显式事件且 topics
      为空，只可能是匿名事件）；
    - ``LOG_TOPIC_MISMATCH``：显式指定的非匿名事件与 topics[0] 的
      topic0 不一致。

    分派成功后的日志还原（topics 数量、indexed 值、data 严格解码）仍抛
    :class:`AbiEventError`，其错误码不变。
    """

    #: 全部公开错误码。
    CODES = (
        "LOG_ABI_INVALID",
        "LOG_ENTRY_INVALID",
        "LOG_EVENT_NOT_FOUND",
        "LOG_EVENT_AMBIGUOUS",
        "LOG_EVENT_REQUIRED",
        "LOG_TOPIC_MISMATCH",
    )

    def __init__(self, code: str, message: str):
        if code not in self.CODES:
            raise ValueError(f"未知的日志分派错误码：{code!r}")
        self.code = code
        super().__init__(f"{code}: {message}")


class AbiMetadataError(ValueError):
    """函数或 error 的 ABI 元数据非法。

    触发情形：ABI 根不是数组、function/error 条目缺 name/type/inputs、
    name 不是合法标识符、参数描述非法、类型字符串无法解析、error 规范
    签名重复，或规范签名无法生成 selector。
    """


class AbiFunctionNotFoundError(ValueError):
    """按函数名或规范签名在 ABI 中找不到函数条目。"""


class AbiOverloadError(ValueError):
    """只给函数名但同名重载不止一个，无法唯一选择。"""


class AbiSelectorError(ValueError):
    """calldata 的四字节 selector 在 ABI 中匹配不到任何函数。"""


class AbiCalldataLengthError(ValueError):
    """calldata 少于四字节，无法读取 selector。"""


class AbiTrailingDataError(ValueError):
    """参数解码完成后仍有尾随字节未被消费。"""


class AbiErrorNotFoundError(ValueError):
    """按错误名或规范签名在 ABI 中找不到 error 条目。"""


class AbiErrorOverloadError(ValueError):
    """只给错误名但同名重载不止一个，无法唯一选择。"""


class AbiErrorSelectorError(ValueError):
    """revert data 的四字节 selector 在 ABI 中匹配不到任何 error。"""


class AbiErrorDataLengthError(ValueError):
    """revert data 少于四字节，无法读取 selector。"""


class AbiErrorTrailingDataError(ValueError):
    """error 参数解码完成后仍有尾随字节未被消费。"""


class AbiDeploymentDataError(ValueError):
    """creation bytecode / deployment data 类型或十六进制非法，或部署
    数据的创建字节码前缀长度不足、逐字节核对不一致。"""


class AbiContractCallError(ValueError):
    """合约调用分派（function / receive / fallback）失败。

    ``code`` 取下列唯一错误码之一：

    - ``CONTRACT_CALL_ABI_INVALID``：ABI 根非法（不是 JSON 字符串/条目
      数组、根不是数组），function / receive / fallback 条目非法
      （receive/fallback 带 inputs、function 元数据非法等），或重复登记
      （规范签名重复、receive/fallback 各至多一个）；
    - ``CONTRACT_CALL_TARGET_NOT_FOUND``：按函数名/规范签名或 receive /
      fallback 字面目标找不到目标，或解码时空 calldata 无 receive、未知
      selector 无 fallback，调用没有可分派的目标；
    - ``CONTRACT_CALL_AMBIGUOUS``：只给函数名但同名函数不止一个，无法
      唯一选择；
    - ``CONTRACT_CALL_DATA_INVALID``：calldata / data 不是 bytes 或可选
      ``0x`` 前缀的偶数位十六进制字符串（fallback 原样 data 同口径）；
    - ``CONTRACT_CALL_RECEIVE_NONEMPTY``：receive 只接受空实参且 data 必须
      为空，编码时给出非空实参或非空 data，或解码成 receive 却带非空数据；
    - ``CONTRACT_CALL_FALLBACK_ARGS``：fallback 只接受空实参，给出了非空
      实参。

    值层失败（实参不能按声明类型编码、参数区不能严格解码）仍抛
    :class:`ABIValueError`；参数解码后存在尾随字节继续抛
    :class:`AbiTrailingDataError`。
    """

    #: 全部公开错误码。
    CODES = (
        "CONTRACT_CALL_ABI_INVALID",
        "CONTRACT_CALL_TARGET_NOT_FOUND",
        "CONTRACT_CALL_AMBIGUOUS",
        "CONTRACT_CALL_DATA_INVALID",
        "CONTRACT_CALL_RECEIVE_NONEMPTY",
        "CONTRACT_CALL_FALLBACK_ARGS",
    )

    def __init__(self, code: str, message: str):
        if code not in self.CODES:
            raise ValueError(f"未知的合约调用分派错误码：{code!r}")
        self.code = code
        super().__init__(f"{code}: {message}")


class AbiContractAbiError(ValueError):
    """整份合约 ABI 只读清单（parse_contract_abi）解析或校验失败。

    ``code`` 取下列唯一错误码之一：

    - ``ABI_ROOT_INVALID``：ABI 输入类型错误（既不是 JSON 字符串也不是
      list/tuple），或 JSON 字符串解析后的根不是数组；
    - ``ABI_ENTRY_INVALID``：条目不是对象、缺少 ``type`` 或带有未知类型，
      或条目字段非法（缺 name/inputs、名称或类型非法、参数不能形成 ABI
      类型等）；
    - ``ABI_ENTRY_DUPLICATE``：constructor / receive / fallback 条目重复，
      或 receive / fallback 带有非空 inputs；
    - ``ABI_SIGNATURE_COLLISION``：同类的 function / error / event 规范
      签名重复，或同类 function / error 的四字节 selector 相同；
    - ``ABI_TOPIC0_COLLISION``：两个不同的非匿名事件具有相同 topic0
      （规范签名不同但哈希截位冲突）。

    function 与 error 之间 selector 相同不冲突；匿名事件没有签名 topic0，
    不参与 topic0 去重。
    """

    #: 全部公开错误码。
    CODES = (
        "ABI_ENTRY_INVALID",
        "ABI_ROOT_INVALID",
        "ABI_ENTRY_DUPLICATE",
        "ABI_SIGNATURE_COLLISION",
        "ABI_TOPIC0_COLLISION",
    )

    def __init__(self, code: str, message: str):
        if code not in self.CODES:
            raise ValueError(f"未知的合约 ABI 清单错误码：{code!r}")
        self.code = code
        super().__init__(f"{code}: {message}")


#: 函数调用路径对值层异常的公开名称；与既有 ABIValueError 是同一个类，
#: 已有的类型解析/值编解码/路径/事件入口行为不受影响。
AbiValueError = ABIValueError
