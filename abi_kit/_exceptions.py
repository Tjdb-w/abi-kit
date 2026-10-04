"""异常类型。

- ABITypeError：无效的 ABI 类型字符串或类型对象（类型层）。
- ABIValueError：值与类型不匹配、编码数据非法或类型对象不适用于值层
  操作（值层）。
- AbiPathError：路径语法非法或按路径读取/定点替换失败（路径层）。
- AbiEventError：事件 ABI 非法或日志（topics/data）还原、校验失败
  （事件层）。
- AbiMetadataError：函数 ABI 元数据非法（条目缺 name/type/inputs、
  类型字符串无法解析、规范签名无法生成 selector 等）。
- AbiFunctionNotFoundError：ABI 中找不到指定名称或规范签名的函数。
- AbiOverloadError：只给函数名但存在多个重载，无法唯一选择。
- AbiSelectorError：calldata 的四字节 selector 匹配不到任何函数。
- AbiCalldataLengthError：calldata 不足四字节，无法读取 selector。
- AbiValueError：函数实参不能按声明类型编码，或 calldata 主体不能
  按声明参数类型严格解码。
- AbiTrailingDataError：calldata 主体被完整消费后仍有尾随字节。

均为 ValueError 子类，各自独立。
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
      非 indexed 参数 tuple 无法严格解码（长度、偏移、填充、残留等）。
    """

    #: 全部公开错误码。
    CODES = (
        "EVENT_ABI_INVALID",
        "EVENT_TOPIC_COUNT",
        "EVENT_TOPIC0_MISMATCH",
        "EVENT_TOPIC_VALUE",
        "EVENT_DATA_INVALID",
    )

    def __init__(self, code: str, message: str):
        if code not in self.CODES:
            raise ValueError(f"未知的事件错误码：{code!r}")
        self.code = code
        super().__init__(f"{code}: {message}")


class AbiMetadataError(ValueError):
    """函数 ABI 元数据非法。

    触发情形包括：ABI 不是条目序列、条目不是对象、function 条目缺
    ``name``/``type``/``inputs``、参数描述非法、类型字符串无法解析、
    规范签名无法计算 selector，或用于选择函数的规范签名非法。
    """


class AbiFunctionNotFoundError(ValueError):
    """ABI 中找不到指定名称或规范签名的函数。"""


class AbiOverloadError(ValueError):
    """只提供函数名但存在多个同名重载，无法唯一选择。"""


class AbiSelectorError(ValueError):
    """calldata 的四字节 selector 在 ABI 的函数条目中匹配不到。"""


class AbiCalldataLengthError(ValueError):
    """calldata 不足四字节，无法读取 selector。"""


class AbiValueError(ValueError):
    """函数实参不能按声明类型编码，或 calldata 主体不能严格解码。"""


class AbiTrailingDataError(ValueError):
    """calldata 主体被完整消费后仍有尾随字节。"""
