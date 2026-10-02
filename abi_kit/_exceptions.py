"""唯一异常类型：本包所有无效输入均抛出 ABITypeError。"""


class ABITypeError(ValueError):
    """无效的 ABI 类型字符串或类型对象。"""
