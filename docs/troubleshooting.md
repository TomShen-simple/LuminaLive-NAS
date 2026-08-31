# 故障排查

## 查看状态

```bash
docker compose ps
docker compose logs --tail=300 lumina-live
curl -i http://NAS-IP:18780/healthz
curl -i http://NAS-IP:18780/status.json
```

## 首次启动 503

首次启动需要下载源清单并读取真实视频分片。低性能 NAS 或网络较慢时可能需要 1～5 分钟。只要日志仍在运行且没有循环崩溃，继续等待即可。

## 频道少或缺少某个卫视

项目只发布本轮真实分片验证通过的频道。上游失效、地区限制或运营商路由都会导致频道临时缺席。可以在 `config/local.m3u` 添加自己的合法备用源，并在 `channels.json` 增加别名。

## 播放器仍显示旧频道

强制刷新订阅，或临时追加缓存参数：

```text
http://NAS-IP:18780/live/yangshi.m3u?v=2
```

服务端已经发送 `no-cache`，但部分播放器仍会自行缓存。

## 端口冲突

修改 `.env`：

```dotenv
HOST_PORT=18781
```

重新部署后订阅地址也要换成 `18781`。

## 数据目录没有权限

为运行 Docker 的系统服务授予 `config`、`data` 的读写权限。避免直接开放整个存储池权限。

## 内存或 CPU 占用高

扫描阶段会短时升高。弱性能设备可以设置：

```dotenv
CHANNEL_WORKERS=6
MEM_LIMIT=512m
CPU_LIMIT=1.0
```

## 本地 IPTV 被过滤

私网源默认禁用以防止远程清单借容器访问 NAS 内网。只有在你明确使用可信 `local.m3u` 时设置：

```dotenv
ALLOW_PRIVATE_UPSTREAMS=true
```

