# 【开源分享】LuminaLive NAS：央视卫视合并成一个 M3U，支持极空间/群晖/威联通等 Docker NAS

![LuminaLive NAS 封面](https://raw.githubusercontent.com/TomShen-simple/LuminaLive-NAS/main/docs/images/forum/01-cover.png)

大家好，分享一个最近整理并开源的小项目：**LuminaLive NAS**。

我之前的直播方案是把源交给 VPS 解析，但实际使用中遇到了几个很影响体验的问题：换台要等十多秒、持续转圈、源失效后闪退，偶尔还会碰到跳转到广告内容的假源。更麻烦的是，央视、卫视和地方台被拆成不同链接，维护和使用都不方便。

于是我重新做了一个面向家庭 NAS 的通用版本：让 NAS 定时读取公开或自定义 M3U，检查 HLS 清单以及真实视频分片，只把本轮能够播放的频道整理成一个订阅交给 APTV、TiviMate、VLC、Kodi 等客户端。

项目完全开源、免费。

> GitHub：<https://github.com/TomShen-simple/LuminaLive-NAS>  
> v1.0.0：<https://github.com/TomShen-simple/LuminaLive-NAS/releases/tag/v1.0.0>  
> Docker 镜像：`ghcr.io/tomshen-simple/lumina-live-nas:latest`

---

## 它和普通直播源转发有什么区别？

![LuminaLive NAS 工作原理](https://raw.githubusercontent.com/TomShen-simple/LuminaLive-NAS/main/docs/images/forum/02-how-it-works.png)

LuminaLive NAS 的定位不是视频中转服务器。

NAS 只负责：

1. 定时读取多个公开 M3U 或用户自己的 M3U；
2. 识别央视、卫视等频道名称和别名；
3. 请求 HLS 清单，并继续检查其中的真实视频分片；
4. 将通过检测的频道合并成一份 M3U；
5. 本轮扫描失败时保留上一份成功结果，避免订阅突然变空。

播放器拿到订阅后，**播放流量直接连接已经验证的上游地址**，不会让整段视频长期绕行 NAS 或 VPS。因此少了一次网络绕路，也减少了 NAS 的持续带宽占用。

这不能让质量很差的上游凭空变快，但可以减少明显失效、伪装成直播页或连真实分片都打不开的候选源。

---

## 目前提供的功能

- 央视和卫视合并在**同一个 M3U 订阅**中；
- 检查 HLS 主清单、媒体清单和真实视频分片；
- 默认每 30 分钟重新检测；
- 扫描失败时保留最近一次可用播放列表；
- 支持 NAS 本地的 `config/local.m3u`；
- 支持添加额外的远程 M3U；
- 支持自定义频道、别名和检测并发数；
- 提供 `/healthz` 和 `/status.json` 状态接口；
- 支持 `linux/amd64` 与 `linux/arm64`；
- Docker 镜像可以匿名拉取，无需登录 GHCR；
- 容器采用只读根文件系统、丢弃多余权限，并限制 CPU/内存。

### 支持哪些 NAS？

只要设备能正常运行 64 位 Docker/Compose，原则上就能部署，例如：

- 极空间；
- 群晖；
- 威联通；
- 飞牛 fnOS；
- 绿联；
- 铁威马；
- Unraid；
- TrueNAS SCALE；
- CasaOS / OpenMediaVault；
- 普通 Linux 小主机或软路由。

这里所说的“通用”并不包括不能运行 Docker 的老型号、32 位 ARM 设备，以及厂商关闭容器功能的机型。

---

## 通用 Docker Compose 部署

![Docker Compose 部署](https://raw.githubusercontent.com/TomShen-simple/LuminaLive-NAS/main/docs/images/forum/03-compose.png)

### 方法一：使用命令行

```bash
git clone https://github.com/TomShen-simple/LuminaLive-NAS.git
cd LuminaLive-NAS
cp .env.example .env
mkdir -p config data
docker compose pull
docker compose up -d
```

查看运行状态：

```bash
docker compose ps
docker compose logs --tail=200 lumina-live
```

首次启动需要读取和检测候选源，通常需要等待 1～5 分钟。检测期间健康状态可能暂时显示为 `starting` 或 `unhealthy`，生成第一份播放列表后会变为 `healthy`。

浏览器可以打开：

```text
http://NAS局域网IP:18780/healthz
http://NAS局域网IP:18780/status.json
```

在 APTV 等播放器中添加的订阅地址是：

```text
http://NAS局域网IP:18780/live/yangshi.m3u
```

例如 NAS 地址是 `192.168.1.20`：

```text
http://192.168.1.20:18780/live/yangshi.m3u
```

### 方法二：NAS 图形化 Compose

不方便 SSH 的用户，可以下载 v1.0.0 源码压缩包，解压到 NAS 的 Docker 共享目录，然后在 NAS 的 Compose 页面导入 `compose.yaml`。

默认 `.env` 设置为：

```dotenv
BIND_IP=0.0.0.0
HOST_PORT=18780
CONFIG_DIR=./config
DATA_DIR=./data
TZ=Asia/Shanghai
```

如果 NAS 面板不支持相对路径，把 `CONFIG_DIR` 和 `DATA_DIR` 改成该设备中真实存在的绝对路径。**不要照抄别人 NAS 的存储池编号。**

完整通用教程：  
<https://github.com/TomShen-simple/LuminaLive-NAS/blob/main/docs/compose.md>

---

## 以极空间为例

1. 在极空间文件管理中建立 `lumina-live` 文件夹；
2. 在里面建立 `config`、`data` 两个子目录；
3. 下载并解压项目，保证 `compose.yaml`、`.env`、`config` 位于同一级目录；
4. 打开极空间的「Docker」→「Compose」→「新建项目」；
5. 项目名称填写 `lumina-live`；
6. 存储位置选择刚才准备的文件夹；
7. 导入或粘贴 `compose.yaml`；
8. 检查 `.env` 环境变量后点击部署；
9. 容器状态变为 `healthy` 后，把 M3U 地址添加到播放器。

新版极空间 Compose 可以设置“存储位置”并使用相对路径；旧版如果提示路径无效，可以用“查询路径”取得 `config`、`data` 的实际绝对路径。

极空间完整图文步骤：  
<https://github.com/TomShen-simple/LuminaLive-NAS/blob/main/docs/zspace.md>

---

## 有自己的直播源怎么办？

把自己的播放列表放到：

```text
config/local.m3u
```

本地列表会优先于远程列表参与匹配。

如果自己的上游使用 `192.168.x.x`、`10.x.x.x` 等局域网地址，需要在 `.env` 中打开：

```dotenv
ALLOW_PRIVATE_UPSTREAMS=true
```

还可以添加额外的远程列表：

```dotenv
EXTRA_M3U_URLS=https://example.com/a.m3u,https://example.com/b.m3u
```

频道名称和别名可以在 `config/channels.json` 中修改。

---

## 构建与测试情况

![GitHub Actions 构建结果](https://raw.githubusercontent.com/TomShen-simple/LuminaLive-NAS/main/docs/images/forum/04-actions.png)

当前公开版本已经完成：

- 6 项单元测试；
- Python 编译检查；
- Docker Compose 配置校验；
- 普通 Docker 镜像构建；
- `linux/amd64` 镜像构建与发布；
- `linux/arm64` 镜像构建与发布；
- GHCR 匿名拉取验证；
- 实际 M3U 扫描及 HTTP 接口集成测试。

对应的 GitHub Actions：

- CI：<https://github.com/TomShen-simple/LuminaLive-NAS/actions/runs/33355719773>
- main 多架构镜像：<https://github.com/TomShen-simple/LuminaLive-NAS/actions/runs/33355719786>
- v1.0.0 多架构镜像：<https://github.com/TomShen-simple/LuminaLive-NAS/actions/runs/33356032456>

---

## 更新、停止与卸载

更新镜像：

```bash
docker compose pull
docker compose up -d
```

停止容器但保留配置：

```bash
docker compose down
```

`config` 和 `data` 位于 NAS 宿主机，正常更新或重建容器不会清空它们。

---

## 几个需要提前说明的问题

### 1. 能保证每个频道永远可用吗？

不能。公开直播源会随地区、运营商、时间和上游策略变化。这个项目负责自动筛选当前能通过检测的候选源，并保留上一次成功结果，但不可能控制第三方上游。

### 2. 为什么某个卫视没有出现？

这通常表示当前没有候选源通过清单和视频分片检测，而不是程序故意删除频道。可以加入自己的 `local.m3u`，或等待下一个检测周期。

### 3. 它会破解会员、DRM 或地区限制吗？

不会。项目不破解 DRM、会员或地区限制，也不提供、托管或销售节目内容。

### 4. 可以把端口直接开放到公网吗？

不建议。默认按家庭局域网服务设计。远程使用优先通过 Tailscale、WireGuard 等家庭 VPN；必须反向代理时，请配置 HTTPS、身份认证和访问限制。

---

## 项目地址

- GitHub：<https://github.com/TomShen-simple/LuminaLive-NAS>
- Release：<https://github.com/TomShen-simple/LuminaLive-NAS/releases/tag/v1.0.0>
- 通用 Compose 教程：<https://github.com/TomShen-simple/LuminaLive-NAS/blob/main/docs/compose.md>
- 极空间教程：<https://github.com/TomShen-simple/LuminaLive-NAS/blob/main/docs/zspace.md>
- 兼容性说明：<https://github.com/TomShen-simple/LuminaLive-NAS/blob/main/docs/platforms.md>
- 故障排查：<https://github.com/TomShen-simple/LuminaLive-NAS/blob/main/docs/troubleshooting.md>

如果项目对你有帮助，欢迎在 GitHub 点一个 Star。遇到频道匹配、NAS 兼容性或部署问题，也欢迎提交 Issue，并附上 NAS 型号、CPU 架构和脱敏后的容器日志。

