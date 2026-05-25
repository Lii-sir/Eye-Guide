"""
EyeGuide 应用入口模块。

这是应用的主入口点，负责启动 GUI 界面。
"""

from eyeguide.ui.desktop import launch_app


def main() -> None:
    """
    启动 EyeGuide 应用的 GUI 界面。

    该函数是应用的主入口，调用 launch_app() 启动桌面 GUI。
    """
    launch_app()

