"""
事件模块：定义应用事件系统的核心数据结构。

该模块定义了整个应用中使用的事件数据类，用于在应用不同组件之间传递消息和状态信息。
事件系统采用键值对的方式，支持灵活的事件类型扩展。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppEvent:
    """
    应用事件数据类。

    该类表示应用中的一个原子事件，用于在系统各组件间传递信息。事件具有种类和值两个属性，
    支持任意的事件类型和数据负载。不可变的设计确保事件在系统中流转时不被意外修改。

    属性：
        kind: 事件类型标识符（字符串），用于区分不同的事件类别（如 "location_update", "error" 等）
        value: 事件值或负载（字符串），包含具体的事件数据或消息内容
    """
    kind: str
    value: str
