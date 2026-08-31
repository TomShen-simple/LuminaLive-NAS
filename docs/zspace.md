# 极空间 Docker Compose 部署教程

本文以当前极空间 ZOS Docker 应用为例。新版 Compose 已支持“存储位置”、相对路径和可视化 `.env`；旧版界面仍可用“查询路径”获取绝对路径。

## 一、准备文件夹

1. 打开极空间「文件管理」。
2. 在用于 Docker 数据的共享目录中新建 `lumina-live`。
3. 在其中新建 `config` 和 `data` 两个子目录。
4. 从 GitHub Releases 下载项目压缩包，解压后保证 `compose.yaml`、`.env`、`config` 在同一级目录。
5. 将 `.env.example` 复制并改名为 `.env`。

目录应类似：

```text
lumina-live/
├─ compose.yaml
├─ .env
├─ config/
│  └─ channels.json
└─ data/
```

## 二、修改 `.env`

建议设置：

```dotenv
BIND_IP=0.0.0.0
HOST_PORT=18780
TZ=Asia/Shanghai
CONFIG_DIR=./config
DATA_DIR=./data
```

如果你的极空间 Compose 页面有“存储位置”，选择刚才的 `lumina-live` 文件夹，相对路径可直接使用。

若系统提示相对路径无效：

1. 在 Compose 页面点击「查询路径」。
2. 选择 `config` 和 `data` 文件夹并复制实际路径。
3. 将 `.env` 改为类似下面的绝对路径：

```dotenv
CONFIG_DIR=查询到的config完整路径
DATA_DIR=查询到的data完整路径
```

不要复制别人极空间里的号码或存储池路径，每台设备都不同。

## 三、创建 Compose 项目

1. 打开极空间「Docker」。
2. 进入「Compose」。
3. 点击「新建项目」。
4. 项目名称填写 `lumina-live`。
5. 存储位置选择准备好的 `lumina-live` 文件夹。
6. 选择“本地导入”并导入 `compose.yaml`，或把文件内容粘贴到编辑器。
7. 确认 `.env` 环境变量已被识别；新版界面可直接在环境变量区域核对。
8. 点击「创建/部署」。

首次需要拉取镜像，之后还要扫描频道，通常等待 1～5 分钟。

## 四、查看状态

在 Docker → 容器中打开 `lumina-live`：

- 状态应为“运行中”。
- 健康状态最终应为 `healthy`。
- 日志应出现 `refresh complete` 和发布频道数量。

浏览器访问：

```text
http://极空间局域网IP:18780/healthz
http://极空间局域网IP:18780/status.json
```

播放器订阅地址：

```text
http://极空间局域网IP:18780/live/yangshi.m3u
```

例如极空间 IP 是 `192.168.1.20`：

```text
http://192.168.1.20:18780/live/yangshi.m3u
```

## 五、极空间常见问题

### 1. 端口打不开

- 确认 `18780` 没有被其他容器占用。
- 将 `.env` 中 `HOST_PORT` 改成其他端口，例如 `18781`，然后重新部署。
- 新版 ZOS 一般不需要手写 iptables/systemd 防火墙脚本；先检查 Docker 项目的端口映射。

### 2. 相对路径报错

更新 ZOS 和 Docker 应用；或使用 Compose 页面的「查询路径」换成绝对路径。

### 3. 镜像拉取失败

检查 DNS、网络和极空间 Docker 的镜像仓库设置。也可以通过 SSH 下载源码后执行本地构建命令。

### 4. 一直显示 unhealthy

打开容器日志。如果仍在扫描可继续等待；若日志显示没有可用频道，检查极空间能否访问 GitHub Raw 和上游 HLS。

### 5. 换 NAS

停止 Compose 项目，把整个 `lumina-live` 文件夹复制到新设备，在新设备重新导入 `compose.yaml` 即可。不要复制运行中的容器本身。

## 六、更新

在 Compose 项目页面执行“重新拉取/重新部署”。命令行方式为：

```bash
docker compose pull
docker compose up -d
```

`config` 和 `data` 位于宿主机，更新镜像不会清空配置和最后一次可用列表。

