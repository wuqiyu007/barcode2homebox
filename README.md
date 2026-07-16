# 扫码录入 Homebox

把商品条码扫一下，自动查到商品信息并一键写进 Homebox 库存。

## 架构

```
摄像头 / 图片
   → 条码解码 (ZXing 浏览器端 / pyzbar 服务端)
   → 商品查询 (Open Food Facts 免费 → 云市场官方 GS1 兜底 → AI 识图兜底)
   → 写入 Homebox (REST API，自动适配 v1/v2)
```

## 为什么不用「AI 读条码」或「爬 gds.org.cn」

- **AI 视觉读条码数字不可靠**：条码是纯数字，差一位就全错，应交给专业解码库。
- **gds.org.cn 官网是 SPA + 反爬**：静态页无数据、无公开接口，爬取脆弱易失效；官方真实接口需企业认证付费。
- 正确做法：条码库解码 + 权威数据源 API + Homebox API。本项目的兜底数据源走**云市场代理的中国物品编码中心数据**（¥5/400 次，几乎零成本），等价于官方数据。

## 快速开始（本地）

```bash
cd barcode2homebox
cp .env.example .env      # 填入 Homebox 地址/token
pip install -r backend/requirements.txt
# 解码图片需要系统 zbar：apt install libzbar0 (mac: brew install zbar)
python backend/app.py
# 浏览器打开 http://localhost:8000
```

## Docker 部署（推荐：公共镜像，一行启动）

镜像已发布到 GitHub Container Registry，任何装有 Docker 的电脑都能直接拉取运行，**无需克隆代码、无需本地构建、无需 .env 文件**：

```bash
# 1) 编辑同目录的 docker-compose.yml，在 environment 区块把示例默认值改成你的真实值
#    （至少填 HOMEBOX_URL、GS1_SECRET_ID、GS1_SECRET_KEY；其余按需）
# 2) 启动
docker compose up -d
# 访问 http://<本机IP>:8000
```

> 镜像默认 **public**，`docker pull` 无需登录。所有配置直接写在 `docker-compose.yml` 的 `environment` 区块，改完文件即生效，**不依赖任何 `.env`**。

docker-compose.yml 核心（开箱即用，仅需改 environment 里的几个值）：

```yaml
services:
  barcode2homebox:
    image: ghcr.io/wuqiyu007/barcode2homebox:latest
    container_name: barcode2homebox
    restart: always
    environment:
      - HOMEBOX_URL=https://homebox.example.com:666
      - HOMEBOX_TOKEN=
      - APP_SECRET=change-me-to-a-random-32byte-hex
      - HOMEBOX_EMAIL=
      - HOMEBOX_PASSWORD=
      - HOMEBOX_LOCATION_ID=
      - HOMEBOX_TIMEOUT=30
      - GS1_API_URL=http://ap-guangzhou.cloudmarket-apigw.com/service-8lp6ruw0/getBarcode
      - GS1_SECRET_ID=
      - GS1_SECRET_KEY=
      - VISION_API_URL=
      - VISION_API_KEY=
      - VISION_MODEL=gpt-4o-mini
    ports:
      - 8000:8000
    volumes:
      - ./data:/app/data
```

## Docker 部署（本地构建，适合二次开发）

```bash
cd barcode2homebox
cp .env.example .env
docker compose up -d --build
# 访问 http://<本机IP>:8000
```

> ⚠️ **摄像头需要安全上下文**：浏览器仅在 `https://` 或 `localhost` 下允许调用摄像头。
> 若走 NAS 局域网 `http://IP:8000`，实时摄像头会被浏览器拦截，此时请用「上传图片」方式（不受限）。
> 建议用反代 + 证书（如自有域名）提供 https 后再用摄像头。

## 配置项（写在 `docker-compose.yml` 的 `environment` 里）

> 所有配置项已直接写在 `docker-compose.yml` 的 `environment` 区块（见上方示例），把示例默认值改成你的真实值即可，`docker compose up -d` 生效。
> **切勿把真实密钥提交到公开仓库**——基于本仓库二次开发请 fork 到私仓，不要把填了密钥的 compose 推回公开仓库。

| 变量 | 说明 | 默认值 |
|---|---|---|
| `HOMEBOX_URL` | Homebox 地址，如 `https://homebox.example.com:666` | `https://homebox.example.com:666` |
| `HOMEBOX_TOKEN` | 直接给 token（推荐）；或填账号密码自动登录 | 空 |
| `APP_SECRET` | 会话 cookie 签名密钥，固定值避免重启后强制登出 | `change-me-to-a-random-32byte-hex` |
| `HOMEBOX_EMAIL` / `HOMEBOX_PASSWORD` | 账号密码登录（无 token 时使用） | 空 |
| `HOMEBOX_LOCATION_ID` | 可选，物品归属位置 | 空 |
| `HOMEBOX_TIMEOUT` | Homebox 请求超时（秒） | `30` |
| `GS1_API_URL` / `GS1_SECRET_ID` / `GS1_SECRET_KEY` | 云市场官方 GS1 接口（兜底，国货/非食品覆盖） | 见文件内示例 |
| `VISION_API_URL` / `VISION_API_KEY` / `VISION_MODEL` | 可选 AI 识图兜底（OpenAI 兼容） | `VISION_MODEL`=`gpt-4o-mini` |

### 获取云市场 GS1 接口
阿里云市场 / 腾讯云市场搜索「商品条码查询」，购买后拿到调用地址、SecretId 和 SecretKey，填入 `GS1_API_URL`、`GS1_SECRET_ID` 与 `GS1_SECRET_KEY`。

## 接口

- `GET  /api/health`
- `POST /api/scan`   (multipart image) 解码+查询
- `POST /api/lookup` (`{barcode}`) 仅查询
- `POST /api/add`    (`{barcode}` 或手写 `{name,...}`) 查询并写入 Homebox
