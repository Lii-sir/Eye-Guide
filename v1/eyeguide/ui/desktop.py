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
from eyeguide.core.paths import bundled_path, first_existing_path
from eyeguide.domain.models import Mode, RoutePlan
from eyeguide.services.location import LocationError
from eyeguide.services.navigation import NavigationError, NavigationService
from eyeguide.services.navigation.formatters import format_gps_origin
from eyeguide.services.navigation.models import CandidateSearchResult, LocationCandidate


class EyeGuideGUI:
    def __init__(self, root: tk.Tk, config: AppConfig | None = None) -> None:
        self.root = root
        self.root.title("EyeGuide 视障辅助导航系统原型")
        self.root.geometry("1080x720")
        self.root.minsize(1000, 680)
        self.root.configure(bg="#F9FAFB")

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

        # 优化窗体基本属性
        self.root.title("EyeGuide 视障辅助导航系统原型")
        self.root.geometry("1080x720")  # 稍微加宽，给双栏充足空间
        self.root.minsize(1000, 680)
        self.root.configure(bg="#F9FAFB")  # 现代浅色背景

        self._build_layout()
        self.root.after(self.config.ui_poll_interval_ms, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        # 配置全局现代简约样式
        style = ttk.Style()
        style.theme_use("clam")
        
        # 配色定义
        BG_COLOR = "#F9FAFB"
        CARD_BG = "#FFFFFF"
        PRIMARY = "#1E3A8A"       # 深邃蓝
        PRIMARY_HOVER = "#1D4ED8"
        TEXT_MAIN = "#1F2937"
        BORDER_COLOR = "#E5E7EB"
        TERM_BG = "#1E293B"       # 暗色终端底色

        style.configure(".", background=BG_COLOR, foreground=TEXT_MAIN, font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=BG_COLOR)
        
        # 头部标题样式
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"), foreground=PRIMARY, background=BG_COLOR)
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 10), foreground="#6B7280", background=BG_COLOR)
        
        # 精简无边框卡片样式
        style.configure("Card.TLabelframe", background=CARD_BG, bordercolor=BORDER_COLOR, relief="flat", borderwidth=0)
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"), foreground=PRIMARY, background=CARD_BG)
        
        # 右侧独立终端包裹样式
        style.configure("Terminal.TLabelframe", background=TERM_BG, bordercolor=TERM_BG, relief="flat", borderwidth=0)
        style.configure("Terminal.TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"), foreground="#94A3B8", background=TERM_BG)

        # 输入框和下拉框扁平化外观调整
        style.configure("TEntry", fieldbackground=CARD_BG, bordercolor=BORDER_COLOR, lightcolor=BORDER_COLOR, darkcolor=BORDER_COLOR, relief="flat")
        style.configure("TCombobox", fieldbackground=CARD_BG, bordercolor=BORDER_COLOR, lightcolor=BORDER_COLOR, darkcolor=BORDER_COLOR, arrowcolor="#4B5563", relief="flat")

        # 现代 Notebook 标签页样式优化
        style.configure("TNotebook", background=BG_COLOR, borderwidth=0, relief="flat")
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 10), padding=(14, 6), background="#E5E7EB", foreground=TEXT_MAIN, lightcolor=BG_COLOR, borderwidth=0)
        style.map("TNotebook.Tab", 
                  background=[("selected", PRIMARY), ("active", "#D1D5DB")],
                  foreground=[("selected", "white")])

        # 按钮样式优化
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6), background="#E5E7EB", foreground=TEXT_MAIN, borderwidth=0, relief="flat")
        style.map("TButton", background=[("active", "#D1D5DB")])
        
        style.configure("Action.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 8), background=PRIMARY, foreground="white")
        style.map("Action.TButton", background=[("active", PRIMARY_HOVER)])
        
        style.configure("Stop.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 8), background="#EF4444", foreground="white")
        style.map("Stop.TButton", background=[("active", "#DC2626")])

        # 核心防误触机制：重写 TCombobox 的滚轮绑定，使其静止时不响应滚动切换
        self.root.bind_class("TCombobox", "<MouseWheel>", lambda e: "break")
        self.root.bind_class("TCombobox", "<Button-4>", lambda e: "break")
        self.root.bind_class("TCombobox", "<Button-5>", lambda e: "break")

        # 主包裹容器
        container = ttk.Frame(self.root, padding=24)
        container.pack(fill=tk.BOTH, expand=True)

        # 1. 头部区域 (Header)
        header = ttk.Frame(container)
        header.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(header, text="EyeGuide 控制面板", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(header, text="桌面端视障辅助导航原型 ·感知、定位、播报多模态模块化管理", style="Subtitle.TLabel").pack(anchor=tk.W, pady=(4, 0))

        # 2. 实时状态看板 (Status Grid)
        summary = ttk.LabelFrame(container, text=" 系统实时看板 ", style="Card.TLabelframe", padding=14)
        summary.pack(fill=tk.X, pady=(0, 20))
        
        # 内部框架使卡片背景统一
        inner_summary = tk.Frame(summary, bg=CARD_BG)
        inner_summary.pack(fill=tk.X, expand=True)
        inner_summary.columnconfigure((0, 1, 2), weight=1)

        def create_status_badge(parent, row, col, title, text_var, color):
            f = tk.Frame(parent, bg="#F3F4F6", bd=0, relief="flat")
            f.grid(row=row, column=col, padx=6, pady=6, sticky="ew")
            tk.Label(f, text=title, font=("Microsoft YaHei UI", 9), fg="#6B7280", bg="#F3F4F6").pack(anchor=tk.W, padx=12, pady=(6, 2))
            tk.Label(f, textvariable=text_var, font=("Microsoft YaHei UI", 11, "bold"), fg=color, bg="#F3F4F6", wraplength=260).pack(anchor=tk.W, padx=12, pady=(0, 8))

        create_status_badge(inner_summary, 0, 0, "系统核心状态", self.status_var, PRIMARY)
        create_status_badge(inner_summary, 0, 1, "GPS 模块连接", self.gps_status_var, "#10B981")
        create_status_badge(inner_summary, 0, 2, "当前卫星定位坐标", self.gps_fix_var, "#4B5563")
        create_status_badge(inner_summary, 1, 0, "视觉感知实时播报", self.hazard_var, "#EF4444")
        
        # 导航路径跨列拉伸
        f_route = tk.Frame(inner_summary, bg="#EFF6FF", bd=0, relief="flat")
        f_route.grid(row=1, column=1, columnspan=2, padx=6, pady=6, sticky="ew")
        tk.Label(f_route, text="当前实时导航指令", font=("Microsoft YaHei UI", 9), fg="#1E40AF", bg="#EFF6FF").pack(anchor=tk.W, padx=12, pady=(6, 2))
        tk.Label(f_route, textvariable=self.route_var, font=("Microsoft YaHei UI", 12, "bold"), fg="#1E3A8A", bg="#EFF6FF").pack(anchor=tk.W, padx=12, pady=(0, 8))

        # 3. 核心双栏架构 (Body)
        body = ttk.Frame(container)
        body.pack(fill=tk.BOTH, expand=True)

        # 左栏：配置与控制 (配备平滑自适应滚动的 Canvas 容器)
        left_shell = ttk.Frame(body)
        left_shell.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 16))
        left_shell.columnconfigure(0, weight=1)
        left_shell.rowconfigure(0, weight=1)

        left_canvas = tk.Canvas(left_shell, bg=BG_COLOR, highlightthickness=0, bd=0)
        left_scrollbar = ttk.Scrollbar(left_shell, orient=tk.VERTICAL, command=left_canvas.yview)
        left_scrollbar.grid(row=0, column=1, sticky="ns")
        left_canvas.grid(row=0, column=0, sticky="nsew")
        left_canvas.configure(yscrollcommand=left_scrollbar.set)

        left_content = ttk.Frame(left_canvas)
        left_content.columnconfigure(0, weight=1)
        left_window = left_canvas.create_window((0, 0), window=left_content, anchor="nw")

        def _sync_left_scrollregion(_event=None) -> None:
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))

        def _resize_left_content(event) -> None:
            left_canvas.itemconfigure(left_window, width=event.width)

        def _is_descendant(widget: tk.Misc | None, ancestor: tk.Misc) -> bool:
            while widget is not None:
                if widget == ancestor:
                    return True
                parent_name = widget.winfo_parent()
                if not parent_name:
                    return False
                try:
                    widget = widget.nametowidget(parent_name)
                except KeyError:
                    return False
            return False

        def _on_left_mousewheel(event) -> None:
            hovered_widget = self.root.winfo_containing(event.x_root, event.y_root)
            if hovered_widget is None or not _is_descendant(hovered_widget, left):
                return
            delta = event.delta or 0
            if delta == 0 and getattr(event, "num", None) in (4, 5):
                delta = 120 if event.num == 4 else -120
            left_canvas.yview_scroll(int(-delta / 120), "units")

        left_content.bind("<Configure>", _sync_left_scrollregion)
        left_canvas.bind("<Configure>", _resize_left_content)
        self.root.bind_all("<MouseWheel>", _on_left_mousewheel, add="+")
        self.root.bind_all("<Button-4>", _on_left_mousewheel, add="+")
        self.root.bind_all("<Button-5>", _on_left_mousewheel, add="+")
        left = left_content

        # 模块容器的内部填充助手
        def create_module_frame(parent, text):
            f = ttk.LabelFrame(parent, text=text, style="Card.TLabelframe", padding=14)
            return f

        # --- 模式控制模块 ---
        mode_frame = create_module_frame(left, "模式启动控制")
        mode_frame.pack(fill=tk.X, pady=(0, 16))
        
        btn_grid = tk.Frame(mode_frame, bg=CARD_BG)
        btn_grid.pack(fill=tk.X)
        btn_grid.columnconfigure((0, 1), weight=1)
        
        ttk.Button(btn_grid, text="🌍 启动自由探索", style="Action.TButton", command=self.start_explore).grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        ttk.Button(btn_grid, text="🗺️ 启动路线导航", style="Action.TButton", command=self.start_navigation).grid(row=0, column=1, padx=6, pady=6, sticky="ew")
        ttk.Button(btn_grid, text="🧪 启动模拟测试", command=self.start_simulation).grid(row=1, column=0, padx=6, pady=6, sticky="ew")
        ttk.Button(btn_grid, text="🔊 测试语音播报", command=self.test_speech).grid(row=1, column=1, padx=6, pady=6, sticky="ew")
        ttk.Button(mode_frame, text="🛑 停止当前会话", style="Stop.TButton", command=self.stop_session).pack(fill=tk.X, padx=6, pady=(14, 4))

        # --- 统一设置区域 (Notebook) ---
        settings_notebook = ttk.Notebook(left)
        settings_notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        # 标签页1：导航与感知
        tab_nav = ttk.Frame(settings_notebook, padding=14)
        settings_notebook.add(tab_nav, text=" 路线与感知配置 ")

        form = tk.Frame(tab_nav, bg=CARD_BG)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        def add_form_row(target_form, row_idx, label_text, widget):
            tk.Label(target_form, text=label_text, font=("Microsoft YaHei UI", 9, "bold"), fg="#555555", bg=CARD_BG).grid(row=row_idx, column=0, sticky="w", padx=(0, 14), pady=8)
            widget.grid(row=row_idx, column=1, sticky="ew", pady=8)

        combo_provider = ttk.Combobox(form, textvariable=self.navigation_provider_var, values=["osm", "amap"], state="readonly")
        add_form_row(form, 0, "导航提供方", combo_provider)

        entry_key = ttk.Entry(form, textvariable=self.amap_api_key_var, show="*")
        add_form_row(form, 1, "高德 Web Key", entry_key)

        entry_origin = ttk.Entry(form, textvariable=self.origin_var)
        add_form_row(form, 2, "起点位置", entry_origin)

        entry_dest = ttk.Entry(form, textvariable=self.destination_var)
        add_form_row(form, 3, "终点目的地", entry_dest)

        combo_dedup = ttk.Combobox(form, textvariable=self.object_speech_dedup_mode_var, values=["详细播报", "简单播报"], state="readonly")
        combo_dedup.bind("<<ComboboxSelected>>", self._on_object_speech_dedup_mode_changed)
        add_form_row(form, 4, "物体去重模式", combo_dedup)

        nav_actions = tk.Frame(tab_nav, bg=CARD_BG)
        nav_actions.pack(fill=tk.X, pady=(12, 0))
        nav_actions.columnconfigure((0, 1), weight=1)
        ttk.Button(nav_actions, text="⏭️ 下一条指令", command=self.controller.next_route_step).grid(row=0, column=0, padx=4, sticky="ew")
        ttk.Button(nav_actions, text="🔄 重复当前指令", command=self.controller.repeat_route_step).grid(row=0, column=1, padx=4, sticky="ew")

        # 标签页2：硬件 GPS 与模拟
        tab_hardware = ttk.Frame(settings_notebook, padding=14)
        settings_notebook.add(tab_hardware, text=" 定位与模拟硬件 ")
 
        form_hw = tk.Frame(tab_hardware, bg=CARD_BG)
        form_hw.pack(fill=tk.X)
        form_hw.columnconfigure(1, weight=1)

        ports = self.controller.gps_service.list_available_ports()
        if ports and not self.gps_port_var.get():
            self.gps_port_var.set(ports[0])
        self.gps_port_combo = ttk.Combobox(form_hw, textvariable=self.gps_port_var, values=ports, state="normal")
        add_form_row(form_hw, 0, "GPS 串口选择", self.gps_port_combo)

        entry_video = ttk.Entry(form_hw, textvariable=self.video_path)
        btn_pick = ttk.Button(form_hw, text="选择文件...", command=self.pick_video)
        
        tk.Label(form_hw, text="模拟测试视频", font=("Microsoft YaHei UI", 9, "bold"), fg="#555555", bg=CARD_BG).grid(row=1, column=0, sticky="w", padx=(0, 14), pady=8)
        entry_video.grid(row=1, column=1, sticky="ew", pady=8, padx=(0, 4))
        btn_pick.grid(row=1, column=2, sticky="e", pady=8)

        gps_actions = tk.Frame(tab_hardware, bg=CARD_BG)
        gps_actions.pack(fill=tk.X, pady=(14, 0))
        gps_actions.columnconfigure((0, 1), weight=1)
        
        ttk.Button(gps_actions, text="🔄 刷新串口", command=self.refresh_gps_ports).grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        ttk.Button(gps_actions, text="⚡ 连接 GPS", command=self.start_gps).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        ttk.Button(gps_actions, text="🔌 断开 GPS", command=self.stop_gps).grid(row=1, column=0, padx=4, pady=4, sticky="ew")
        ttk.Button(gps_actions, text="📍 坐标设为起点", command=self.use_current_gps_as_origin).grid(row=1, column=1, padx=4, pady=4, sticky="ew")

        # 右栏：核心日志区
        right = ttk.LabelFrame(body, text="", style="Terminal.TLabelframe", padding=0)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 0))
        
        term_header = tk.Frame(right, bg="#0F172A", height=28)
        term_header.pack(fill=tk.X)
        term_header.pack_propagate(False)
        tk.Label(term_header, text="  🖥️ EYEGUIDE CORE TERMINAL LOGS", font=("Consolas", 9, "bold"), fg="#64748B", bg="#0F172A").pack(side=tk.LEFT, padx=6)

        self.log_box = tk.Text(
            right, 
            wrap=tk.WORD, 
            height=25, 
            font=("Consolas", 10), 
            bg=TERM_BG,
            fg="#F1F5F9",      
            insertbackground="white",
            relief="flat",
            padx=12,
            pady=12,
            highlightthickness=0,
            bd=0
        )
        self.log_box.pack(fill=tk.BOTH, expand=True)
        
        self._append_log(
            "[系统准备就绪]\n"
            "快捷键指南 (活动窗口内有效):\n"
            " 💡 [Q] 退出会话 | [N] 推进下一条导航 | [R] 重复语音导航\n\n"
            "环境提示:\n"
            " - 若未检测到真实外部串口 GPS，系统将自动尝试调用 Windows Location Service 兜底。\n"
            " - 若环境缺少 YOLO 相关依赖，可流畅使用内置 OpenCV 启发式算法完成测试流程。"
        )

    # ==========================================
    # 以下原业务接口逻辑完整保留，未作修改
    # ==========================================
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


def _configure_window_icon(root: tk.Tk) -> None:
    icon_png = first_existing_path(
        bundled_path("assets", "icons", "eye-guide-window.png"),
        bundled_path("assets", "icons", "eye-guide.png"),
        bundled_path("eye-guide-window.png"),
        bundled_path("eye-guide.png"),
    )
    if icon_png is not None:
        try:
            icon_image = tk.PhotoImage(file=str(icon_png))
            icon_photo = root.iconphoto(True, icon_image)
            root._eye_guide_icon = icon_image
        except tk.TclError:
            pass

    icon_ico = first_existing_path(
        bundled_path("assets", "icons", "eye-guide-window.ico"),
        bundled_path("assets", "icons", "eye-guide.ico"),
        bundled_path("eye-guide-window.ico"),
        bundled_path("eye-guide.ico"),
    )
    if icon_ico is not None:
        try:
            root.wm_iconbitmap(str(icon_ico))
            root.iconbitmap(str(icon_ico))
        except tk.TclError:
            pass


def launch_app() -> None:
    root = tk.Tk()
    root.withdraw()
    _configure_window_icon(root)
    EyeGuideGUI(root)
    root.deiconify()
    root.mainloop()