# 贡献指南

## 开发环境

```bash
git clone https://github.com/FruityMaxine/vega-vpn-portal.git
cd vega-vpn-portal
cp .env.example .env
# 填写 .env 中的必填项（至少 PORTAL_BASE_URL、MARZBAN_* 密钥）
docker compose up -d --build
```

signup-service 热重载：改完 `signup-service/main.py` 后运行 `docker compose restart signup-service`。

## 分支与 PR

- 主分支：`main`（直接可部署）
- 功能分支命名：`feat/描述`、`fix/描述`、`docs/描述`
- PR 合并前需确认：`docker compose up -d --build` 无报错，三个容器均健康

## UI 修改规范

门户前端严格使用 Animal Island 风格（参考 [guokaigdg/animal-island-ui](https://github.com/guokaigdg/animal-island-ui)）。修改前先查阅该仓库的现有组件，不要引入其他组件库。

## 功能范围约定

- 提交对最终用户不可见的功能前，请先开 Issue 说明理由
- 设备教程页（`portal/html/devices/`）保持各平台独立维护，不合并为单页
- Marzban 镜像摘要变更需附说明：新摘要对应的上游版本、测试结果

## 代码风格

- Python：遵循 PEP 8，使用 FastAPI 惯例
- JavaScript：ES2022，无构建步骤，保持 Vanilla JS
- HTML/CSS：与现有 `style.css` / `animal-components.css` 保持一致

## 提交信息格式

```
<type>(<scope>): <描述>

type: feat | fix | docs | chore | refactor
scope: portal | signup-service | marzban | caddy | docs
```

示例：`fix(signup-service): 修复邀请码大小写不敏感校验`

## 问题反馈

使用 GitHub Issues，优先附上：
- 复现步骤
- `docker compose logs` 相关片段
- 服务器 OS 和 Docker 版本

## 使用 Claude Code

本项目包含 `CLAUDE.md`，Claude Code 会自动读取。

```bash
claude    # 在项目根目录启动 Claude Code
```

`CLAUDE.md` 包含架构说明、关键文件列表和贡献约束，Claude Code 可直接使用。
