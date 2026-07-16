"""Homebox REST 客户端：自动适配 v1(/items) 与 v2(/entities) API，JWT 鉴权。
环境变量：
  HOMEBOX_URL      例如 https://homebox.example.com:666 或 http://homebox:7745
  HOMEBOX_TOKEN    直接给 token（可选）
  HOMEBOX_EMAIL / HOMEBOX_PASSWORD  不给 token 时用于登录获取
  HOMEBOX_LOCATION_ID  可选，指定物品归属位置
"""
import io
import os
import logging
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("homebox")

BASE = os.getenv("HOMEBOX_URL", "").rstrip("/")
TIMEOUT = int(os.getenv("HOMEBOX_TIMEOUT", "30"))


def get_token() -> str | None:
    token = os.getenv("HOMEBOX_TOKEN")
    if token:
        return token
    email = os.getenv("HOMEBOX_EMAIL")
    pwd = os.getenv("HOMEBOX_PASSWORD")
    if not (BASE and email and pwd):
        return None
    try:
        r = requests.post(
            f"{BASE}/api/v1/users/login",
            json={"username": email, "password": pwd},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("token")
        log.warning("Homebox login failed: %s", r.status_code)
    except Exception as e:  # noqa: BLE001
        log.warning("Homebox login error: %s", e)
    return None


def login(email: str, password: str) -> dict:
    """用 Homebox 账号密码登录，返回 {ok, token?, email?, error?}。
    Homebox v0.26+ 的 LoginForm 使用 username 字段（值是邮箱），返回的 token 带 "Bearer " 前缀。
    """
    if not (BASE and email and password):
        return {"ok": False, "error": "missing_homebox_url_or_credentials"}
    try:
        r = requests.post(
            f"{BASE}/api/v1/users/login",
            json={"username": email, "password": password},
            timeout=TIMEOUT,
            verify=False,
        )
        if r.status_code == 200:
            body = r.json() or {}
            token = body.get("token") or body.get("jwt")
            if token:
                # Homebox 返回 "Bearer xxx"，去掉前缀只保留裸 token
                token = token.removeprefix("Bearer ").strip()
                return {"ok": True, "token": token, "email": email}
        return {"ok": False, "error": f"login_failed_http_{r.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"login_error: {e}"}


def detect_version(token: str) -> str:
    """探测 Homebox API 版本：v2=entities, v1=items。"""
    h = {"Authorization": f"Bearer {token}"}
    try:
        if requests.get(f"{BASE}/api/v1/entities?pageSize=1", headers=h, timeout=TIMEOUT, verify=False).status_code == 200:
            return "v2"
    except Exception:  # noqa: BLE001
        pass
    try:
        if requests.get(f"{BASE}/api/v1/items?pageSize=1", headers=h, timeout=TIMEOUT, verify=False).status_code == 200:
            return "v1"
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _item_entity_type_id(token: str) -> str | None:
    """v2: 取一个 isLocation=false 的实体类型 id（默认物品类型）。"""
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{BASE}/api/v1/entity-types", headers=h, timeout=TIMEOUT, verify=False)
        if r.status_code == 200:
            for t in r.json():
                if not t.get("isLocation"):
                    return t["id"]
    except Exception as e:  # noqa: BLE001
        log.warning("entity-types fetch failed: %s", e)
    return None


def delete_item(token: str, item_id: str) -> bool:
    """删除实体/物品（v2 优先，回退 v1）。"""
    h = {"Authorization": f"Bearer {token}"}
    for path in (f"/api/v1/entities/{item_id}", f"/api/v1/items/{item_id}"):
        try:
            r = requests.delete(f"{BASE}{path}", headers=h, timeout=TIMEOUT, verify=False)
            if r.status_code in (200, 204):
                return True
        except requests.RequestException:
            continue
    return False


def _download_image(url: str) -> bytes | None:
    """下载商品图片，返回图片字节。
    GS1 图片服务 (api-yun.cn) 返回 HTML 包裹的 base64 图片，需要提取。
    """
    if not url:
        return None
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "")
        # 直接是图片
        if "image" in ct:
            return r.content if len(r.content) > 100 else None
        # HTML 包裹：尝试提取 base64 图片
        import re, base64 as b64mod
        body = r.text
        # 匹配 data:image/xxx;base64, 格式
        m = re.search(r'data:(image/\w+);base64,([A-Za-z0-9+/=]+)', body)
        if m:
            return b64mod.b64decode(m.group(2))
        # 匹配 __VIEWSTATE 中的 base64
        m = re.search(r'__VIEWSTATE[^"]*value="([A-Za-z0-9+/=]{100,})"', body)
        if m:
            try:
                raw = b64mod.b64decode(m.group(1))
                # 检查 JPEG/PNG 魔数
                if raw[:3] == b'\xff\xd8\xff' or raw[:8] == b'\x89PNG\r\n\x1a\n':
                    return raw
            except Exception:  # noqa: BLE001
                pass
        # 尝试直接返回（可能是其他格式的图片）
        if len(r.content) > 100:
            return r.content
    except Exception as e:  # noqa: BLE001
        log.warning("image download failed: %s", e)
    return None


def _upload_image(token: str, entity_id: str, image_bytes: bytes, filename: str = "product.jpg") -> str | None:
    """上传图片到 Homebox 实体，返回 imageId。上传后触发缩略图生成。"""
    h = {"Authorization": f"Bearer {token}"}
    # 自动检测图片格式
    if image_bytes[:3] == b'\xff\xd8\xff':
        mime, ext = "image/jpeg", "jpg"
    elif image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        mime, ext = "image/png", "png"
    elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        mime, ext = "image/webp", "webp"
    else:
        mime, ext = "image/jpeg", "jpg"
    fname = f"product.{ext}"
    try:
        r = requests.post(
            f"{BASE}/api/v1/entities/{entity_id}/attachments",
            headers=h,
            files={"file": (fname, io.BytesIO(image_bytes), mime)},
            data={"name": fname, "primary": "true"},
            timeout=TIMEOUT,
        )
        if r.status_code in (200, 201):
            image_id = (r.json() or {}).get("imageId")
            # 触发缩略图生成（后台异步，不等待完成）
            try:
                requests.post(
                    f"{BASE}/api/v1/actions/create-missing-thumbnails",
                    headers=h, timeout=10,
                )
            except Exception:  # noqa: BLE001
                pass
            return image_id
    except Exception as e:  # noqa: BLE001
        log.warning("image upload failed: %s", e)
    return None


def add_item(token: str, product: dict, location_id: str | None = None) -> dict:
    """把商品写入 Homebox。返回 {ok, id?, error?}。"""
    if not token:
        return {"ok": False, "error": "no_homebox_token"}
    # 优先使用前端传入的位置，其次用环境变量配置
    loc = location_id or os.getenv("HOMEBOX_LOCATION_ID") or None
    version = detect_version(token)
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 构建描述
    desc_parts = []
    if product.get("brand"):
        desc_parts.append(f"品牌: {product['brand']}")
    if product.get("barcode"):
        desc_parts.append(f"条码: {product['barcode']}")
    if product.get("source"):
        desc_parts.append(f"来源: {product['source']}")
    description = "\n".join(desc_parts)

    # notes: 品类信息
    notes = product.get("category", "")

    if version == "v2":
        type_id = _item_entity_type_id(token)
        # 1. 创建实体（POST，只传基础字段）
        payload = {
            "name": product["name"],
            "description": description,
            "quantity": 1,
        }
        if type_id:
            payload["entityTypeId"] = type_id
        if loc:
            payload["parentId"] = loc
        r = requests.post(f"{BASE}/api/v1/entities", json=payload, headers=h, timeout=TIMEOUT, verify=False)

        if r.status_code not in (200, 201):
            return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:300]}

        entity_id = (r.json() or {}).get("id")
        if not entity_id:
            return {"ok": False, "error": "no_entity_id"}

        # 2. PUT 全量更新扩展字段
        put_body = {
            "name": product["name"],
            "description": description,
            "quantity": 1,
            "notes": notes,
        }
        if product.get("barcode"):
            put_body["serialNumber"] = product["barcode"]
        if product.get("specification"):
            put_body["modelNumber"] = product["specification"]
        if product.get("manufacturer"):
            put_body["manufacturer"] = product["manufacturer"]
        if loc:
            put_body["parentId"] = loc

        requests.put(
            f"{BASE}/api/v1/entities/{entity_id}",
            json=put_body, headers=h, timeout=TIMEOUT,
        )

        # 3. 下载并上传商品图片
        image_id = None
        if product.get("image"):
            img_bytes = _download_image(product["image"])
            if img_bytes:
                image_id = _upload_image(token, entity_id, img_bytes)
                if image_id:
                    log.info("uploaded image: %s", image_id)

        return {
            "ok": True,
            "id": entity_id,
            "version": version,
            "imageId": image_id,
        }

    elif version == "v1":
        payload = {"name": product["name"], "description": description}
        if loc:
            payload["locationId"] = loc
        r = requests.post(f"{BASE}/api/v1/items", json=payload, headers=h, timeout=TIMEOUT, verify=False)
        if r.status_code in (200, 201):
            return {"ok": True, "id": (r.json() or {}).get("id"), "version": version}
        return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:300]}

    return {"ok": False, "error": "cannot_detect_homebox_version"}
