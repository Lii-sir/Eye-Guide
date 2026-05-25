"""
路径管理模块：提供应用路径处理和运行时环境配置功能。

该模块负责处理应用的文件系统路径问题，特别是处理打包后的可执行文件环境。它提供了获取应用根目录、
资源包路径、DLL 文件目录等功能，并支持自动配置运行时环境变量以确保依赖库能被正确加载。

主要功能：
- 获取应用根目录（支持开发环境和打包后的可执行环境）
- 查找和解析资源路径
- 配置 Windows 运行时 DLL 搜索路径
- 设置环境变量确保依赖库可访问
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """
    获取应用的根目录路径。

    该函数能够自动检测应用是否以打包后的可执行程序形式运行（如 PyInstaller 生成的 .exe）。
    如果是，返回 PyInstaller 的临时解包目录；否则，返回源代码所在的项目根目录。

    返回值：
        Path: 应用根目录的路径对象。对于打包应用，返回 sys._MEIPASS；
              对于开发环境，返回源文件所在目录的上两级目录。

    示例：
        在开发环境中，如果该文件位于 /project/eyeguide/core/paths.py，
        则返回 /project
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def bundled_path(*parts: str) -> Path:
    """
    获取应用包内资源文件的路径。

    该函数以应用根目录作为基准，拼接提供的路径部分，用于访问捆绑在应用中的资源文件
    （如模型文件、配置文件等）。

    参数：
        *parts: 路径部分，相对于应用根目录的相对路径组件

    返回值：
        Path: 完整的资源文件路径对象

    示例：
        bundled_path("models", "yolo_v8.pt") 返回 /project/models/yolo_v8.pt
    """
    return app_root().joinpath(*parts)


def first_existing_path(*candidates: str | Path) -> Path | None:
    """
    从多个候选路径中找到第一个存在的路径。

    该函数遍历提供的路径列表，返回第一个实际存在于文件系统中的路径。
    用于处理可能存在多个位置的资源文件的查找。

    参数：
        *candidates: 候选路径列表，可以是字符串或 Path 对象

    返回值：
        Path | None: 第一个存在的路径对象；如果所有候选路径都不存在，返回 None

    示例：
        path = first_existing_path("/etc/config.json", "~/.config/app.json", "./config.json")
        # 返回存在的第一个路径
    """
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def runtime_roots() -> list[Path]:
    """
    获取运行时根目录列表。

    该函数返回应用可能需要访问的根目录列表。对于打包后的应用，会包含主根目录和 _internal 子目录；
    对于开发环境，仅返回应用根目录。

    返回值：
        list[Path]: 根目录路径列表

    说明：
        _internal 目录通常由 PyInstaller 创建，包含打包后的库文件和依赖
    """
    root = app_root()
    roots = [root]
    internal_dir = root / "_internal"
    if internal_dir.exists():
        roots.append(internal_dir)
    return roots


def runtime_dll_dirs() -> list[Path]:
    """
    获取运行时 DLL 搜索目录列表（Windows 专用）。

    该函数收集应用需要的所有 DLL 文件目录，包括根目录、torch 库目录和 OpenCV 目录。
    返回的列表会去除重复项并过滤掉不存在的目录。

    返回值：
        list[Path]: 有效且存在的 DLL 目录路径列表（已去重）

    说明：
        这个函数主要用于 Windows 系统。它收集 PyTorch 和 OpenCV 等深度学习框架的 DLL 文件所在目录，
        用于后续的系统环境变量配置。
    """
    dll_dirs: list[Path] = []
    for root in runtime_roots():
        dll_dirs.extend(
            [
                root,
                root / "torch" / "lib",
                root / "cv2",
            ]
        )
    # 去重：保留第一次出现的目录，忽略后续重复
    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for directory in dll_dirs:
        key = str(directory).lower()
        if key in seen:
            continue
        seen.add(key)
        if directory.exists():
            unique_dirs.append(directory)
    return unique_dirs


def configure_runtime_environment() -> None:
    """
    配置运行时环境变量（Windows 专用）。

    该函数是应用启动时调用的初始化函数，用于配置 Windows 系统的 DLL 搜索路径。
    它将所有必需的 DLL 目录添加到系统搜索路径，确保深度学习框架（PyTorch、OpenCV）和其他
    原生库能够被正确加载。

    工作原理：
        1. 收集所有 DLL 目录
        2. 为每个目录调用 os.add_dll_directory()（Python 3.8+ Windows API）
        3. 将这些目录添加到 PATH 环境变量的前面

    异常处理：
        该函数会静默忽略以下异常，确保不会因为单个失败而中断应用启动：
        - AttributeError: 运行环境不支持 add_dll_directory 方法
        - FileNotFoundError: 目录不存在
        - OSError: 其他操作系统级错误

    说明：
        这个函数应该在应用启动的最早阶段调用，通常在导入深度学习框架之前。
    """
    import os

    dll_dirs = runtime_dll_dirs()
    # 为每个 DLL 目录添加到系统搜索路径
    for dll_dir in dll_dirs:
        try:
            os.add_dll_directory(str(dll_dir))
        except (AttributeError, FileNotFoundError, OSError):
            pass

    # 更新 PATH 环境变量，将 DLL 目录放在最前面
    if dll_dirs:
        existing_path = os.environ.get("PATH", "")
        prepend_dirs = [str(path) for path in dll_dirs]
        os.environ["PATH"] = os.pathsep.join(prepend_dirs + [existing_path])
