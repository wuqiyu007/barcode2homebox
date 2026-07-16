"""商品条码查询：多源兜底。
策略：中国条码(69x)优先走 GS1(更权威更全)，否则走 OFF(国际食品覆盖)。
若首选源查不到，回落到另一源。两源都空则返回未找到。
AI 识图兜底由 app.py 在"完全查不到"时调用。
"""
import os
import base64
import datetime
import hashlib
import hmac
import json
import logging

import requests

import config as cfg

log = logging.getLogger("lookup")

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
TIMEOUT = int(os.getenv("LOOKUP_TIMEOUT", "12"))


def lookup_openfoodfacts(barcode: str) -> dict | None:
    """免费、稳定、全球食品库。返回结构化商品信息或 None。"""
    try:
        headers = {"User-Agent": "barcode2homebox/1.0 (home-inventory-scanner)"}
        r = requests.get(OFF_URL.format(barcode=barcode), timeout=TIMEOUT, headers=headers)
        if r.status_code != 200:
            return None
        d = r.json()
        if d.get("status") != 1 or not d.get("product"):
            return None
        p = d["product"]
        name = (
            p.get("product_name_zh")
            or p.get("product_name")
            or p.get("generic_name")
        )
        if not name:
            return None
        return {
            "found": True,
            "source": "openfoodfacts",
            "barcode": barcode,
            "name": name.strip(),
            "brand": (p.get("brands") or "").split(",")[0].strip() or None,
            "category": (p.get("categories") or "").split(",")[-1].strip() or None,
            "image": p.get("image_front_url") or p.get("image_url"),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("OFF lookup failed for %s: %s", barcode, e)
        return None


def _build_gs1_auth(secret_id: str, secret_key: str) -> tuple[str, dict]:
    """构建腾讯云市场 API 网关 HMAC-SHA1 签名鉴权。

    签名算法:
      signing_str = "x-date: <GMT_date>"  (不带尾部换行)
      signature   = Base64(HMAC-SHA1(signing_str, secret_key))
      Authorization = JSON {"id": secret_id, "x-date": date, "signature": signature}
    """
    gmt_date = datetime.datetime.now(datetime.UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    signing_str = f"x-date: {gmt_date}"
    sig = base64.b64encode(
        hmac.new(secret_key.encode(), signing_str.encode(), hashlib.sha1).digest()
    ).decode()
    auth = json.dumps({"id": secret_id, "x-date": gmt_date, "signature": sig})
    headers = {
        "Authorization": auth,
        "x-date": gmt_date,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }
    return gmt_date, headers


def lookup_gs1(barcode: str) -> dict | None:
    """腾讯云市场官方 GS1 接口（中国物品编码中心数据）。

    需配置 GS1_API_URL + GS1_SECRET_ID + GS1_SECRET_KEY（界面设置或环境变量）。
    返回字段：名称、品牌、规格、厂家、分类、图片等。
    """
    c = cfg.load()
    url = c.get("gs1_api_url")
    if not url:
        return None
    secret_id = c.get("gs1_secret_id")
    secret_key = c.get("gs1_secret_key")
    if not secret_id or not secret_key:
        log.warning("GS1_API_URL set but GS1_SECRET_ID/SECRET_KEY missing")
        return None

    try:
        _, headers = _build_gs1_auth(secret_id, secret_key)
        params = {"Code": barcode}
        r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("GS1 API http %s for %s", r.status_code, barcode)
            return None
        d = r.json()
        # 云市场返回格式：status="200" 表示成功, status="400" 表示未找到
        if d.get("status") not in ("200", 200):
            log.info("GS1: barcode %s not found (status=%s)", barcode, d.get("status"))
            return None

        name = d.get("ItemName")
        if not name:
            return None
        # GS1 对隐私未公开的条码返回 "****(企业未公开详细信息！)"，视为未找到
        if "企业未公开" in name or name.strip().startswith("*"):
            log.info("GS1: barcode %s masked/private data, treating as not found", barcode)
            return None
        brand = d.get("BrandName") or None
        spec = d.get("ItemSpecification") or None
        firm = d.get("FirmName") or None
        category = d.get("ItemClassName") or None
        images = d.get("Image")
        image = None
        if images and isinstance(images, list) and len(images) > 0:
            image = images[0].get("Imageurl")
        return {
            "found": True,
            "source": "gs1-china",
            "barcode": barcode,
            "name": str(name).strip(),
            "brand": str(brand).strip() if brand else None,
            "specification": spec,
            "manufacturer": firm,
            "category": category,
            "image": image,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("GS1 lookup failed for %s: %s", barcode, e)
        return None


def lookup(barcode: str) -> dict:
    """主查询：多源兜底。中国条码优先 GS1，国际条码优先 OFF，回落到另一源。"""
    # 中国条码前缀 690-699，优先走 GS1（更权威更全）
    if barcode.startswith("69"):
        sources = (lookup_gs1, lookup_openfoodfacts)
    else:
        sources = (lookup_openfoodfacts, lookup_gs1)
    for fn in sources:
        res = fn(barcode)
        if res:
            return res
    return {"found": False, "barcode": barcode}
