"""本包对外暴露的异常类型。

- ABITypeError：无效的类型字符串或类型对象（解析/格式化层）。
- ABIValueError：值与类型不匹配，或 ABI 编码数据非法（值层编解码）。
- AbiPathError：嵌套值路径非法或路径读取/定点替换失败。其唯一错误码
  通过 ``code`` 属性给出。
"""


class ABITypeError(ValueError):
    """无效的 ABI 类型字符串或类型对象。"""


class ABIValueError(ValueError):
    """ABI 值与类型不匹配，或 ABI 编码数据非法。"""


class AbiPathError(ValueError):
    """路径语法、路径寻址或路径替换值不合法。

    错误码（``code``）取值：

    - ``PATH_SYNTAX``：路径语法非法（空段、空字段名、非十进制索引、
      多余分隔符等）。
    - ``PATH_OUT_OF_RANGE``：数组或元组的数字索引越界。
    - ``PATH_NOT_FOUND``：字段名在元组中不存在。
    - ``PATH_TYPE_MISMATCH``：路径继续步进到非容器值，或在非容器上寻址。
    - ``PATH_VALUE_MISMATCH``：替换值与路径所指的 ABI 类型不一致。
    """

    #: 路径语法非法。
    PATH_SYNTAX = "PATH_SYNTAX"
    #: 数字索引越界。
    PATH_OUT_OF_RANGE = "PATH_OUT_OF_RANGE"
    #: 元组中不存在该字段。
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    #: 对非容器值继续步进。
    PATH_TYPE_MISMATCH = "PATH_TYPE_MISMATCH"
    #: 替换值与目标类型不匹配。
    PATH_VALUE_MISMATCH = "PATH_VALUE_MISMATCH"

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")
