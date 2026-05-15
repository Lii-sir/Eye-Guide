from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from eyeguide.app.session import SessionController
from eyeguide.core.config import AppConfig
from eyeguide.core.events import AppEvent
from eyeguide.domain.models import Mode
from eyeguide.services.location import LocationError
from eyeguide.services.navigation import NavigationError, NavigationService
from eyeguide.services.navigation.formatters import format_gps_origin


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
        self.gps_port_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="等待启动")
        self.gps_status_var = tk.StringVar(value="GPS 未连接")
        self.gps_fix_var = tk.StringVar(value="暂无定位坐标")
        self.hazard_var = tk.StringVar(value="暂无实时信息")
        self.route_var = tk.StringVar(value="暂无导航指令")

        self.root.title("EyeGuide 盲人辅助指路原型")
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
            text="电脑端原型：导航、摄像头感知、语音播报分层实现，便于后续接入真实定位和更强模型。",
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

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_mode_panel(left)
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
            "- 路线导航为步行导航，会根据实时位置自动推进\n"
            "- 若未安装 YOLO，可先使用 OpenCV 启发式检测验证流程\n"
        )

    def _build_mode_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="模式启动", style="Card.TLabelframe", padding=12)
        frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(frame, text="启动自由探索", command=self.start_explore).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="启动路线导航", command=self.start_navigation).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="启动模拟测试", command=self.start_simulation).pack(fill=tk.X, pady=4)
        ttk.Button(frame, text="停止当前会话", command=self.stop_session).pack(fill=tk.X, pady=(12, 4))

    def _build_navigation_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="路线导航设置", style="Card.TLabelframe", padding=12)
        frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(frame, text="起点").pack(anchor=tk.W)
        ttk.Entry(frame, textvariable=self.origin_var).pack(fill=tk.X, pady=(2, 8))
        ttk.Label(frame, text="终点").pack(anchor=tk.W)
        ttk.Entry(frame, textvariable=self.destination_var).pack(fill=tk.X, pady=(2, 8))
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
        ttk.Button(frame, text="用当前 GPS 位置填入起点", command=self.use_current_gps_as_origin).pack(
            fill=tk.X, pady=4
        )

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

        self._append_log("正在获取路线，请稍候...")
        self.status_var.set("正在生成路线")
        self.root.update_idletasks()
        self._append_log("当前导航模式：步行导航")

        try:
            route_plan = self.navigation_service.build_route(origin, destination, prefer_online=True)
        except NavigationError as exc:
            self._append_log(f"[导航失败] 起点={origin or '当前位置'}; 终点={destination}")
            self._append_log(f"[导航失败详情] {exc}")
            self.status_var.set("路线生成失败")
            self.route_var.set("暂无导航指令")
            messagebox.showerror("路线获取失败", f"{exc}\n\n未生成真实路线，已停止启动导航。")
            return

        self.route_var.set(route_plan.steps[0].instruction if route_plan.steps else "暂无导航指令")
        self._start_session(Mode.NAVIGATION, self.config.vision.default_camera_index, route_plan)

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
                "GPS 串口可能已经连接，但还没有拿到有效坐标。\n\n"
                "请到室外或靠近窗边等待一会儿，看到“GPS 已定位”后再写入起点。",
            )
            return
        self.origin_var.set(format_gps_origin(fix))
        self._append_log("已将当前 GPS 坐标填入起点。")

    def _start_session(self, mode: Mode, source, route_plan) -> None:
        if self.controller.is_running:
            messagebox.showinfo("会话正在运行", "请先停止当前会话。")
            return

        if mode != Mode.NAVIGATION:
            self.route_var.set("暂无导航指令")

        try:
            self.controller.start(mode, source, route_plan=route_plan)
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

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
