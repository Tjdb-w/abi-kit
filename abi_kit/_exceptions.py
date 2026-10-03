"""异常类型。

- ABITypeError：无效的 ABI 类型字符串或类型对象（类型层）。
- ABIValueError：值与类型不匹配、编码数据非法或类型对象不适用于值层
  操作（值层）。
- AbiPathError：路径语法非法或按路径读取/定点替换失败（路径层）。
- AbiEventError：事件 ABI 非法或日志（topics/data）还原、校验失败
  （事件层）。

四者各自独立，均为 ValueError 子类。
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
