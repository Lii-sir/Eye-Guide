"""WebSocket 协议模型与通用消息构造工具。

阶段 B 的重点是统一移动端与电脑端之间的帧协议，并显式携带
`frame_id + client_ts + image + gps + imu`，这样服务端处理完成后，
可以把同一帧的标识原样回传给客户端，避免“结果对错帧”的问题。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ClientEnvelope(BaseModel):
    """客户端发给服务端的统一消息外层结构。

    说明：
    - `type` 用于区分消息类别，例如 `sensor.frame`、`sensor.gps`
    - `seq` 是请求级别的递增编号，便于日志与排障
    - `ts` 是客户端发送这条消息时的时间戳，单位秒
    - 真正的“帧采集时间”由 `payload.client_ts` 单独表达
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    type: str = Field(..., description="消息类型，例如 sensor.frame / sensor.gps / route.set")
    device_id: str = Field(..., min_length=1, description="客户端设备标识")
    seq: int = Field(..., ge=0, description="客户端递增序号")
    ts: float = Field(..., description="客户端发送时间戳，单位秒")
    session_id: str | None = Field(default=None, description="可选会话标识")
    payload: dict[str, Any] = Field(default_factory=dict, description="业务载荷")


class GpsPayload(BaseModel):
    """GPS 定位数据。

    这个模型既可以被独立的 `sensor.gps` 消息复用，
    也可以被 `sensor.frame` 内嵌使用，实现“帧与定位同步上报”。
    """

    model_config = ConfigDict(extra="allow")

    lat: float = Field(..., ge=-90.0, le=90.0, description="纬度")
    lon: float = Field(..., ge=-180.0, le=180.0, description="经度")
    accuracy: float | None = Field(default=None, ge=0.0, description="定位精度，单位米")
    speed_mps: float = Field(default=0.0, ge=0.0, description="速度，单位米每秒")
    heading_deg: float | None = Field(default=None, description="航向角，单位度")
    altitude: float | None = Field(default=None, description="海拔高度，单位米")


class ImuPayload(BaseModel):
    """IMU 数据。

    这里先定义成偏通用的姿态/惯导快照。
    阶段 B 里服务端暂时不参与复杂融合计算，但会把它原样记录并回传，
    便于后续做姿态辅助、稳定性分析和时间同步排障。
    """

    model_config = ConfigDict(extra="allow")

    heading_deg: float | None = Field(default=None, description="航向角，单位度")
    pitch_deg: float | None = Field(default=None, description="俯仰角，单位度")
    roll_deg: float | None = Field(default=None, description="横滚角，单位度")
    yaw_deg: float | None = Field(default=None, description="偏航角，单位度")
    accel_x: float | None = Field(default=None, description="X 轴加速度")
    accel_y: float | None = Field(default=None, description="Y 轴加速度")
    accel_z: float | None = Field(default=None, description="Z 轴加速度")
    gyro_x: float | None = Field(default=None, description="X 轴角速度")
    gyro_y: float | None = Field(default=None, description="Y 轴角速度")
    gyro_z: float | None = Field(default=None, description="Z 轴角速度")


class FramePayload(BaseModel):
    """统一的单帧上行协议。

    阶段 B 之后，客户端应尽量把图像帧、GPS、IMU 打包成同一条消息发送，
    这样服务端在处理这一帧时就能拿到与它时间最接近的传感器快照。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    frame_id: str = Field(..., min_length=1, description="帧唯一标识")
    client_ts: float | None = Field(
        default=None,
        description="客户端采集该帧时的时间戳，建议使用毫秒时间戳",
    )
    image_b64: str | None = Field(default=None, description="Base64 图像数据")
    image: str | None = Field(default=None, description="兼容字段，等价于 image_b64")
    gps: GpsPayload | None = Field(default=None, description="与该帧同步的 GPS 快照")
    imu: ImuPayload | None = Field(default=None, description="与该帧同步的 IMU 快照")


class RouteSetPayload(BaseModel):
    """设置导航路线请求。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    destination: str = Field(..., min_length=1, description="目标地点")
    origin: str = Field(default="@gps", description="起点，默认使用当前 GPS")
    provider: str = Field(default="osm", description="导航提供方")
    amap_api_key: str | None = Field(default=None, description="高德 Web API Key")
    prefer_online: bool = Field(default=True, description="优先使用在线路线服务")
    use_demo_fallback: bool = Field(default=True, description="在线失败时是否回退到演示路线")


class RouteSearchPayload(BaseModel):
    """搜索终点候选请求。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    destination: str = Field(..., min_length=1, description="搜索关键词")
    origin: str = Field(default="@gps", description="用于辅助检索的起点")
    provider: str = Field(default="osm", description="导航提供方")
    amap_api_key: str | None = Field(default=None, description="高德 Web API Key")
    limit: int = Field(default=5, ge=1, le=8, description="最大候选数")


class SpeechTestPayload(BaseModel):
    """语音测试请求。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    text: str | None = Field(default=None, description="测试播报文本")


class ConfigUpdatePayload(BaseModel):
    """运行时配置更新请求。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    provider: str | None = Field(default=None, description="默认导航提供方")
    amap_api_key: str | None = Field(default=None, description="高德 Web API Key")
    object_speech_dedup_mode: Literal["simple", "detailed"] | None = Field(
        default=None,
        description="普通物体播报去重模式",
    )


def ok_message(
    message_type: str,
    payload: dict[str, Any] | None = None,
    *,
    seq: int | None = None,
) -> dict[str, Any]:
    """构造标准成功响应。"""

    body: dict[str, Any] = {"type": message_type, "ok": True, "payload": payload or {}}
    if seq is not None:
        body["seq"] = seq
    return body


def error_message(
    code: str,
    message: str,
    *,
    seq: int | None = None,
) -> dict[str, Any]:
    """构造标准错误响应。"""

    body: dict[str, Any] = {
        "type": "error",
        "ok": False,
        "payload": {"code": code, "message": message},
    }
    if seq is not None:
        body["seq"] = seq
    return body
