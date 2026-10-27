阶段 A：PC 服务化（1-2 天）
新增 FastAPI 网关（HTTP + WebSocket），把 SceneAnalyzer、RouteGuidance、NavigationService 挂到会话管理器；Tkinter 保留不动作为调试端。

阶段 B：通信协议与防延迟错位（1 天）
统一消息协议：frame_id + client_ts + image + gps + imu；服务端返回同 frame_id。
服务端用“单槽队列/LIFO”（只保留最新帧），客户端丢弃超时结果（如 >500ms）。

阶段 C：手机 Web 控制端 MVP（2-3 天）
手机浏览器页面实现：摄像头、GPS、WebSocket、TTS、基础状态面板。
你强调的配置项全部做成下拉框（camera/gps-source/quality/fps/profile）。

阶段 D：稳定性与上线前联调（2 天）
做心跳重连、动态码率（按 RTT 自适应分辨率/FPS/JPEG 质量）、日志追踪、局域网 HTTPS 证书与权限指引。


关键设计决策（避免返工）

视觉结果以“语义事件”为主，框渲染为辅：语音时效优先，框可容忍轻微滞后。
上行默认 JPEG 二进制，不走 base64：节省带宽和解码开销。
协议版本化：protocol_version 字段，后续加 IMU/导航指令不破兼容。
PC 端维持核心推理：手机仅做采集、控制、播报。