# vega-vpn-portal

**Version:** 0.6.2.2 | **Stack:** FastAPI + nginx + Marzban + Caddy | **Docker Compose**

## What

Marzban 的自托管用户门户。signup-service（FastAPI）桥接静态前端与 Marzban Admin API，提供邀请码注册、JWT 会话、流量统计。

## Quick Start

```bash
./setup.sh              # 初始化：生成密钥、复制 .env、启动服务
docker compose logs -f  # 查看启动日志
docker compose ps       # 确认三个容器均 running
```

## Commands

```bash
# 开发
docker compose up -d --build      # 构建并启动
docker compose down               # 停止
docker compose restart signup-service  # 热重载 FastAPI（改代码后）

# 日志
docker compose logs -f marzban
docker compose logs -f signup-service
docker compose logs -f portal

# Marzban CLI（创建用户 / 查看版本）
docker exec vpn-marzban marzban-cli admin create
docker exec vpn-marzban marzban-cli --version

# 备份
bash backup.sh

# 统计守护进程（宿主机运行，非 Docker）
python3 stats-daemon.py
```

## Architecture

```
portal/html/        Animal Island 静态 UI（nginx :3100）
signup-service/     FastAPI 中间件（:8800）— 注册/邀请/JWT
marzban/            配置文件（xray_config.json + .env.example）
caddy-vpn.conf      Caddy 反代配置示例
docker-compose.yml  三服务编排
stats-daemon.py     宿主机统计守护，写 portal/html/stats/*.json
```

## Key Files

```
signup-service/main.py          FastAPI 路由（注册、登录、订阅接口）
signup-service/marzban_client.py  Marzban Admin API 封装
portal/html/app.js              门户前端主逻辑
portal/html/me.html             用户账户页（订阅链接、流量、QR码）
portal/html/devices/*.html      9 个设备教程页
marzban/xray/xray_config.json   Xray 协议配置（引用 .env 变量）
caddy-vpn.conf                  Caddy 配置示例（含 Token 守门逻辑）
```

## UI 规范

门户 UI **严格遵循 Animal Island 风格**（[guokaigdg/animal-island-ui](https://github.com/guokaigdg/animal-island-ui)）。修改或新增前端组件必须保持该风格一致性，禁止引入其他 UI 库。

## 铁律

- **Marzban 镜像摘要固定**，不得随意改为 `:latest`——要升级先在测试环境验证，再更新摘要
- **所有密钥来自 `.env`**，禁止硬编码进代码或配置文件
- 用户可见字符串用中文没问题
- 不要新增对最终用户不可见的功能，除非先在 Issue 讨论

## Configuration

见 `.env.example`。关键变量：`PORTAL_BASE_URL`、`MARZBAN_SUDO_PASSWORD`、`MARZBAN_JWT_SECRET_KEY`、`REALITY_PRIVATE_KEY`、`SS_PASSWORD`。

## Contributing

见 [CONTRIBUTING.md](CONTRIBUTING.md)。
