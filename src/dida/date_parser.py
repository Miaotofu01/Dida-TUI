"""日期解析器（第 6 个深模块）：纯函数。t06 填充。

把一行的输入解析成：标题 + 可选截止 + 可选优先级 + 可选标签 + 解析诊断，
供新建（t15）与改期（t14）共用。零依赖、无副作用、可直接单测。

公开接口：``parse(text) -> ParsedTask``；返回类型（字段名与诊断的表达方式）
由 t06 定稿。写回服务端前必须严格校验日期格式——服务端对非法日期是静默忽略的。
"""

from __future__ import annotations


def parse(text: str) -> object:
    """解析一行输入。返回类型由 t06 定稿。"""
    raise NotImplementedError("日期解析由 t06 实现")
