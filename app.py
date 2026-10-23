import ctypes
import logging
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from calendar_picker import CalendarPicker
from core import (
    CRON_PRESETS,
    DATA,
    ORIENTATION_LABELS,
    PORTRAIT_RESOLUTION_PRESETS,
    PROXY_LABELS,
    RANKING_LABELS,
    RANKING_START_DATES,
    RESOLUTION_PRESETS,
    SQUARE_RESOLUTION_PRESETS,
    Cancelled,
    desktop_size,
    load_config,
    make_client,
    next_run,
    ranking_params,
    ranking_period_label,
    resolution_size,
    save_config,
    set_wallpaper,
    startup,
    startup_enabled,
)

logger = logging.getLogger(__name__)
FLASHZERI_URL = "https://wallpaper.flashzeri.com"


class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.events = queue.Queue()
        self.busy = False
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pixiv-network"
        )
        self.cancel = threading.Event()
        self.generation = 0
        self.restart_pending = False
        self.quitting = False
        self.tray = None
        self.work_url = self.config.get("last_url", "")
        self.save_after = None
        self.geometry_after = None
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        minimum_width = min(680, screen_width)
        minimum_height = min(760, screen_height)
        default_width = min(1100, max(minimum_width, screen_width - 60))
        default_height = min(800, max(minimum_height, screen_height - 60))
        window_width, window_height = default_width, default_height
        geometry = self.config["window_geometry"]
        if geometry:
            match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
            width, height, x, y = (
                int(match[1]),
                int(match[2]),
                int(match[3]),
                int(match[4]),
            )
            window_width = min(screen_width, max(minimum_width, width))
            window_height = min(screen_height, max(minimum_height, height))
            x = min(screen_width - window_width, max(0, x))
            y = min(screen_height - window_height, max(0, y))
        else:
            x = (screen_width - window_width) // 2
            y = (screen_height - window_height) // 2
        root.title("Pixiv Wallpaper")
        icon_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        with Image.open(icon_root / "assets" / "app-icon.png") as icon:
            self.window_icon = ImageTk.PhotoImage(
                icon.resize((64, 64), Image.Resampling.LANCZOS)
            )
        root.iconphoto(True, self.window_icon)
        root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        root.minsize(min(680, window_width), min(760, window_height))
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.bind("<Configure>", self.geometry_changed, add="+")

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=5, uniform="panels")
        outer.columnconfigure(1, weight=6, uniform="panels")
        outer.rowconfigure(0, weight=1)

        preview_panel = ttk.LabelFrame(outer, text="当前壁纸", padding=12)
        preview_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        settings_panel = ttk.LabelFrame(outer, text="设置", padding=12)
        settings_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        preview_area = ttk.Frame(preview_panel, height=240)
        preview_area.pack(fill="both", expand=True, pady=8)
        preview_area.pack_propagate(False)
        self.preview = ttk.Label(preview_area, anchor="center", text="暂无壁纸预览")
        self.preview.pack(fill="both", expand=True)
        self.caption = tk.StringVar(value=self.config.get("last_title", "尚未设置壁纸"))
        self.caption_label = ttk.Label(preview_panel, textvariable=self.caption)
        self.caption_label.pack(anchor="w", fill="x")
        preview_panel.bind(
            "<Configure>",
            lambda event: self.caption_label.configure(
                wraplength=max(100, event.width - 28)
            ),
        )
        self.cache_summary = tk.StringVar(value=self.cache_text())
        ttk.Label(preview_panel, textvariable=self.cache_summary, wraplength=360).pack(
            anchor="w", pady=(8, 0)
        )
        ttk.Button(
            preview_panel, text="打开当前壁纸路径", command=self.open_current_path
        ).pack(anchor="w", pady=(8, 0))

        settings_panel.columnconfigure(0, weight=1)
        queue_settings = ttk.LabelFrame(settings_panel, text="队列设置", padding=8)
        queue_settings.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        queue_settings.columnconfigure(1, weight=1)
        filter_settings = ttk.LabelFrame(settings_panel, text="筛选设置", padding=8)
        filter_settings.grid(row=1, column=0, sticky="ew", pady=6)
        filter_settings.columnconfigure(1, weight=1)
        schedule_settings = ttk.LabelFrame(settings_panel, text="定时更新", padding=8)
        schedule_settings.grid(row=2, column=0, sticky="ew", pady=6)
        schedule_settings.columnconfigure(1, weight=1)
        network_settings = ttk.LabelFrame(settings_panel, text="网络设置", padding=8)
        network_settings.grid(row=3, column=0, sticky="ew", pady=6)
        network_settings.columnconfigure(1, weight=1)
        startup_settings = ttk.LabelFrame(settings_panel, text="启动设置", padding=8)
        startup_settings.grid(row=4, column=0, sticky="ew", pady=6)
        startup_settings.columnconfigure(1, weight=1)
        self.cron = tk.StringVar(value=self.config.get("cron", "*/30 * * * *"))

        self.calendar_popup = None
        self.ranking_date = tk.StringVar(
            value=self.config["ranking_date"] or "保持最新"
        )
        self.ranking_time_display = tk.StringVar(
            value=ranking_period_label(
                self.config["ranking"], self.config["ranking_date"]
            )
        )
        self.date_label = ttk.Label(queue_settings, text="榜单时间")
        self.date_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.date_entry = ttk.Button(
            queue_settings,
            textvariable=self.ranking_time_display,
            command=self.show_calendar,
            width=30,
        )
        self.date_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.ranking_date.trace_add("write", self.date_selected)

        self.orientation_display = tk.StringVar(
            value=ORIENTATION_LABELS[self.config["orientation"]]
        )
        ttk.Label(filter_settings, text="横/竖屏筛选").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.orientation_box = ttk.Combobox(
            filter_settings,
            textvariable=self.orientation_display,
            values=list(ORIENTATION_LABELS.values()),
            state="readonly",
            width=10,
        )
        self.orientation_box.grid(row=0, column=1, sticky="ew", pady=4)

        group = self.config["orientation"]
        self.resolution_group = group
        self.resolution_key = {
            "any": "1080x1080",
            "landscape": "1920x1080",
            "portrait": "1080x1920",
        }[group]
        presets = self.current_resolution_presets()
        self.resolution_display = tk.StringVar(
            value=presets.get(self.resolution_key, self.resolution_key)
        )
        self.custom_resolution = (
            self.resolution_key if self.resolution_key not in presets else ""
        )
        self.resolution_box = ttk.Combobox(
            filter_settings,
            textvariable=self.resolution_display,
            values=[*presets.values(), "自定义"],
            state="readonly" if self.resolution_key in presets else "normal",
        )
        ttk.Label(filter_settings, text="最低分辨率").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.resolution_box.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(schedule_settings, text="CRON").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        cron_row = ttk.Frame(schedule_settings)
        cron_row.grid(row=0, column=1, sticky="ew", pady=4)
        cron_row.columnconfigure(0, weight=1)
        preset = next(
            (name for name, value in CRON_PRESETS.items() if value == self.cron.get()),
            None,
        )
        self.cron_display = tk.StringVar(value=preset or self.cron.get())
        self.cron_box = ttk.Combobox(
            cron_row,
            textvariable=self.cron_display,
            values=[*CRON_PRESETS, "自定义"],
            state="readonly" if preset else "normal",
        )
        self.cron_box.grid(row=0, column=0, sticky="ew")
        self.cron_box.bind("<<ComboboxSelected>>", self.cron_selected)
        self.cron_display.trace_add("write", self.cron_edited)

        self.autostart = tk.BooleanVar(value=startup_enabled())
        self.saved_autostart = self.autostart.get()
        ttk.Label(startup_settings, text="开机启动").grid(
            row=0, column=0, sticky="w", pady=4
        )
        ttk.Checkbutton(startup_settings, variable=self.autostart).grid(
            row=0, column=1, sticky="w", pady=4
        )

        self.ranking = tk.StringVar(value=RANKING_LABELS[self.config["ranking"]])
        self.ranking_label = ttk.Label(queue_settings, text="排行榜")
        self.ranking_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.ranking_box = ttk.Combobox(
            queue_settings,
            textvariable=self.ranking,
            values=list(RANKING_LABELS.values()),
            state="readonly",
        )
        self.ranking_box.grid(row=0, column=1, sticky="ew", pady=4)

        saved_mirror = self.config["mirror_url"]
        self.mirror_url = tk.StringVar(value=saved_mirror)
        self.mirror_mode = tk.StringVar(
            value=(
                "Pixiv 直连"
                if not saved_mirror
                else "FlashZeri + pixiv.re 中转"
                if saved_mirror == FLASHZERI_URL
                else saved_mirror
            )
        )
        ttk.Label(network_settings, text="镜像").grid(
            row=0, column=0, sticky="w", pady=4
        )
        mirror_row = ttk.Frame(network_settings)
        mirror_row.grid(row=0, column=1, sticky="ew", pady=4)
        mirror_row.columnconfigure(0, weight=1)
        self.mirror_box = ttk.Combobox(
            mirror_row,
            textvariable=self.mirror_mode,
            values=["Pixiv 直连", "FlashZeri + pixiv.re 中转", "自定义"],
            state="readonly",
            width=34,
        )
        self.mirror_box.grid(row=0, column=0, sticky="ew")
        self.mirror_box.bind("<<ComboboxSelected>>", self.mirror_selected)
        ttk.Button(mirror_row, text="连通性测试", command=self.test_connection).grid(
            row=0, column=1, padx=(6, 0)
        )
        self.custom_mirror = saved_mirror if saved_mirror != FLASHZERI_URL else ""
        if saved_mirror and saved_mirror != FLASHZERI_URL:
            self.mirror_box.configure(state="normal")
        self.mirror_mode.trace_add("write", self.mirror_edited)
        self.mirror_url.trace_add("write", self.queue_save)
        self.config_feedback = tk.StringVar(value="")
        self.proxy_mode = tk.StringVar(
            value=PROXY_LABELS.get(self.config.get("proxy_mode", "system"), "系统")
        )
        ttk.Label(network_settings, text="代理").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.proxy_box = ttk.Combobox(
            network_settings,
            textvariable=self.proxy_mode,
            values=list(PROXY_LABELS.values()),
            state="readonly",
            width=12,
        )
        self.proxy_box.grid(row=1, column=1, sticky="ew", pady=4)
        if self.config.get("proxy_mode") == "custom":
            self.proxy_mode.set(self.config.get("proxy_url", ""))
        self.proxy_box.bind("<<ComboboxSelected>>", self.proxy_selected)
        footer = ttk.Frame(outer)
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for column in range(3):
            footer.columnconfigure(column, weight=1)
        buttons = [
            ("队列下一张", self.update),
            ("当前作品来源", self.open_source),
            ("退出", self.quit),
        ]
        for index, (label, action) in enumerate(buttons):
            ttk.Button(footer, text=label, command=action).grid(
                row=0, column=index, sticky="ew", padx=3, pady=3
            )
        self.progress = ttk.Progressbar(footer, mode="determinate", value=0)
        self.progress.grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=3, pady=(6, 3)
        )
        self.status = tk.StringVar(value="就绪")
        self.schedule = tk.StringVar()
        status_label = ttk.Label(footer, textvariable=self.status)
        status_label.grid(row=3, column=0, columnspan=3, sticky="ew", padx=3)
        footer.bind(
            "<Configure>",
            lambda event: status_label.configure(wraplength=max(100, event.width - 8)),
        )
        ttk.Label(footer, textvariable=self.schedule).grid(
            row=4, column=0, columnspan=3, sticky="w", padx=3
        )

        self.orientation_display.trace_add("write", self.orientation_selected)
        self.proxy_mode.trace_add("write", self.queue_save)
        self.ranking.trace_add("write", self.ranking_changed)
        for variable in (self.cron, self.autostart):
            variable.trace_add("write", self.queue_preferences_save)
        self.resolution_box.bind("<<ComboboxSelected>>", self.resolution_selected)
        self.resolution_display.trace_add("write", self.resolution_edited)
        self.update_proxy_state()
        self.reset_schedule()
        self.show_preview(self.config.get("last_path"))
        self.apply_startup_wallpaper()
        root.after(250, self.poll)
        if sys.platform == "win32":
            threading.Thread(target=self.run_tray, daemon=True).start()
        if "--hidden" in sys.argv:
            root.after(1000, self.hide)

    def current_resolution_presets(self):
        if self.resolution_group == "portrait":
            return PORTRAIT_RESOLUTION_PRESETS
        if self.resolution_group == "any":
            return SQUARE_RESOLUTION_PRESETS
        return RESOLUTION_PRESETS

    def selected_orientation(self):
        return next(
            key
            for key, label in ORIENTATION_LABELS.items()
            if label == self.orientation_display.get()
        )

    def orientation_selected(self, *args):
        orientation = self.selected_orientation()
        group = orientation
        self.resolution_group = group
        self.resolution_key = {
            "any": "1080x1080",
            "landscape": "1920x1080",
            "portrait": "1080x1920",
        }[group]
        presets = self.current_resolution_presets()
        self.custom_resolution = ""
        self.resolution_display.set(
            presets.get(self.resolution_key, self.resolution_key)
        )
        self.resolution_box.configure(
            values=[*presets.values(), "自定义"],
            state="readonly" if self.resolution_key in presets else "normal",
        )
        self.queue_save()

    def resolution_selected(self, event=None):
        if self.resolution_display.get() == "自定义":
            self.resolution_box.configure(state="normal")
            self.resolution_display.set(self.custom_resolution)
            self.resolution_box.focus_set()
            self.resolution_box.selection_range(0, "end")
        else:
            self.resolution_box.configure(state="readonly")

    def resolution_edited(self, *args):
        value = self.resolution_display.get()
        presets = self.current_resolution_presets()
        if value not in presets.values() and value != "自定义":
            self.custom_resolution = value
            self.resolution_key = value
        elif value in presets.values():
            self.resolution_key = next(
                key for key, label in presets.items() if label == value
            )
        self.queue_save()

    def geometry_changed(self, event):
        if event.widget != self.root or self.root.state() != "normal":
            return
        if self.geometry_after:
            self.root.after_cancel(self.geometry_after)
        self.geometry_after = self.root.after(500, self.save_window_geometry)

    def save_window_geometry(self):
        self.geometry_after = None
        if self.root.state() != "normal":
            return
        geometry = self.root.geometry()
        if geometry != self.config.get("window_geometry"):
            self.config["window_geometry"] = geometry
            try:
                save_config(self.config)
            except OSError:
                logger.exception("Could not persist window geometry")

    def selected_ranking(self):
        return next(
            key for key, label in RANKING_LABELS.items() if label == self.ranking.get()
        )

    def date_selected(self, *args):
        value = self.ranking_date.get()
        self.ranking_time_display.set(
            ranking_period_label(
                self.selected_ranking(), "" if value == "保持最新" else value
            )
        )
        self.queue_save()

    def ranking_changed(self, *args):
        value = self.ranking_date.get()
        if value != "保持最新" and value < RANKING_START_DATES[self.selected_ranking()]:
            self.ranking_date.set("保持最新")
        else:
            self.date_selected()

    def show_calendar(self):
        if (
            self.calendar_popup is not None
            and self.calendar_popup.window.winfo_exists()
        ):
            self.calendar_popup.window.lift()
            return
        value = self.ranking_date.get()
        self.calendar_popup = CalendarPicker(
            self.root,
            "" if value == "保持最新" else value,
            lambda date: self.ranking_date.set(date or "保持最新"),
            self.date_entry,
            RANKING_START_DATES[self.selected_ranking()],
        )
        self.refresh_calendar(self.calendar_popup)

    def refresh_calendar(self, popup):
        mode = self.selected_ranking()
        try:
            client = self.make_client()
        except ValueError as error:
            popup.publication.set("无法读取发布日期，请先修正网络设置。")
            self.status.set("网络设置未应用：" + str(error))
            return
        client.session.close()
        config = {
            "mirror_url": self.mirror_url.get().strip(),
            "proxy_mode": self.selected_proxy(),
            "proxy_url": self.proxy_mode.get(),
        }

        def worker(emit, cancel):
            client = None
            try:
                client = make_client(config)
                client.cancel = cancel
                listing = client.json("ranking.php", **ranking_params(mode), p=1)
                published = str(listing.get("date", ""))
                ending = datetime.strptime(published, "%Y%m%d").strftime("%Y-%m-%d")
                if ranking_params(mode, ending)["date"] != published:
                    raise ValueError("Pixiv 返回的发布日期无效")
                period = str(
                    listing.get("date_range_text") or ranking_period_label(mode, ending)
                )
                emit("calendar_publication", (popup, ending, period))
            except Cancelled:
                return
            except Exception:
                logger.exception("Ranking publication lookup failed")
                emit(
                    "calendar_error",
                    (popup, "发布日期查询失败，请检查网络连接或稍后重试。"),
                )
            finally:
                if client is not None:
                    client.session.close()

        self.submit_network(worker)

    def apply_startup_wallpaper(self):
        if sys.platform != "win32" or not self.config.get("last_path"):
            return
        try:
            last_path = Path(self.config["last_path"])
            if not last_path.is_file():
                raise FileNotFoundError("当前壁纸缓存已不存在，请点击“队列下一张”")
            set_wallpaper(last_path)
            self.status.set("已恢复当前壁纸")
        except Exception as error:
            logger.exception("Could not apply last wallpaper on startup")
            self.status.set("当前壁纸恢复失败：" + str(error))

    def submit_network(self, worker):
        if self.quitting:
            return
        generation = self.generation
        cancel = self.cancel

        def emit(kind, payload):
            if not cancel.is_set():
                self.events.put((kind, payload, generation))

        def run():
            if not cancel.is_set():
                worker(emit, cancel)

        # Only one active worker; cancelled queued jobs are discarded on edit.
        self.network_future = self.executor.submit(run)

    def cron_selected(self, event=None):
        value = self.cron_display.get()
        if value == "自定义":
            self.cron_box.configure(state="normal")
            self.cron_display.set(self.cron.get())
            self.cron_box.focus_set()
            self.cron_box.selection_range(0, "end")
        else:
            self.cron_box.configure(state="readonly")
            self.cron.set(CRON_PRESETS[value])

    def cron_edited(self, *args):
        value = self.cron_display.get()
        if value != "自定义":
            self.cron.set(CRON_PRESETS.get(value, value))

    def mirror_selected(self, event=None):
        if self.mirror_mode.get() == "自定义":
            self.mirror_box.configure(state="normal")
            self.mirror_mode.set(self.custom_mirror)
            self.mirror_box.focus_set()
            self.mirror_box.selection_range(0, "end")
        else:
            self.mirror_box.configure(state="readonly")

    def mirror_edited(self, *args):
        value = self.mirror_mode.get()
        if value == "自定义":
            self.mirror_url.set("")
        elif value in ("Pixiv 直连", "FlashZeri + pixiv.re 中转"):
            self.mirror_url.set(
                FLASHZERI_URL if value == "FlashZeri + pixiv.re 中转" else ""
            )
        else:
            self.custom_mirror = value
            self.mirror_url.set(value)

    def test_connection(self):
        if (
            self.mirror_mode.get() not in ("Pixiv 直连", "FlashZeri + pixiv.re 中转")
            and not self.mirror_url.get().strip()
        ):
            self.status.set("连通性测试失败：请输入自定义镜像地址")
            return
        try:
            client = self.make_client()
        except Exception as error:
            self.status.set("连通性测试失败：" + str(error))
            return
        config = {
            "mirror_url": client.mirror_url,
            "proxy_mode": self.selected_proxy(),
            "proxy_url": self.proxy_mode.get(),
        }
        client.session.close()
        mode = self.selected_ranking()
        self.status.set("正在测试排行榜 API 连通性…")

        def worker(emit, cancel):
            client = None
            try:
                client = make_client(config)
                client.cancel = cancel
                listing = client.json("ranking.php", **ranking_params(mode), p=1)
                contents = listing.get("contents")
                published = str(listing.get("date", ""))
                if (
                    not isinstance(contents, list)
                    or not contents
                    or not all(isinstance(item, dict) for item in contents)
                ):
                    raise ValueError("排行榜响应缺少有效作品列表")
                ending = datetime.strptime(published, "%Y%m%d").strftime("%Y-%m-%d")
                emit(
                    "status",
                    "排行榜 API 连通正常，榜单日期：" + ending + "（未测试图片下载）",
                )
            except Cancelled:
                pass
            except Exception as error:
                logger.warning("Connection test failed: %s", type(error).__name__)
                emit("status", "连通性测试失败：" + str(error))
            finally:
                if client is not None:
                    client.session.close()

        self.submit_network(worker)

    def selected_proxy(self):
        return next(
            (
                key
                for key, label in PROXY_LABELS.items()
                if label == self.proxy_mode.get()
            ),
            "custom",
        )

    def proxy_selected(self, event=None):
        if self.proxy_mode.get() == PROXY_LABELS["custom"]:
            self.proxy_box.configure(state="normal")
            self.proxy_mode.set(self.config.get("proxy_url", ""))
            self.proxy_box.focus_set()
            self.proxy_box.selection_range(0, "end")
        else:
            self.proxy_box.configure(state="readonly")

    def update_proxy_state(self):
        self.proxy_box.configure(
            state="normal" if self.selected_proxy() == "custom" else "readonly"
        )

    def queue_preferences_save(self, *args):
        if self.quitting:
            return
        if not self.busy:
            self.status.set("正在应用设置…")
        if self.save_after:
            self.root.after_cancel(self.save_after)
        self.save_after = self.root.after(800, self.autosave)

    def queue_save(self, *args):
        if self.quitting:
            return
        self.restart_pending = self.restart_pending or self.busy
        if (
            self.calendar_popup is not None
            and self.calendar_popup.window.winfo_exists()
        ):
            self.calendar_popup.close()
        self.cancel.set()
        if hasattr(self, "network_future"):
            self.network_future.cancel()
        self.cancel = threading.Event()
        self.generation += 1
        self.busy = False
        self.set_activity(False)
        if self.save_after:
            self.root.after_cancel(self.save_after)
        self.config_feedback.set("设置尚未应用")

        self.status.set("正在应用设置…")
        self.save_after = self.root.after(800, self.autosave)

    def autosave(self):
        if self.save_after:
            self.root.after_cancel(self.save_after)
        self.save_after = None
        if self.save(show_error=False) and self.restart_pending:
            self.restart_pending = False
            self.update(False)

    def make_client(self):
        return make_client(
            {
                "mirror_url": self.mirror_url.get().strip(),
                "proxy_mode": self.selected_proxy(),
                "proxy_url": self.proxy_mode.get(),
            }
        )

    def reset_schedule(self):
        try:
            self.due = next_run(self.config["cron"])
            self.schedule.set("下次更新：" + self.due.strftime("%Y-%m-%d %H:%M:%S"))
        except ValueError:
            self.due = None
            self.schedule.set("CRON 无效，请重新保存设置")

    def save(self, show_error=True):
        try:
            next_run(self.cron.get())
            if (
                self.mirror_mode.get()
                not in ("Pixiv 直连", "FlashZeri + pixiv.re 中转")
                and not self.mirror_url.get().strip()
            ):
                raise ValueError("请输入自定义镜像地址")
            selected_date = self.ranking_date.get().strip()
            selected_date = "" if selected_date == "保持最新" else selected_date
            if not selected_date and self.ranking_date.get() != "保持最新":
                raise ValueError("请选择榜单时间")
            ranking_params(self.selected_ranking(), selected_date)
            labels = {
                label: key for key, label in self.current_resolution_presets().items()
            }
            entered_resolution = self.resolution_box.get()
            resolution = labels.get(entered_resolution, entered_resolution)
            width, height = resolution_size(resolution)
            orientation = self.selected_orientation()
            if orientation == "portrait" and width >= height:
                raise ValueError("竖屏筛选要求最低分辨率为竖向比例")
            if orientation == "landscape" and width <= height:
                raise ValueError("横屏筛选要求最低分辨率为横向比例")
            validation_client = self.make_client()
            validation_client.session.close()
            updated = dict(self.config)
            updated.update(
                mirror_url=self.mirror_url.get().strip().rstrip("/"),
                proxy_mode=self.selected_proxy(),
                proxy_url=self.proxy_mode.get().strip()
                if self.selected_proxy() == "custom"
                else self.config.get("proxy_url", ""),
                ranking_date=selected_date,
                ranking=next(
                    key
                    for key, label in RANKING_LABELS.items()
                    if label == self.ranking.get()
                ),
                resolution=resolution,
                orientation=orientation,
                cron=self.cron.get().strip(),
            )
            changed = [key for key in updated if updated[key] != self.config.get(key)]
            startup_changed = self.autostart.get() != self.saved_autostart
            if sys.platform == "win32" and startup_changed:
                startup(self.autostart.get())
                self.saved_autostart = self.autostart.get()
            save_config(updated)
            cron_changed = updated["cron"] != self.config["cron"]
            self.config = updated
            if cron_changed:
                self.reset_schedule()
            self.config_feedback.set("设置已自动保存")
            if self.status.get().startswith(("设置未应用：", "正在应用设置")):
                self.status.set("就绪")
            logger.info(
                "Settings applied: %s",
                ", ".join(changed + (["autostart"] if startup_changed else []))
                or "unchanged",
            )
            return True
        except Exception as error:
            self.config_feedback.set("未应用：" + str(error))
            self.status.set("设置未应用：" + str(error))
            if show_error:
                messagebox.showerror("设置无效", str(error))
            return False

    def set_activity(self, active):
        self.progress.stop()
        self.progress.configure(
            mode="indeterminate" if active else "determinate", value=0
        )
        if active:
            self.progress.start(12)

    def update(self, manual=True):
        if self.quitting:
            return
        if not manual and (self.save_after or self.restart_pending):
            return
        if self.busy:
            if not manual:
                return
            self.cancel.set()
            if hasattr(self, "network_future"):
                self.network_future.cancel()
            self.generation += 1
            self.cancel = threading.Event()
            self.busy = False
            self.set_activity(False)
            logger.info("Current update cancelled by queue-next request")
        if manual and not self.save():
            return
        self.restart_pending = False
        self.busy = True
        self.status.set("正在启动队列下一张…")
        self.set_activity(True)
        config = dict(self.config)
        screen = desktop_size()

        def worker(emit, cancel):
            try:
                from core import choose_wallpaper

                path, work = choose_wallpaper(
                    config, screen, lambda text: emit("status", text), cancel=cancel
                )
                emit("done", (path, work))
            except Cancelled:
                return
            except Exception as error:
                logging.exception("Wallpaper update failed")
                emit("error", str(error))

        self.submit_network(worker)

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if len(event) == 3 and event[2] != self.generation:
                    continue
                kind, payload = event[:2]
                if kind == "show":
                    self.root.deiconify()
                    self.root.lift()
                elif kind == "tray_ready":
                    if self.root.state() == "iconic":
                        self.hide()
                elif kind == "update":
                    self.update()
                elif kind == "quit":
                    exited = self.quit()
                    if not exited:
                        self.root.after(250, self.poll)
                    return
                elif kind == "status":
                    self.status.set(payload)
                elif kind == "calendar_publication":
                    popup, ending, period = payload
                    if self.calendar_popup is popup and popup.window.winfo_exists():
                        popup.set_publication(ending, period)

                elif kind == "calendar_error":
                    popup, error = payload
                    if self.calendar_popup is popup and popup.window.winfo_exists():
                        popup.publication.set(error)
                elif kind == "error":
                    self.busy = False
                    self.set_activity(False)
                    self.status.set("更新失败：" + payload)
                    self.notify("Pixiv Wallpaper 更新失败", payload)
                elif kind == "done":
                    self.busy = False
                    self.set_activity(False)
                    path, work = payload
                    try:
                        set_wallpaper(path)
                    except Exception as error:
                        logger.exception("Could not apply downloaded wallpaper")
                        self.status.set("更新失败：" + str(error))
                        continue
                    work_id = str(work.get("id", ""))
                    queue_key = str(work.pop("_queue_key", ""))
                    self.work_url = "https://www.pixiv.net/artworks/" + work_id
                    title = (
                        str(work.get("title", "Pixiv illustration"))
                        + " / "
                        + str(work.get("userName", ""))
                    )
                    history = [
                        item for item in self.config["history"] if item["id"] != work_id
                    ]
                    history.append(
                        {
                            "id": work_id,
                            "title": title,
                            "path": str(path),
                            "url": self.work_url,
                            "updated": datetime.now().isoformat(timespec="seconds"),
                        }
                    )
                    self.config.update(
                        last_path=str(path),
                        last_title=title,
                        last_url=self.work_url,
                        queue_key=queue_key,
                        history=history[-500:],
                    )
                    try:
                        save_config(self.config)
                    except OSError:
                        logging.exception("Could not persist wallpaper history")
                    logger.info(
                        "Wallpaper applied: id=%s ranking=%s date=%s",
                        work_id,
                        self.config["ranking"],
                        self.config["ranking_date"] or "latest",
                    )
                    self.caption.set(title)
                    self.show_preview(str(path))
                    self.cache_summary.set(self.cache_text())
                    self.status.set("更新成功：" + datetime.now().strftime("%H:%M:%S"))
                    self.notify("Pixiv Wallpaper 更新成功", title)
        except queue.Empty:
            pass
        if self.due and datetime.now() >= self.due:
            self.reset_schedule()
            self.update(False)
        self.root.after(250, self.poll)

    def cache_text(self):
        try:
            from core import WALLPAPERS

            images = list(WALLPAPERS.glob("*.jpg"))
            size = sum(image.stat().st_size for image in images if image.is_file())
            return f"已缓存壁纸：{len(images)} 张 · {size / (1024 * 1024):.1f} MB"
        except OSError:
            return "已缓存壁纸：尚无缓存"

    def show_preview(self, path):
        if not path:
            return
        try:
            with Image.open(path) as image:
                image.thumbnail((440, 420))
                self.photo = ImageTk.PhotoImage(image.copy())
            self.preview.configure(image=self.photo, text="")
        except Exception:
            logging.exception("Could not load wallpaper preview: %s", path)

    def open_current_path(self):
        path = Path(self.config.get("last_path", ""))
        if not path.is_file():
            messagebox.showerror(
                "无法打开当前壁纸", "当前壁纸文件不存在，请先更新壁纸。"
            )
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", f"/select,{path.resolve()}"])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path.resolve())])
            else:
                subprocess.Popen(["xdg-open", str(path.resolve().parent)])
        except Exception as error:
            messagebox.showerror("无法打开当前壁纸路径", str(error))

    def open_source(self):
        if self.work_url:
            webbrowser.open(self.work_url)

    def notify(self, title, message):
        if self.tray:
            try:
                self.tray.notify(message, title)
            except Exception:
                logging.exception("Tray notification failed")

    def run_tray(self):
        try:
            import pystray

            icon_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            with Image.open(icon_root / "assets" / "app-icon.png") as image:
                icon = image.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
            self.tray = pystray.Icon(
                "PixivWallpaper",
                icon,
                "Pixiv Wallpaper",
                pystray.Menu(
                    pystray.MenuItem(
                        "打开", lambda *_: self.events.put(("show", None)), default=True
                    ),
                    pystray.MenuItem(
                        "队列下一张", lambda *_: self.events.put(("update", None))
                    ),
                    pystray.MenuItem(
                        "退出", lambda *_: self.events.put(("quit", None))
                    ),
                ),
            )
            self.tray.run(setup=self.tray_setup)
        except Exception:
            logging.exception("Tray unavailable")
            self.tray = None

    def tray_setup(self, icon):
        icon.visible = True
        self.events.put(("tray_ready", None))

    def hide(self):
        self.save_window_geometry()
        if self.tray and self.tray.visible:
            self.root.withdraw()
        elif self.root.state() != "iconic":
            self.root.iconify()

    def quit(self):
        self.save_window_geometry()
        if self.save_after:
            self.root.after_cancel(self.save_after)
            self.save_after = None
            if not self.save(show_error=False):
                return
        self.quitting = True
        self.cancel.set()
        self.generation += 1
        if hasattr(self, "network_future"):
            self.network_future.cancel()
            if not self.network_future.done():
                self.status.set("正在取消请求并退出…")
                self.root.after(100, self.quit)
                return
        self.cancel.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.tray:
            self.tray.stop()
        self.root.destroy()
        return True


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        handlers=[
            RotatingFileHandler(
                DATA / "app.log",
                maxBytes=2 * 1024 * 1024,
                backupCount=2,
                encoding="utf-8",
            )
        ],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if sys.platform == "win32":
        ctypes.windll.user32.SetProcessDPIAware()
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        ]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\PixivWallpaper")
        if not mutex:
            raise ctypes.WinError()
        if kernel.GetLastError() == 183:
            sys.exit(0)
    root = tk.Tk()
    App(root)
    root.mainloop()
