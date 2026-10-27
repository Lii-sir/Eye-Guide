# EyeGuide 从零重写：目标架构与实施蓝图

> 状态：提案，等待确认后作为 V2 架构基线
> 日期：2026-10-12；工程规划更新：2026-10-23
> 项目根目录：D:\python_programs\LXD_project\EyeGuide
> 语言：中文为主，接口名和代码标识保留英文

> 工程落地补充：[数据库、模型调度与部署实施规划](engineering-delivery-plan-2026-10-23.md)。数据库和进程资源边界以补充规划为准。

## 1. 架构决策摘要

建议将下一代 EyeGuide 作为一个新实现（V2）从空的应用骨架开始，而不是继续改造当前会话控制器和分析器。

采用以下策略：

1. **冻结 V1**：当前实现保留为行为、算法、协议和 UI 参考，不再往其中叠加新功能。
2. **在同一仓库建立 V2**：新代码放入 src/eyeguide；V1 暂时留在原位置，避免早期删除造成资产或算法丢失。
3. **先定接口，再写适配器**：先完成领域模型、应用用例、运行时和 fake adapters；核心链路通过测试后再接入真实模型、硬件和 UI。
4. **先完成垂直切片**：优先完成“假帧源 → 感知用例 → 结构化结果 → WebSocket 响应”的最小闭环，而不是一次性实现全部层。
5. **迁移时保持兼容**：V2 初期沿用现有 mobile-web WebSocket 消息格式；等新旧映射稳定后再版本化协议。
6. **不拆微服务**：V2 仍是单进程模块化单体；当前没有证据表明必须先承担分布式部署复杂度。
7. **保留模型与算法资产**：未跟踪的 weights/yolo26n.pt、其他模型、配置、数据集和训练结果都视为独立资产，不因重写而搬动、覆盖或清理。

V1 当前确实同时承担两个入口和两套会话编排：桌面入口创建 SessionController（eyeguide/ui/desktop.py:21-32），API 每条 WebSocket 创建 ApiSession（eyeguide/api/session.py:81-110）；两边都使用 SceneAnalyzer，但生命周期、语音输出和路由状态的所有权不同。SceneAnalyzer 又同时持有模型、调度推理、生成风险事件和绘制结果（eyeguide/services/vision/analyzer.py:52-155）。这些是采用新架构而非继续局部修补的主要依据。

## 2. V2 的产品范围

### 2.1 V2 首版必须支持

| 能力 | V2 首版要求 |
| --- | --- |
| 视频输入 | 桌面摄像头和本地视频都能作为 FrameSource；移动端可通过 API 上传帧 |
| 感知结果 | 输出检测框、风险事件、盲道状态、后端状态和处理时序 |
| 探索模式 | 使用当前帧结果展示环境风险，并由入口选择对应播报方式 |
| 视频模拟 | 可重复播放本地视频，支持测试和回归 |
| 路线导航 | 搜索地点、设置步行路线、手动推进、重复指令和 GPS 自动推进 |
| 定位 | 至少支持 GPS fix 抽象、NMEA parser 和串口 adapter；Windows 定位可作为可选 adapter |
| 语音 | 通过 SpeechOutput 端口输出；桌面与手机浏览器可使用不同的具体 adapter |
| Mobile Web | V2 API 能与现有 mobile-web 建立连接、上传帧并接收结果 |
| 降级状态 | 模型或设备不可用时能报告 capability/status，不因可选模型缺失而让基础 API 无法启动 |
| 测试 | 纯逻辑、应用服务和协议测试不需要真实摄像头、GPS、音频、GPU 或公网 |

### 2.2 V2 首版明确不做

- 首轮不做微服务、消息中间件或完整账户系统。数据库纳入架构：Fake 闭环后实现 SQLite 偏好与会话摘要；多用户部署阶段实现 PostgreSQL 与身份适配。
- 不同时升级或更换视觉模型。
- 不在架构重写中调优风险阈值或改变播报策略。
- 不把系统定位描述为安全认证设备。
- 不重写训练和图片搜索工具；这些工具先作为独立工具保留。
- 不在第一阶段重做 Mobile Web 的视觉设计。
- 不默认将图像、连续位置或用户轨迹持久化。

### 2.3 行为兼容策略

第一轮优先复刻可观察行为，不把重写与产品改版混在一起：

- 保留现有模式和消息含义。
- 保留普通目标类别、事件排序、播报冷却/去重和盲道策略，直到特征测试建立。
- 保留最新帧优先策略及 frame.accepted、frame.dropped、frame.result 消息。
- 保留桌面导航失败时不启动导航的行为；API demo fallback 仍由请求显式控制。
- 对尚未确认的差异，先记录 ADR，不凭工程便利改变用户行为。

## 3. 目标架构

~~~text
入口层
  ├─ Desktop UI
  ├─ FastAPI REST / WebSocket
  ├─ Mobile Web（独立静态客户端）
  └─ CLI
        │ Commands / Queries / DTO
        ▼
应用层
  ├─ PerceptionService
  ├─ SessionService
  ├─ NavigationService
  ├─ SettingsService
  └─ HealthService
        │
        ├──────────────► 领域层：纯规则、值对象、状态转移
        │
        ▼
运行时层：SessionManager、workers、latest-frame slot、取消和生命周期
        │
        ▼
端口层：FrameSource、VisionPipeline、LocationSource、RouteProvider、
        SpeechOutput、EventPublisher、Clock
        ▲
        │ 由适配器实现
适配器层
  ├─ OpenCV / YOLO / Depth / PaddleSeg
  ├─ Serial NMEA / Windows Location
  ├─ OSRM / AMap / Demo
  └─ SAPI / pyttsx3 / Console / Browser TTS
~~~

### 3.1 依赖规则

- Entrypoint 依赖 Application。
- Application 依赖 Domain 和 Ports。
- Adapter 实现 Ports，但 Domain 不依赖 Adapter。
- Composition Root 负责选择配置、创建 Adapter 并注入 Application。
- API DTO 仅存在于 API 入口；桌面 ViewModel 仅存在于桌面入口。
- 领域事件是结构化数据，不是已经渲染好的 UI 文案。

建议在 CI 中加一条架构依赖检查：domain 不得导入 Tkinter、FastAPI、OpenCV、Torch、Paddle、requests 或 pyserial。实现初期可以用 import-linter 一类工具，也可以先用简单的静态检查测试。

## 4. V2 目录布局

第一阶段采用 src layout，使源码包与测试/仓库工具配置分开：

~~~text
src/eyeguide/
  domain/
    common.py
    perception.py
    navigation.py
    location.py
    session.py
    events.py
  application/
    commands.py
    results.py
    perception_service.py
    session_service.py
    navigation_service.py
    settings_service.py
    health_service.py
  ports/
    frame_source.py
    vision.py
    location.py
    navigation.py
    speech.py
    event_publisher.py
    clock.py
  runtime/
    composition.py
    session_manager.py
    session_runtime.py
    latest_frame_slot.py
    worker_group.py
    health_registry.py
  adapters/
    capture/
      opencv_camera.py
      opencv_video.py
    vision/
      pipeline.py
      yolo.py
      depth_anything.py
      blind_road.py
      heuristic.py
      renderer.py
    location/
      nmea_parser.py
      serial_gps.py
      windows_location.py
    navigation/
      osrm.py
      amap.py
      demo.py
    speech/
      sapi.py
      pyttsx3_backend.py
      console.py
    system/
      config_loader.py
      paths.py
      logging_setup.py
  entrypoints/
    desktop/
      app.py
      controller.py
      presenter.py
      view_model.py
    api/
      app.py
      routes.py
      websocket.py
      protocol_v1.py
      mapper.py
    cli/
      main.py
  bootstrap.py

tests/
  unit/
    domain/
    application/
    runtime/
  contract/
    capture/
    vision/
    location/
    navigation/
    speech/
  integration/
    sessions/
    multi_client/
  api/
    protocol/
    websocket/
  replay/
    manifests/
  packaging/
~~~

V1 当前路径在重写期只读保留，避免在 V2 尚未可运行时用搬目录产生虚假的进度。最终切换时再统一包路径和命令入口。

## 5. 核心对象和接口

### 5.1 Domain 对象

建议第一轮实现以下对象，并通过构造约束和测试锁定单位：

~~~text
FrameId
SessionId
GeoPoint
LocationFix
Detection
HazardEvent
BlindRoadState
FrameInput
FrameResult
RouteStep
RoutePlan
NavigationState
SessionState
SessionSnapshot
BackendStatus
TimingInfo
~~~

关键约束：

- 时间统一使用带时区的 UTC 时间或单调时钟语义；协议 epoch 毫秒只在边界转换。
- 距离单位统一为米，速度统一为米/秒。
- 坐标使用明确的地理坐标约定，并在地图 adapter 负责坐标系转换。
- FrameResult 记录 frame_id、采集时间、分析时间和缓存数据的新鲜度。
- 单目深度产生的距离作为估计值处理，不将精度扩大为安全保证。
- Domain 对象不保存 API 响应结构或已经格式化的 TTS 句子。

### 5.2 FrameInput 与 FrameResult

~~~text
FrameInput
  frame_id
  captured_at
  image
  gps: optional LocationFix
  imu: optional ImuSnapshot
  source: desktop_camera | video_file | mobile
  session_id

FrameResult
  frame_id
  captured_at
  analyzed_at
  detections[]
  hazard_events[]
  blind_road: BlindRoadState
  backends: BackendStatus[]
  timing: TimingInfo
~~~

image buffer 所有权需明确：分析期间只读；若某个 OpenCV/model adapter 会原地修改图像，adapter 必须复制或在契约中声明。不能由并发模型共享后再假定缓冲区不可变。

### 5.3 SessionState

~~~text
IDLE
STARTING
RUNNING
STOPPING
STOPPED
FAILED
~~~

只允许定义的状态转移；重复 Stop 幂等。Start 在资源部分创建后失败，必须释放已创建资源。Stop 超时时返回未完成状态，不能报告完全成功。

### 5.4 Ports 初始接口

概念接口如下，最终签名在第一阶段代码评审时确认：

~~~python
class FrameSource(Protocol):
    def open(self) -> None: ...
    def read(self) -> FrameReadResult: ...
    def close(self) -> None: ...

class VisionPipeline(Protocol):
    def analyze(self, frame: FrameInput) -> FrameResult: ...
    def capabilities(self) -> list[BackendStatus]: ...
    def close(self) -> None: ...

class LocationSource(Protocol):
    def start(self) -> None: ...
    def latest_fix(self) -> LocationFix | None: ...
    def stop(self) -> None: ...

class RouteProvider(Protocol):
    def search(self, request: RouteSearchRequest) -> CandidateSearchResult: ...
    def build_route(self, request: RouteRequest) -> RoutePlan: ...

class SpeechOutput(Protocol):
    def enqueue(self, command: SpeechCommand) -> SpeechDecision: ...
    def cancel(self, channel: str | None = None) -> None: ...
    def stop(self) -> None: ...

class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...
~~~

FrameReadResult 必须区分正常帧、视频 EOF、可恢复错误和致命错误，避免将摄像头掉线与视频自然结束当成同一分支。

## 6. 应用服务和处理流程

### 6.1 PerceptionService

桌面和 WebSocket 都调用同一应用服务：

~~~text
FrameInput
  → VisionPipeline
  → RiskPolicy
  → NotificationPolicy
  → FrameResult
  → EventPublisher
~~~

VisionPipeline 负责适配模型和组装感知输出；RiskPolicy 负责纯业务判断；NotificationPolicy 只选择应当播报的结构化事件；由入口选择本机 TTS 或浏览器 TTS。

### 6.2 SessionService

职责：

1. 检查 session 是否允许启动；
2. 创建 SessionRuntime；
3. 通过 FrameSource 获取帧；
4. 调用 PerceptionService；
5. 更新 SessionSnapshot；
6. 发布状态/错误/分析事件；
7. 按停止信号关闭 worker 和资源。

SessionService 不知道 Tk widget 或 WebSocket 对象。

### 6.3 NavigationService

职责：

1. 处理候选搜索和路线构建用例；
2. 解析 GPS 起点；
3. 维护当前导航状态；
4. 调用纯 RouteProgressPolicy 判断是否推进；
5. 返回结构化决策和 provider 来源。

provider fallback 必须是显式策略。Demo 路线不得伪装成真实地图路线。

### 6.4 SpeechPolicy 与 SpeechOutput

Domain/application 产出待播报的结构化 SpeechCommand，包括频道、优先级、去重键、冷却、过期时间等；具体 backend 只负责输出。移动浏览器沿用客户端 TTS，但消费与桌面相同的 HazardEvent/SpeechCommand 映射规则。

## 7. Runtime 和并发所有权

### 7.1 本地会话

~~~text
SessionManager
  └─ SessionRuntime
      ├─ FrameSource
      ├─ VisionPipeline session handle
      ├─ NavigationState
      ├─ SpeechOutput handle
      ├─ CancellationToken
      └─ WorkerGroup
~~~

### 7.2 WebSocket 会话

每条连接拥有：

~~~text
ClientSessionRuntime
  ├─ per-client latest-frame slot
  ├─ per-client GPS/IMU snapshot
  ├─ per-client NavigationState
  ├─ connection cancellation
  └─ response sender
~~~

进程级资源包括配置、logger、健康注册表、adapter factory、ModelRegistry、InferenceScheduler 和数据库连接池；模型通过调度器受控访问，可变会话状态保持隔离。具体约束见工程落地补充第 5 节。

### 7.3 最新帧槽

保留移动端 latest-frame 语义：待处理槽容量为 1；worker 忙时新帧替换待处理旧帧；每次覆盖记录 dropped_count 并返回旧帧 ID。结果仍带 frame_id、queue_delay、infer_delay、result_age 和 stale 标记。

## 8. 配置、依赖和模型资产

### 8.1 配置分层

建议顺序：

~~~text
应用默认值
  → 用户配置文件
  → 环境变量
  → 命令行覆盖
  → 每会话运行时设置
~~~

API Key 不进入普通配置回显、日志或错误信息。启动配置和运行时配置分开；更新设置通过应用服务，不允许 UI 访问私有属性。

### 8.2 依赖组

最终分组需以 import 审计和干净安装测试确认，可先评估：

~~~text
base
api
desktop
vision-yolo
vision-depth
vision-blind-road
gps-windows
training
dev
~~~

不要先改锁文件再猜测模块依赖。API-only 和 CPU 基础安装应能独立验证。

### 8.3 模型资产

- 先确认唯一 canonical model path；
- 未跟踪的 weights/yolo26n.pt 必须保持原样；
- 模型清单记录名称、版本、输入规格、许可证、文件校验值和外部获取方式；
- 不默认自动下载大型权重；
- 源码运行、PyInstaller 运行和用户外置权重使用一个 resolver；
- 模型缺失应反映在 BackendStatus，而非造成 API 应用整体导入失败。

## 9. 分阶段从零实现

### Phase 0：冻结 V1 和确认决策

交付：

- V1 基线、协议清单和行为样例；
- 当前模型资产清单（不读取私密内容）；
- V2 ADR 列表；
- 新旧代码并行和切换方案。

验收：

- 新分支/工作区已准备；
- V1 不再承载新功能；
- 所有未跟踪模型权重有保护措施。

### Phase 1：空架构和 Fake 垂直切片

实现：

- src package；
- domain 和 ports 最小类型；
- composition root；
- FakeFrameSource、FakeVisionPipeline、FakeSpeechOutput、FakeClock；
- healthz；
- WebSocket hello/ping；
- 空 desktop bootstrap。

验收：

- 不装 GPU/模型可启动；
- 所有 Fake 集成测试通过；
- 基础 API 可以连接并正确断开。

### Phase 2：领域规则

实现：

- SessionState 转移；
- 风险分类；
- 事件优先级与选择；
- GPS 坐标、新鲜度和精度规则；
- Haversine 距离；
- 路线自动推进；
- latest-frame slot。

验收：

- 纯单元测试完全使用 fake clock；
- Domain 导入不触发第三方硬件/模型/GUI import；
- 阈值边界和非法状态转移已覆盖。

### Phase 3：应用服务和 Fake 集成

实现：

- AnalyzeFrame；
- StartSession / StopSession；
- SetRoute / AdvanceRoute；
- UpdateSettings；
- GetHealth。

验收：

- Fake-only 环境跑通探索、模拟、导航；
- 故障注入覆盖启动失败、读取错误、EOF、分析延迟、停止和断连。

### Phase 4：真实视觉适配器

顺序：

1. OpenCV camera/video；
2. YOLO；
3. Depth Anything；
4. Blind-road segmentation；
5. heuristic fallback；
6. rendering 和性能指标。

每一个 adapter 使用相同 contract tests；录制回放与 V1 的结果差异，任何算法阈值变化单独审查。

### Phase 5：GPS、地图、语音

顺序：

1. NMEA parser；
2. Serial GPS；
3. Windows Location；
4. Demo provider；
5. OSRM / AMap；
6. Console TTS；
7. SAPI / pyttsx3。

普通 CI 使用 fake；硬件和公网仅进入可选集成测试。

### Phase 6：API 和移动端

先将现有 WebSocket 请求/响应做 schema snapshot；API transport 调用共用应用服务。尽可能让当前 mobile-web 不改即可运行。新增协议版本前，必须有双版本兼容测试。

### Phase 7：桌面端

用 presenter 订阅应用事件；Tk callbacks 只转成命令。路线 HTTP、模型加载等操作不得阻塞 UI 主线程。第一版只做探索/模拟/停止/状态，然后逐项恢复路线/GPS/配置功能。

### Phase 8：打包、入口切换、归档 V1

切换前必须通过：

- unit + contract + fake integration；
- 固定视频 replay；
- API schema/WebSocket tests；
- Windows desktop smoke；
- HTTPS 局域网 Mobile Web smoke；
- 模型缺失降级；
- 摄像头/GPS/客户端断开；
- 多客户端隔离；
- 干净环境安装和打包。

通过后才更新正式入口、README 和打包配置；V1 暂存归档，再经单独确认后清理。

## 10. 测试计划

| 测试层 | 重点 | 是否依赖真实硬件 |
| --- | --- | --- |
| Domain unit | 风险、路线、状态机、事件策略、坐标 | 否 |
| Application unit | 用例编排、错误映射、fallback 决策 | 否 |
| Port contract | 输入输出、空结果、异常、close 幂等 | 普通使用 Fake；另有 adapter integration |
| Runtime integration | latest-frame、取消、stop、资源释放、并发 | 否，使用阻塞/延迟 Fake |
| API contract | schema、错误码、消息兼容 | 否 |
| API WebSocket | hello/frame/route/disconnect | 否，FakeVision |
| Replay | 固定帧结果和策略差异 | 可选真实权重 |
| Hardware | 摄像头、串口 GPS、音频设备 | 是，可选 Windows runner |
| Packaging | clean install、模型路径、可执行文件 | Windows 发布前 |

必须专门测试的故障：

- 模型初始化失败；
- 任一单模型推理失败；
- frame source 打开/读取失败；
- GPS fix 过期或精度差；
- provider 超时；
- TTS 输出设备失败；
- stop 与推理竞争；
- WebSocket 中途断开；
- 同一进程多个客户端并行工作。

## 11. 第一批实施范围

第一批只实现架构骨架，不连接真实模型和设备：

~~~text
src/eyeguide/domain/common.py
src/eyeguide/domain/session.py
src/eyeguide/domain/events.py
src/eyeguide/ports/clock.py
src/eyeguide/ports/frame_source.py
src/eyeguide/ports/vision.py
src/eyeguide/ports/speech.py
src/eyeguide/runtime/latest_frame_slot.py
src/eyeguide/runtime/session_manager.py
src/eyeguide/application/session_service.py
src/eyeguide/entrypoints/api/app.py
tests/unit/domain/test_session_state.py
tests/unit/runtime/test_latest_frame_slot.py
tests/integration/test_fake_session.py
~~~

第一批不做：YOLO、Depth、PaddleSeg、真实 GPS、真实地图、完整桌面 UI、mobile-web 重写、旧代码删除。

## 12. 待确认 ADR

在 Phase 1 开始前建议确认：

1. V2 是否以 Windows 为唯一正式支持平台，还是需保留 Linux/macOS API 环境。
2. 模型是 per-session 实例还是进程共享，以及推理并发上限。
3. 多个移动客户端同时连接时，是否允许各自维护路线和浏览器播报。
4. 摄像头异常是否自动重连；本地视频 EOF 是结束还是循环。
5. stale frame 的显示和播报是否采用一套统一过期策略。
6. 过期或精度不足 GPS 是否禁止所有自动路线推进。
7. Demo fallback 的 UI/API 标记和默认开关。
8. API Key 保存于用户配置、环境变量还是只允许单次请求提交。

## 13. 现有代码证据

- 桌面入口和 GUI 装配：app.py:1-8；eyeguide/main.py:7-16；eyeguide/ui/desktop.py:21-32。
- 本地会话创建与处理循环：eyeguide/app/session.py:28-73、198-291。
- API 每连接创建独立会话：eyeguide/api/server.py:64-69；eyeguide/api/session.py:81-110。
- API 进程级 Speech/Navigation 资源：eyeguide/api/lifecycle.py:16-65。
- SceneAnalyzer 多职责边界：eyeguide/services/vision/analyzer.py:52-155。
- 风险筛选、优先级和盲道过滤：eyeguide/services/vision/analyzer.py:312-387。
- 移动端单槽帧覆盖与结果：eyeguide/api/session.py:262-337、541-645。
- API 协议类型：eyeguide/api/protocol.py:15-137。
- 旧事件字符串结构：eyeguide/core/events.py:13-26。
- Python 和依赖配置：pyproject.toml:5-135。
- 未跟踪模型权重：Git 状态显示 weights/yolo26n.pt；本次未读取或改动该文件。
