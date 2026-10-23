import ctypes
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from croniter import croniter
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

PIXIV_BASE_URL = "https://www.pixiv.net"

RESOLUTION_PRESETS = {
    "1280x720": "1280 × 720",
    "1366x768": "1366 × 768",
    "1600x900": "1600 × 900",
    "1920x1080": "1920 × 1080",
    "1920x1200": "1920 × 1200",
    "2560x1080": "2560 × 1080",
    "2560x1440": "2560 × 1440",
    "3440x1440": "3440 × 1440",
    "3840x2160": "3840 × 2160",
    "5120x2160": "5120 × 2160",
    "7680x4320": "7680 × 4320",
}
SQUARE_RESOLUTION_PRESETS = {
    f"{size}x{size}": f"{size} × {size}"
    for size in (720, 768, 900, 1080, 1200, 1440, 2160, 4320)
}
PORTRAIT_RESOLUTION_PRESETS = {
    f"{height}x{width}": f"{height} × {width}"
    for width, height in (map(int, key.split("x")) for key in RESOLUTION_PRESETS)
}
ORIENTATION_LABELS = {"any": "无", "landscape": "横屏", "portrait": "竖屏"}
CRON_PRESETS = {
    "每 5 分钟": "*/5 * * * *",
    "每 10 分钟": "*/10 * * * *",
    "每 30 分钟": "*/30 * * * *",
    "每 1 小时": "0 * * * *",
    "每天 09:00": "0 9 * * *",
    "每周一 09:00": "0 9 * * 1",
}
RANKING_LABELS = {
    "daily": "今日",
    "weekly": "本周",
    "monthly": "本月",
    "rookie": "新人",
    "male": "受男性欢迎",
    "female": "受女性欢迎",
    "original": "原创",
    "daily_ai": "AI 生成",
}


RANKING_START_DATES = {
    "daily": "2007-09-13",
    "weekly": "2008-04-15",
    "monthly": "2008-04-15",
    "rookie": "2010-03-31",
    "male": "2011-10-12",
    "female": "2011-10-12",
    "original": "2012-05-01",
    "daily_ai": "2022-10-31",
}


def ranking_date(value):
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("榜单日期格式：YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("榜单日期格式：YYYY-MM-DD，例如 2026-10-21") from None
    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError("榜单日期格式：YYYY-MM-DD")
    return parsed.strftime("%Y%m%d")


def ranking_params(mode, date=""):
    if mode not in RANKING_LABELS:
        raise ValueError("请选择公开的全年龄排行榜")
    params = {"mode": mode, "format": "json"}
    # These are the modes that support Pixiv's illustration-only ranking endpoint.
    if mode in ("daily", "weekly", "monthly", "rookie"):
        params["content"] = "illust"
    api_date = ranking_date(date)
    if api_date:
        if date < RANKING_START_DATES[mode]:
            raise ValueError(
                "所选日期早于该榜单的起始日期：" + RANKING_START_DATES[mode]
            )
        params["date"] = api_date
    return params


PROXY_LABELS = {"system": "系统", "off": "直连", "custom": "HTTP/SOCKS5"}
DEFAULTS = {
    "mirror_url": "",
    "proxy_mode": "system",
    "proxy_url": "",
    "ranking_date": "",
    "ranking": "daily",
    "resolution": "1920x1080",
    "orientation": "any",
    "cron": "*/30 * * * *",
    "history": [],
    "queue_key": "",
    "window_geometry": "",
}


def application_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DATA = application_dir() / "data"
WALLPAPERS = DATA / "wallpapers"


def next_run(expression, base=None):
    fields = len(str(expression).split())
    seconds_first = fields == 6
    if fields not in (5, 6) or not croniter.is_valid(
        expression, second_at_beginning=seconds_first
    ):
        raise ValueError("CRON 格式：分 时 日 月 星期；或 秒 分 时 日 月 星期")
    return croniter(
        expression,
        base or datetime.now(),
        second_at_beginning=seconds_first,
    ).get_next(datetime)


def load_config():
    try:
        raw = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError, TypeError):
        raw = {}
    config = {key: raw.get(key, value) for key, value in DEFAULTS.items()}
    for key in ("last_path", "last_title", "last_url"):
        if isinstance(raw.get(key), str):
            config[key] = raw[key]
    for key in ("mirror_url", "proxy_url", "ranking_date", "cron", "queue_key"):
        if not isinstance(config[key], str):
            config[key] = DEFAULTS[key]
    config["orientation"] = (
        config["orientation"]
        if isinstance(config["orientation"], str)
        and config["orientation"] in ORIENTATION_LABELS
        else DEFAULTS["orientation"]
    )
    config["resolution"] = (
        "1080x1920" if config["orientation"] == "portrait" else "1920x1080"
    )
    if not isinstance(config["window_geometry"], str) or not re.fullmatch(
        r"\d+x\d+[+-]\d+[+-]\d+", config["window_geometry"]
    ):
        config["window_geometry"] = ""
    if (
        not isinstance(config["proxy_mode"], str)
        or config["proxy_mode"] not in PROXY_LABELS
    ):
        config["proxy_mode"] = DEFAULTS["proxy_mode"]
    try:
        resolution_size(config["resolution"])
    except ValueError:
        config["resolution"] = DEFAULTS["resolution"]
    if (
        not isinstance(config["ranking"], str)
        or config["ranking"] not in RANKING_LABELS
    ):
        config["ranking"] = DEFAULTS["ranking"]
    try:
        ranking_params(config["ranking"], config["ranking_date"])
    except ValueError:
        config["ranking_date"] = ""
    queue_key = ""
    if isinstance(config["queue_key"], str):
        queue_key = config["queue_key"]
    config["queue_key"] = queue_key
    config["history"] = (
        [
            item
            for item in config["history"]
            if isinstance(item, dict) and str(item.get("id", "")).isdigit()
        ][-500:]
        if isinstance(config["history"], list)
        else []
    )
    return config


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            import os

            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_config(config):
    persisted = dict(config)
    persisted.pop("resolution", None)
    write_json(DATA / "config.json", persisted)


def resolution_size(key):
    if (
        not isinstance(key, str)
        or re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", key) is None
    ):
        raise ValueError("分辨率格式：宽x高，例如 1920x1080；宽高须为正整数，不含空格")
    try:
        return tuple(map(int, key.split("x")))
    except ValueError:
        raise ValueError("分辨率数值过长") from None


def desktop_size():
    if sys.platform != "win32":
        return 1920, 1080
    user = ctypes.windll.user32
    return user.GetSystemMetrics(0), user.GetSystemMetrics(1)


def fit_wallpaper(image, screen):
    image = image.convert("RGB")
    if (image.width > image.height) == (screen[0] > screen[1]):
        return ImageOps.fit(image, screen, method=Image.Resampling.LANCZOS)
    background = ImageOps.fit(image, screen, method=Image.Resampling.LANCZOS)
    background = background.filter(
        ImageFilter.GaussianBlur(radius=max(12, min(screen) * 0.025))
    )
    background = ImageEnhance.Brightness(background).enhance(0.48)
    foreground = ImageOps.contain(image, screen, method=Image.Resampling.LANCZOS)
    background.paste(
        foreground,
        ((screen[0] - foreground.width) // 2, (screen[1] - foreground.height) // 2),
    )
    return background


def desktop_ok(width, height, minimum, orientation="landscape"):
    direction_ok = (
        orientation == "any"
        or (orientation == "landscape" and width > height)
        or (orientation == "portrait" and height > width)
    )
    return width >= minimum[0] and height >= minimum[1] and direction_ok


def work_tags(work):
    tags = work.get("tags", []) if isinstance(work, dict) else []
    if isinstance(tags, dict):
        tags = tags.get("tags", [])
    if not isinstance(tags, list):
        return []
    return [
        str(item.get("tag", "") if isinstance(item, dict) else item) for item in tags
    ]


def safe_work(work):
    if not isinstance(work, dict):
        return False
    tags = [tag.casefold() for tag in work_tags(work)]
    return (
        "xRestrict" in work
        and "illustType" in work
        and "sl" in work
        and work.get("xRestrict") == 0
        and work.get("restrict", 0) == 0
        and work.get("illustType") == 0
        and work.get("sl") in (0, 1, 2)
        and not any(
            "r-18" in tag
            or "r18" in tag
            or tag in ("裸", "裸体", "ヌード", "nude", "nsfw")
            for tag in tags
        )
    )


class SourceUnavailable(Exception):
    pass


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def wait_cancel(seconds, cancel):
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise Cancelled()


def normalize_mirror_url(value):
    value = str(value).strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
        or parsed.port == 0
    ):
        raise ValueError("镜像地址须为 HTTP/HTTPS 服务地址，不含路径或认证信息")
    return value


class PixivClient:
    """Access Pixiv metadata and original images using one network proxy."""

    def __init__(self, proxy_mode="off", proxy_url="", mirror_url=""):
        self.cancel = None
        self.mirror_url = normalize_mirror_url(mirror_url)
        self.base_url = self.mirror_url or PIXIV_BASE_URL
        self.session = requests.Session()
        self.session.trust_env = False
        if proxy_mode == "system":
            from urllib.request import getproxies

            self.session.proxies.update(getproxies())
        if proxy_mode == "custom":
            proxy_url = normalize_proxy_url(proxy_url)
            self.session.proxies.update(http=proxy_url, https=proxy_url)
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.pixiv.net/",
            }
        )

    def url(self, path):
        return urljoin(self.base_url + "/", str(path).lstrip("/"))

    def json(self, path, **params):
        check_cancel(self.cancel)
        started = time.monotonic()
        try:
            with self.session.get(
                self.url(path), params=params, timeout=(10, 25), allow_redirects=False
            ) as response:
                logging.info(
                    "API response path=%s page=%s date=%s status=%s elapsed=%.2fs",
                    path,
                    params.get("p", ""),
                    params.get("date", "latest"),
                    response.status_code,
                    time.monotonic() - started,
                )
                if response.is_redirect:
                    raise RuntimeError("数据源要求跳转，已拒绝继续请求")
                if response.status_code in (403, 429):
                    raise SourceUnavailable(
                        f"Pixiv 拒绝访问或限流（HTTP {response.status_code}）"
                    )
                response.raise_for_status()
                check_cancel(self.cancel)
                result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Pixiv 返回了无效 JSON 对象")
            if result.get("error"):
                raise RuntimeError(result.get("message") or "数据源拒绝访问")
            return result
        finally:
            logging.info(
                "API request finished path=%s elapsed=%.2fs",
                path,
                time.monotonic() - started,
            )

    def download(self, value, target):
        check_cancel(self.cancel)
        url = value
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        allowed = {"i.pximg.net"}
        if parsed.scheme != "https" or host not in allowed:
            raise ValueError("图片地址不属于当前 HTTPS 数据源")
        started = time.monotonic()
        size = 0
        logging.info("Image request started host=%s path=%s", host, parsed.path)
        if self.mirror_url:
            redirect_started = time.monotonic()
            with self.session.get(
                self.mirror_url + "/images" + parsed.path,
                timeout=(10, 25),
                allow_redirects=False,
                stream=True,
            ) as redirect:
                if redirect.status_code != 302:
                    raise RuntimeError("镜像图片入口未返回 302")
                url = redirect.headers.get("Location", "")
                destination = urlparse(url)
                if (
                    destination.scheme != "https"
                    or destination.hostname != "i.pixiv.re"
                    or destination.username
                    or destination.password
                    or destination.port not in (None, 443)
                ):
                    raise ValueError("镜像图片跳转目标不是 https://i.pixiv.re")
                allowed = {"i.pixiv.re"}
            logging.info(
                "Image redirect accepted elapsed=%.2fs destination=i.pixiv.re",
                time.monotonic() - redirect_started,
            )
            check_cancel(self.cancel)
        temp = target.with_suffix(".part")
        try:
            with self.session.get(
                url, stream=True, timeout=(10, 30), allow_redirects=False
            ) as response:
                if response.is_redirect:
                    raise RuntimeError("Pixiv 图片服务器要求跳转，已拒绝继续请求")
                if (urlparse(response.url).hostname or "").casefold() not in allowed:
                    raise RuntimeError(
                        "Pixiv 图片服务器跳转到非 Pixiv 主机，已拒绝下载"
                    )
                if response.status_code in (403, 429):
                    raise SourceUnavailable(
                        f"Pixiv 图片服务器拒绝访问或限流（HTTP {response.status_code}）"
                    )
                response.raise_for_status()
                size = 0
                with temp.open("wb") as stream:
                    for chunk in response.iter_content(65536):
                        check_cancel(self.cancel)
                        size += len(chunk)
                        if size > 50 * 1024 * 1024:
                            raise ValueError("图片超过 50 MB")
                        stream.write(chunk)
            with Image.open(temp) as image:
                image.verify()
            temp.replace(target)
            logging.info(
                "Image download complete host=%s bytes=%d elapsed=%.2fs",
                urlparse(url).hostname,
                size,
                time.monotonic() - started,
            )
        finally:
            logging.info(
                "Image request finished host=%s bytes=%d elapsed=%.2fs",
                urlparse(url).hostname,
                size,
                time.monotonic() - started,
            )
            temp.unlink(missing_ok=True)


def normalize_proxy_url(value):
    value = str(value or "").strip()
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        raise ValueError("代理端口无效") from None
    if (
        parsed.scheme not in ("http", "https", "socks5", "socks5h")
        or not parsed.hostname
        or not port
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("代理格式：http://127.0.0.1:7890 或 socks5h://127.0.0.1:1080")
    return value


def make_client(config):
    return PixivClient(
        proxy_mode=config.get("proxy_mode", "system"),
        proxy_url=config.get("proxy_url", ""),
        mirror_url=config.get("mirror_url", ""),
    )


def _candidate_dimensions(item):
    try:
        return int(item["width"]), int(item["height"])
    except (KeyError, TypeError, ValueError):
        return 0, 0


def _cache_path(work_id, screen):
    return WALLPAPERS / f"{work_id}-{screen[0]}x{screen[1]}.jpg"


def choose_wallpaper(config, screen, status=lambda text: None, cancel=None):
    client = make_client(config)
    started = time.monotonic()
    try:
        return _choose_wallpaper(client, config, screen, status, cancel)
    finally:
        logging.info(
            "Wallpaper selection finished ranking=%s date=%s elapsed=%.2fs",
            config.get("ranking"),
            config.get("ranking_date") or "latest",
            time.monotonic() - started,
        )
        client.session.close()


def ranking_period_label(mode, ending=""):
    if not ending:
        return "保持最新"
    ranking_date(ending)
    end = datetime.strptime(ending, "%Y-%m-%d").date()
    days = (
        30
        if mode == "monthly"
        else 7
        if mode in ("weekly", "rookie", "original")
        else 1
    )
    if days == 1:
        return end.isoformat()
    return (end - timedelta(days=days - 1)).isoformat() + " ～ " + end.isoformat()


def _choose_wallpaper(client, config, screen, status, cancel):
    # Use the selected source threshold even on a higher-resolution monitor.
    minimum = resolution_size(config.get("resolution", "1920x1080"))
    mode = config.get("ranking", "daily")
    params = ranking_params(mode, config.get("ranking_date", ""))
    check_cancel(cancel)
    client.cancel = cancel
    queue_key = ""
    candidates = {}
    status("正在读取排行榜：" + RANKING_LABELS[mode])
    seen_pages = set()
    for page in count(1):
        check_cancel(cancel)
        status(f"正在读取排行榜第 {page} 页")
        try:
            listing = client.json("ranking.php", **params, p=page)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                if page > 1:
                    break
                raise RuntimeError(
                    "该日期的榜单尚未发布或不可访问，请选择其他日期或保持最新"
                ) from error
            raise
        if page == 1:
            published = str(listing.get("date", ""))
            if "date" in params and published != params["date"]:
                raise ValueError("返回的榜单日期与所选日期不符，未使用其他日期替代")
            if len(published) == 8 and published.isdigit():
                params["date"] = published
            if len(published) != 8 or not published.isdigit():
                raise ValueError("Pixiv 返回的榜单发布日期无效")
            effective_date = f"{published[:4]}-{published[4:6]}-{published[6:]}"
            ranking_params(mode, effective_date)
            queue_key = mode + ":" + effective_date
            period = str(listing.get("date_range_text") or published or "保持最新")
            status("榜单时间：" + RANKING_LABELS[mode] + "/" + period)
        items = listing.get("contents", [])
        if not isinstance(items, list):
            raise ValueError("排行榜作品列表格式无效")
        if not items:
            break
        if page > 1 and str(listing.get("date", "")) != params["date"]:
            raise ValueError("排行榜分页日期不一致")
        fingerprint = hashlib.sha256(
            json.dumps(items, sort_keys=True).encode()
        ).hexdigest()
        if fingerprint in seen_pages:
            raise ValueError("上游重复返回同一页排行榜，已停止翻页")
        seen_pages.add(fingerprint)
        for item in items:
            if not isinstance(item, dict):
                continue
            width, height = _candidate_dimensions(item)
            content_type = item.get("illust_content_type")
            if (
                str(item.get("illust_type", "")) == "0"
                and isinstance(content_type, dict)
                and content_type.get("sexual") == 0
                and desktop_ok(
                    width, height, minimum, config.get("orientation", "landscape")
                )
            ):
                ident = str(item.get("illust_id", ""))
                if ident.isdigit():
                    candidates[ident] = item
        if listing.get("next") is False:
            break
        wait_cancel(0.35, cancel)

    logging.info(
        "Ranking traversal complete mode=%s date=%s pages=%d candidates=%d",
        mode,
        params.get("date"),
        page,
        len(candidates),
    )
    if not candidates:
        raise RuntimeError(
            "没有找到符合方向和最低分辨率要求的插画，可尝试其他榜单或较低规格。"
        )

    identifiers = list(candidates)
    current_id = (
        urlparse(str(config.get("last_url", ""))).path.rstrip("/").rsplit("/", 1)[-1]
        if config.get("queue_key") == queue_key
        else ""
    )
    start = identifiers.index(current_id) + 1 if current_id in identifiers else 0
    identifiers = identifiers[start:] + identifiers[:start]
    status(f"找到 {len(identifiers)} 个候选，按榜单顺序循环查找")

    for index, work_id in enumerate(identifiers, 1):
        check_cancel(cancel)
        status(f"正在检查作品 {index}/{len(identifiers)}：{work_id}")
        try:
            work = client.json("ajax/illust/" + work_id).get("body", {})
            if not safe_work(work):
                continue
            cached = _cache_path(work_id, screen)
            if cached.exists():
                try:
                    dimensions = json.loads(
                        cached.with_suffix(".json").read_text(encoding="utf-8")
                    )
                    if not desktop_ok(
                        *_candidate_dimensions(dimensions),
                        minimum,
                        config.get("orientation", "landscape"),
                    ):
                        raise ValueError("缓存原图不符合当前筛选条件")
                    with Image.open(cached) as image:
                        image.verify()
                except (OSError, ValueError):
                    cached.unlink(missing_ok=True)
                else:
                    status("使用已缓存壁纸：" + cached.name)
                    check_cancel(cancel)
                    work["_queue_key"] = queue_key
                    return cached, work
            pages = client.json("ajax/illust/" + work_id + "/pages").get("body", [])
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code in (403, 429):
                raise
            logging.warning(
                "Artwork metadata request failed: id=%s error=%s",
                work_id,
                type(error).__name__,
            )
            continue
        except (
            requests.RequestException,
            RuntimeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            logging.warning(
                "Artwork metadata skipped: id=%s error=%s",
                work_id,
                type(error).__name__,
            )
            continue

        for page in pages:
            try:
                width, height = _candidate_dimensions(page)
                if not desktop_ok(
                    width, height, minimum, config.get("orientation", "landscape")
                ):
                    continue
                WALLPAPERS.mkdir(parents=True, exist_ok=True)
                cached = _cache_path(work_id, screen)
                original = DATA / f".download-{work_id}.image"
                status("正在下载原图：" + work_id)
                client.download(page["urls"]["original"], original)
                status("正在处理图片：" + work_id)
                processing_started = time.monotonic()
                try:
                    with Image.open(original) as image:
                        image = ImageOps.exif_transpose(image)
                        if not desktop_ok(
                            image.width,
                            image.height,
                            minimum,
                            config.get("orientation", "landscape"),
                        ):
                            continue
                        fitted = fit_wallpaper(image, screen)
                        temporary = cached.with_suffix(".tmp")
                        try:
                            fitted.save(
                                temporary, format="JPEG", quality=92, optimize=True
                            )
                            check_cancel(cancel)
                            temporary.replace(cached)
                            write_json(
                                cached.with_suffix(".json"),
                                {"width": image.width, "height": image.height},
                            )
                        finally:
                            temporary.unlink(missing_ok=True)
                finally:
                    logging.info(
                        "Image processing finished id=%s elapsed=%.2fs",
                        work_id,
                        time.monotonic() - processing_started,
                    )
                    original.unlink(missing_ok=True)
                work["_queue_key"] = queue_key
                return cached, work
            except requests.HTTPError as error:
                if error.response is not None and error.response.status_code in (
                    403,
                    429,
                ):
                    raise
                continue
            except (
                KeyError,
                TypeError,
                ValueError,
                OSError,
                requests.RequestException,
            ) as error:
                logging.warning(
                    "Artwork image skipped: id=%s error=%s",
                    work_id,
                    type(error).__name__,
                )
                continue
        wait_cancel(0.25, cancel)

    raise RuntimeError("候选作品都无法下载或不满足尺寸要求；原壁纸保持不变。")


def set_wallpaper(path):
    if sys.platform != "win32":
        raise RuntimeError("更换桌面壁纸仅支持 Windows")
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Control Panel\Desktop",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, "10")
        winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, "0")
    if not ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path.resolve()), 3):
        raise ctypes.WinError()


def startup(enabled):
    import winreg

    command = f'"{sys.executable}"'
    if not getattr(sys, "frozen", False):
        command += f' "{Path(__file__).with_name("app.py").resolve()}"'
    command += " --hidden"
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
    ) as key:
        if enabled:
            winreg.SetValueEx(key, "PixivWallpaper", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "PixivWallpaper")
            except FileNotFoundError:
                pass


def startup_enabled():
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        ) as key:
            winreg.QueryValueEx(key, "PixivWallpaper")
        return True
    except FileNotFoundError:
        return False
