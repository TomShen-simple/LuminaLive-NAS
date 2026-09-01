# LuminaLive NAS 通用 Docker Compose 部署教程

这份教程适用于支持标准 Docker Compose、CPU 架构为 `amd64` 或 `arm64` 的 NAS，包括极空间、群晖、威联通、飞牛、绿联、铁威马、Unraid、TrueNAS SCALE、CasaOS、OpenMediaVault 和普通 Linux 主机。

项目使用公开镜像：

```text
s1mpleboy/lumina-live-nas:latest
```

播放时客户端直接连接通过验证的上游，视频流量不会持续绕行 NAS。NAS 主要负责读取、检测和生成 M3U。

## 一、部署前确认

必须满足：

1. NAS 已安装 Docker、Container Manager、Container Station 或同类容器应用。
2. CPU 是 `x86_64/amd64` 或 `aarch64/arm64`。
3. NAS 可以访问 Docker Hub 和你使用的 M3U/HLS 上游。
4. TCP `18780` 未被其他程序占用。
5. 准备两个可写目录，分别保存配置和运行数据。

SSH/终端用户可以检查：

```bash
uname -m
docker version
docker compose version
```

看到 `x86_64`、`amd64`、`aarch64` 或 `arm64` 即可。32 位 ARMv7/ARMv6 不支持。

## 二、推荐目录结构

在 NAS 的 Docker 数据目录中新建：

```text
lumina-live/
├── compose.yaml
├── .env                 # 可选
├── config/              # 必须持久化
└── data/                # 必须持久化
```

命令行示例：

```bash
mkdir -p /你的Docker目录/lumina-live/config
mkdir -p /你的Docker目录/lumina-live/data
cd /你的Docker目录/lumina-live
```

不要照抄别人的 `/volume1`、`/mnt/pool` 或极空间存储池编号。每台 NAS 的真实路径可能不同。

## 三、可直接使用的 `compose.yaml`

仓库根目录的 [compose.yaml](../compose.yaml) 不依赖 `.env` 也能直接运行。只使用图形化 NAS 面板时，可以新建 `compose.yaml` 并粘贴以下内容：

```yaml
services:
  lumina-live:
    image: ${IMAGE_NAME:-s1mpleboy/lumina-live-nas:latest}
    container_name: ${CONTAINER_NAME:-lumina-live}
    restart: unless-stopped
    ports:
      - "${BIND_IP:-0.0.0.0}:${HOST_PORT:-18780}:8780"
    environment:
      TZ: "${TZ:-Asia/Shanghai}"
      REFRESH_INTERVAL: "${REFRESH_INTERVAL:-1800}"
      STARTUP_DELAY: "${STARTUP_DELAY:-3}"
      HTTP_TIMEOUT: "${HTTP_TIMEOUT:-10}"
      CHANNEL_WORKERS: "${CHANNEL_WORKERS:-24}"
      MAX_CANDIDATES_PER_CHANNEL: "${MAX_CANDIDATES_PER_CHANNEL:-8}"
      MAX_BANDWIDTH: "${MAX_BANDWIDTH:-10000000}"
      ALLOW_PRIVATE_UPSTREAMS: "${ALLOW_PRIVATE_UPSTREAMS:-false}"
      EXTRA_M3U_URLS: "${EXTRA_M3U_URLS:-}"
      UPSTREAM_USER_AGENT: "${UPSTREAM_USER_AGENT:-Mozilla/5.0 (Linux; Android 14; TV) AppleWebKit/537.36 Chrome/126.0 Safari/537.36}"
      LOG_LEVEL: "${LOG_LEVEL:-INFO}"
      ADMIN_TOKEN: "${ADMIN_TOKEN:-}"
    volumes:
      - ${CONFIG_DIR:-./config}:/config
      - ${DATA_DIR:-./data}:/data
    read_only: true
    tmpfs:
      - /tmp:size=128m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: ${PIDS_LIMIT:-256}
    mem_limit: ${MEM_LIMIT:-1g}
    cpus: ${CPU_LIMIT:-2.0}
    stop_grace_period: 30s
    logging:
      driver: json-file
      options:
        max-size: "${LOG_MAX_SIZE:-10m}"
        max-file: "${LOG_MAX_FILE:-3}"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8780/healthz"]
      interval: 30s
      timeout: 8s
      retries: 3
      start_period: ${HEALTH_START_PERIOD:-3m}
```

这个文件默认会：

- 自动选择当前 NAS 的 amd64/arm64 镜像。
- 把宿主机 `18780` 映射到容器 `8780`。
- 把当前项目下的 `config`、`data` 持久化。
- 限制日志大小，避免日志无限占满 NAS。
- 去除多余 Linux 权限并启用只读根文件系统。
- 首次扫描预留 3 分钟健康检查启动窗口。

## 四、可选 `.env` 配置

只使用默认值时，可以不创建 `.env`。需要改端口、绝对路径、性能参数或管理令牌时，将 [.env.example](../.env.example) 复制为 `.env`：

```bash
cp .env.example .env
```

常用配置：

```dotenv
IMAGE_NAME=s1mpleboy/lumina-live-nas:latest
CONTAINER_NAME=lumina-live
BIND_IP=0.0.0.0
HOST_PORT=18780
TZ=Asia/Shanghai

CONFIG_DIR=./config
DATA_DIR=./data

REFRESH_INTERVAL=1800
HTTP_TIMEOUT=10
CHANNEL_WORKERS=24
MAX_CANDIDATES_PER_CHANNEL=8

ALLOW_PRIVATE_UPSTREAMS=false
EXTRA_M3U_URLS=
ADMIN_TOKEN=
```

关键变量说明：

| 变量 | 默认值 | 用途 |
|---|---:|---|
| `HOST_PORT` | `18780` | NAS 对外访问端口 |
| `CONFIG_DIR` | `./config` | 频道配置、本地 M3U |
| `DATA_DIR` | `./data` | 生成列表、状态和备份 |
| `REFRESH_INTERVAL` | `1800` | 重新检测间隔，单位秒，最小 300 |
| `CHANNEL_WORKERS` | `24` | 并发检测数；弱性能 NAS 建议 6～12 |
| `HTTP_TIMEOUT` | `10` | 单次上游请求超时，单位秒 |
| `ALLOW_PRIVATE_UPSTREAMS` | `false` | 是否允许访问局域网/私网 IPTV 源 |
| `EXTRA_M3U_URLS` | 空 | 额外远程 M3U，多个地址用英文逗号分隔 |
| `ADMIN_TOKEN` | 空 | 反向代理或远程管理时使用的管理令牌 |
| `MEM_LIMIT` | `1g` | 容器内存上限 |
| `CPU_LIMIT` | `2.0` | 容器 CPU 上限 |

图形化 NAS 面板如果不能正确处理相对路径，把目录改成真实绝对路径：

```dotenv
CONFIG_DIR=/真实路径/lumina-live/config
DATA_DIR=/真实路径/lumina-live/data
```

## 五、命令行部署

### 方法 A：克隆完整项目

```bash
git clone https://github.com/TomShen-simple/LuminaLive-NAS.git
cd LuminaLive-NAS
cp .env.example .env
mkdir -p config data
docker compose pull
docker compose up -d
```

### 方法 B：只使用 Compose 文件

创建目录并保存上面的 `compose.yaml`：

```bash
mkdir -p lumina-live/config lumina-live/data
cd lumina-live
docker compose pull
docker compose up -d
```

Compose 的相对目录以 `compose.yaml` 所在目录为基准。

## 六、NAS 图形界面通用部署

不同厂商名称不同，但操作逻辑一致：

1. 在文件管理器新建 `lumina-live/config` 和 `lumina-live/data`。
2. 打开 Docker/容器应用的“项目”“Compose”或“应用栈”。
3. 新建项目，名称填写 `lumina-live`。
4. 上传或粘贴 `compose.yaml`。
5. 项目工作目录选择 `lumina-live` 文件夹。
6. 如果面板支持 `.env`，可同时上传；不上传则使用 Compose 默认值。
7. 点击“创建”“部署”或“启动”。
8. 等待镜像拉取和首次频道扫描完成。

如果界面要求逐项填写卷映射：

| NAS 宿主机目录 | 容器目录 | 权限 |
|---|---|---|
| `lumina-live/config` | `/config` | 读写 |
| `lumina-live/data` | `/data` | 读写 |

端口映射：

| NAS 端口 | 容器端口 | 协议 |
|---:|---:|---|
| `18780` | `8780` | TCP |

## 七、确认部署成功

命令行检查：

```bash
docker compose ps
docker compose logs --tail=200 lumina-live
curl -i http://127.0.0.1:18780/healthz
```

首次启动会检测真实 HLS 分片，通常需要 1～5 分钟。在此期间健康接口返回 `503`、容器显示 `starting` 属于正常现象。完成后会返回类似：

```json
{"ok": true, "playlistReady": true}
```

浏览器检查：

```text
Web 管理后台：http://NAS局域网IP:18780/
健康检查：http://NAS局域网IP:18780/healthz
状态数据：http://NAS局域网IP:18780/status.json
合并订阅：http://NAS局域网IP:18780/live/yangshi.m3u
```

央视、卫视和通过匹配的自定义频道都在同一个 `yangshi.m3u` 中。

## 八、在 APTV 等播放器中添加

以 NAS 地址 `192.168.1.20` 为例，在 APTV 中新增“远程 M3U/直播源”，填写：

```text
http://192.168.1.20:18780/live/yangshi.m3u
```

建议：

1. NAS 在路由器中设置固定局域网 IP。
2. 播放器刷新间隔设置为 30～60 分钟。
3. 迁移端口后同步修改播放器地址。
4. 如果播放器缓存旧列表，删除旧订阅后重新添加，或临时使用 `?v=时间戳`。

## 九、Web 管理与自定义 M3U

打开：

```text
http://NAS局域网IP:18780/
```

后台可以：

- 查看频道在线率、检测延迟和上游主机。
- 查看离线频道及检测结果。
- 增删额外远程 M3U。
- 可视化维护 `config/local.m3u`。
- 保存后立即触发重新检测。

本地文件也可以手工创建：

```text
config/local.m3u
```

示例：

```m3u
#EXTM3U
#EXTINF:-1 tvg-name="东方卫视" group-title="卫视",东方卫视
https://example.com/dragon-tv/index.m3u8
```

若使用 `192.168.x.x`、`10.x.x.x` 等可信局域网 IPTV 地址，需要：

```dotenv
ALLOW_PRIVATE_UPSTREAMS=true
```

不要对不可信远程 M3U 开启该选项。

## 十、更新镜像

```bash
cd /你的Docker目录/lumina-live
docker compose pull
docker compose up -d --remove-orphans
docker image prune -f
```

图形界面中选择“拉取最新镜像”后“重新创建/重新部署”。不要删除 `config`、`data` 目录。

确认更新：

```bash
docker inspect lumina-live --format '{{.Config.Image}}'
docker compose ps
```

## 十一、备份、恢复和迁移 NAS

需要备份：

```text
compose.yaml
.env
config/
data/
```

停止容器后打包最稳妥：

```bash
docker compose down
tar -czf lumina-live-backup.tar.gz compose.yaml .env config data
docker compose up -d
```

迁移到新 NAS：

1. 复制整个 `lumina-live` 文件夹。
2. 根据新 NAS 修改 `.env` 中的绝对路径。
3. 执行 `docker compose pull && docker compose up -d`。
4. 保持 NAS IP 和端口不变，播放器通常无需修改。

## 十二、停止和卸载

停止但保留全部数据：

```bash
docker compose down
```

卸载容器和镜像：

```bash
docker compose down
docker image rm s1mpleboy/lumina-live-nas:latest
```

以上命令不会删除宿主机的 `config`、`data`。只有确认不再需要配置、列表和备份后，才手工删除项目目录。

## 十三、安全建议

- 默认只在家庭局域网访问，不要把 `18780` 直接映射到公网。
- 远程访问优先使用 Tailscale、WireGuard 等家庭 VPN。
- 经过反向代理开放管理后台时，设置足够长且随机的 `ADMIN_TOKEN`。
- `config` 和 `data` 只授予 Docker 服务所需的读写权限，不建议 `chmod -R 777`。
- 自定义 M3U 和频道内容必须由使用者确认拥有合法使用权。

## 十四、快速故障定位

### 镜像拉取失败

```bash
docker pull s1mpleboy/lumina-live-nas:latest
```

若仍失败，检查 NAS DNS、Docker Hub 连通性和系统时间。

### 端口冲突

在 `.env` 修改：

```dotenv
HOST_PORT=18781
```

重新部署后，访问和订阅地址都改用 `18781`。

### 一直 `unhealthy`

```bash
docker compose logs --tail=300 lumina-live
```

重点检查 NAS 是否能访问远程 M3U、HLS 域名以及系统 DNS。频道上游全部失效时，服务不会生成空列表覆盖上一次有效结果。

### 容器没有目录写入权限

通过 NAS 文件管理器给 Docker 服务账号授予 `config`、`data` 读写权限。优先使用厂商权限界面，不要直接开放整个存储池。

### 低性能 NAS 扫描占用高

```dotenv
CHANNEL_WORKERS=8
CPU_LIMIT=1.0
MEM_LIMIT=512m
```

更完整的问题说明见 [故障排查](troubleshooting.md)。
