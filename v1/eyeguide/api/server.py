"""FastAPI 服务入口。"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from eyeguide.api.lifecycle import api_lifespan, get_services
from eyeguide.api.session import ApiSession

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MOBILE_WEB_DIR = PROJECT_ROOT / "mobile-web"


def create_app() -> FastAPI:
    """创建并配置 EyeGuide API 应用。"""

    app = FastAPI(
        title="EyeGuide API",
        version="0.1.0",
        lifespan=api_lifespan,
    )

    # 如果移动端静态页面目录存在，则直接挂载到 /mobile。
    # 这样手机与电脑在同一局域网时，可以直接访问：
    # http://<电脑IP>:<端口>/mobile/
    if MOBILE_WEB_DIR.exists():
        app.mount("/mobile", StaticFiles(directory=str(MOBILE_WEB_DIR), html=True), name="mobile")

    @app.get("/healthz")
    async def healthz() -> dict:
        """健康检查接口。"""

        services = get_services(app)
        return {
            "ok": True,
            "ts": time.time(),
            "speech_backend": services.speech.backend_name,
            "default_camera_index": services.config.vision.default_camera_index,
            "mobile_web_enabled": MOBILE_WEB_DIR.exists(),
        }

    @app.get("/", response_model=None)
    async def root():
        """首页入口。

        如果前端页面已经存在，则把用户引导到移动控制端。
        否则返回一份简单的 API 说明，方便纯接口调试。
        """

        if MOBILE_WEB_DIR.exists():
            return RedirectResponse(url="/mobile/")
        return {
            "name": "EyeGuide API",
            "healthz": "/healthz",
            "websocket": "/ws",
            "mobile": "/mobile/",
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """统一 WebSocket 入口。"""

        session = ApiSession(websocket, get_services(websocket.app))
        await session.run()

    return app


app = create_app()
