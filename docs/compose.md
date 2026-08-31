# 通用 Docker Compose 部署教程

## 1. 支持条件

部署前确认：

1. NAS 可以运行 Docker 或厂商的“容器/Container”应用。
2. CPU 架构为 `x86_64/amd64` 或 `aarch64/arm64`。
3. NAS 能访问 GitHub Container Registry 和配置中的直播源。
4. 局域网没有占用 TCP `18780` 端口。

查看架构：

```bash
uname -m
docker version
docker compose version
```

## 2. 准备目录

```bash
mkdir -p /你的Docker目录/lumina-live/config
mkdir -p /你的Docker目录/lumina-live/data
cd /你的Docker目录/lumina-live
```

下载项目：

```bash
git clone https://github.com/TomShen-simple/LuminaLive-NAS.git .
cp .env.example .env
```

没有 Git 时，也可以从 GitHub 的 Releases 下载源码压缩包并解压。

## 3. 配置 `.env`

普通命令行部署可以保持相对路径：

```dotenv
BIND_IP=0.0.0.0
HOST_PORT=18780
CONFIG_DIR=./config
DATA_DIR=./data
TZ=Asia/Shanghai
```

图形化 NAS 面板如果不支持相对路径，应换成面板显示的绝对路径：

```dotenv
CONFIG_DIR=/volume1/docker/lumina-live/config
DATA_DIR=/volume1/docker/lumina-live/data
```

不要照抄上面的 `/volume1`；它只是群晖常见示例。

## 4. 启动

使用预构建的多架构镜像：

```bash
docker compose pull
docker compose up -d
```

若所在网络访问 Docker Hub 更稳定，可在 `.env` 使用镜像：

```dotenv
IMAGE_NAME=wst2946437060/lumina-live-nas:latest
```

若 GHCR 无法访问，可本地构建：

```bash
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

## 5. 验证

```bash
docker compose ps
docker compose logs --tail=200 lumina-live
curl -fsS http://127.0.0.1:18780/healthz
curl -fsS http://127.0.0.1:18780/status.json
```

首次扫描期间 `/healthz` 返回 503 属于正常现象。完成后会变为：

```json
{"ok": true, "playlistReady": true}
```

把以下地址加入 APTV、TiviMate、VLC、Kodi 等播放器：

```text
http://NAS局域网IP:18780/live/yangshi.m3u
```

直接访问 `http://NAS局域网IP:18780/` 是 Web 管理后台，可以查看健康度、频道验证延迟、离线频道和上游主机，并维护自定义 M3U。写操作默认限制在局域网；如需反向代理管理，请设置 `ADMIN_TOKEN`。

## 6. 资源与权限

默认限制为 2 CPU、1 GB 内存。较弱 NAS 可以调整：

```dotenv
CHANNEL_WORKERS=8
CPU_LIMIT=1.0
MEM_LIMIT=512m
```

扫描会短时间占用 CPU 和网络，但正常播放是客户端直连上游，不会持续经过 NAS。

若容器无法写入数据目录：

```bash
ls -ld config data
chmod -R u+rwX config data
```

优先用 NAS 的共享文件夹权限界面授权，不建议无条件 `chmod -R 777`。

## 7. 自定义源

把自己的 M3U 保存为 `config/local.m3u`。其频道名需要和 `config/channels.json` 中的名称或别名对应。

使用局域网 IPTV 地址时：

```dotenv
ALLOW_PRIVATE_UPSTREAMS=true
```

修改后重启，或者等待下一个检测周期：

```bash
docker compose restart lumina-live
```

## 8. 反向代理与公网

本项目默认按局域网服务设计。必须远程使用时，优先通过家庭 VPN（Tailscale、WireGuard）访问。若使用 HTTPS 反向代理，至少启用身份认证和访问限制；不要直接将 `18780` 映射到公网。
