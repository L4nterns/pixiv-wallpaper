import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import core
from core import (
    RESOLUTION_PRESETS,
    PixivClient,
    desktop_ok,
    next_run,
    resolution_size,
    safe_work,
)


class CoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = patch.object(core, "DATA", Path(directory.name))
        override.start()
        self.addCleanup(override.stop)

    def test_config_values_are_validated(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(core, "DATA", Path(directory)),
        ):
            (Path(directory) / "config.json").write_text(
                json.dumps(
                    {
                        "orientation": "portrait",
                        "resolution": [],
                        "proxy_mode": [],
                        "history": ["invalid", {"id": "12"}],
                        "window_geometry": "bad geometry",
                    }
                ),
                encoding="utf-8",
            )
            config = core.load_config()
            self.assertEqual(config["orientation"], "portrait")
            self.assertEqual(config["resolution"], "1080x1920")
            self.assertEqual(config["history"], [{"id": "12"}])
            self.assertEqual(config["window_geometry"], "")

        from unittest.mock import MagicMock

        client = PixivClient()
        response = MagicMock()
        response.status_code = 429
        response.is_redirect = False
        response.__enter__.return_value = response
        with patch.object(client.session, "get", return_value=response) as request:
            with self.assertRaises(core.SourceUnavailable):
                client.json("ranking.php")
            request.assert_called_once()

    def test_portable_data(self):
        self.assertEqual(
            core.application_dir() / "data",
            Path(core.__file__).resolve().parent / "data",
        )
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "PixivWallpaper.exe"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertEqual(core.application_dir(), executable.parent)

    def test_cron(self):
        self.assertEqual(
            next_run("0 */3 * * *", datetime(2026, 1, 1, 10)), datetime(2026, 1, 1, 12)
        )
        for value in ("bad", "* * * * * * *", "61 * * * *", "60 * * * * *"):
            with self.assertRaises(ValueError):
                next_run(value)

    def test_seconds_cron(self):
        self.assertEqual(
            next_run("*/5 * * * * *", datetime(2026, 1, 1, 10, 0, 1)),
            datetime(2026, 1, 1, 10, 0, 5),
        )
        self.assertEqual(
            next_run("*/5 * * * * *", datetime(2026, 1, 1, 10, 0, 5)),
            datetime(2026, 1, 1, 10, 0, 10),
        )
        self.assertEqual(
            next_run("*/5 * * * * *", datetime(2026, 1, 1, 10, 0, 59)),
            datetime(2026, 1, 1, 10, 1, 0),
        )

    def test_opposite_orientation_wallpaper_preserves_full_artwork(self):
        from PIL import Image

        portrait = Image.new("RGB", (100, 200), "red")
        wallpaper = core.fit_wallpaper(portrait, (200, 100))
        self.assertEqual(wallpaper.size, (200, 100))
        self.assertGreater(wallpaper.getpixel((100, 50))[0], 240)
        self.assertLess(wallpaper.getpixel((10, 50))[0], 200)

    def test_resolution_presets(self):
        self.assertIn("1920x1080", RESOLUTION_PRESETS)
        self.assertIn("2560x1440", RESOLUTION_PRESETS)
        self.assertNotIn("auto", RESOLUTION_PRESETS)
        self.assertEqual(core.DEFAULTS["orientation"], "any")
        self.assertEqual(core.PORTRAIT_RESOLUTION_PRESETS["1080x1920"], "1080 × 1920")
        self.assertEqual(core.DEFAULTS["resolution"], "1920x1080")
        self.assertEqual(core.SQUARE_RESOLUTION_PRESETS["1080x1080"], "1080 × 1080")
        self.assertEqual(core.SQUARE_RESOLUTION_PRESETS["720x720"], "720 × 720")
        self.assertEqual(core.DEFAULTS["cron"], "*/30 * * * *")
        self.assertEqual(resolution_size("1920x1080"), (1920, 1080))
        self.assertEqual(resolution_size("2345x1234"), (2345, 1234))
        for value in (
            "invalid",
            "0x1080",
            "1920X1080",
            "1920×1080",
            " 1920x1080",
            "1920x1080 ",
            "01x2",
            "1.5x2",
            "-1x2",
            "1920 x 1080",
            "",
            None,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolution_size(value)

    def test_desktop_size(self):
        self.assertTrue(desktop_ok(3840, 2160, (1920, 1080)))
        self.assertTrue(desktop_ok(3440, 1440, (2560, 1080)))
        for width, height in (
            (1080, 1920),
            (2160, 3840),
            (3000, 4000),
            (4000, 4000),
            (1000, 1000),
            (1280, 720),
        ):
            self.assertFalse(desktop_ok(width, height, (1920, 1080)))
        self.assertFalse(desktop_ok(1920, 1080, (2560, 1440)))

    def test_portrait_is_rejected_in_listing_pages_and_actual_download(self):
        from PIL import Image

        for stage in ("listing", "pages", "download"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                listing = {
                    "illust_id": "1",
                    "illust_type": "0",
                    "width": 3000,
                    "height": 2000,
                    "illust_content_type": {"sexual": 0},
                }
                page = {
                    "width": 3000,
                    "height": 2000,
                    "urls": {"original": "https://i.pximg.net/1.jpg"},
                }
                if stage == "listing":
                    listing.update(width=2160, height=3840)
                if stage == "pages":
                    page.update(width=2160, height=3840)

                def response(path, listing=listing, page=page, **params):
                    if path == "ranking.php":
                        return {
                            "contents": [listing],
                            "date": "20261021",
                            "next": False,
                        }
                    if path.endswith("/pages"):
                        return {"body": [page]}
                    return {
                        "body": {"id": "1", "illustType": 0, "xRestrict": 0, "sl": 2}
                    }

                def download(url, target):
                    Image.new("RGB", (2160, 3840)).save(target, format="PNG")

                with (
                    patch.object(core, "DATA", Path(directory)),
                    patch.object(core, "WALLPAPERS", Path(directory) / "wallpapers"),
                    patch.object(PixivClient, "json", side_effect=response),
                    patch.object(
                        PixivClient, "download", side_effect=download
                    ) as fetch,
                    patch.object(core.time, "sleep"),
                ):
                    with self.assertRaises(RuntimeError):
                        core.choose_wallpaper(
                            core.DEFAULTS | {"orientation": "landscape"}, (1920, 1080)
                        )
                    self.assertEqual(fetch.call_count, int(stage == "download"))
                    self.assertFalse(list(Path(directory).rglob("*.jpg")))

    def test_safety(self):
        base = dict(xRestrict=0, restrict=0, illustType=0, sl=2, tags=["風景", "天空"])
        self.assertTrue(safe_work(base))
        old_api_shape = base | {"tags": {"tags": [{"tag": "風景"}]}}
        self.assertTrue(safe_work(old_api_shape))
        for change in (
            dict(xRestrict=1),
            dict(xRestrict=2),
            dict(restrict=1),
            dict(sl=4),
            dict(illustType=1),
            dict(illustType=2),
            dict(tags=["R-18G"]),
        ):
            self.assertFalse(safe_work(base | change))
        self.assertFalse(safe_work({}))

    def test_landscape_filter_uses_orientation_only(self):
        self.assertTrue(desktop_ok(2000, 1999, (1920, 1080), "landscape"))
        self.assertFalse(desktop_ok(2000, 2000, (1920, 1080), "landscape"))
        self.assertFalse(desktop_ok(1999, 2000, (1920, 1080), "landscape"))

    def test_landscape_filter_can_be_disabled_without_disabling_resolution_filter(self):
        self.assertTrue(desktop_ok(1920, 2000, (1920, 1080), "any"))
        self.assertFalse(desktop_ok(1919, 2000, (1920, 1080), "any"))
        self.assertTrue(desktop_ok(1920, 1080, (1920, 1080), "landscape"))
        self.assertFalse(desktop_ok(1080, 1920, (1920, 1080), "landscape"))
        self.assertTrue(desktop_ok(1080, 1920, (1080, 1920), "portrait"))
        self.assertFalse(desktop_ok(1920, 1080, (1080, 1920), "portrait"))

        client = PixivClient()
        self.assertEqual(client.url("ajax/test"), "https://www.pixiv.net/ajax/test")

    def test_cancel_prevents_network_requests(self):
        import threading

        cancel = threading.Event()
        cancel.set()
        client = PixivClient()
        client.cancel = cancel
        with patch.object(client.session, "get") as request:
            with self.assertRaises(core.Cancelled):
                client.json("ranking.php")
            request.assert_not_called()
        with self.assertRaises(core.Cancelled):
            core.choose_wallpaper(core.DEFAULTS, (3840, 2160), cancel=cancel)

    def test_public_ranking_modes_and_illustration_filter(self):
        for mode in core.RANKING_LABELS:
            with self.subTest(mode=mode):
                params = core.ranking_params(mode)
                self.assertEqual(params["mode"], mode)
                if mode in ("daily", "weekly", "monthly", "rookie"):
                    self.assertEqual(params["content"], "illust")
                else:
                    self.assertNotIn("content", params)
                mixed = [
                    {
                        "illust_id": str(i),
                        "illust_type": kind,
                        "width": 3000,
                        "height": 2000,
                        "illust_content_type": {"sexual": 0},
                    }
                    for i, kind in enumerate(("1", "2", None), 1)
                ]
                with (
                    patch.object(
                        PixivClient,
                        "json",
                        return_value={
                            "contents": mixed,
                            "date": "20261021",
                            "next": False,
                        },
                    ) as request,
                    patch.object(core.time, "sleep"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "没有找到"):
                        core.choose_wallpaper(
                            core.DEFAULTS | {"ranking": mode}, (1920, 1080)
                        )
                    self.assertEqual(request.call_count, 1)
                    for call in request.call_args_list:
                        self.assertEqual(call.kwargs["mode"], mode)
                        self.assertEqual(
                            call.kwargs.get("content"), params.get("content")
                        )
        for mode in ("daily_r18", "weekly_r18", "r18g", "daily_r18_ai"):
            with self.assertRaises(ValueError):
                core.ranking_params(mode)

    def test_short_ranking_stops_at_missing_later_page(self):
        from unittest.mock import Mock

        import requests

        candidate = {
            "illust_id": "123",
            "illust_type": "0",
            "width": 3000,
            "height": 2000,
            "illust_content_type": {"sexual": 0},
        }
        response = Mock(status_code=404)
        missing = requests.HTTPError(response=response)
        with tempfile.TemporaryDirectory() as directory:
            from PIL import Image

            cached = Path(directory) / "123-1920x1080.jpg"
            Image.new("RGB", (20, 20)).save(cached)
            core.write_json(
                cached.with_suffix(".json"), {"width": 3000, "height": 2000}
            )
            with (
                patch.object(core, "WALLPAPERS", Path(directory)),
                patch.object(core.time, "sleep"),
                patch.object(
                    PixivClient,
                    "json",
                    side_effect=[
                        {"contents": [candidate], "date": "20261021"},
                        missing,
                        {
                            "body": {
                                "id": "123",
                                "illustType": 0,
                                "xRestrict": 0,
                                "sl": 2,
                            }
                        },
                    ],
                ) as request,
            ):
                path, _ = core.choose_wallpaper(
                    core.DEFAULTS | {"ranking": "daily_ai"}, (1920, 1080)
                )
                self.assertEqual(path, cached)
                self.assertEqual(request.call_count, 3)

    def test_ranking_reads_pages_past_four_until_upstream_end(self):
        page_numbers = []

        def response(path, **params):
            if path == "ranking.php":
                page_numbers.append(params["p"])
                return {
                    "date": "20261021",
                    "contents": [{"page_marker": params["p"]}],
                    "next": params["p"] < 6,
                }
            raise AssertionError(path)

        with (
            patch.object(core, "WALLPAPERS", core.DATA / "test-wallpapers"),
            patch.object(PixivClient, "json", side_effect=response),
            patch.object(core.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "没有找到"):
                core.choose_wallpaper(core.DEFAULTS, (1920, 1080))
        self.assertEqual(page_numbers, [1, 2, 3, 4, 5, 6])

    def test_local_ranking_periods_cover_month_and_year_boundaries(self):
        for mode, ending, expected in [
            ("daily", "2026-10-21", "2026-10-21"),
            ("weekly", "2026-10-21", "2026-10-15 ～ 2026-10-21"),
            ("monthly", "2026-10-22", "2026-09-23 ～ 2026-10-22"),
            ("rookie", "2026-10-22", "2026-10-16 ～ 2026-10-22"),
            ("original", "2026-01-03", "2025-12-28 ～ 2026-01-03"),
            ("monthly", "2024-03-01", "2024-02-01 ～ 2024-03-01"),
        ]:
            with self.subTest(mode=mode, ending=ending):
                self.assertEqual(core.ranking_period_label(mode, ending), expected)
        self.assertEqual(core.ranking_period_label("weekly"), "保持最新")

    def test_ranking_start_dates_are_inclusive(self):
        from datetime import timedelta

        for mode, first in core.RANKING_START_DATES.items():
            with self.subTest(mode=mode):
                self.assertEqual(
                    core.ranking_params(mode, first)["date"], first.replace("-", "")
                )
                earlier = (
                    (datetime.fromisoformat(first) - timedelta(days=1))
                    .date()
                    .isoformat()
                )
                with self.assertRaisesRegex(ValueError, "起始日期"):
                    core.ranking_params(mode, earlier)

    def test_latest_uses_server_edition_across_midnight_and_delayed_publication(self):
        for local_time, published in [
            (datetime(2026, 10, 23, 0, 1), "20261021"),
            (datetime(2026, 10, 23, 18), "20261022"),
        ]:
            with (
                self.subTest(local_time=local_time),
                patch.object(core, "datetime", wraps=datetime) as clock,
                patch.object(core.time, "sleep"),
                patch.object(
                    PixivClient,
                    "json",
                    side_effect=[
                        {"date": published, "contents": [{}]},
                        {"date": published, "contents": []},
                    ],
                ) as request,
            ):
                clock.now.return_value = local_time
                config = core.DEFAULTS | {"ranking_date": ""}
                with self.assertRaisesRegex(RuntimeError, "没有找到"):
                    core.choose_wallpaper(config, (1920, 1080))
                self.assertNotIn("date", request.call_args_list[0].kwargs)
                self.assertEqual(request.call_args_list[1].kwargs["date"], published)
                self.assertEqual(config["ranking_date"], "")

    def test_ranking_dates_are_validated(self):
        for value in ("2026-02-30", "2026-1-01", "20261021", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                core.ranking_date(value)
        self.assertEqual(
            core.ranking_params("weekly", "2026-10-21")["date"], "20261021"
        )
        self.assertNotIn("date", core.ranking_params("weekly"))

    def test_historical_mismatch_and_missing_dates_do_not_fall_back(self):
        with patch.object(
            PixivClient, "json", return_value={"date": "20261022", "contents": []}
        ):
            with self.assertRaisesRegex(ValueError, "日期不符"):
                core.choose_wallpaper(
                    core.DEFAULTS | {"ranking_date": "2026-10-21"}, (1920, 1080)
                )
        error = requests.HTTPError(response=Mock(status_code=404))
        with patch.object(PixivClient, "json", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "尚未发布"):
                core.choose_wallpaper(
                    core.DEFAULTS | {"ranking_date": "2026-10-21"}, (1920, 1080)
                )

    def test_latest_pins_pagination_to_published_date_and_reports_actual_range(self):
        report = Mock()
        with (
            patch.object(
                PixivClient,
                "json",
                side_effect=[
                    {
                        "date": "20261021",
                        "date_range_text": "2026年10月15日～2026年10月21日",
                        "contents": [{"illust_id": "1"}],
                    },
                    {"date": "20261021", "contents": []},
                ],
            ) as request,
            patch.object(core.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "没有找到"):
                core.choose_wallpaper(
                    core.DEFAULTS | {"ranking": "weekly"}, (1920, 1080), report
                )
            self.assertNotIn("date", request.call_args_list[0].kwargs)
            self.assertEqual(request.call_args_list[1].kwargs["date"], "20261021")
            report.assert_any_call("榜单时间：本周/2026年10月15日～2026年10月21日")

    def test_cycle_uses_selected_ranking_and_edition_membership(self):
        from PIL import Image

        editions = {
            ("daily", "20261021"): ["1", "2", "3"],
            ("daily", "20261020"): ["4", "5"],
            ("weekly", "20261021"): ["5", "2", "4"],
        }

        def response(path, **params):
            if path == "ranking.php":
                ids = editions[(params["mode"], params["date"])]
                return {
                    "date": params["date"],
                    "next": False,
                    "contents": [
                        {
                            "illust_id": ident,
                            "illust_type": "0",
                            "width": 3000,
                            "height": 2000,
                            "illust_content_type": {"sexual": 0},
                        }
                        for ident in ids
                    ],
                }
            return {
                "body": {
                    "id": path.rsplit("/", 1)[-1],
                    "illustType": 0,
                    "xRestrict": 0,
                    "sl": 2,
                }
            }

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            for ident in ("3", "4", "5"):
                Image.new("RGB", (20, 20)).save(cache / f"{ident}-1920x1080.jpg")
                core.write_json(
                    cache / f"{ident}-1920x1080.json", {"width": 3000, "height": 2000}
                )
            for mode, ending, expected in [
                ("daily", "2026-10-21", "3"),
                ("daily", "2026-10-20", "4"),
                ("weekly", "2026-10-21", "4"),
            ]:
                with (
                    self.subTest(mode=mode, ending=ending),
                    patch.object(core, "WALLPAPERS", cache),
                    patch.object(core.time, "sleep"),
                    patch.object(PixivClient, "json", side_effect=response),
                ):
                    config = core.DEFAULTS | {
                        "ranking": mode,
                        "ranking_date": ending,
                        "last_url": "https://www.pixiv.net/artworks/2",
                        "queue_key": mode + ":" + ending,
                    }
                    _, work = core.choose_wallpaper(config, (1920, 1080))
                    self.assertEqual(work["id"], expected)

    def test_live_result_order_advances_wraps_and_tracks_changed_rankings(self):
        from PIL import Image

        scenarios = [
            (["1", "2", "3"], "", "1", None),
            (["1", "2", "3"], "2", "3", None),
            (["1", "2", "3"], "3", "1", None),
            (["1", "2", "3"], "99", "1", None),
            (["3", "1", "2"], "1", "2", None),
            (["1", "2", "3"], "1", "3", "2"),
            (["1"], "1", "1", None),
        ]
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            for work_id in ("1", "2", "3"):
                Image.new("RGB", (20, 20)).save(cache / f"{work_id}-1920x1080.jpg")
                core.write_json(
                    cache / f"{work_id}-1920x1080.json", {"width": 3000, "height": 2000}
                )
            for ids, current, expected, rejected in scenarios:
                for selection in ("", "2026-10-21"):
                    with self.subTest(
                        ids=ids, current=current, selection=selection, rejected=rejected
                    ):
                        for cached_page in (core.DATA / "rankings").rglob("*.json"):
                            cached_page.unlink()
                        listing = [
                            {
                                "illust_id": ident,
                                "id": ident,
                                "illust_type": "0",
                                "width": 3000,
                                "height": 2000,
                                "illust_content_type": {"sexual": 0},
                                "illustType": 0,
                                "xRestrict": 0,
                                "sl": 2,
                            }
                            for ident in ids
                        ]

                        def response(
                            path, listing=listing, rejected=rejected, **params
                        ):
                            if path == "ranking.php":
                                return {
                                    "contents": listing,
                                    "date": "20261021",
                                    "next": False,
                                }
                            ident = path.rsplit("/", 1)[-1]
                            return {
                                "body": {
                                    "id": ident,
                                    "illustType": 0,
                                    "xRestrict": 0,
                                    "sl": 4 if ident == rejected else 2,
                                }
                            }

                        config = core.DEFAULTS | {
                            "ranking_date": selection,
                            "last_url": "https://www.pixiv.net/artworks/" + current,
                            "queue_key": "daily:2026-10-21",
                        }
                        with (
                            patch.object(core, "WALLPAPERS", cache),
                            patch.object(core.time, "sleep"),
                            patch.object(
                                PixivClient, "json", side_effect=response
                            ) as request,
                        ):
                            for _ in range(2):
                                path, work = core.choose_wallpaper(config, (1920, 1080))
                                self.assertEqual(work["id"], expected)
                                self.assertEqual(path.name, f"{expected}-1920x1080.jpg")
                            self.assertEqual(
                                sum(
                                    call.args[0] == "ranking.php"
                                    for call in request.call_args_list
                                ),
                                2,
                            )

    def test_ranking_empty_content_type_does_not_abort_update(self):
        good = {
            "illust_id": "123",
            "illust_type": "0",
            "width": 1920,
            "height": 1080,
            "illust_content_type": {"sexual": 0},
        }
        invalid = [
            dict(good, illust_id=str(index), illust_content_type=value)
            for index, value in enumerate([[], None, {}, {"sexual": 1}])
        ]
        work = dict(
            id="123",
            title="Test",
            userName="Artist",
            xRestrict=0,
            illustType=0,
            sl=2,
            tags=[],
        )
        requested_ids = []

        def response(path, **params):
            if path == "ranking.php":
                return {
                    "contents": [*invalid, [], good],
                    "date": "20261021",
                    "next": False,
                }
            requested_ids.append(path)
            self.assertEqual(path, "ajax/illust/123")
            return {"body": work}

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            expected = cache / "123-3840x2160.jpg"
            from PIL import Image

            Image.new("RGB", (20, 20)).save(expected)
            core.write_json(
                expected.with_suffix(".json"), {"width": 3000, "height": 2000}
            )
            with (
                patch.object(core, "WALLPAPERS", cache),
                patch.object(PixivClient, "json", side_effect=response),
                patch.object(core.time, "sleep"),
            ):
                path, selected = core.choose_wallpaper(core.DEFAULTS, (3840, 2160))
            self.assertEqual(path, expected)
            self.assertEqual(selected["id"], "123")
            self.assertEqual(requested_ids, ["ajax/illust/123"])

    def test_ranking_missing_safety_fields_is_not_accepted(self):
        unsafe = {
            "illust_id": "123",
            "width": 3840,
            "height": 2160,
            "illust_content_type": [],
        }
        with (
            patch.object(
                PixivClient,
                "json",
                return_value={"contents": [unsafe], "date": "20261021", "next": False},
            ),
            patch.object(core.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "没有找到"):
                core.choose_wallpaper(core.DEFAULTS, (1920, 1080))

    def test_mirror_routes_api_and_redirects_image_without_relaying_pixels(self):
        import io
        from unittest.mock import MagicMock

        from PIL import Image

        client = core.make_client(
            {"mirror_url": "http://localhost:8080", "proxy_mode": "off"}
        )
        self.assertEqual(client.url("ranking.php"), "http://localhost:8080/ranking.php")
        payload = io.BytesIO()
        Image.new("RGB", (2, 2)).save(payload, format="PNG")
        redirect = MagicMock()
        redirect.__enter__.return_value = redirect
        redirect.status_code = 302
        redirect.headers = {"Location": "https://i.pixiv.re/img-original/test.png"}
        image = MagicMock()
        image.__enter__.return_value = image
        image.is_redirect = False
        image.status_code = 200
        image.url = redirect.headers["Location"]
        image.iter_content.return_value = [payload.getvalue()]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                client.session, "get", side_effect=[redirect, image]
            ) as request,
        ):
            path = Path(directory) / "test.png"
            client.download("https://i.pximg.net/img-original/test.png", path)
            self.assertTrue(path.is_file())
            self.assertEqual(
                request.call_args_list[0].args[0],
                "http://localhost:8080/images/img-original/test.png",
            )
            self.assertEqual(request.call_args_list[1].args[0], image.url)
        for destination in (
            "http://i.pixiv.re/x",
            "https://evil.test/x",
            "https://i.pixiv.re:444/x",
        ):
            redirect.headers = {"Location": destination}
            with (
                patch.object(client.session, "get", return_value=redirect),
                self.assertRaises(ValueError),
            ):
                client.download(
                    "https://i.pximg.net/img-original/test.png", Path("unused.png")
                )

    def test_mirror_address_validation(self):
        for value in (
            "ftp://host",
            "https://host/path",
            "https://user:pass@host",
            "https://host?url=other",
            "https://host:bad",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                core.normalize_mirror_url(value)
        self.assertEqual(core.normalize_mirror_url(""), "")
        self.assertEqual(
            core.normalize_mirror_url("https://mirror.test/"), "https://mirror.test"
        )

    def test_cache_rechecks_original_dimensions_and_missing_metadata(self):
        from PIL import Image

        for dimensions, orientation in [
            ({"width": 2000, "height": 2500}, "landscape"),
            ({"width": 1800, "height": 1200}, "any"),
            (None, "any"),
        ]:
            with (
                self.subTest(dimensions=dimensions),
                tempfile.TemporaryDirectory() as directory,
            ):
                cached = Path(directory) / "1-1920x1080.jpg"
                Image.new("RGB", (20, 20)).save(cached)
                if dimensions:
                    core.write_json(cached.with_suffix(".json"), dimensions)

                def response(path, **params):
                    if path == "ranking.php":
                        return {
                            "date": "20261022",
                            "next": False,
                            "contents": [
                                {
                                    "illust_id": "1",
                                    "illust_type": "0",
                                    "width": 3000,
                                    "height": 2000,
                                    "illust_content_type": {"sexual": 0},
                                }
                            ],
                        }
                    if path.endswith("/pages"):
                        return {"body": []}
                    return {
                        "body": {"id": "1", "illustType": 0, "xRestrict": 0, "sl": 2}
                    }

                with (
                    patch.object(core, "WALLPAPERS", Path(directory)),
                    patch.object(core.time, "sleep"),
                    patch.object(PixivClient, "json", side_effect=response),
                    self.assertRaisesRegex(RuntimeError, "候选作品"),
                ):
                    core.choose_wallpaper(
                        core.DEFAULTS | {"orientation": orientation}, (1920, 1080)
                    )

    def test_proxy_routing(self):
        direct = core.make_client(
            {"proxy_mode": "custom", "proxy_url": "http://127.0.0.1:7890"}
        )
        self.assertFalse(direct.session.trust_env)
        self.assertEqual(direct.session.proxies["https"], "http://127.0.0.1:7890")
        custom = core.make_client(
            {"proxy_mode": "custom", "proxy_url": "http://127.0.0.1:7890"}
        )
        self.assertFalse(custom.session.trust_env)
        self.assertEqual(custom.session.proxies["https"], "http://127.0.0.1:7890")
        with patch(
            "urllib.request.getproxies",
            return_value={"https": "http://system.test:7890"},
        ):
            system = core.make_client({"proxy_mode": "system"})
            self.assertEqual(system.session.proxies["https"], "http://system.test:7890")
        self.assertFalse(core.make_client({"proxy_mode": "off"}).session.proxies)
        with self.assertRaises(ValueError):
            core.normalize_proxy_url("invalid")

    def test_presets(self):
        for expression in core.CRON_PRESETS.values():
            self.assertIsInstance(next_run(expression), datetime)


if __name__ == "__main__":
    unittest.main()
