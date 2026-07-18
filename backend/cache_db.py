"""本地条码缓存：把「扫过且成功解析」的结果存入 SQLite，
下次扫到相同条码先查缓存，命中则直接返回，省去 GS1 / 视觉识别的查询费用。

库文件位于挂载卷 /app/data/barcode_cache.db，容器重建不丢。
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("cache_db")

DB_PATH = Path(os.getenv("CACHE_DB_PATH", "/app/data/barcode_cache.db"))
_lock = threading.Lock()

_COLS = (
    "barcode, name, brand, specification, manufacturer, category, "
    "image, source, payload, hit_count, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cache (
            barcode TEXT PRIMARY KEY,
            name TEXT,
            brand TEXT,
            specification TEXT,
            manufacturer TEXT,
            category TEXT,
            image TEXT,
            source TEXT,
            payload TEXT,
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    return conn


def _to_product(row) -> dict:
    (barcode, name, brand, spec, manu, cat,
     image, source, _payload, _hit, _c, _u) = row
    return {
        "found": True,
        "from_cache": True,
        "source": source or "cache",
        "barcode": barcode,
        "name": name,
        "brand": brand,
        "specification": spec,
        "manufacturer": manu,
        "category": cat,
        "image": image,
    }


def get(barcode: str) -> dict | None:
    """命中返回带 from_cache=True 的商品 dict，否则 None。"""
    try:
        with _lock:
            conn = _conn()
            try:
                cur = conn.execute(
                    f"SELECT {_COLS} FROM cache WHERE barcode=?", (barcode,)
                )
                row = cur.fetchone()
            finally:
                conn.close()
        return _to_product(row) if row else None
    except Exception as e:  # noqa: BLE001
        log.warning("cache get failed for %s: %s", barcode, e)
        return None


def put(product: dict) -> None:
    """写入/覆盖一条缓存。仅在产品成功解析（found 且有 name）时生效。"""
    if not product or not product.get("found"):
        return
    name = (product.get("name") or "").strip()
    if not name:
        return
    barcode = str(product.get("barcode") or "").strip()
    if not barcode:
        return
    now = _now()
    try:
        with _lock:
            conn = _conn()
            try:
                conn.execute(
                    """INSERT INTO cache
                       (barcode,name,brand,specification,manufacturer,category,image,source,payload,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(barcode) DO UPDATE SET
                         name=excluded.name, brand=excluded.brand,
                         specification=excluded.specification, manufacturer=excluded.manufacturer,
                         category=excluded.category, image=excluded.image, source=excluded.source,
                         payload=excluded.payload, updated_at=excluded.updated_at""",
                    (
                        barcode, name,
                        product.get("brand"),
                        product.get("specification"),
                        product.get("manufacturer"),
                        product.get("category"),
                        product.get("image"),
                        product.get("source"),
                        json.dumps(product, ensure_ascii=False),
                        now, now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:  # noqa: BLE001
        log.warning("cache put failed for %s: %s", barcode, e)


def record_hit(barcode: str) -> None:
    try:
        with _lock:
            conn = _conn()
            try:
                conn.execute(
                    "UPDATE cache SET hit_count = hit_count + 1 WHERE barcode=?", (barcode,)
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:  # noqa: BLE001
        pass


def stats() -> dict:
    try:
        with _lock:
            conn = _conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(hit_count),0) FROM cache"
                ).fetchone()
            finally:
                conn.close()
        return {"count": row[0] or 0, "hits": row[1] or 0}
    except Exception:  # noqa: BLE001
        return {"count": 0, "hits": 0}


def clear() -> int:
    """清空缓存表，返回清空前条数。"""
    try:
        with _lock:
            conn = _conn()
            try:
                n = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] or 0
                if n:
                    conn.execute("DELETE FROM cache")
                    conn.commit()
            finally:
                conn.close()
        return n
    except Exception:  # noqa: BLE001
        return 0
