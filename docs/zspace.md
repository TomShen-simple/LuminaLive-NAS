# 极空间 ZOS Docker Compose 完整部署教程

本教程使用公开镜像 `s1mpleboy/lumina-live-nas:latest`。不需要在极空间本地编译，也不需要配置 VPS、域名或公网端口。

## 一、准备目录和文件

1. 打开极空间「文件管理」。
2. 在用于 Docker 数据的共享文件夹中新建 `lumina-live`。
3. 在 `lumina-live` 中新建 `config` 和 `data`。
4. 下载项目中的 `compose.yaml`。
5. 可选：下载 `.env.example`，改名为 `.env`。

最终目录：

```text
lumina-live/
├── compose.yaml
├── .env                 # 可选
├── config/
└── data/
```

不创建 `.env` 也可以部署，Compose 会使用默认端口 `18780`、相对目录和 Docker Hub 镜像。

## 二、在极空间创建 Compose 项目

不同 ZOS 版本的按钮文字可能略有差异，基本步骤如下：

1. 打开极空间「Docker」。
2. 进入「Compose」或「项目」。
3. 点击「新建项目」。
4. 项目名称填写 `lumina-live`。
5. “存储位置/项目目录”选择刚创建的 `lumina-live` 文件夹。
6. 选择“本地导入”上传 `compose.yaml`，或将其内容粘贴到编辑器。
7. 如果界面支持环境变量文件，上传 `.env`；没有该选项也没关系。
8. 确认镜像为 `s1mpleboy/lumina-live-nas:latest`。
9. 点击「创建」「部署」或「启动」。

首次会下载多架构镜像并检测频道，一般等待 1～5 分钟。

## 三、极空间可直接粘贴的 Compose

如果不想上传文件，可在 Compose 编辑器粘贴：

```yaml
services:
  lumina-live:
    image: s1mpleboy/lumina-live-nas:latest
    container_name: lumina-live
    restart: unless-stopped
    ports:
      - "18780:8780"
    environment:
      TZ: Asia/Shanghai
      REFRESH_INTERVAL: "1800"
      HTTP_TIMEOUT: "10"
      CHANNEL_WORKERS: "24"
      MAX_CANDIDATES_PER_CHANNEL: "8"
      ALLOW_PRIVATE_UPSTREAMS: "false"
      LOG_LEVEL: INFO
    volumes:
      - ./config:/config
      - ./data:/data
    read_only: true
    tmpfs:
      - /tmp:size=128m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 256
    mem_limit: 1g
    cpus: 2.0
    stop_grace_period: 30s
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8780/healthz"]
      interval: 30s
      timeout: 8s
      retries: 3
      start_period: 3m
```

这份极空间版使用固定默认值，适合只粘贴一个文件的场景。

## 四、处理极空间目录路径

新版 ZOS 的 Compose 项目设置了“存储位置”后，一般可以直接使用：

```yaml
- ./config:/config
- ./data:/data
```

如果系统提示相对路径无效：

1. 在 Compose 或卷映射页面点击「查询路径」「选择文件夹」。
2. 分别选择 `config`、`data`。
3. 复制极空间返回的完整路径。
4. 把 Compose 中左侧路径替换为真实路径：

```yaml
volumes:
  - /极空间返回的完整路径/lumina-live/config:/config
  - /极空间返回的完整路径/lumina-live/data:/data
```

不要复制其他用户教程里的存储池编号。

## 五、确认容器状态

在「Docker → 容器 → lumina-live」中检查：

- 容器状态为“运行中”。
- 首次扫描期间健康状态可能是 `starting`。
- 扫描完成后健康状态为 `healthy`。
- 日志没有持续重复退出或权限错误。

浏览器访问：

```text
http://极空间局域网IP:18780/
http://极空间局域网IP:18780/healthz
http://极空间局域网IP:18780/status.json
```

例如极空间 IP 为 `192.168.1.20`：

```text
http://192.168.1.20:18780/
```

## 六、在 APTV 添加订阅

在 APTV 中新增远程 M3U，地址填写：

```text
http://极空间局域网IP:18780/live/yangshi.m3u
```

示例：

```text
http://192.168.1.20:18780/live/yangshi.m3u
```

这个地址已经把央视、卫视和匹配成功的自定义频道合并在一个列表里。

建议在路由器中给极空间设置固定局域网 IP，否则 IP 变化后播放器地址也要修改。

## 七、维护自定义 M3U

浏览器打开 Web 管理后台：

```text
http://极空间局域网IP:18780/
```

切换到“M3U 管理”，可以：

1. 添加或删除远程 M3U 地址。
2. 添加频道名称、分组和播放地址。
3. 直接编辑原始 `local.m3u`。
4. 点击“保存并重新检测”。

文件会持久化在极空间的：

```text
lumina-live/config/local.m3u
```

如果自定义源是局域网 IPTV 地址，在 Compose 环境变量中改为：

```yaml
ALLOW_PRIVATE_UPSTREAMS: "true"
```

只对可信的本地源开启。

## 八、修改端口或性能参数

### 端口被占用

将端口映射改为：

```yaml
ports:
  - "18781:8780"
```

之后访问和 APTV 订阅都使用 `18781`。

### 低性能机型

将环境和资源限制调整为：

```yaml
environment:
  CHANNEL_WORKERS: "8"
mem_limit: 512m
cpus: 1.0
```

## 九、更新版本

在极空间 Compose 项目中：

1. 停止项目或选择重新部署。
2. 选择“重新拉取镜像/拉取最新镜像”。
3. 重新创建容器。
4. 保留原项目目录和卷映射。

如果使用 SSH：

```bash
cd /项目实际路径/lumina-live
docker compose pull
docker compose up -d --remove-orphans
```

更新镜像不会删除 `config` 和 `data`。

## 十、备份和迁移

备份整个 `lumina-live` 文件夹即可，重点包括：

```text
compose.yaml
.env
config/
data/
```

换新极空间或其他 NAS 时：

1. 停止旧 Compose 项目。
2. 复制整个文件夹到新 NAS。
3. 修改绝对路径（若使用）。
4. 重新导入 Compose 并部署。

## 十一、极空间常见问题

### 镜像下载失败

- 确认镜像名称为 `s1mpleboy/lumina-live-nas:latest`。
- 检查极空间 DNS、网关和系统时间。
- 尝试在镜像仓库页面单独拉取该镜像。

### 容器一直 `unhealthy`

- 首次部署先等待 5 分钟。
- 查看容器日志，确认远程 M3U 和 HLS 域名可访问。
- 检查 `config`、`data` 是否有写权限。

### Web 页面能开但频道很少

项目只发布本轮真实视频分片验证成功的频道。进入健康度面板查看离线频道，并通过“M3U 管理”添加自己合法可用的备用源。

### APTV 仍显示旧列表

在 APTV 中手动刷新或删除后重新添加订阅。也可以临时使用：

```text
http://极空间局域网IP:18780/live/yangshi.m3u?v=20260901
```

### 删除项目后配置不见了

如果删除 Compose 项目时同时选择删除宿主机目录，数据无法由容器恢复。更新、重建容器时不要勾选删除 `config`、`data`。
