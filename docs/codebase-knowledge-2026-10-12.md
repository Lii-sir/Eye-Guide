# EyeGuide 代码库知识梳理

> 文档类型：基于源码证据的代码库知识文档
> 主要语言：中文
> 观察日期：2026-10-12（Asia/Shanghai）
> 文档路径：D:\python_programs\LXD_project\EyeGuide\docs\codebase-knowledge-2026-10-12.md

## 1. 溯源信息

| 项目 | 内容 |
| --- | --- |
| 项目根目录 | D:\python_programs\LXD_project\EyeGuide |
| Git 远程仓库 | https://github.com/Lii-sir/Eye-Guide.git（仅读取远程元数据，未拉取） |
| 检查提交 | 4c3d9712f3d0a4b0f722d64c61f872bb3679acbb |
| 当前分支 | phone-cloud |
| 跟踪文件数量 | 62 个 |
| 初始工作区状态 | 分支 phone-cloud，存在未跟踪文件 weights/yolo26n.pt |
| 检查范围 | 跟踪源码、项目配置、README、桌面端、FastAPI/WebSocket、移动端页面、视觉/GPS/导航/语音模块 |
| 排除范围 | 二进制文件、模型内容、虚拟环境、依赖缓存、构建产物、数据集、训练产物、密钥、网络访问和项目代码执行 |
| 历史范围 | 未进行 Git 历史分析；本文不作历史意图判断 |
| 检查方法 | 只读读取、Git 元数据检查、带行号源码检查和定向符号搜索 |
| 验证结果 | 未执行项目、未安装依赖、未运行测试、未修改源码；最终工作区状态与初始状态一致 |
| 总体可信度 | 静态架构和调用关系：中高；运行时性能、模型可用性、硬件行为：中低 |

说明：当前 .gitignore 忽略 docs/（.gitignore:16）。本文件是按用户要求写入项目目录的知识文档，未改变项目源码。

## 2. 执行摘要

### 2.1 项目定位

已观察：EyeGuide 是面向视障出行辅助场景的 Windows/Python 原型，目标不是单纯展示目标检测，而是串联：

~~~text
视频帧
  → 目标检测 / 深度估计 / 盲道分割
  → 风险筛选
  → 语音播报与界面展示
  → GPS 定位与路线导航
~~~

README 将项目描述为桌面视觉导航原型，包含自由探索、路线导航、盲道感知、视频模拟和 GPS 接入（README.md:18-27、65-85）。

### 2.2 运行入口

已观察：

- 桌面入口：app.py → eyeguide.main.main() → eyeguide.ui.desktop.launch_app()（app.py:1-8；eyeguide/main.py:7-16）。
- API 入口：eyeguide.api.server:create_app()，提供 /healthz、/、/ws，并在 mobile-web 存在时挂载 /mobile（eyeguide/api/server.py:19-69）。
- 移动端：mobile-web/app.js 通过 WebSocket 连接 /ws，上传手机摄像头帧以及 GPS/IMU 数据（mobile-web/app.js:99-164、343-510）。
- 离线工具：eyeguide/tools/ 中包含图片搜索、盲道数据集转换和盲道训练脚本。

### 2.3 当前最重要的架构事实

已观察：

1. 本地桌面端由 SessionController 统筹视觉、语音、GPS、路线和视频窗口（eyeguide/app/session.py:28-73）。
2. 移动端每个 WebSocket 连接创建一个独立的 ApiSession，拥有自己的视觉分析器、路线状态、GPS/IMU 状态和最新帧缓冲（eyeguide/api/session.py:81-110）。
3. API 进程级生命周期共享一个 SpeechEngine 和一个 NavigationService（eyeguide/api/lifecycle.py:16-65）。
4. SceneAnalyzer 同时负责模型后端创建、推理调度、缓存、风险事件生成、盲道策略和检测框绘制（eyeguide/services/vision/analyzer.py:52-155）。
5. 桌面端和 API 端存在两套会话编排逻辑，尚未通过同一组应用用例统一。
6. AppEvent 仍是自由字符串 kind 加字符串 value（eyeguide/core/events.py:13-26）。
7. API 已实现“单槽最新帧优先”策略：新帧可以覆盖尚未处理的旧帧（eyeguide/api/session.py:541-555）。

### 2.4 初步重构判断

推断：当前更适合采用“模块化单体 + 端口/适配器”的渐进式重构，而不是立即拆成微服务。首要问题是职责边界、状态所有权、资源生命周期和测试缺失，而不是部署规模。

建议优先处理：

1. 建立纯业务规则测试；
2. 抽取统一的帧输入、分析结果、领域事件和会话快照；
3. 把视觉模型调用、风险策略、渲染和语音策略拆开；
4. 统一桌面端与移动端共用的应用用例；
5. 明确每个本地会话、WebSocket 会话和进程级服务的资源所有权；
6. 保持现有 WebSocket 协议兼容，先建立契约测试再改协议。

## 3. 功能边界

| 功能域 | 当前能力 | 主要证据 |
| --- | --- | --- |
| 自由探索 | 摄像头实时分析近距离风险并播报 | README.md:65-70；eyeguide/ui/desktop.py:338-339；eyeguide/app/session.py:215-291 |
| 路线导航 | 搜索候选地点、生成步行路线、手动或 GPS 自动推进 | README.md:71-73；eyeguide/ui/desktop.py:341-402；eyeguide/app/route_guidance.py:129-231 |
| 视频模拟 | 加载本地视频，离线回放感知流程 | README.md:79-81；eyeguide/ui/desktop.py:484-492 |
| 目标检测 | YOLO 检测和 ByteTrack 跟踪；不可用时可使用启发式路径 | README.md:53-61；eyeguide/services/vision/yolo_backend.py:81-138；eyeguide/services/vision/analyzer.py:108-137 |
| 距离估计 | Depth Anything V2 估计深度，并从目标框提取距离 | eyeguide/services/vision/depth_backend.py:93-149 |
| 盲道感知 | PaddleSeg/PP-MobileSeg-Tiny 识别盲道和盲道占用 | README.md:75-77；eyeguide/services/vision/blind_road_backend.py:79-105 |
| 风险播报 | 根据类别、方向、距离、优先级、冷却和去重生成事件 | eyeguide/services/vision/analyzer.py:312-364；eyeguide/services/speech/engine.py:275-333 |
| GPS | NMEA 串口、Windows 定位补充、最新位置状态 | eyeguide/services/location/service.py:31-203 |
| 手机 Web | 摄像头、GPS、IMU、WebSocket、浏览器 TTS、路线控制 | mobile-web/app.js:343-510；mobile-web/app.js:638-810 |
| 训练工具 | YOLO 标注转 PaddleSeg、盲道模型训练、图片搜索 | README.md:180-195；eyeguide/tools/ |

## 4. 总体架构现状

### 4.1 当前结构

~~~text
app.py
  └─ eyeguide.main
       └─ eyeguide.ui.desktop
            ├─ NavigationService
            └─ SessionController
                 ├─ SpeechEngine
                 ├─ SceneAnalyzer
                 │    ├─ YoloDetector
                 │    ├─ DepthAnythingV2MetricEstimator
                 │    ├─ BlindRoadSegmenter
                 │    ├─ HeuristicVisionDetector
                 │    ├─ ObjectTracker
                 │    └─ UnicodeFrameRenderer
                 ├─ GpsService
                 └─ RouteGuidance

FastAPI create_app
  └─ api_lifespan
       ├─ SpeechEngine（进程级共享）
       └─ NavigationService（进程级共享）
            └─ 每个 WebSocket → ApiSession
                 ├─ SceneAnalyzer（连接级）
                 ├─ RouteGuidance（连接级）
                 └─ 单槽最新帧 worker
~~~

### 4.2 桌面端主流程

~~~text
用户点击探索/导航/模拟
  → EyeGuideGUI._start_session()
  → SessionController.start()
  → 后台线程打开摄像头或视频
  → 读取帧
  → SceneAnalyzer.analyze()
  → RouteGuidance.sync_with_location()
  → SpeechEngine.speak()
  → Renderer 绘制
  → OpenCV 窗口显示
  → Queue[AppEvent] 回传桌面状态
~~~

关键证据：

- SessionController 在构造时创建多个具体服务（eyeguide/app/session.py:55-73）。
- _run_loop() 同时执行采集、分析、路线同步、语音、渲染、窗口显示和键盘处理（eyeguide/app/session.py:198-291）。
- 桌面 UI 通过队列轮询事件并直接更新 Tk 控件（eyeguide/ui/desktop.py:564-587）。

### 4.3 移动端主流程

~~~text
手机浏览器
  → WebSocket envelope
  → ApiSession._handle_message()
  → sensor.frame
  → Base64 解码
  → 单槽 latest-frame
  → frame worker
  → SceneAnalyzer.analyze()
  → frame.result
  → 浏览器绘制、更新状态、浏览器 TTS
~~~

关键证据：

- API 消息类型由 ApiSession._handle_message() 分发（eyeguide/api/session.py:149-183）。
- 帧请求包含 frame_id、客户端时间、图像、GPS 和 IMU（eyeguide/api/protocol.py:74-91）。
- 服务端限制图像解码大小为 3,000,000 字节，并返回处理时序信息（eyeguide/api/session.py:44-45、660-682、596-644）。
- 移动端对过期结果不绘制检测框，但可能在较宽松的语音时间窗口内继续播报事件（mobile-web/app.js:643-671）。

## 5. 模块职责分析

### 5.1 Domain：领域模型

当前 eyeguide/domain/models.py 集中放置：

- Mode
- DetectionEvent
- DetectionBox
- RouteStep
- RoutePlan
- FrameAnalysis
- GpsFix
- GeoPoint

证据：eyeguide/domain/models.py:26-227。

观察：

- 领域模型同时承载视觉、路线、GPS 和 UI 展示数据。
- DetectionEvent 混合了领域事件内容和语音调度参数。
- FrameAnalysis 包含 overlays 等渲染相关字段。
- GpsFix.is_valid 只验证经纬度范围，不验证时间新鲜度、精度或来源可信度。

重构建议：

~~~text
domain/
  perception.py       # Detection、HazardEvent、BlindRoadState
  navigation.py       # RoutePlan、RouteStep、NavigationState
  location.py         # GeoPoint、LocationFix
  session.py          # SessionState、SessionSnapshot
  events.py
~~~

渲染文案、API 字段和语音队列控制字段应逐步从核心领域对象中移出。

### 5.2 Application：应用用例

当前没有清晰独立的 application 层。主要业务编排分散在：

- SessionController
- ApiSession
- EyeGuideGUI
- RouteGuidance

建议逐步引入：

~~~text
application/
  perception_use_case.py
  local_session_use_case.py
  navigation_use_case.py
  settings_use_case.py
  health_use_case.py
~~~

建议用例：

| 用例 | 输入 | 输出 |
| --- | --- | --- |
| AnalyzeFrame | 帧、GPS、IMU、来源信息 | 统一 FrameResult |
| StartSession | 模式、采集源、路线 | 会话 ID 和状态 |
| StopSession | 会话 ID | 停止结果和资源状态 |
| SearchDestination | 起点、终点关键词、provider | 候选列表 |
| SetRoute | 起点、终点、fallback 策略 | 路线快照 |
| AdvanceRoute | 会话/路线 ID | 新导航状态 |
| UpdateSettings | 受支持的设置字段 | 脱敏配置快照 |
| GetHealth | 无 | 服务和后端健康状态 |

### 5.3 Vision：视觉链路

当前 SceneAnalyzer 是最明显的“大类”：

- 创建所有视觉后端；
- 管理帧计数和缓存；
- 并行或串行调度推理；
- 把 YOLO 输出转为 DetectionBox；
- 根据距离/位置/类别生成 DetectionEvent；
- 应用盲道优先策略；
- 绘制检测框；
- 生成性能叠加层。

证据：eyeguide/services/vision/analyzer.py:52-155、165-226、291-417。

建议拆分：

~~~text
application/perception/
  analyze_frame.py

domain/perception/
  risk_policy.py
  notification_policy.py

ports/
  object_detector.py
  depth_estimator.py
  blind_road_segmenter.py

adapters/vision/
  yolo_adapter.py
  depth_anything_adapter.py
  blind_road_adapter.py
  heuristic_adapter.py

presentation/
  frame_renderer.py
~~~

第一阶段必须保持以下行为：

- YOLO 可用时启用 YOLO 主路径；
- YOLO 不可用时才走启发式 fallback；
- 目标中心比例限制为 0.08 到 0.92；
- 目标距离大于 2 米时不产生普通风险播报；
- 盲道检测后只保留 blind_road: 事件；
- 事件按优先级升序选出第一条作为摘要。

证据：eyeguide/services/vision/analyzer.py:108-145、312-364、382-387。

### 5.4 Runtime：会话与资源生命周期

当前本地会话：

- 使用一个后台 Thread；
- 使用一个 Event 停止；
- 使用锁保护当前 VideoCapture；
- stop() 释放 capture 后等待最多 3 秒；
- shutdown() 继续停止 GPS 和 SpeechEngine。

证据：eyeguide/app/session.py:70-73、138-171、341-373。

当前 API 会话：

- 每个 WebSocket 创建 asyncio worker；
- 断连时设置 _closed、唤醒 slot、取消 worker；
- 推理通过 asyncio.to_thread() 执行。

证据：eyeguide/api/session.py:112-147、514-579。

建议建立统一运行时状态：

~~~text
IDLE
  → STARTING
  → RUNNING
  → STOPPING
  → IDLE

STARTING / RUNNING
  → FAILED
  → STOPPING
~~~

每个会话明确拥有：

- session_id
- 模式
- 帧源
- 取消信号
- worker
- analyzer
- route state
- 最近一次结果
- 最近一次错误

进程级资源与会话级资源必须分开：

| 资源 | 建议所有者 |
| --- | --- |
| 摄像头/视频文件 | 本地会话 |
| WebSocket 最新帧槽 | 单个 WebSocket 会话 |
| 路线进度 | 单个会话 |
| GPS 串口服务 | 桌面应用级，是否可被 API 共享需另行决定 |
| SpeechEngine | 桌面应用级或明确的 API 广播策略 |
| 地图 provider | 进程级可共享，但请求必须可取消/限时 |
| 模型 | 需通过 ADR 决定是进程共享还是每会话实例 |

## 6. 关键行为和错误路径

### 6.1 正常路径

1. 桌面启动后创建 GUI、导航服务和会话控制器（eyeguide/ui/desktop.py:21-32）。
2. 用户启动探索、导航或模拟（eyeguide/ui/desktop.py:338-402、484-492）。
3. 会话线程打开摄像头/视频，读取帧并调用分析器（eyeguide/app/session.py:215-243）。
4. 分析器产生检测框、盲道状态、事件和性能信息（eyeguide/services/vision/analyzer.py:103-155）。
5. 路线模块根据 GPS 同步当前步骤（eyeguide/app/route_guidance.py:129-173）。
6. 桌面端播放第一条风险事件；移动端把结果回传给浏览器，由浏览器执行 TTS（eyeguide/app/session.py:252-267；mobile-web/app.js:691-707）。

### 6.2 错误路径

| 场景 | 当前行为 | 证据 |
| --- | --- | --- |
| 摄像头/视频无法打开 | 发送 error，设置停止事件 | eyeguide/app/session.py:215-221 |
| 视频读取失败 | 尝试重新打开；失败后结束循环并释放资源 | eyeguide/app/session.py:228-240、284-291 |
| YOLO 不可用 | 使用启发式检测路径 | eyeguide/services/vision/analyzer.py:108-137 |
| 深度模型失败 | 返回无深度结果，并记录加载错误 | eyeguide/services/vision/depth_backend.py:61-80、93-119 |
| 盲道模型失败 | 标记不可用，必要时从 GPU 回退 CPU | eyeguide/services/vision/blind_road_backend.py:131-166 |
| 地图服务失败 | AMap 在特定错误下回退 OSM；桌面失败关闭，API 可按请求回退 demo | eyeguide/services/navigation/service.py:30-50；eyeguide/api/session.py:415-427 |
| GPS 串口异常 | 更新状态并按间隔重连 | eyeguide/services/location/service.py:82-116 |
| TTS 后端异常 | 关闭并重建后端，最终可退回控制台输出 | eyeguide/services/speech/engine.py:416-481 |
| WebSocket 消息校验失败 | 返回结构化错误 | eyeguide/api/session.py:149-183 |
| WebSocket 断开 | 取消该连接的帧 worker | eyeguide/api/session.py:133-147 |

### 6.3 已观察到的边界情况

| 边界情况 | 当前行为 | 证据 |
| --- | --- | --- |
| 重复启动本地会话 | 抛出 RuntimeError | eyeguide/app/session.py:115-123 |
| 空终点 | 桌面端弹窗后返回 | eyeguide/ui/desktop.py:341-346 |
| 多个终点候选 | 打开选择对话框，取消则不启动导航 | eyeguide/ui/desktop.py:357-367 |
| 没有 GPS 时使用 @gps 起点 | API 返回 route.pending | eyeguide/api/session.py:399-413 |
| GPS 精度高于阈值 | 暂不自动推进，并发送 GPS 状态 | eyeguide/app/route_guidance.py:144-149 |
| 当前路线步骤没有目标坐标 | 不自动推进 | eyeguide/app/route_guidance.py:151-155 |
| 到达最后一步 | 播报接近目的地，不再增加索引 | eyeguide/app/route_guidance.py:209-215 |
| 移动端帧拥塞 | 新帧覆盖未处理旧帧，发送 frame.dropped | eyeguide/api/session.py:323-337、541-555 |
| 图片过大或无法解码 | 返回 BAD_FRAME_DATA | eyeguide/api/session.py:660-682 |
| 结果过期 | 手机端不绘制框，但可能继续播报较新的过期事件 | mobile-web/app.js:643-671 |
| 检测到盲道 | 普通目标事件从事件列表中移除 | eyeguide/services/vision/analyzer.py:382-387 |

## 7. 关键接口、数据和配置

### 7.1 内部数据模型

| 类型 | 当前作用 | 重构注意 |
| --- | --- | --- |
| DetectionEvent | 风险文本与播报元数据 | 将业务事件和 TTS 调度字段逐步分离 |
| DetectionBox | 目标框、置信度、跟踪 ID、距离和方向 | 增加来源帧/结果新鲜度信息 |
| FrameAnalysis | 一帧综合分析结果 | 从中移出渲染 overlays |
| RoutePlan | 路线整体信息和步骤列表 | 保持 provider、距离、时长兼容 |
| GpsFix | 定位结果 | 增加新鲜度策略，但避免第一阶段改变行为 |
| AppEvent | 桌面服务向 UI 传递事件 | 由自由字符串升级为结构化事件 |

证据：eyeguide/domain/models.py:42-227；eyeguide/core/events.py:13-26。

### 7.2 API 协议

API 外层消息字段：

~~~text
type
device_id
seq
ts
session_id
payload
~~~

证据：eyeguide/api/protocol.py:15-32。

当前主要消息：

~~~text
hello
ping
sensor.gps
sensor.imu
sensor.frame
route.search
route.set
route.next
route.repeat
route.clear
speech.test
config.update
~~~

证据：eyeguide/api/session.py:158-171。

当前结果消息包括：

~~~text
session.ready
hello.ack
frame.accepted
frame.dropped
frame.result
gps.updated
route.search.result
route.set.result
route.pending
route.clear.result
speech.test.result
error
~~~

移动端直接依赖这些消息类型和字段，因此协议重构应先建立 schema/契约测试，再考虑改名或分版本。

### 7.3 配置

当前配置使用不可变 dataclass：

- VisionConfig
- SpeechConfig
- GpsConfig
- NavigationConfig
- AppConfig

证据：eyeguide/core/config.py:13-140。

在已检查范围内没有发现完整的配置加载优先级，因此后续需要明确：

- 启动配置和运行时配置是否分离；
- API Key 从哪里读取；
- 哪些配置立即生效；
- 哪些配置只在下次会话生效；
- 配置输出如何脱敏。

## 8. 推荐目标架构

### 8.1 分层

~~~text
入口层
  ├─ Tkinter 桌面端
  ├─ FastAPI/WebSocket
  └─ mobile-web

应用层
  ├─ AnalyzeFrame
  ├─ StartSession / StopSession
  ├─ NavigationUseCase
  ├─ UpdateSettings
  └─ HealthUseCase

领域层
  ├─ 风险规则
  ├─ 播报候选规则
  ├─ 路线进度规则
  ├─ 领域事件
  └─ 会话状态

端口层
  ├─ FrameSource
  ├─ VisionPipeline
  ├─ LocationSource
  ├─ RouteProvider
  ├─ SpeechOutput
  └─ Clock

适配器层
  ├─ OpenCV / YOLO / Depth / PaddleSeg
  ├─ NMEA / Windows Location
  ├─ AMap / OSRM / Demo
  └─ SAPI / pyttsx3 / Browser Speech
~~~

依赖方向建议：

~~~text
入口层 → 应用层 → 领域层 / 端口层
适配器层 → 实现端口层
组合根 → 创建并注入适配器和应用服务
~~~

领域层不应直接导入 Tkinter、FastAPI、OpenCV、Torch、Paddle、pyserial、requests 或 SAPI。

### 8.2 建议目录

不要求一次性移动所有文件，可以先建立逻辑边界，再渐进迁移：

~~~text
eyeguide/
  domain/
    perception.py
    navigation.py
    location.py
    session.py
    events.py

  application/
    perception_use_case.py
    session_use_case.py
    navigation_use_case.py
    settings_use_case.py
    health_use_case.py

  ports/
    frame_source.py
    vision_pipeline.py
    location_source.py
    route_provider.py
    speech_output.py
    clock.py

  adapters/
    vision/
    location/
    navigation/
    speech/

  runtime/
    session_runtime.py
    frame_slot.py
    lifecycle.py
    health.py

  entrypoints/
    desktop/
    api/

  core/
    config.py
    paths.py
    logging.py

  tools/
~~~

### 8.3 建议统一结果对象

~~~text
FrameInput
  - frame_id
  - captured_at
  - image
  - gps
  - imu
  - source

FrameResult
  - frame_id
  - result_at
  - detections
  - hazards
  - blind_road
  - backend_status
  - timings
  - freshness

SessionSnapshot
  - session_id
  - state
  - mode
  - backend_status
  - latest_hazard
  - route
  - location
  - error
~~~

API JSON、桌面显示文本和浏览器展示数据都从这些内部结果映射，不应让 Pydantic 请求对象、Tk 控件或 Ultralytics 对象穿透整个系统。

## 9. 分阶段实施建议

### 阶段 0：冻结基线

实施内容：

- 记录当前提交、分支和工作区状态。
- 保留未跟踪的 weights/yolo26n.pt，不要清理。
- 记录桌面端和 API 端的时序。
- 确认模型路径、启动命令、环境变量和外部服务。
- 准备少量可重复视频或 fake 输入。

测试/验收：

- 桌面探索启动/停止；
- 模拟视频结束；
- API WebSocket 连接/断开；
- 路线成功和失败；
- 无模型时的降级；
- TTS 不可用时的行为。

### 阶段 1：先补纯逻辑测试

优先测试：

| 模块 | 测试内容 |
| --- | --- |
| location/nmea.py | RMC/GLL/GGA、经纬度转换、速度换算、无效字段 |
| app/route_guidance.py | 空路线、手动推进、最后一步、精度阈值、到达半径、冷却 |
| navigation/service.py | 成功、失败、AMap→OSM fallback、demo fallback、未知 provider |
| services/speech/engine.py | 优先级、冷却、去重、频道替换、取消、后端失败 |
| domain/models.py | 坐标边界、空值、路线步骤边界 |

这一阶段不移动目录，先通过 characterization test 锁定当前行为。

### 阶段 2：建立端口和 Fake 实现

新增最小协议：

~~~text
VisionPipeline.analyze(frame_input)
FrameSource.read()
LocationSource.latest_fix()
RouteProvider.build_route()
SpeechOutput.speak()
Clock.now()
~~~

新增 fake：

~~~text
FakeVisionPipeline
FakeFrameSource
FakeLocationSource
FakeRouteProvider
FakeSpeechOutput
FakeClock
~~~

验收标准：

- 无摄像头、GPU、串口、TTS 和公网时可运行核心用例；
- 领域模块导入不会加载大型模型库；
- 每个 fake 都可以注入模拟成功、失败、延迟和空结果。

### 阶段 3：拆分视觉推理和风险策略

将 SceneAnalyzer 拆成：

~~~text
模型推理
  → 感知结果
  → 风险规则
  → 播报候选
  → 渲染结果
~~~

保持已有阈值和行为不变，先测试：

- 类别是否属于风险类别；
- 中心区域边界；
- 2 米距离边界；
- 0.5 米、1.4 米距离分档；
- 盲道优先过滤；
- YOLO fallback；
- 推理缓存和尺寸变化；
- backend 失败时的局部降级。

### 阶段 4：拆分导航和定位

将 RouteGuidance 拆成：

~~~text
RouteProgressState
RouteProgressPolicy
NavigationUseCase
SpeechPresenter
~~~

重点处理：

- Haversine 距离；
- GPS 精度；
- fix 是否过期；
- 到达半径；
- 自动推进冷却；
- 最后一步行为；
- route provider 的 fallback 原因；
- API Key 脱敏。

地图请求应移出 Tk 主线程和 FastAPI 事件循环，使用受控后台任务或异步 HTTP。

### 阶段 5：统一本地会话和 WebSocket 会话模型

建立 SessionRuntime：

- 明确状态转换；
- 明确启动失败回滚；
- 明确 stop/shutdown 幂等性；
- 明确线程/async task 所有权；
- 明确 capture、worker、analyzer、route、speech 的生命周期；
- 保留移动端单槽最新帧语义。

重点测试：

- 重复 start；
- stop-before-start；
- 重复 stop；
- capture 阻塞；
- 视频 EOF；
- 摄像头断开；
- 推理过程中断开 WebSocket；
- 新帧覆盖旧帧；
- 多个 WebSocket 互相隔离。

### 阶段 6：API 协议适配

先不改变现有消息名称和字段，只拆代码：

~~~text
WebSocket Transport
  → DTO 校验 / 编解码
  → Application Session
  → Use Case
  → Response Mapper
~~~

建立协议测试：

- hello、ping；
- GPS/IMU；
- frame.accepted/frame.dropped/frame.result；
- route.search/route.set/route.next/route.repeat/route.clear；
- speech.test；
- config.update；
- 非法 Base64；
- 超大图片；
- 不支持的消息类型；
- 连接断开时 worker 清理。

### 阶段 7：桌面 UI 变薄

目标：

~~~text
Tk 回调
  → 命令对象
  → 应用用例
  → 结构化状态事件
  → Presenter
  → Tk 控件
~~~

当前桌面 UI 直接修改 controller 私有字段（eyeguide/ui/desktop.py:552-562），应改成公开的 UpdateSettings 用例。

### 阶段 8：依赖、路径、模型和打包收敛

当前 pyproject.toml 将 API、桌面、Torch CUDA、Paddle、Ultralytics、Windows 地理定位等依赖集中在一起（pyproject.toml:11-122）。

建议根据实际 import 关系评估：

~~~text
base
desktop
api
vision-yolo
vision-depth
vision-blind-road
gps-windows
training
dev
~~~

同时统一：

- 模型路径；
- 源码运行路径；
- PyInstaller 路径；
- 外置模型目录；
- 模型 manifest；
- README 命令；
- CLI entry points；
- 开发证书和 API Key 管理。

## 10. 测试目录建议

~~~text
tests/
  unit/
    domain/
      test_risk_policy.py
      test_route_progress.py
    location/
      test_nmea.py
    navigation/
      test_navigation_service.py
    speech/
      test_speech_policy.py
    core/
      test_config.py
      test_paths.py

  contract/
    test_vision_pipeline_contract.py
    test_route_provider_contract.py
    test_location_source_contract.py
    test_speech_output_contract.py

  integration/
    test_explore_session.py
    test_simulation_session.py
    test_navigation_session.py
    test_api_lifespan.py

  api/
    test_protocol_schema.py
    test_websocket_session.py
    test_latest_frame_slot.py

  replay/
    manifests/
    test_video_replay.py

  packaging/
    test_entrypoints.py
~~~

### CI 建议

1. 快速检查：格式、静态检查、类型检查、纯单元测试。
2. 普通 PR：contract tests、fake 集成测试、WebSocket 契约测试。
3. 夜间/手动：真实模型回放、GPU、地图服务、Windows GUI、打包程序。
4. 发布前：干净环境安装矩阵、模型资产校验、桌面端和移动端人工验收。

普通 CI 不应依赖真实摄像头、真实语音设备、公网地图、自动下载模型、GPU 或手机浏览器权限。

## 11. 主要风险和待确认问题

| 问题 | 当前证据 | 影响 |
| --- | --- | --- |
| YOLO 权重路径不一致 | README/代码使用 models/YOLO/yolo26n.pt，Git 中还有 yolo11n.pt 和未跟踪 weights/yolo26n.pt | 影响模型加载、打包和文档 |
| 是否存在真实测试 | Git 跟踪清单未发现测试目录或 CI | 无法判断重构回归风险 |
| 视觉模型是否可并发 | 全局 _VISION_EXECUTOR，但每个 API session 都创建 analyzer | 可能产生 GPU/模型线程安全问题 |
| 停止是否一定可靠 | 本地 stop 依赖释放 capture 和 3 秒 join | 原生 OpenCV 阻塞时可能无法及时退出 |
| API SpeechEngine 是否应进程共享 | API lifespan 创建一个 SpeechEngine，而路线状态按连接隔离 | 多手机连接时可能语音互相抢占 |
| stale 结果规则是否统一 | 服务端 500ms，浏览器语音阈值 1500ms | 影响实时性和用户对风险提示的理解 |
| Windows 定位 fallback 是否考虑过期串口 fix | 当前只要存在 fix 就可能不再尝试 Windows fallback | 影响定位可靠性 |
| 真实 CLI 是否可用 | README 有多个 uv run 命令，pyproject 未发现可见 project.scripts | 影响开发和发布体验 |
| API Key 是否会泄漏 | 当前协议允许请求携带 amap_api_key | 需要审查日志、异常和响应脱敏 |
| 视频 EOF 是否应该循环 | 当前失败后会尝试重新打开视频源 | 可能导致模拟视频循环，而不是自然结束 |

## 12. 首批任务清单

建议按以下顺序拆分 PR：

1. 增加本知识文档、当前架构图和协议清单。
2. 为 NMEA、路线推进和导航 fallback 增加纯逻辑测试。
3. 为 SpeechEngine 增加 fake backend 注入点和队列测试。
4. 为 SceneAnalyzer 增加 fake 视觉后端和风险策略测试。
5. 定义结构化应用事件和 FrameResult。
6. 建立 AnalyzeFrame 用例，由桌面端先接入。
7. 建立 SessionRuntime 和可注入 FrameSource。
8. 将 API 的 WebSocket transport 与业务会话拆开，但保持协议不变。
9. 建立统一 NavigationUseCase。
10. 让桌面 UI 通过公开设置用例更新配置。
11. 审计依赖、模型路径和 CLI entry points。
12. 经过兼容期后，再删除旧接口和移动目录。

每个 PR 应包含：

- 目标；
- 是否改变用户行为；
- 受影响模块；
- 新增测试；
- 启动/验证命令；
- 回滚方式；
- 未解决问题。

## 13. 结论

EyeGuide 当前已经具备“视觉感知 + 风险播报 + GPS + 路线导航 + 桌面/移动入口”的完整原型闭环，但业务编排、模型推理、渲染、外部 I/O 和生命周期管理仍然交叉。

推荐的重构顺序：

~~~text
行为基线
  → 纯逻辑测试
  → 类型和端口
  → 视觉策略拆分
  → 导航策略拆分
  → 会话生命周期统一
  → API/桌面复用用例
  → 依赖和打包收敛
~~~

这样可以在不同时改变模型、阈值、播报策略和 UI 的情况下，逐步得到更容易测试、更容易替换模型和更容易支持新入口的结构。

## 14. 证据索引

| 证据位置 | 支持内容 |
| --- | --- |
| app.py:1-8；eyeguide/main.py:7-16 | 桌面启动入口 |
| eyeguide/ui/desktop.py:21-32、330-604 | 桌面功能和回调 |
| eyeguide/app/session.py:28-73、198-373 | 本地会话和采集生命周期 |
| eyeguide/services/vision/analyzer.py:19-226、278-417 | 视觉编排、缓存和风险策略 |
| eyeguide/services/vision/yolo_backend.py:20-138 | YOLO 加载和推理 |
| eyeguide/services/vision/depth_backend.py:17-149 | 深度模型加载和距离估计 |
| eyeguide/services/vision/blind_road_backend.py:54-235 | 盲道模型加载、缓存和后处理 |
| eyeguide/domain/models.py:26-227 | 领域数据模型 |
| eyeguide/core/events.py:13-26；eyeguide/core/config.py:13-140 | 事件和配置 |
| eyeguide/app/route_guidance.py:20-250 | 路线推进和 GPS 策略 |
| eyeguide/services/location/nmea.py:8-126；service.py:31-203 | NMEA 和 GPS 生命周期 |
| eyeguide/services/navigation/models.py:9-41；service.py:10-165 | 路线 provider 和 fallback |
| eyeguide/services/speech/engine.py:237-487 | 语音队列、去重、取消和 fallback |
| eyeguide/api/protocol.py:15-169；server.py:19-69；lifecycle.py:16-88 | API 协议和进程生命周期 |
| eyeguide/api/session.py:78-183、262-337、514-710、730-880 | WebSocket 会话、帧队列和结果映射 |
| mobile-web/app.js:99-164、343-510、638-810 | 手机采集、协议和过期结果处理 |
| pyproject.toml:5-135；.gitignore:1-16；README.md | 项目、依赖、资产和文档边界 |
| Git 检查结果 | 提交、分支、62 个跟踪文件和未跟踪 weights/yolo26n.pt |
