# 部署文档：腾讯云 2 核 2G · Docker Compose

本文目标：把 AI Monitor 部署到一台 2 核 2G 的腾讯云服务器，用 `http://公网IP` 直接访问。
假设系统为 **Ubuntu 24.04 LTS**（Debian 12 同样适用）。

> 为什么用 Docker Compose：PostgreSQL、后端、Nginx 各一个容器，版本和配置都写进文件里，
> 换机器时重复执行同样的命令即可。项目里的 `docker-compose.yml`、`deploy/` 就是为此准备的。

---

## 0. 部署后是什么样

```text
浏览器 / 接入方
      │  80 端口（唯一对外端口）
      ▼
┌─────────────────────────────────────────────┐
│ Nginx 容器                                   │
│   /          → /usr/share/nginx/html（前端） │
│   /api/      → backend:8001                  │
│   /health    → backend:8001/health           │
└──────────────────┬──────────────────────────┘
                   │ Docker 内部网络
                   ▼
┌─────────────────────────────────────────────┐
│ backend 容器（uvicorn，1 worker）            │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│ postgres 容器（数据存在 pgdata 卷里）        │
└─────────────────────────────────────────────┘
```

- 对外只开放 **80**。PostgreSQL（5432）和后端（8001）都不映射到宿主机。
- 前端和后端同源，所以不需要配 CORS；前端代码里的 `/api/v1` 原样可用。
- 2G 内存的预算大致是：系统 + Docker 约 250MB，PostgreSQL 约 150MB，
  后端（含 LangChain）约 300–400MB，Nginx 约 15MB，合计 **不到 1G**。
  剩余内存配 2G swap 兜底。

本次随文档新增的部署文件：

| 文件 | 作用 |
|---|---|
| `docker-compose.yml` | 三个服务的编排、健康检查、内存相关参数 |
| `deploy/backend.Dockerfile` | 后端镜像 |
| `deploy/nginx.conf` | 静态文件、反向代理、SSE 与缓存配置 |
| `deploy/.env.example` | 生产配置模板（复制到项目根目录为 `.env.production`） |
| `deploy/compose.sh` | 所有 compose 命令的入口，自动带上 `--env-file .env.production` |
| `deploy/check-env.sh` | 启动前配置自检 |
| `deploy/package.sh` | 本地打包脚本，只生成部署必需的最小包 |
| `deploy/backup.sh` | 数据库每日备份脚本 |
| `.dockerignore` | 避免把 node_modules / .venv 塞进构建上下文 |

---

## 1. 服务器准备

### 1.1 安全组 / 防火墙

腾讯云控制台放通入站：

| 端口 | 来源 | 用途 |
|---|---|---|
| 22 | 你的公网 IP（尽量别用 0.0.0.0/0） | SSH |
| 80 | 0.0.0.0/0 | 网站与上报接口 |

**不要**放通 5432 和 8001。数据库只给容器访问，后端只给 Nginx 访问。

轻量应用服务器在「防火墙」面板配置；CVM 在「安全组」配置。

> 如果买的是「宝塔面板」之类的应用镜像，系统里可能已经装了 Nginx 占着 80 端口。
> 先在宝塔面板或 `systemctl` 里停掉它，否则后面容器会报 `address already in use`。
> 最省事的是直接选纯净的 Ubuntu 系统镜像。

配完再在服务器上确认：

```bash
sudo ufw status
```

若启用了 ufw：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw enable
```

### 1.2 安装 Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

最后一条把当前用户加入 docker 组，**重新登录 SSH 后生效**。之后执行 `docker` 不用加 sudo。

国内访问 `get.docker.com` 慢时，用系统源安装：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
```

确认版本：

```bash
docker --version
docker compose version
```

> 命令是 `docker compose`（v2 插件），不是老的 `docker-compose`。

### 1.3 配置镜像加速与日志上限

腾讯云服务器可以用内网镜像加速拉取 Docker Hub 镜像：

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://mirror.ccs.tencentyun.com"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
```

`log-opts` 限制容器日志大小。不配的话，`json-file` 日志会一直涨，小磁盘很容易被写满。

> 如果拉镜像仍然超时，换用可用的加速地址，或重试几次。常见报错是
> `failed to resolve source metadata for docker.io/...`。

### 1.4 加 2G swap（2G 内存必做）

构建后端镜像时要安装 LangChain 全家桶，峰值内存可能超过 2G。没有 swap 时
构建过程可能被系统 OOM kill（输出 `Killed`）。

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
sudo sysctl vm.swappiness=10
free -h
```

`free -h` 里能看到 `Swap: 2.0Gi` 就成功了。`swappiness=10` 让小内存机器尽量先用物理内存，
真不够时才换出，避免正常运行时频繁换页。

---

## 2. 本地构建与上传

### 2.1 本地构建前端

**不要在 2G 服务器上构建前端**。`vue-tsc` + Vite 构建峰值很容易超过 1G，且 Node 依赖本身也占磁盘。
在本地（Windows，Node 20.19+ 或 22.12+）执行：

```bash
cd ai-monitor-frontend
npm ci
npm run build
```

确认产物存在：

```bash
ls dist/index.html
ls dist/assets
```

`dist/` 在 `.gitignore` 里，所以如果服务器是用 `git clone` 部署的，**必须单独把 `dist` 传上去**，
否则 Nginx 挂载到空目录，访问就是 404。

### 2.2 打包并上传

在本地项目根目录（Git Bash）执行：

```bash
./deploy/package.sh
```

脚本会构建前端，并且只把运行必需的文件复制到临时目录，最后生成项目上级目录下的
`ai-monitor-deploy.tar.gz`。`docs/`、`prototype/`、前端源码、`node_modules`、`.venv`
和真实的 `.env` 文件都不会进包。

常用参数：

```bash
./deploy/package.sh --skip-build    # 前端已经构建过，跳过构建
./deploy/package.sh --fresh         # 强制 npm ci 后再构建
./deploy/package.sh --with-backup   # 额外带上 deploy/backup.sh
```

上传：

```bash
scp ../ai-monitor-deploy.tar.gz ubuntu@<公网IP>:/tmp/
```

> 登录用户名按镜像来：Ubuntu 镜像通常是 `ubuntu`，Debian 是 `admin`，也可能是 `root`。
> 腾讯云控制台的「登录」页面会直接给出完整 SSH 命令，照抄即可。
>
> 部署包不包含 `.env.development` / `.env.production`：那是本机的数据库地址和模型 Key，
> 生产配置在 §3 单独创建。`ai-monitor-frontend/dist` 必须存在，脚本会检查。

服务器上解压：

```bash
sudo mkdir -p /opt/ai-monitor
sudo tar -xzf /tmp/ai-monitor-deploy.tar.gz -C /opt/ai-monitor
sudo chown -R "$USER":"$USER" /opt/ai-monitor
cd /opt/ai-monitor
ls
```

应该能看到 `docker-compose.yml`、`deploy/`、`ai-monitor-backend/`、`ai-monitor-frontend/dist/`。

> 如果你的代码在 Git 仓库里，也可以 `git clone` 到 `/opt/ai-monitor`，
> 但同样要记得补传 `ai-monitor-frontend/dist`。

---

## 3. 写服务器配置

先明确三层配置的边界，别把文件传混了：

| 用途 | 实际文件 | 模板 | 实际文件进 Git 吗 |
|---|---|---|---|
| 本地开发 | `ai-monitor-backend/.env.development` | `ai-monitor-backend/.env.example` | 否 |
| 生产部署 | 项目根目录 `.env.production` | `deploy/.env.example` | 否 |
| 本地验收测试 | 不单独建文件 | 用 `TEST_DATABASE_URL` 临时覆盖 | — |

两份 `.env.example` 模板（分别对应本地开发和生产）会提交进 Git；
实际配置文件（`.env.development` / `.env.production`）不会。

- 本地开发那份只留在你自己的电脑上，不要传到服务器；生产的 `.env.production`
  也不要下载回本地覆盖。
- 测试脚本默认连 `ai_monitor_test` 库（库名由开发库自动替换得到），不需要额外配置；
  测试库在另一台机器时才用 shell 变量覆盖，见 README 的「开发」一节。
- 生产的 `.env.production` 同时承担两个角色：给 compose 做变量插值、通过 `env_file`
  注入 backend 容器。所以它里面**不写 `DATABASE_URL`**，由 compose 用数据库密码自动拼出来。
- Compose **不会自动读** `.env.production`（它只认 `.env`），所以文档里的命令统一用
  `./deploy/compose.sh`；这个脚本就是 `docker compose --env-file .env.production` 的封装。

敏感配置全部走环境变量，代码里没有真实值：

| 配置 | 本地开发 | 生产 |
|---|---|---|
| 数据库密码 | `ai-monitor-backend/.env.development` 的 `DATABASE_URL` | 根 `.env.production` 的 `POSTGRES_PASSWORD`，compose 拼成连接串 |
| JWT 签名密钥 | `ai-monitor-backend/.env.development` | 根 `.env.production` |
| LLM / Tavily Key | `ai-monitor-backend/.env.development` | 根 `.env.production` |
| 前端后端地址 | `ai-monitor-frontend/.env.development` 的 `VITE_DEV_PROXY_TARGET` | Nginx 同源转发，不需要配置 |

代码里只有占位符和合理的默认值（时区、模型名等），没有真实密码、Key 或服务器地址。

### 3.1 生成两个密钥

在服务器上：

```bash
openssl rand -hex 24      # 第一行输出：PostgreSQL 密码
openssl rand -base64 48   # 第二行输出：JWT 签名密钥
```

PostgreSQL 密码特意用十六进制：它要拼进 `DATABASE_URL`，十六进制不含 `@ : / #` 等字符，
省掉 URL 转义的麻烦。

JWT 密钥必须 **不少于 32 字节**。`DEBUG=false` 时，后端启动会检查：还是占位符或太短就直接拒绝启动。

### 3.2 创建项目根目录的 `.env.production`

```bash
cd /opt/ai-monitor
cp deploy/.env.example .env.production
chmod 600 .env.production
nano .env.production
```

填成类似这样：

```dotenv
# 上面第一条命令的输出
POSTGRES_PASSWORD=3f9a...
# 上面第二条命令的输出
JWT_SECRET_KEY=xK8v...
# 环境标识；production 会强制 DEBUG=false，并拒绝连测试库
APP_ENV=production
DEBUG=false

# 可留空；留空时 AI 助手返回 503，其他功能正常
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash
LLM_TEMPERATURE=0.3
ASSISTANT_TIMEZONE=Asia/Shanghai
ASSISTANT_MAX_TOOL_ROUNDS=10
TAVILY_API_KEY=
```

> 注释单独占一行，不要写在 `KEY=value` 后面。compose 的 `env_file` 解析不保证把行内
> `#` 当注释，密码后面粘上注释文字会导致数据库认证失败。

说明：

- **这个 `.env.production` 和 `ai-monitor-backend/.env.development` 不是同一个文件**。
  本地开发用后者；容器部署时，`docker-compose.yml` 用根目录这个 `.env.production`
  给容器注入环境变量。
- `DATABASE_URL` **不用手写**，compose 会用 `POSTGRES_PASSWORD` 自动拼出
  `postgresql+psycopg://ai_monitor:密码@postgres:5432/ai_monitor`。
- `.env.production` 已被 `.gitignore` 忽略，权限设成 600，别提交、别贴到聊天里。
- 后端按 `APP_ENV` 选择加载 `.env.development` / `.env.production`，compose 已把
  `APP_ENV=production` 固定写进 backend 容器，不需要你手动设置。
- 直接敲 `docker compose` 不会读取 `.env.production`，变量插值会为空。后面的命令
  一律用 `./deploy/compose.sh`，它已经带上了 `--env-file .env.production`。
- 模板里其余项都有默认值或注释；一时用不到的先留空，不影响启动。

### 3.3 启动前自检

```bash
chmod +x deploy/check-env.sh deploy/compose.sh deploy/backup.sh
./deploy/check-env.sh
```

它会检查：根目录 `.env.production` 是否存在、数据库密码是否还是占位符、JWT 密钥是否少于 32 字节、
`APP_ENV` 是否为 `production`、`DEBUG` 是否为 `false`、生产配置里是否误写了 `DATABASE_URL`、
前端 `dist` 是否已上传。
有不通过项就按提示改，**不要跳过**——这些错误如果留到启动阶段，表现往往是容器反复重启或 502，
排查起来更费时间。

---

## 4. 首次启动

### 4.1 构建后端镜像

```bash
cd /opt/ai-monitor
./deploy/compose.sh build backend
```

第一次要下载基础镜像并安装依赖，2 核机器大约 5–15 分钟。中途看到 `Killed` 就回到 §1.4 确认 swap 已生效。

### 4.2 启动三个容器

```bash
./deploy/compose.sh up -d
./deploy/compose.sh ps
```

期望看到 `postgres`、`backend`、`nginx` 都是 `Up`（`postgres` 和 `backend` 后面会有 `(healthy)`）。
`backend` 的 `start_period` 是 30 秒，刚启动时显示 `starting` 是正常的。

看启动日志：

```bash
./deploy/compose.sh logs --tail=50 backend
```

### 4.3 建表

```bash
./deploy/compose.sh exec -T postgres psql -U ai_monitor -d ai_monitor \
  < ai-monitor-backend/sql/schema.sql
```

`-T` 是关闭伪终端，只有这样才能把本地的 SQL 文件通过标准输入送进容器。

> `schema.sql` 开头写的「先 CREATE USER / CREATE DATABASE」是裸机部署的前提。
> 这里 compose 的 `POSTGRES_USER` / `POSTGRES_DB` 在首次启动时已经建好了，
> 直接执行建表脚本即可，不要再手动建一遍。
>
> 建表脚本会以执行者身份创建表。这里的 `POSTGRES_USER` 就是 `ai_monitor`，
> 所以表 owner 正确，不需要额外授权。

### 4.4 写入内置模型价格（必做）

```bash
./deploy/compose.sh exec -T backend python scripts/init_models.py
```

这一步把 DeepSeek / OpenAI 等内置模型的价格写进 `models` 表。**上报日志时要按这张表算成本**，
不执行的话成本会算成 0。脚本是幂等的，以后调价了重新执行即可。

### 4.5 创建管理员（需要管理后台时）

```bash
./deploy/compose.sh exec backend python scripts/init_admin.py --username admin
```

按提示输入两次密码，输入时不显示。不要用 `-T`，否则没有终端可以接收密码输入。

脚本的行为：

- 用户名不存在：创建新账号并直接设为管理员。
- 用户名已存在且是普通用户：只把角色提升为 `admin`，**不修改密码**。
- 已经是管理员：提示无需修改，直接退出。

脚本可以反复执行。创建完成后，用这个账号登录，侧边栏会出现「管理后台」入口。

---

## 5. 验证清单

按顺序做，每步通过再下一步。

```bash
# 1. 服务器本机
curl http://127.0.0.1/health
# 期望：{"status":"ok"}

# 2. 公网（在你自己的电脑上执行）
curl http://<公网IP>/health
# 同样期望 {"status":"ok"}

# 3. 容器状态
./deploy/compose.sh ps

# 4. 资源占用
free -h
docker stats --no-stream
```

浏览器打开 `http://<公网IP>`：

1. 能打开登录页，刷新一次页面不出现 404（验证 `try_files` 生效）。
2. 注册账号 → 登录成功。
3. 创建一个项目，拿到 `proj_` 开头的 API Key。
4. 按下一节的命令上报一条日志，回到「调用日志」页能看到它。
5. 如果配了 `LLM_API_KEY`，打开 AI 助手问一句，答案应该是**逐字出现**而不是整段突然出现
   （验证 SSE 没有被 Nginx 缓冲）。
6. 如果创建了管理员，用管理员账号登录，侧边栏能看到「管理后台」入口。

---

## 6. 接入方怎么上报（地址变了）

部署后接入地址是 `http://<公网IP>/api/v1/logs`，**不再带 `:8001`**。
Nginx 会把 `/api/` 转给后端容器。

```bash
curl -X POST http://<公网IP>/api/v1/logs \
  -H "X-API-Key: proj_你的Key" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "latency": 1200,
    "status": "success",
    "input_tokens": 800,
    "output_tokens": 300
  }'
```

接入方仍然遵守原来的四条铁律：调用完成后报、后台发送不阻塞业务、异常就地吞掉、失败调用也要报。
上报超时时间别设太长（比如 3 秒），平台不可用时不能拖慢接入方。

---

## 7. 日常运维

### 7.1 看日志

```bash
./deploy/compose.sh logs -f --tail=100 backend
./deploy/compose.sh logs -f --tail=100 nginx
./deploy/compose.sh logs -f postgres
```

Docker 日志已限制为每个容器最多 3 个 10MB 文件，不会无限增长。

### 7.2 更新前端

本地重新 `npm run build`，把新的 `dist/` 覆盖上传到 `/opt/ai-monitor/ai-monitor-frontend/dist/`。
Nginx 直接读磁盘上的文件，**不用重启容器**，浏览器刷新即可。

`index.html` 配了 `no-cache`，带哈希的 `/assets/*` 配了长缓存，正常发版不会出现新旧文件混用。

### 7.3 更新后端

上传新代码后（至少包含 `ai-monitor-backend/app`、`main.py`、`scripts`）：

```bash
cd /opt/ai-monitor
./deploy/compose.sh build backend
./deploy/compose.sh up -d backend
./deploy/compose.sh ps
```

依赖清单变化（`pyproject.toml` / `uv.lock`）才需要重新 `build`，只改业务代码也可以走同样流程，安全且简单。

### 7.4 数据库结构升级

`sql/schema.sql` 只用于**全新库**。已有数据的库升级，按编号执行 `sql/migrations/` 下的增量脚本：

```bash
./deploy/compose.sh exec -T postgres psql -U ai_monitor -d ai_monitor \
  < ai-monitor-backend/sql/migrations/006_xxx.sql
```

按编号顺序执行，别跳号。

### 7.5 备份与恢复

手动备份：

```bash
cd /opt/ai-monitor
chmod +x deploy/backup.sh
./deploy/backup.sh
ls -lh /opt/backup/
```

挂到 cron，每天凌晨 3:17 备份并清理 7 天前的文件：

```bash
crontab -e
# 写入这一行：
17 3 * * * /opt/ai-monitor/deploy/backup.sh >> /opt/backup/backup.log 2>&1
```

> cron 以当前用户运行，所以该用户必须在 docker 组里（§1.2）。
> 如果不想依赖 docker 组，就把这行加到 root 的 crontab，并给脚本里的 docker 命令加 sudo。

恢复（**会覆盖现有数据，谨慎**）：

```bash
cd /opt/ai-monitor
./deploy/compose.sh stop backend nginx
./deploy/compose.sh exec -T postgres dropdb -U ai_monitor --if-exists ai_monitor
./deploy/compose.sh exec -T postgres createdb -U ai_monitor -O ai_monitor ai_monitor
gunzip -c /opt/backup/ai_monitor_2026-10-28.sql.gz \
  | ./deploy/compose.sh exec -T postgres psql -U ai_monitor -d ai_monitor
./deploy/compose.sh start backend nginx
```

### 7.6 观察资源

```bash
free -h
docker stats --no-stream
df -h
```

2G 机器的关注点：

- `available` 长期低于 100MB，且 swap 大量使用 → 该考虑升级到 4G，或减少后端占用。
- 磁盘：Docker 镜像本身约 1G，PG 数据随日志量增长。至少留 10G 可用。
- 清理无用镜像：`docker image prune -f`（不要加 `-a`，会把当前要用的镜像也删掉）。

---

## 8. 安全要点

1. **只开 80**（以及 SSH）。数据库和后端端口不映射到宿主机。
2. `.env.production` 权限 600；不要把密钥提交进 Git。
3. PostgreSQL 密码、JWT 密钥都用随机值，别用 `123456` 或占位符。
4. SSH 尽量用密钥登录，安全组里的 22 端口限制来源 IP。
5. **当前是 HTTP 明文**：登录 token、API Key、上报内容都会以明文经过公网。
   短期验证够用，长期对外必须上 HTTPS（见 §10）。
6. 定期更新基础镜像：

   ```bash
   ./deploy/compose.sh pull postgres nginx
   ./deploy/compose.sh up -d
   ```

   更新前先跑一次备份。

---

## 9. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `permission denied ... /var/run/docker.sock` | 当前用户不在 docker 组 | 重新登录 SSH，或命令前加 `sudo` |
| 拉镜像超时 / `failed to resolve source metadata` | Docker Hub 访问不稳定 | 确认 §1.3 的镜像加速已生效，重试 |
| `./deploy/compose.sh build` 输出 `Killed` | 内存不够 | 确认 swap 已开启（`free -h`），关闭其他进程后重试 |
| `backend` 反复重启，日志提示 `JWT_SECRET_KEY 不安全` | 密钥太短或是占位符 | 用 `openssl rand -base64 48` 重新生成，`DEBUG=false` 时至少 32 字节 |
| 访问页面 502 | 后端没起来或启动失败 | `./deploy/compose.sh logs backend`；确认 `postgres` 是 healthy |
| 页面能开，刷新后 404 | `dist` 没上传，Nginx 挂了空目录 | 确认 `/opt/ai-monitor/ai-monitor-frontend/dist/index.html` 存在 |
| 首页正常但接口全 502 | Nginx 找不到 `backend` 服务 | 确认三个容器在同一个 compose 项目里，`./deploy/compose.sh ps` |
| 上报返回 413 | 上报正文超过 Nginx 限制 | 已配置 10m；正文确实更大时调 `deploy/nginx.conf` 的 `client_max_body_size` |
| AI 助手答案不逐字出现 | SSE 被代理缓冲 | 已配 `proxy_buffering off`；若自己改了 Nginx，别删这几行 |
| 登录后提示数据库错误 | 容器内连接串主机名写错 | compose 里必须是 `postgres`，不是 `localhost` |
| 建表时报 `permission denied` | 用错了执行用户 | 必须 `psql -U ai_monitor -d ai_monitor` |
| 公网打不开，服务器本机 `curl` 正常 | 安全组 / 防火墙没放通 80 | 检查腾讯云防火墙与 `ufw status` |
| `bind: address already in use` | 宿主机已有 Nginx / 宝塔占用 80 | 停掉它，或改用纯净系统镜像 |
| 内存告警 / 容器被杀 | 2G 不够用 | 看 `docker stats`；加 swap 只救急，长期上 4G |

排查顺序固定：`./deploy/compose.sh ps` → `./deploy/compose.sh logs <服务>` → `curl 127.0.0.1/health` → 公网 curl。

---

## 10. 后续：换成域名 + HTTPS

国内服务器用域名走 80/443，需要域名完成 **ICP 备案**并解析到这台机器。

步骤概览（现在不用做）：

1. 域名 A 记录指向公网 IP。
2. 用 Certbot（或 Caddy）申请 Let's Encrypt 证书。
3. Nginx 增加 `listen 443 ssl`，80 端口 301 跳到 443。
4. 安全组放通 443。

注意：如果以后在前面加了 CDN，要确认 CDN 不会缓冲 SSE（`/api/v1/chat/stream`），
否则 AI 助手会退回「整段出现」的表现。

---

## 附：常用命令速查

```bash
cd /opt/ai-monitor

./deploy/compose.sh ps                  # 状态
./deploy/compose.sh up -d               # 启动
./deploy/compose.sh restart backend     # 重启后端
./deploy/compose.sh down                # 停止（保留数据卷）
./deploy/compose.sh down -v             # 停止并删除数据卷（会丢数据！）
./deploy/compose.sh logs -f backend     # 跟日志
./deploy/compose.sh exec -T backend python scripts/init_models.py
./deploy/compose.sh exec backend python scripts/init_admin.py --username admin
./deploy/compose.sh exec -T postgres psql -U ai_monitor -d ai_monitor
```
