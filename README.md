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
- 正确做法：条码库解码 + 权威数据源 API + Homebox API。本项目的兜底数据源走**云市场代理的中国物品编码中心数据**（¥5/200 次，几乎零成本），等价于官方数据。

## 快速开始（本地）

```bash
cd barcode2homebox
cp .env.example .env      # 填入 Homebox 地址/token
pip install -r backend/requirements.txt
# 解码图片需要系统 zbar：apt install libzbar0 (mac: brew install zbar)
python backend/app.py
# 浏览器打开 http://localhost:8000
```

## Docker 部署（绿联 NAS 等）

```bash
cd barcode2homebox
cp .env.example .env      # 填好凭据
docker compose up -d --build
# 访问 http://<NAS_IP>:8000
```

> ⚠️ **摄像头需要安全上下文**：浏览器仅在 `https://` 或 `localhost` 下允许调用摄像头。
> 若走 NAS 局域网 `http://IP:8000`，实时摄像头会被浏览器拦截，此时请用「上传图片」方式（不受限）。
> 建议用反代 + 证书（如你已有的 example.com 域名）提供 https 后再用摄像头。

## 配置项（.env）

| 变量 | 说明 |
|---|---|
| `HOMEBOX_URL` | Homebox 地址，如 `https://hass.example.com:666` |
| `HOMEBOX_TOKEN` | 直接给 token（推荐）；或填账号密码自动登录 |
| `HOMEBOX_LOCATION_ID` | 可选，物品归属位置 |
| `GS1_API_URL` / `GS1_SECRET_ID` / `GS1_SECRET_KEY` | 云市场官方 GS1 接口（兜底，国货/非食品覆盖） |
| `VISION_API_URL` / `VISION_API_KEY` / `VISION_MODEL` | 可选 AI 识图兜底（OpenAI 兼容） |

### 获取云市场 GS1 接口
阿里云市场 / 腾讯云市场搜索「商品条码查询」，购买后拿到调用地址、SecretId 和 SecretKey，填入 `GS1_API_URL`、`GS1_SECRET_ID` 与 `GS1_SECRET_KEY`。

## 接口

- `GET  /api/health`
- `POST /api/scan`   (multipart image) 解码+查询
- `POST /api/lookup` (`{barcode}`) 仅查询
- `POST /api/add`    (`{barcode}` 或手写 `{name,...}`) 查询并写入 Homebox
