"""应用级资源初始化与关闭清理。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from eyeguide.core.config import AppConfig
from eyeguide.core.paths import configure_runtime_environment
from eyeguide.services.navigation import NavigationService
from eyeguide.services.speech import SpeechEngine


@dataclass(slots=True)
class ApiServices:
    """FastAPI 应用共享的服务容器。

    用于存储应用级别的共享资源和服务实例，包括配置、语音合成引擎和导航服务。
    这些服务在应用启动时初始化，在应用关闭时释放。
    """

    config: AppConfig # 应用配置对象，包含视觉和导航相关参数
    speech: SpeechEngine # 语音合成引擎，负责文本转语音播报
    navigation: NavigationService # 导航服务，负责路线规划和导航指引

    def shutdown(self) -> None:
        """关闭共享资源，避免线程残留。

        调用各个服务的清理方法，确保资源被正确释放。
        特别地，需要关闭语音引擎以释放音频设备和相关线程。
        """

        self.speech.stop()


@asynccontextmanager
async def api_lifespan(app: FastAPI):
    """在应用启动时准备运行时环境与共享服务。

    这是 FastAPI 的生命周期事件处理器。执行流程：
    1. 启动阶段（yield 前）：配置运行时环境、初始化应用配置、创建共享服务
    2. 运行阶段（yield）：应用处理请求
    3. 关闭阶段（finally）：调用服务的 shutdown 方法进行资源清理

    Args:
        app: FastAPI 应用实例

    Yields:
        在 yield 处应用开始处理请求
    """

    configure_runtime_environment()
    config = AppConfig()
    services = ApiServices(
        config=config,
        speech=SpeechEngine(config.speech),
        navigation=NavigationService(),
    )
    app.state.services = services
    try:
        yield
    finally:
        services.shutdown()



def get_services(app: FastAPI) -> ApiServices:
    """从 FastAPI 应用状态中获取共享服务。

    在 api_lifespan 中初始化的服务实例会存储在应用状态中。
    通过这个函数可以在任何需要的地方获取这些共享服务。

    Args:
        app: FastAPI 应用实例

    Returns:
        ApiServices: 包含应用级共享服务的容器对象

    Raises:
        RuntimeError: 如果应用状态中未找到已初始化的服务
    """

    services = getattr(app.state, "services", None) # 从应用状态中获取服务实例
    if services is None:
        raise RuntimeError("API services are not initialized")
    return services
