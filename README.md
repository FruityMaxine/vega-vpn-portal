<div align="center">
  <img src="docs/banner.png" alt="Vega VPN Portal" width="120" />

  # vega-vpn-portal

  [![Version](https://img.shields.io/badge/version-0.6.2.2-blue?style=flat-square)](VERSION)
  [![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
  [![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](signup-service/requirements.txt)
  [![JavaScript](https://img.shields.io/badge/JavaScript-ES2022-F7DF1E?style=flat-square&logo=javascript&logoColor=black)](portal/html/app.js)
  [![HTML5](https://img.shields.io/badge/HTML5-portal-E34F26?style=flat-square&logo=html5&logoColor=white)](portal/html/)
  [![Docker](https://img.shields.io/badge/Docker-compose-2496ED?style=flat-square&logo=docker&logoColor=white)](docker-compose.yml)
  [![Powered by Marzban](https://img.shields.io/badge/Powered_by-Marzban-8B5CF6?style=flat-square)](https://github.com/Gozargah/Marzban)
  [![UI: Animal Island](https://img.shields.io/badge/UI-Animal_Island-FF9F43?style=flat-square)](https://github.com/guokaigdg/animal-island-ui)

  Marzban 的自托管用户门户——带邀请码注册、流量自助查询、多协议订阅，以及 9 个设备教程页。
</div>

---

## 包含什么

- **Animal Island 风格用户门户**：邀请码注册、一键复制订阅链接、流量统计、二维码/链接多协议支持（VLESS Reality + Shadowsocks-2022）
- **FastAPI 中间件**（`signup-service`）：处理注册逻辑、邀请码验证、JWT 会话，桥接门户与 Marzban Admin API
- **一键 docker-compose 部署**：Marzban + signup-service + nginx 门户，配合 Caddy 做反向代理与自动 TLS
- **9 个设备专属教程页**：iOS / Android / macOS / Windows / Linux / 路由器 / Apple TV / Android TV / 智能电视，每页独立维护
- **Marzban 镜像摘要固定**：`gozargah/marzban@sha256:8e422c21...`，重新部署时后端版本不受上游变动影响

## 不是什么

- 不是 VPN 协议实现——它封装 Marzban，Marzban 封装 Xray
- 不是安全研究项目——使用标准 bcrypt + JWT，没有特别之处

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/FruityMaxine/vega-vpn-portal.git
cd vega-vpn-portal

# 2. 一键初始化（检查依赖、生成密钥、填写域名、启动服务）
chmod +x setup.sh
./setup.sh

# 3. 将 setup.sh 打印的 Caddy 配置块粘贴进你的 Caddyfile，然后：
caddy reload --config /etc/caddy/Caddyfile

# 4. 把 DNS 指向你的服务器 IP，等证书签发完成（约 30 秒）
```

门户访问地址：`https://your-domain.com`
Marzban 管理后台：`https://your-domain.com/dashboard/`

## 架构

```
Browser
  |
  v
Caddy (443 TLS termination)
  |
  +---> /            --> portal (nginx :3100, Animal Island static UI)
  +---> /api/        --> signup-service (FastAPI :8800, invite/signup/JWT)
  +---> /dashboard/  --> Marzban (uvicorn :8000, admin panel + sub API)
                             |
                             v
                          Xray-core
                            :8443  VLESS + Reality
                            :2083  Shadowsocks-2022 TCP+UDP
```

## 配置参考

所有配置通过环境变量传入，见 `.env.example`：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `PORTAL_BASE_URL` | 是 | — | 公网域名，如 `https://vpn.example.com` |
| `YOUR_SERVER_IP` | 是 | — | 服务器公网 IP（写入 xray_config.json） |
| `MARZBAN_SUDO_USERNAME` | 是 | `admin` | Marzban 管理员用户名 |
| `MARZBAN_SUDO_PASSWORD` | 是 | — | Marzban 管理员密码（强密码） |
| `MARZBAN_JWT_SECRET_KEY` | 是 | — | `openssl rand -hex 32` 生成 |
| `MARZBAN_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | `1440` | Token 有效期（分钟） |
| `REALITY_PRIVATE_KEY` | 是 | — | VLESS Reality 私钥（`xray x25519` 生成） |
| `REALITY_SHORT_ID` | 是 | — | Reality Short ID |
| `SS_PASSWORD` | 是 | — | Shadowsocks-2022 密码（`openssl rand -base64 32`） |
| `CADDY_ADMIN_PASS_TOKEN` | 是 | — | Caddy 守门 Token（`openssl rand -hex 24`） |
| `STATS_OUT_DIR` | 否 | `./portal/html/stats` | stats-daemon.py 的输出目录 |
| `BACKUP_DIR` | 否 | `./backups` | backup.sh 输出目录 |

## 截图

_待填写——将截图文件放入 `docs/screenshots/` 并更新以下路径：_

| 门户首页 | 我的账户 | 设备教程 | 下载页 |
|----------|----------|----------|--------|
| `docs/screenshots/home.png` | `docs/screenshots/me.png` | `docs/screenshots/tutorial.png` | `docs/screenshots/downloads.png` |

## 技术栈

- **Marzban** — Xray 多协议代理管理面板（上游：[Gozargah/Marzban](https://github.com/Gozargah/Marzban)）
- **Xray-core** — VLESS+Reality / Shadowsocks-2022 协议
- **FastAPI + uvicorn** — signup-service 中间件
- **nginx** — 静态门户文件服务
- **Caddy** — 反向代理 + 自动 TLS
- **Docker Compose** — 服务编排
- **Animal Island UI** — 门户视觉风格（[guokaigdg/animal-island-ui](https://github.com/guokaigdg/animal-island-ui)）
- **Vanilla JS / HTML5 / CSS3** — 无前端框架

## 可复现性说明

Marzban 镜像固定到摘要 `sha256:8e422c21997e5d2e3fa231eeff73c0a19193c20fc02fa4958e9368abb9623b8d`（对应 2025-01-09 构建）。即使上游推送新版，重新部署时拉取的仍是同一层。如需升级，主动修改 `docker-compose.yml` 中的摘要并在测试环境验证。

## 许可证 / 致谢

MIT — Copyright (c) 2026 FruityMaxine。见 [LICENSE](LICENSE)。

上游依赖：
- [Gozargah/Marzban](https://github.com/Gozargah/Marzban) — AGPL-3.0
- [guokaigdg/animal-island-ui](https://github.com/guokaigdg/animal-island-ui) — MIT

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。
