import os
import sys
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from app import App
from core import CRON_PRESETS, DEFAULTS


@unittest.skipUnless(
    os.name == "nt" or os.environ.get("DISPLAY"), "GUI display required"
)
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.config_patch = patch("app.load_config", return_value=dict(DEFAULTS))
        self.persist_patch = patch("app.save_config")
        self.config_patch.start()
        self.persist = self.persist_patch.start()
        self.tray_patch = patch.object(App, "run_tray")
        self.startup_patch = patch("app.startup")
        self.startup_patch.start()
        self.tray_patch.start()
        for target in ("app.startup_enabled",):
            dependency = patch(target, return_value=False)
            dependency.start()
            self.addCleanup(dependency.stop)
        self.calendar_refresh_patch = patch.object(App, "refresh_calendar")
        self.calendar_refresh_patch.start()
        self.addCleanup(self.calendar_refresh_patch.stop)
        self.app = App(self.root)

    def tearDown(self):
        for identifier in self.root.tk.call("after", "info"):
            self.root.after_cancel(identifier)
        self.app.cancel.set()
        self.app.executor.shutdown(wait=True, cancel_futures=True)
        self.root.destroy()
        self.config_patch.stop()
        self.persist_patch.stop()
        self.tray_patch.stop()
        self.startup_patch.stop()

    def test_orientation_switches_resolution_presets_and_restores_each_group(self):
        self.assertEqual(int(self.app.orientation_box.grid_info()["row"]), 0)
        self.assertEqual(int(self.app.resolution_box.grid_info()["row"]), 1)
        self.assertEqual(
            tuple(self.app.orientation_box["values"]), ("无", "横屏", "竖屏")
        )
        self.assertEqual(self.app.resolution_display.get(), "1080 × 1080")
        self.assertIn("720 × 720", self.app.resolution_box["values"])
        self.app.orientation_display.set("竖屏")
        self.assertEqual(self.app.resolution_display.get(), "1080 × 1920")
        self.app.resolution_display.set("2560 × 1440")
        self.app.orientation_display.set("横屏")
        self.assertEqual(self.app.resolution_display.get(), "1920 × 1080")
        self.app.resolution_display.set("2560 × 1440")
        self.app.orientation_display.set("无")
        self.assertEqual(self.app.resolution_display.get(), "1080 × 1080")
        self.app.resolution_display.set("720 × 720")
        self.assertEqual(self.app.config["orientation"], "any")
        self.assertTrue(self.app.save(show_error=False))
        self.assertEqual(self.app.config["resolution"], "720x720")
        self.app.orientation_display.set("竖屏")
        self.assertEqual(self.app.resolution_display.get(), "1080 × 1920")
        self.assertTrue(self.app.save(show_error=False))
        self.assertEqual(self.app.config["orientation"], "portrait")
        self.assertEqual(self.app.config["resolution"], "1080x1920")
        self.assertEqual(self.persist.call_args.args[0]["orientation"], "portrait")

    def test_mirror_presets_and_custom_address(self):
        from app import FLASHZERI_URL

        self.app.mirror_mode.set("FlashZeri + pixiv.re 中转")
        self.app.mirror_selected()
        self.assertEqual(self.app.mirror_url.get(), FLASHZERI_URL)
        self.app.mirror_mode.set("自定义")
        self.app.mirror_selected()
        self.assertFalse(self.app.save(show_error=False))
        self.assertEqual(str(self.app.mirror_box["state"]), "normal")
        self.app.mirror_mode.set("https://mirror.example")
        self.assertEqual(self.app.mirror_url.get(), "https://mirror.example")
        self.assertTrue(self.app.save(show_error=False))
        self.app.mirror_mode.set("Pixiv 直连")
        self.app.mirror_selected()
        self.assertEqual(self.app.mirror_url.get(), "")
        self.assertEqual(str(self.app.mirror_box["state"]), "readonly")
        self.app.mirror_mode.set("自定义")
        self.app.mirror_selected()
        self.assertEqual(self.app.mirror_mode.get(), "https://mirror.example")

    def test_queue_next_cancels_busy_request_and_restarts(self):
        from unittest.mock import Mock

        old_cancel = self.app.cancel
        old_future = Mock()
        self.app.network_future = old_future
        self.app.busy = True
        with (
            patch.object(self.app, "save", return_value=True),
            patch.object(self.app, "submit_network") as submit,
            patch("app.desktop_size", return_value=(1920, 1080)),
            patch.object(self.app, "set_activity"),
        ):
            self.app.update()
        self.assertTrue(old_cancel.is_set())
        old_future.cancel.assert_called_once()
        self.assertTrue(self.app.busy)
        submit.assert_called_once()
        self.assertEqual(self.app.status.get(), "正在启动队列下一张…")

    def test_connection_checks_api_and_closes_session(self):
        client = Mock()
        client.mirror_url = ""
        client.json.return_value = {
            "date": "20261022",
            "contents": [{"illust_id": "1"}],
        }
        emit = Mock()
        with (
            patch("app.make_client", return_value=client),
            patch.object(self.app, "submit_network") as submit,
        ):
            self.app.test_connection()
            submit.call_args.args[0](emit, self.app.cancel)
        self.assertIn("连通正常", emit.call_args.args[1])
        self.assertEqual(client.session.close.call_count, 2)
        client.json.return_value = {"contents": []}
        with (
            patch("app.make_client", return_value=client),
            patch.object(self.app, "submit_network") as submit,
        ):
            self.app.test_connection()
            submit.call_args.args[0](emit, self.app.cancel)
        self.assertIn("失败", emit.call_args.args[1])

    def test_window_geometry_is_persisted(self):
        self.root.geometry("800x760+11+13")
        self.root.update_idletasks()
        self.app.save_window_geometry()
        self.assertRegex(self.app.config["window_geometry"], r"^800x760[+-]\d+[+-]\d+$")
        self.persist.assert_called()

    def test_activity_clears_after_every_terminal_event(self):
        payloads = [
            ("error", "error"),
            ("done", ("unused.jpg", {"id": "1", "title": "Test"})),
        ]
        with (
            patch.object(self.app, "show_preview"),
            patch.object(self.app, "notify"),
            patch("app.set_wallpaper"),
        ):
            for kind, payload in payloads:
                self.app.set_activity(True)
                self.app.progress.configure(value=23)
                self.app.events.put((kind, payload))
                self.app.poll()
                self.assertEqual(str(self.app.progress.cget("mode")), "determinate")
                self.assertEqual(float(self.app.progress.cget("value")), 0)

    def test_startup_applies_displayed_wallpaper_without_replacing_backup(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            wallpaper = Path(directory) / "last.jpg"
            Image.new("RGB", (20, 20)).save(wallpaper)
            self.app.config["last_path"] = str(wallpaper)
            with (
                patch("app.sys.platform", "win32"),
                patch("app.set_wallpaper") as apply,
            ):
                self.app.apply_startup_wallpaper()
                apply.assert_called_once_with(wallpaper)
                self.assertEqual(self.app.status.get(), "已恢复当前壁纸")
                wallpaper.unlink()
                apply.reset_mock()
                with self.assertLogs("app", level="ERROR"):
                    self.app.apply_startup_wallpaper()
                apply.assert_not_called()
                self.assertIn("缓存已不存在", self.app.status.get())

    def test_ranking_change_cancels_old_results_and_saves_selection(self):
        self.app.busy = True
        old_cancel = self.app.cancel
        old_generation = self.app.generation
        self.app.ranking.set("受女性欢迎")
        self.assertTrue(old_cancel.is_set())
        with patch.object(self.app, "update") as update:
            self.app.autosave()
            update.assert_called_once_with(False)
        self.assertEqual(self.app.config["ranking"], "female")
        with patch("app.set_wallpaper") as apply:
            self.app.events.put(("done", ("old.jpg", {"id": "1"}), old_generation))
            self.app.poll()
            apply.assert_not_called()
        self.assertEqual(int(self.app.ranking_box.grid_info()["row"]), 0)
        self.assertFalse(hasattr(self.app, "tag_box"))

    def test_calendar_rejects_invalid_proxy_without_mislabeling_error(self):
        self.calendar_refresh_patch.stop()
        popup = Mock()
        popup.publication = Mock()
        self.app.proxy_mode.set("")
        with patch.object(self.app, "submit_network") as submit:
            self.app.refresh_calendar(popup)
        submit.assert_not_called()
        self.assertNotIn("代理格式", popup.publication.set.call_args.args[0])
        self.assertIn("代理格式", self.app.status.get())

    def test_calendar_respects_ranking_start_and_switching_rankings(self):
        self.app.ranking_date.set("2007-09-13")
        self.root.update()
        with patch.object(self.app, "submit_network") as submit:
            self.app.show_calendar()
            popup = self.app.calendar_popup
            popup.set_publication("2007-09-14", "2007年9月14日")
            self.root.update()
            self.assertTrue(popup.day_buttons[12].instate(["disabled"]))
            self.assertFalse(popup.day_buttons[13].instate(["disabled"]))
            popup.move(-1)
            self.assertEqual(popup.month.get(), 9)
            popup.day_buttons[13].invoke()
            self.app.autosave()
            self.assertEqual(self.app.config["ranking_date"], "2007-09-13")
            self.app.ranking.set("本月")
            self.app.autosave()
            self.assertEqual(self.app.config["ranking_date"], "")
            self.assertEqual(self.app.ranking_time_display.get(), "保持最新")
            submit.assert_not_called()
        self.assertFalse(hasattr(self.app, "date_load_button"))
        self.assertFalse(hasattr(self.app, "period"))

    def test_calendar_refresh_uses_live_publication_and_does_not_freeze_latest(self):
        self.calendar_refresh_patch.stop()
        client = Mock()
        client.json.return_value = {
            "date": "20261022",
            "date_range_text": "2026年10月22日",
        }
        with patch("app.make_client", return_value=client):
            self.app.show_calendar()
            self.app.network_future.result(timeout=2)
            self.app.poll()
        popup = self.app.calendar_popup
        self.assertEqual(popup.maximum.isoformat(), "2026-10-22")
        self.assertTrue(popup.day_buttons[23].instate(["disabled"]))
        self.assertFalse(popup.day_buttons[22].instate(["disabled"]))
        self.assertEqual(self.app.ranking_date.get(), "保持最新")
        self.assertEqual(self.app.ranking_time_display.get(), "保持最新")
        self.assertIn("2026年10月22日", popup.publication.get())
        self.app.autosave()
        self.assertEqual(self.app.config["ranking_date"], "")
        self.assertNotIn("date", client.json.call_args.kwargs)
        popup.close()
        self.assertEqual(self.app.ranking_time_display.get(), "保持最新")
        client.json.return_value = {
            "date": "20261023",
            "date_range_text": "2026年10月23日",
        }
        with patch("app.make_client", return_value=client):
            self.app.show_calendar()
            self.app.network_future.result(timeout=2)
            self.app.poll()
        self.assertEqual(self.app.calendar_popup.maximum.isoformat(), "2026-10-23")
        self.app.calendar_popup.close()
        self.assertEqual(self.app.ranking_time_display.get(), "保持最新")

    def test_calendar_browsing_does_not_change_selection_and_latest_is_not_a_date(self):
        self.app.ranking.set("本周")
        self.app.ranking_date.set("2008-04-15")
        self.app.autosave()
        self.assertEqual(
            self.app.ranking_time_display.get(), "2008-04-09 ～ 2008-04-15"
        )
        generation = self.app.generation
        self.app.show_calendar()
        popup = self.app.calendar_popup
        popup.move(1)
        popup.close()
        self.assertEqual(self.app.generation, generation)
        self.assertEqual(self.app.config["ranking_date"], "2008-04-15")
        old_cancel = self.app.cancel
        self.app.show_calendar()
        self.app.calendar_popup.select("")
        self.app.autosave()
        self.assertTrue(old_cancel.is_set())
        self.assertEqual(self.app.config["ranking_date"], "")
        self.assertEqual(self.app.ranking_time_display.get(), "保持最新")

    def test_save_does_not_move_cron_deadline(self):
        original_due = self.app.due
        self.app.autosave()
        self.assertEqual(self.app.due, original_due)

    def test_correcting_proxy_clears_stale_setting_error(self):
        self.app.proxy_mode.set("HTTP/SOCKS5")
        self.app.proxy_selected()
        self.app.autosave()
        self.assertTrue(self.app.status.get().startswith("设置未应用："))
        self.app.proxy_mode.set("系统")
        self.app.proxy_selected()
        self.assertEqual(self.app.status.get(), "正在应用设置…")
        with self.assertLogs("app", level="INFO") as logs:
            self.app.autosave()
        self.assertEqual(self.app.status.get(), "就绪")
        self.assertTrue(any("Settings applied" in line for line in logs.output))

    def test_preference_save_preserves_running_status(self):
        self.app.busy = True
        self.app.status.set("正在检查作品 3/20")
        self.app.autosave()
        self.assertEqual(self.app.status.get(), "正在检查作品 3/20")

    def test_network_jobs_do_not_overlap(self):
        import threading

        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        order = []

        def first(emit, cancel):
            order.append("first-start")
            entered.set()
            release.wait(2)
            order.append("first-end")

        def second(emit, cancel):
            order.append("second")
            completed.set()

        self.app.submit_network(first)
        self.assertTrue(entered.wait(1))
        self.app.submit_network(second)
        self.assertFalse(completed.is_set())
        release.set()
        self.assertTrue(completed.wait(2))
        self.assertEqual(order, ["first-start", "first-end", "second"])

    def test_schedule_preferences_do_not_cancel_or_restart(self):
        self.app.busy = True
        original_cancel = self.app.cancel
        original_generation = self.app.generation
        with patch.object(self.app, "update") as update:
            for variable, value in (
                (self.app.autostart, True),
                (self.app.cron, "0 * * * *"),
            ):
                variable.set(value)
                self.assertFalse(original_cancel.is_set())
                self.assertEqual(self.app.generation, original_generation)
                self.assertTrue(self.app.busy)
                self.app.autosave()
            update.assert_not_called()
        self.assertEqual(self.app.config["cron"], "0 * * * *")

    def test_settings_cancel_and_restart_and_ignore_stale_results(self):
        old_cancel = self.app.cancel
        old_generation = self.app.generation
        self.app.busy = True
        self.app.ranking_date.set("2026-10-21")
        self.assertTrue(old_cancel.is_set())
        self.assertTrue(self.app.restart_pending)
        with patch.object(self.app, "update") as update:
            self.app.autosave()
            update.assert_called_once_with(False)
        with patch("app.set_wallpaper") as apply:
            self.app.events.put(("done", ("old.jpg", {"id": "old"}), old_generation))
            self.app.events.put(("status", "OLD STATUS", old_generation))
            self.app.poll()
            apply.assert_not_called()
            self.assertNotEqual(self.app.status.get(), "OLD STATUS")

    def test_current_wallpaper_path_opens_in_file_manager(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "wallpaper.jpg"
            image.touch()
            self.app.config["last_path"] = str(image)
            with patch("app.subprocess.Popen") as popen:
                self.app.open_current_path()
        if sys.platform == "darwin":
            popen.assert_called_once_with(["open", "-R", str(image)])
        elif sys.platform == "win32":
            popen.assert_called_once_with(["explorer", f"/select,{image}"])
        else:
            popen.assert_called_once_with(["xdg-open", directory])

    def test_tray_hide_and_show(self):
        from unittest.mock import Mock

        self.root.update()
        self.assertFalse(self.root.bind("<Unmap>"))
        self.app.tray = Mock(visible=True)
        self.app.hide()
        self.assertEqual(self.root.state(), "withdrawn")
        self.root.deiconify()
        self.root.update()
        self.assertTrue(self.app.ranking_box.winfo_ismapped())

    def test_layout_and_autosave(self):
        self.app.ranking_date.set("2026-10-21")
        for scale in (1.0, 1.5, 2.0):
            self.root.tk.call("tk", "scaling", scale)
            for size in ("1100x680", "1000x650", "960x620"):
                self.root.geometry(size)
                self.root.update()
                with patch(
                    "app.Image.open", return_value=Image.new("RGB", (1920, 1080))
                ):
                    self.app.show_preview("preview")
                self.root.update()
                progress = self.app.progress
                self.assertLess(
                    progress.winfo_rooty() + progress.winfo_height(),
                    self.root.winfo_rooty() + self.root.winfo_height(),
                )
                self.assertTrue(self.app.date_entry.winfo_ismapped())
                label_center = (
                    self.app.date_label.winfo_rooty()
                    + self.app.date_label.winfo_height() / 2
                )
                control_center = (
                    self.app.date_entry.winfo_rooty()
                    + self.app.date_entry.winfo_height() / 2
                )
                self.assertLessEqual(abs(label_center - control_center), 1)
                self.assertEqual(self.app.date_entry.master, self.app.date_label.master)
                self.assertLessEqual(
                    self.app.date_entry.winfo_rootx()
                    + self.app.date_entry.winfo_width(),
                    self.root.winfo_rootx() + self.root.winfo_width(),
                )
                self.assertTrue(self.app.proxy_box.winfo_ismapped())
                self.assertLess(
                    self.app.proxy_box.winfo_rooty()
                    + self.app.proxy_box.winfo_height(),
                    progress.winfo_rooty(),
                )
        self.assertFalse(self.errors, self.errors)
        self.app.proxy_mode.set("HTTP/SOCKS5")
        self.app.proxy_selected()
        self.assertEqual(str(self.app.proxy_box.cget("state")), "normal")
        self.app.proxy_mode.set("http://127.0.0.1:7890")
        self.assertEqual(
            self.app.make_client().session.proxies["https"], "http://127.0.0.1:7890"
        )
        self.app.proxy_mode.set("直连")
        self.app.proxy_selected()
        self.assertEqual(str(self.app.proxy_box.cget("state")), "readonly")
        self.assertFalse(self.app.make_client().session.proxies)
        self.app.cron_display.set("每 1 小时")
        self.app.cron_selected()
        self.assertEqual(self.app.cron.get(), CRON_PRESETS["每 1 小时"])
        self.app.cron_display.set("自定义")
        self.app.cron_selected()
        self.assertEqual(str(self.app.cron_box.cget("state")), "normal")
        self.app.cron_display.set("*/30 * * * * *")
        self.app.autosave()
        self.assertTrue(self.persist.called)
        saved = self.app.config["cron"]
        self.app.cron.set("bad")
        self.app.autosave()
        self.assertEqual(self.app.config["cron"], saved)
        self.assertIn("未应用", self.app.config_feedback.get())


if __name__ == "__main__":
    unittest.main()
