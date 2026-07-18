"""barcode2homebox 后端：扫码解码 + 商品查询 + 写入 Homebox。
鉴权：使用 Homebox 账号登录（/api/login），登录后 Homebox 的 JWT 存于签名 cookie，
所有需要访问 Homebox 的接口都从 cookie 取 token，实现「共用 Homebox 登录信息」。

端点：
  GET  /api/health
  POST /api/login      json: {email, password} -> 校验 Homebox 凭据，写会话 cookie
  POST /api/logout     -> 清除会话 cookie
  GET  /api/me         -> 当前登录状态
  GET  /api/locations  (需登录)
  POST /api/scan       (需登录) multipart: image -> 解码条码 + 查询商品
  POST /api/lookup     (需登录) json: {barcode} -> 仅查询商品
  POST /api/add        (需登录) json: {barcode} 或 {name,...} -> 查询并写入 Homebox
  DELETE /api/delete/{id} (需登录)
  GET  /api/proxy-image (需登录)
前端静态页挂载在 /
"""
import io
import json
import base64
import logging
from pathlib import Path

from PIL import Image


import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import lookup
import homebox_client as hb
import session
import config as cfg
import cache_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

app = FastAPI(title="barcode2homebox")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 鉴权依赖 ----------
def _session(request: Request) -> dict | None:
    return session.decode_session(request.cookies.get(session.COOKIE_NAME))


def require_token(request: Request) -> str:
    """需要 Homebox token 的接口：无有效会话则 401。"""
    sess = _session(request)
    if not sess:
        raise HTTPException(status_code=401, detail="未登录或登录已失效，请先登录")
    return sess["t"]


def require_auth(request: Request):
    """仅需登录态的接口（不直接用 token）：无有效会话则 401。"""
    if not _session(request):
        raise HTTPException(status_code=401, detail="未登录或登录已失效，请先登录")


# ---------- 公开端点 ----------
@app.get("/api/health")
def health():
    return {"ok": True, "homebox_url": bool(hb.BASE)}


@app.post("/api/login")
def api_login(payload: dict, response: Response, request: Request):
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("password", ""))
    if not email or not password:
        raise HTTPException(400, "email 和 password 必填")
    res = hb.login(email, password)
    if not res.get("ok"):
        raise HTTPException(401, "Homebox 登录失败：" + str(res.get("error", "")))
    token = res["token"]
    # 探测一次版本，确认 token 真实可用
    if hb.detect_version(token) == "unknown":
        raise HTTPException(401, "登录成功但无法访问 Homebox，请检查 HOMEBOX_URL")
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=session.COOKIE_NAME,
        value=session.encode_session(token, email),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=session.MAX_AGE,
        path="/",
    )
    return {"ok": True, "email": email}


@app.post("/api/logout")
def api_logout(response: Response):
    response.delete_cookie(session.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    sess = _session(request)
    if not sess:
        return {"authenticated": False}
    return {"authenticated": True, "email": sess.get("email", "")}


# ---------- 设置（界面可改的运行时配置）----------
@app.get("/api/settings")
def api_get_settings(_: None = Depends(require_auth)):
    """返回当前 GS1 / Vision 配置（凭据明文返回，仅供本人设置页使用）。"""
    return cfg.load()


@app.put("/api/settings")
def api_put_settings(payload: dict, _: None = Depends(require_auth)):
    """保存设置；只接受已知键，空字符串表示清空。"""
    saved = cfg.save(payload)
    return {"ok": True, "settings": saved}


@app.post("/api/settings/test")
def api_test_settings(payload: dict, _: None = Depends(require_auth)):
    """用给定（或已保存）的凭据测试连通性。target 指定 gs1 / vision。"""
    target = (payload.get("target") or "gs1").strip().lower()
    c = cfg.load()

    if target == "vision":
        url = _normalize_vision_url((payload.get("vision_api_url") or "").strip() or c.get("vision_api_url"))
        key = (payload.get("vision_api_key") or "").strip() or c.get("vision_api_key")
        model = (payload.get("vision_model") or "").strip() or c.get("vision_model") or "gpt-4o-mini"
        if not (url and key):
            return {"ok": False, "error": "Vision 配置不完整"}
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                timeout=20,
            )
            # 200 即接口可达且鉴权通过（即便某些模型名不匹配也会返回明确错误）
            return {"ok": r.status_code == 200, "status": r.status_code}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # 默认 GS1
    url = (payload.get("gs1_api_url") or "").strip() or c.get("gs1_api_url")
    sid = (payload.get("gs1_secret_id") or "").strip() or c.get("gs1_secret_id")
    skey = (payload.get("gs1_secret_key") or "").strip() or c.get("gs1_secret_key")
    if not (url and sid and skey):
        return {"ok": False, "error": "GS1 配置不完整"}
    try:
        _, headers = lookup._build_gs1_auth(sid, skey)
        r = requests.get(url, params={"Code": "6901234567890"}, headers=headers, timeout=30, verify=False)
        # 200 即接口可达（即便该测试条码未登记也说明鉴权通过）
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ---------- 本地条码缓存 ----------
@app.get("/api/cache/stats")
def api_cache_stats(_: None = Depends(require_auth)):
    """本地条码缓存统计：条数 + 累计命中次数。"""
    return cache_db.stats()


@app.post("/api/cache/clear")
def api_cache_clear(_: None = Depends(require_auth)):
    """清空本地条码缓存，返回清空前条数。"""
    n = cache_db.clear()
    return {"ok": True, "cleared": n}


# ---------- 需登录端点 ----------
@app.get("/api/locations")
def api_locations(token: str = Depends(require_token)):
    """获取 Homebox 所有位置列表，供前端选择。"""
    version = hb.detect_version(token)
    h = {"Authorization": f"Bearer {token}"}
    if version == "v2":
        r = requests.get(f"{hb.BASE}/api/v1/entities?isLocation=true&pageSize=100", headers=h, timeout=hb.TIMEOUT, verify=False)
    else:
        r = requests.get(f"{hb.BASE}/api/v1/locations?pageSize=100", headers=h, timeout=hb.TIMEOUT, verify=False)
    if r.status_code != 200:
        raise HTTPException(502, f"Homebox query failed: {r.status_code}")
    items = r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()
    return [{"id": loc["id"], "name": loc["name"]} for loc in items]


def decode_barcode(image_bytes: bytes) -> str | None:
    """服务端解码（上传图片用）。懒加载 pyzbar，避免无 zbar 时启动失败。"""
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"pyzbar unavailable: {e}")
    try:
        img = Image.open(io.BytesIO(image_bytes))
        results = decode(img)
        if results:
            return results[0].data.decode("utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("decode error: %s", e)
    return None


def _normalize_vision_url(url: str) -> str:
    """把用户可能填的 base url（如 https://api.siliconflow.cn/v1）补全为
    chat/completions 完整端点。OpenAI 兼容接口的真实路径都是 .../v1/chat/completions。"""
    if not url:
        return url
    u = url.strip().rstrip("/")
    if not u.endswith("/chat/completions"):
        u += "/chat/completions"
    return u


def vision_fallback(image_bytes: bytes, mime: str) -> tuple[dict | None, str]:
    """完全查不到时，调用 OpenAI 兼容视觉接口识别包装图。返回 (结果, 诊断信息)。"""
    c = cfg.load()
    url = _normalize_vision_url(c.get("vision_api_url"))
    key = c.get("vision_api_key")
    model = c.get("vision_model") or "gpt-4o-mini"
    if not (url and key):
        return None, "AI 识图未配置（缺少 API 地址或 Key）"

    # 压缩图片：最长边 1024，避免 base64 过大和模型 token 超限
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        compressed = buf.getvalue()
        mime = "image/jpeg"
    except Exception as e:  # noqa: BLE001
        compressed = image_bytes
        log.warning("vision image compress failed, use original: %s", e)

    b64 = base64.b64encode(compressed).decode()
    data_url = f"data:{mime};base64,{b64}"
    # 字段结构与条码(GS1)识别结果严格对齐：name/brand/specification/manufacturer/category
    prompt = (
        "你是一名商品识别助手。这是一张商品包装照片，请识别并用 JSON 返回以下字段，"
        "字段名必须严格使用英文键：\n"
        "name: 商品名称（尽量用中文，如\"Move Free 氨糖软骨素红瓶\"）\n"
        "brand: 品牌（如\"Move Free\"）\n"
        "specification: 规格/净含量（如\"80粒/瓶\"、\"500g\"，无法判断则为空字符串）\n"
        "manufacturer: 生产厂家（如\"美国 Schiff 公司\"，无法判断则为空字符串）\n"
        "category: 品类（如\"保健品\"、\"休闲零食\"，尽量简短中文）\n"
        "如果图片看不清或不是商品，请把 name 设为空字符串。\n"
        "只返回 JSON，不要解释，不要 markdown 代码块，字段名必须严格使用上面的英文键。"
    )
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]}
                ],
            },
            timeout=30,
            verify=False,
        )
        log.info("vision api status=%s len=%s model=%s", r.status_code, len(r.text), model)
        if r.status_code != 200:
            return None, f"AI 接口返回 {r.status_code}: {r.text[:200]}"

        body = r.json()
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None, f"AI 接口返回结构异常，缺少 choices/message/content: {r.text[:200]}"

        # 清洗 markdown 代码块
        text = content.strip()
        if text.startswith("```"):
            text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
        obj = json.loads(text)
        name = (obj.get("name") or "").strip()
        if not name:
            return None, f"AI 识别成功但 name 为空，原始返回：{content[:200]}"

        def _norm(v):
            v = (v or "").strip()
            return v or None

        # 与条码(GS1)识别返回字段对齐，保证两条路径结果结构一致
        result = {
            "found": True,
            "source": "vision-ai",
            "name": name,
            "brand": _norm(obj.get("brand")),
            "specification": _norm(obj.get("specification")),
            "manufacturer": _norm(obj.get("manufacturer")),
            "category": _norm(obj.get("category")),
            "image": None,
        }
        return result, ""
    except json.JSONDecodeError as e:
        log.warning("vision fallback json error: %s content=%s", e, content[:300] if 'content' in locals() else 'N/A')
        return None, f"AI 返回不是有效 JSON: {e}"
    except Exception as e:  # noqa: BLE001
        log.warning("vision fallback failed: %s", e)
        return None, f"AI 接口异常: {e}"


@app.post("/api/scan")
async def scan(image: UploadFile = File(...), _: None = Depends(require_auth)):
    data = await image.read()
    mime = image.content_type or "image/jpeg"
    barcode = decode_barcode(data)
    if not barcode:
        # 解码失败：尝试 AI 识图兜底
        vis, reason = vision_fallback(data, mime)
        if vis:
            return {"barcode": None, "product": vis, "decoded_by": "vision"}
        raise HTTPException(404, f"未识别到条码，且 AI 识图也无结果（{reason}）")
    product = lookup.lookup(barcode)
    if not product.get("found"):
        vis, reason = vision_fallback(data, mime)
        if vis:
            vis["barcode"] = barcode
            cache_db.put(vis)   # 视觉识别成功也入缓存（有条码即可）
            return {"barcode": barcode, "product": vis, "decoded_by": "barcode+vision"}
        return {"barcode": barcode, "product": product, "decoded_by": "barcode"}
    return {"barcode": barcode, "product": product, "decoded_by": "barcode"}


@app.post("/api/lookup")
def api_lookup(payload: dict, _: None = Depends(require_auth)):
    barcode = str(payload.get("barcode", "")).strip()
    if not barcode:
        raise HTTPException(400, "barcode required")
    return lookup.lookup(barcode)


@app.post("/api/add")
def api_add(payload: dict, token: str = Depends(require_token)):
    barcode = str(payload.get("barcode", "")).strip()
    # 入库数量：解析为正整数，非法/缺省归 1
    try:
        quantity = int(payload.get("quantity"))
    except (TypeError, ValueError):
        quantity = 1
    if quantity is None or quantity < 1:
        quantity = 1
    if barcode:
        product = lookup.lookup(barcode)
        if not product.get("found"):
            # 允许前端手动补全后直接传 name
            if not payload.get("name"):
                raise HTTPException(404, "未找到该条码商品，请手动填写名称")
    else:
        product = {
            "found": True,
            "source": "manual",
            "barcode": payload.get("barcode"),
            "name": payload.get("name"),
            "brand": payload.get("brand"),
            "specification": payload.get("specification"),
            "manufacturer": payload.get("manufacturer"),
            "category": payload.get("category"),
            "image": payload.get("image"),
        }
    if not product.get("name"):
        raise HTTPException(400, "name required")
    location_id = payload.get("locationId")  # 前端选择的位置
    result = hb.add_item(token, product, location_id=location_id, quantity=quantity)
    return {"product": product, "homebox": result}


@app.delete("/api/delete/{item_id}")
def api_delete(item_id: str, token: str = Depends(require_token)):
    ok = hb.delete_item(token, item_id)
    if ok:
        return {"ok": True}
    raise HTTPException(404, "删除失败")


@app.get("/api/proxy-image")
def proxy_image(url: str, _: None = Depends(require_auth)):
    """代理外部图片，解决 HTTPS 页面加载 HTTP 图片的混合内容问题。
    同时处理 GS1 图片服务返回 HTML 包裹的情况（提取 base64 图片数据）。
    """
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid url")
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
        if r.status_code != 200:
            raise HTTPException(502, f"upstream {r.status_code}")
        ct = r.headers.get("content-type", "")
        # 直接是图片
        if "image" in ct:
            from fastapi.responses import Response
            return Response(content=r.content, media_type=ct)
        # HTML 包裹：尝试从 ViewState 或 meta 中提取 base64 图片
        import re, base64
        body = r.text
        # 匹配 data:image/xxx;base64, 格式
        m = re.search(r'data:(image/\w+);base64,([A-Za-z0-9+/=]+)', body)
        if m:
            mime = m.group(1)
            img_data = base64.b64decode(m.group(2))
            from fastapi.responses import Response
            return Response(content=img_data, media_type=mime)
        # 匹配 __VIEWSTATE 中的 base64（ASP.NET 图片服务）
        m = re.search(r'__VIEWSTATE[^"]*"[^"]*value="([A-Za-z0-9+/=]{100,})"', body)
        if m:
            try:
                raw = base64.b64decode(m.group(1))
                # 检查是否包含 JPEG/PNG 魔数
                if raw[:3] == b'\xff\xd8\xff' or raw[:8] == b'\x89PNG\r\n\x1a\n':
                    from fastapi.responses import Response
                    mime = "image/jpeg" if raw[:3] == b'\xff\xd8\xff' else "image/png"
                    return Response(content=raw, media_type=mime)
            except Exception:  # noqa: BLE001
                pass
        # 尝试直接返回（可能是其他格式）
        from fastapi.responses import Response
        return Response(content=r.content, media_type=ct or "application/octet-stream")
    except requests.RequestException as e:
        raise HTTPException(502, f"fetch failed: {e}")


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
