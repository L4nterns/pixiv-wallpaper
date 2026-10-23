# Pixiv 镜像服务

在本目录执行：

```sh
docker compose up -d
```

容器使用只读根文件系统，Nginx 的请求临时文件位于 `/var/cache/nginx` 的内存 `tmpfs`，不持久保存。配置文件从当前 Compose 目录挂载。

默认仅监听宿主机 `127.0.0.1:8080`。同机客户端选择“自定义”，地址填 `http://127.0.0.1:8080`。
远程客户端建议在宿主机现有 HTTPS 反向代理后部署，将域名转发至此端口，客户端填写 `https://你的域名`（不带路径）。
若需直接开放局域网端口：

```sh
MIRROR_BIND=0.0.0.0 MIRROR_PORT=8080 docker compose up -d
```

公网不要直接暴露明文 HTTP；请配置 HTTPS、访问控制和防火墙。服务无内置账号认证，不提供任意 URL 转发；只允许 GET/HEAD 排行榜、作品详情和分页 API。宿主机必须能够访问 Pixiv。

- `/health`：本地存活检查，不保证上游可用。
- `/ranking.php`、`/ajax/illust/{id}`、`/ajax/illust/{id}/pages`：固定转发 Pixiv API，移除 Cookie 和 Authorization，不使用登录态。
- `/images/img-original/...` 和 `/images/img-master/...`：302 到 `https://i.pixiv.re/...`，服务器不下载或中转图片内容。

API 返回的原始图片链接不重写；桌面客户端在启用镜像时将图片请求转为服务的 `/images/` 路径，再验证并跟随 302。客户端处理后的壁纸缓存命中时不发起图片下载。客户端必须能访问 i.pixiv.re，该第三方服务的可用性和限流不受本项目控制。API 的访问限制同样可能导致镜像请求失败。

```sh
curl -i http://127.0.0.1:8080/health
curl -i 'http://127.0.0.1:8080/ranking.php?mode=daily&content=illust&format=json&p=1'
curl -I http://127.0.0.1:8080/images/img-original/img/2026/test_p0.jpg
docker compose logs --tail=100
```

最后一项图片路径仅用于检查 302 响应，不是真实作品。日志轮转限制为每份 10MB、保留三份。

中转服务不缓存 Pixiv API 或图片数据，每个 API 请求都会实时转发上游，行为与 Pixiv 直连一致。访问日志记录总请求耗时和上游响应耗时。客户端仅缓存已处理的壁纸图片。图片入口仍只返回 302，不中转图片内容。
