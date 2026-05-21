from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from eyeguide.app.session import SessionController
from eyeguide.core.config import AppConfig
from eyeguide.core.events import AppEvent
from eyeguide.domain.models import Mode, RoutePlan
from eyeguide.services.location import LocationError
from eyeguide.services.navigation import NavigationError, NavigationService
from eyeguide.services.navigation.formatters import format_gps_origin
from eyeguide.services.navigation.models import CandidateSearchResult, LocationCandidate


class EyeGuideGUI:
    def __init__(self, root: tk.Tk, config: AppConfig | None = None) -> None:
        self.root = root
        self.config = config or AppConfig()
        self.navigation_service = NavigationService()
        self.event_queue: Queue[AppEvent] = Queue()
        self.controller = SessionController(self.event_queue, self.config)

        self.video_path = tk.StringVar()
        self.origin_var = tk.StringVar(value="人民广场, 上海")
        self.destination_var = tk.StringVar(value="")
        amap_api_key = os.getenv("AMAP_WEB_API_KEY") or os.getenv("AMAP_KEY") or ""
        self.navigation_provider_var = tk.StringVar(value="osm")
        self.amap_api_key_var = tk.StringVar(value=amap_api_key)
        self.gps_port_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="等待启动")
        self.gps_status_var = tk.StringVar(value="GPS 未连接")
        self.gps_fix_var = tk.StringVar(value="暂无定位坐标")
        self.hazard_var = tk.StringVar(value="暂无实时信息")
        self.route_var = tk.StringVar(value="暂无导航指令")
        self.object_speech_dedup_mode_var = tk.StringVar(
            value=self._dedup_mode_label_from_value(self.config.vision.object_speech_dedup_mode)
        )

        self.root.title("EyeGuide 视障辅助导航原型")
        self.root.geometry("920x700")
        self.root.minsize(840, 620)

        self._build_layout()
        self.root.after(self.config.ui_poll_interval_ms, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"))

        container = ttk.Frame(self.root, padding=18)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="EyeGuide", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header,
            text="桌面端原型：导航、摄像头感知、语音播报分层实现，便于后续接入真实定位和更强模型。",
        ).pack(anchor=tk.W, pady=(4, 0))

        summary = ttk.LabelFrame(container, text="实时状态", style="Card.TLabelframe")
        summary.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(summary, textvariable=self.status_var).pack(anchor=tk.W, padx=12, pady=(10, 4))
        ttk.Label(summary, textvariable=self.gps_status_var, foreground="#1f5f3d").pack(
            anchor=tk.W, padx=12, pady=4
        )
        ttk.Label(summary, textvariable=self.gps_fix_var, foreground="#555555").pack(
            anchor=tk.W, padx=12, pady=4
        )
        ttk.Label(summary, textvariable=self.hazard_var, foreground="#9a1f1f").pack(
            anchor=tk.W, padx=12, pady=4
        )
        ttk.Label(summary, textvariable=self.route_var, foreground="#0f4c81").pack(
            anchor=tk.W, padx=12, pady=(4, 10)
        )

        body = ttk.Frame(container)
        body.pack(fill=tk.BOTH, expand=True)

        left_shell = ttk.Frame(body)
        left_shell.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left_canvas = tk.Canvas(left_shell, highlightthickness=0)
        left_scrollbar = ttk.Scrollbar(left_shell, orient=tk.VERTICAL, command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scrollbar.set)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left = ttk.Frame(left_canvas)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _sync_left_scroll(_event=None) -> None:
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))

        def _resize_left_window(event) -> None:
            left_canvas.itemconfigure(left_window, width=event.width)

        left.bind("<Configure>", _sync_left_scroll)
        left_canvas.bind("<Configure>", _resize_left_window)

        self._build_mode_panel(left)
        self._build_vision_panel(left)
        self._build_gps_panel(left)
        self._build_navigation_panel(left)
        self._build_simulation_panel(left)

        right = ttk.LabelFrame(body, text="运行日志", style="Card.TLabelframe", padding=12)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(12, 0))
        self.log_box = tk.Text(right, wrap=tk.WORD, height=25, font=("Consolas", 10))
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self._append_log(
            "程序说明:\n"
            "- 摄像头窗口中按 Q 退出\n"
            "- 摄像头窗口中按 N 切换到下一条导航\n"
            "- 摄像头窗口中按 R 重复当前导航\n"
            "- 可先连接 USB/NMEA GPS，再将当前位置作为导航起点\n"
            "- 如果串口 GPS 暂时没有定位，程序会尝试调用 Windows 定位服务\n"
            "- 路线导航会根据实时位置自动推进\n"
            "- 若未安装 YOLO，可先使用 OpenCV 启发式检测验证流程\n"
        )

    def _build_mode_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="模式启动", style="Card.TLabelframe", padding=12)
        frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(frame, text="启动自由探索", command=self.start_explore).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="启动路线导航", command=self.start_navigation).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="启动模拟测试", command=self.start_simulation).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="测试语音播报", command=self.test_speech).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="停止当前会话", command=self.stop_session).pack(fill=tk.X, pady=(12, 4))

    def _build_vision_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="感知设置", style="Card.TLabelframe", padding=12)
        frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(frame, text="普通物体播报去重").pack(anchor=tk.W)
        combo = ttk.Combobox(
            frame,
            textvariable=self.object_speech_dedup_mode_var,
            values=["详细播报", "简单播报"],
            state="readonly",
        )
        combo.pack(fill=tk.X, pady=(4, 4))
        combo.bind("<<ComboboxSelected>>", self._on_object_speech_dedup_mode_changed)
        ttk.Label(
            frame,
            text="详细播报：按物体类别、方向、距离挡位去重\n简单播报：按物体类别、距离挡位去重",
            foreground="#555555",
        ).pack(anchor=tk.W)

    def _build_navigation_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="路线导航设置", style="Card.TLabelframe", padding=12)
        frame.pack(fill=tk.X, pady=(0, 12))
        form = ttk.Frame(frame)
        form.pack(fill=tk.X)
        form.columnconfigure(0, weight=0)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="导航提供方").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Combobox(form, textvariable=self.navigation_provider_var, values=["osm", "amap"], state="readonly").grid(
            row=0, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Label(form, text="高德 Web 服务 Key").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Entry(form, textvariable=self.amap_api_key_var, show="*").grid(row=1, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(form, text="起点").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Entry(form, textvariable=self.origin_var).grid(row=2, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(form, text="终点").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Entry(form, textvariable=self.destination_var).grid(row=3, column=1, sticky="ew", pady=(0, 8))

        ttk.Button(frame, text="下一条导航指令", command=self.controller.next_route_step).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="重复当前导航指令", command=self.controller.repeat_route_step).pack(fill=tk.X, pady=4)

    def _build_gps_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="GPS 定位设置", style="Card.TLabelframe", padding=12)
        frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(frame, text="串口").pack(anchor=tk.W)
        ports = self.controller.gps_service.list_available_ports()
        if ports and not self.gps_port_var.get():
            self.gps_port_var.set(ports[0])
        self.gps_port_combo = ttk.Combobox(frame, textvariable=self.gps_port_var, values=ports, state="normal")
        self.gps_port_combo.pack(fill=tk.X, pady=(2, 8))
        ttk.Button(frame, text="刷新串口列表", command=self.refresh_gps_ports).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="连接 GPS", command=self.start_gps).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="断开 GPS", command=self.stop_gps).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="用当前 GPS 位置填入起点", command=self.use_current_gps_as_origin).pack(fill=tk.X, pady=4)

    def _build_simulation_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="模拟测试设置", style="Card.TLabelframe", padding=12)
        frame.pack(fill=tk.X)
        ttk.Entry(frame, textvariable=self.video_path).pack(fill=tk.X, pady=(0, 8))
        ttk.Button(frame, text="选择视频文件", command=self.pick_video).pack(fill=tk.X)

    def pick_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择测试视频",
            filetypes=[("Video Files", "*.mp4 *.avi *.mov *.mkv"), ("All Files", "*.*")],
        )
        if path:
            self.video_path.set(path)

    def start_explore(self) -> None:
        self._start_session(Mode.EXPLORE, self.config.vision.default_camera_index, None)

    def start_navigation(self) -> None:
        origin = self.origin_var.get().strip()
        destination = self.destination_var.get().strip()
        if not destination:
            messagebox.showwarning("缺少终点", "请输入终点后再启动路线导航。")
            return

        self._append_log("正在获取终点候选，请稍候...")
        self.status_var.set("正在匹配终点")
        self.root.update_idletasks()

        provider = self.navigation_provider_var.get()
        amap_api_key = self.amap_api_key_var.get().strip() or None
        search_result = self._search_destination_candidates(origin, destination, provider, amap_api_key)
        self._log_destination_candidates(search_result)

        selected_candidate = self._select_destination_candidate(search_result.candidates)
        if selected_candidate is None and search_result.candidates:
            self.status_var.set("已取消导航")
            self._append_log("已取消终点选择。")
            return

        if selected_candidate is not None:
            self.destination_var.set(selected_candidate.display_name)
            self._append_log(f"已选择终点候选：{selected_candidate.display_name}")

        route_destination = selected_candidate.route_value if selected_candidate is not None else destination

        self._append_log("正在获取路线，请稍候...")
        self.status_var.set("正在生成路线")
        self.root.update_idletasks()
        self._append_log("当前导航模式：步行导航")
        self._append_log(f"当前导航提供方：{provider}")

        try:
            route_plan = self.navigation_service.build_route(
                origin,
                route_destination,
                prefer_online=True,
                provider=provider,
                amap_api_key=amap_api_key,
            )
        except NavigationError as exc:
            self._append_log(f"[导航失败] 起点={origin or '当前位置'}; 终点={destination}")
            self._append_log(f"[导航失败详情] {exc}")
            self.status_var.set("路线生成失败")
            self.route_var.set("暂无导航指令")
            messagebox.showerror("路线获取失败", f"{exc}\n\n未生成真实路线，已停止启动导航。")
            return

        self._log_route_provider_fallback(provider, route_plan.source)

        if selected_candidate is not None:
            route_plan.destination = selected_candidate.display_name
            route_plan.resolved_destination_address = selected_candidate.display_name

        self.route_var.set(route_plan.steps[0].instruction if route_plan.steps else "暂无导航指令")
        if route_plan.resolved_origin_address:
            self._append_log(f"起点匹配：{route_plan.resolved_origin_address}")
        if route_plan.resolved_destination_address:
            self._append_log(f"终点匹配：{route_plan.resolved_destination_address}")
        self._start_session(Mode.NAVIGATION, self.config.vision.default_camera_index, route_plan)

    def _search_destination_candidates(
        self,
        origin: str,
        destination: str,
        provider: str,
        amap_api_key: str | None,
    ) -> CandidateSearchResult:
        try:
            return self.navigation_service.search_candidates_debug(
                origin,
                destination,
                provider=provider,
                amap_api_key=amap_api_key,
                limit=5,
            )
        except NavigationError as exc:
            self._append_log(f"[候选搜索失败] {exc}")
            return CandidateSearchResult(candidates=[], debug_lines=[], provider_used=provider)

    def _log_destination_candidates(self, search_result: CandidateSearchResult) -> None:
        for line in search_result.debug_lines:
            self._append_log(line)
        if not search_result.candidates:
            return
        self._append_log("已找到以下终点候选：")
        for index, candidate in enumerate(search_result.candidates, start=1):
            self._append_log(f"{index}. {candidate.display_name}")

    def _select_destination_candidate(self, candidates: list[LocationCandidate]) -> LocationCandidate | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        dialog = tk.Toplevel(self.root)
        dialog.title("选择终点候选")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("760x360")
        dialog.minsize(640, 300)

        ttk.Label(
            dialog,
            text="检测到多个终点候选，请选择最符合你的目的地：",
            padding=(12, 12, 12, 6),
        ).pack(anchor=tk.W)

        listbox = tk.Listbox(dialog, font=("Microsoft YaHei UI", 10), activestyle="dotbox")
        listbox.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        for candidate in candidates:
            listbox.insert(tk.END, candidate.display_name)
        listbox.selection_set(0)
        listbox.activate(0)

        selected_index = {"value": None}

        def _confirm(_event=None) -> None:
            selection = listbox.curselection()
            if not selection:
                return
            selected_index["value"] = selection[0]
            dialog.destroy()

        def _cancel() -> None:
            dialog.destroy()

        button_bar = ttk.Frame(dialog, padding=(12, 0, 12, 12))
        button_bar.pack(fill=tk.X)
        ttk.Button(button_bar, text="确定", command=_confirm).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(button_bar, text="取消", command=_cancel).pack(side=tk.RIGHT)

        listbox.bind("<Double-Button-1>", _confirm)
        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        self.root.wait_window(dialog)

        index = selected_index["value"]
        if index is None:
            return None
        return candidates[index]

    def start_simulation(self) -> None:
        video_path = self.video_path.get().strip()
        if not video_path:
            messagebox.showwarning("缺少视频", "请先选择测试视频。")
            return
        if not Path(video_path).exists():
            messagebox.showerror("文件不存在", "所选视频文件不存在，请重新选择。")
            return
        self._start_session(Mode.SIMULATION, video_path, None)

    def stop_session(self) -> None:
        if self.controller.is_running:
            self.controller.stop()
        else:
            self.status_var.set("当前没有运行中的会话")

    def test_speech(self) -> None:
        self._append_log("正在测试语音播报...")
        self.controller.preview_speech("这是一条语音测试。如果你能听到这句话，说明播报已经恢复。")

    def refresh_gps_ports(self) -> None:
        ports = self.controller.gps_service.list_available_ports()
        self.gps_port_combo["values"] = ports
        if ports:
            self.gps_port_var.set(ports[0])
            self._append_log(f"已刷新串口列表：{', '.join(ports)}")
        else:
            self._append_log("当前未发现可用串口。")

    def start_gps(self) -> None:
        try:
            self.controller.gps_service.start(self.gps_port_var.get())
        except LocationError as exc:
            messagebox.showerror("GPS 启动失败", str(exc))

    def stop_gps(self) -> None:
        self.controller.gps_service.stop()

    def use_current_gps_as_origin(self) -> None:
        fix = self.controller.gps_service.latest_fix
        if fix is None:
            messagebox.showwarning(
                "暂无定位",
                "GPS 串口可能已经连接，但还没有拿到有效坐标。\n\n请到室外或靠近窗边等待一会儿，再写入起点。",
            )
            return
        self.origin_var.set(format_gps_origin(fix))
        self._append_log("已将当前 GPS 坐标填入起点。")

    def _start_session(self, mode: Mode, source, route_plan: RoutePlan | None) -> None:
        if self.controller.is_running:
            messagebox.showinfo("会话正在运行", "请先停止当前会话。")
            return

        if mode != Mode.NAVIGATION:
            self.route_var.set("暂无导航指令")

        try:
            self.controller.start(mode, source, route_plan=route_plan)
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    def _dedup_mode_label_from_value(self, value: str) -> str:
        return "简单播报" if value.strip().lower() == "simple" else "详细播报"

    def _dedup_mode_value_from_label(self, label: str) -> str:
        return "simple" if label == "简单播报" else "detailed"

    def _on_object_speech_dedup_mode_changed(self, _event=None) -> None:
        mode_value = self._dedup_mode_value_from_label(self.object_speech_dedup_mode_var.get())
        vision_config = replace(self.config.vision, object_speech_dedup_mode=mode_value)
        self.config = replace(self.config, vision=vision_config)
        self.controller._config = self.config

        message = f"普通物体播报去重已切换为：{self.object_speech_dedup_mode_var.get()}"
        if self.controller.is_running:
            message += "，将在下次启动会话时生效"
        self.status_var.set(message)
        self._append_log(message)

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.event_queue.get_nowait()
                if event.kind == "status":
                    self.status_var.set(event.value)
                elif event.kind == "gps_status":
                    self.gps_status_var.set(event.value)
                elif event.kind == "gps_fix":
                    self.gps_fix_var.set(event.value)
                elif event.kind == "hazard":
                    self.hazard_var.set(f"感知播报：{event.value}")
                elif event.kind == "route":
                    self.route_var.set(event.value)
                elif event.kind == "error":
                    self.status_var.set("发生错误")
                    self._append_log(f"[错误] {event.value}")
                    messagebox.showerror("运行错误", event.value)
                else:
                    self._append_log(event.value)
        except Empty:
            pass
        finally:
            self.root.after(self.config.ui_poll_interval_ms, self._poll_events)

    def _log_route_provider_fallback(self, requested_provider: str, actual_source: str) -> None:
        if requested_provider.strip().lower() in {"amap", "gaode", "高德"} and actual_source.strip().lower() in {
            "osm",
            "osrm",
        }:
            self._append_log("[导航回退] 高德导航不可用，已自动回退到 OSM。")

    def _append_log(self, text: str) -> None:
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"{text}\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        self.controller.shutdown()
        self.root.destroy()


def launch_app() -> None:
    root = tk.Tk()
    EyeGuideGUI(root)
    root.mainloop()
