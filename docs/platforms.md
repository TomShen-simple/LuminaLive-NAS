# NAS 兼容性与差异

| 平台 | 建议方式 | 常见数据路径示例 | 注意事项 |
|---|---|---|---|
| 极空间 ZOS | Docker → Compose | 使用“存储位置”或“查询路径” | 新版支持相对路径和 `.env` |
| 群晖 DSM 7.2+ | Container Manager → 项目 | `/volume1/docker/lumina-live` | 项目目录需有写权限 |
| 威联通 QTS/QuTS | Container Station → 应用 | 共享文件夹实际路径 | Compose 版本因固件不同 |
| 飞牛 fnOS | Docker Compose | 选择 Docker 数据目录 | 建议固定 NAS 局域网 IP |
| 绿联 UGOS Pro | Docker → 项目 | 使用界面选择目录 | 检查端口防火墙 |
| 铁威马 TOS | Docker Manager | `/Volume1/docker/...` 等 | 路径大小写敏感 |
| Unraid | Compose 插件或命令行 | `/mnt/user/appdata/...` | 确认 Compose 插件可用 |
| TrueNAS SCALE | 自定义 App/Compose | `/mnt/存储池/...` | 不要写入系统数据集 |
| CasaOS/OMV | Compose/命令行 | 自选绝对路径 | 标准 Linux 用法 |

项目镜像目标平台：

- `linux/amd64`
- `linux/arm64`

不支持：

- ARMv7、ARMv6 等 32 位处理器。
- 无 Docker/OCI 容器能力的 NAS。
- 只支持 Kubernetes 且禁止自定义容器的封闭环境。

