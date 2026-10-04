"""异常类型。

- ABITypeError：无效的 ABI 类型字符串或类型对象（类型层）。
- ABIValueError：值与类型不匹配、编码数据非法或类型对象不适用于值层
  操作（值层）；函数调用实参不能按声明类型编码或解码时同样抛出。
- AbiPathError：路径语法非法或按路径读取/定点替换失败（路径层）。
- AbiEventError：事件 ABI 非法或日志（topics/data）还原、校验失败
  （事件层）。
- AbiMetadataError：函数 ABI 元数据非法（条目缺 name/type/inputs、
  类型字符串无法解析、规范签名无法生成 selector 等）。
- AbiFunctionNotFoundError：按函数名或规范签名找不到函数条目。
- AbiOverloadError：只给函数名但存在多个同名重载，无法唯一选择。
- AbiSelectorError：calldata 的四字节 selector 在 ABI 中匹配不到函数。
- AbiCalldataLengthError：calldata 少于四字节，无法读取 selector。
- AbiTrailingDataError：参数按声明类型解码完成后仍有尾随字节。

所有异常各自独立，均为 ValueError 子类。
"""


class ABITypeError(ValueError):
    """无效的 ABI 类型字符串或类型对象。"""


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
    """

    #: 全部公开错误码。
    CODES = (
        "PATH_SYNTAX",
        "PATH_OUT_OF_RANGE",
        "PATH_NOT_FOUND",
        "PATH_TYPE_MISMATCH",
        "PATH_VALUE_MISMATCH",
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
      非 indexed 参数 tuple 无法严格解码（长度、偏移、填充、残留等）；
    - ``EVENT_VALUE_INVALID``：encode_event_log 的入参不合法（event 非
      EventDefinition、values 非 list/tuple、数量不符，或任一值与声明
      类型不匹配）。
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


class AbiMetadataError(ValueError):
    """函数 ABI 元数据非法。

    触发情形：ABI 根不是数组、function 条目缺 name/type/inputs、
    name 不是合法标识符、参数描述非法、类型字符串无法解析，或规范
    函数签名无法生成 selector。
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


#: 函数调用路径对值层异常的公开名称；与既有 ABIValueError 是同一个类，
#: 已有的类型解析/值编解码/路径/事件入口行为不受影响。
AbiValueError = ABIValueError
