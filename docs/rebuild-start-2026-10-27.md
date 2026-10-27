# EyeGuide V2 重建起点

日期：2026-10-27（Asia/Shanghai）。本轮完成 M0 清理准备，尚未实现 V2 运行骨架。

## V1 基线与 Git

- 远程：`https://github.com/Lii-sir/Eye-Guide.git`。
- M0 清理时分支：`phone-cloud`。
- 基线提交：`4c3d9712f3d0a4b0f722d64c61f872bb3679acbb`。
- 开始时没有已跟踪源码修改，仅 `weights/yolo26n.pt` 未跟踪。
- M0 清理时 `.git` 只读，创建 `rebuild/v2-foundation` 分支失败；变更留在工作区等待续接。

续接时已创建本地 `archive/v1-2026-10-27` 引用，指向上述基线提交，并从该提交创建 `rebuild/v2-foundation` 分支。清理变更在 V2 分支审阅提交，暂不推送。

## 已清理与调整

| 内容 | 处理 |
| --- | --- |
| 根目录 `build/`、`dist/` | 删除旧构建产物、安装包；共 5,868 个文件、1,434,175,623 字节（约 1.43 GB） |
| `packaging/dist/` | 删除旧打包输出目录 |
| 根目录、源码、脚本与打包目录内 `__pycache__/` | 删除可重新生成的缓存 |
| `.gitignore` | 解除整个 `docs/`、`packaging/` 的忽略，改为忽略打包输出；新增权重、开发缓存和环境文件规则 |
| `requirements.txt` | 从 UTF-16 转为 UTF-8；绝对本地安装路径改为 `-e .`，其余依赖条目保持不变 |
| `scripts/clean-generated.ps1` | 增加固定范围的清理命令，支持 `-WhatIf`，删除前校验绝对路径和目录链接 |

打包脚本和架构文档是已有本地文件，本轮解除忽略后才出现在 Git 未跟踪列表。它们需要审阅和提交，尚未被 Git 保存，也不代表 V1 打包脚本已经适配 V2。

## 保留内容

- `eyeguide/`、`app.py`、`mobile-web/`：仍为 V1 正式入口，作为行为、协议和算法对照。
- `models/`、`weights/`、根目录 `yolo11n.pt`、`datasets/`、`training/`、`assets/`：模型、配置、数据与素材。
- `.venv/`、`.uv-cache/`、`eyeguide.egg-info/`：开发环境与安装元数据。
- `.tmp/`：包含运行依赖的 PaddleSeg 源码、开发证书等，不能作为普通缓存整体删除。
- `pyproject.toml`、`uv.lock`：保留 V1 的依赖基线，后续通过干净安装验证 V2 分组。

`weights/yolo26n.pt` 的 SHA-256：

```text
9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef
```

本轮没有创建额外的权重备份；忽略规则不能代替备份。

## 验证范围

清理脚本先执行 `-WhatIf` 核对完整目标，再实际删除。核对前后业务源码和资产的文件数量、大小与修改时间摘要，并对未跟踪 YOLO 权重计算内容 SHA-256。没有调用摄像头、GPU、地图或语音，也没有重新安装依赖。

## 后续实施

以 [工程落地规划](engineering-delivery-plan-2026-10-23.md) 为准，配合 [从零重写架构](greenfield-rewrite-architecture-2026-10-12.md)。

1. 在可写 Git 环境保留 V1 基线引用、创建 V2 分支，并审阅提交本次清理。
2. 固化 V1 WebSocket 协议样例与关键行为。
3. 建立 `src/eyeguide/` 的领域、端口、应用、运行时与入口，解决新旧同名包的安装隔离；先跑通 Fake 帧处理、两会话隔离和停止。
4. 加入 SQLite 偏好保存与会话摘要，再迁移真实模型、GPS、地图、语音和两端入口。
5. 各模块迁移验收后，逐项删除已被替代的 V1 实现。

本次续接已运行：

```powershell
 git branch archive/v1-2026-10-27 4c3d971
 git switch -c rebuild/v2-foundation
 git status --short
```

本地文档、打包脚本需显式选择文件暂存并检查差异后提交。不要以权重已被忽略为由省略暂存审阅。本次没有验证远程同步状态。
