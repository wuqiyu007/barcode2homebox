"""运行时配置持久化。

把「用户可在界面填写」的配置（GS1 / Vision 凭据）从环境变量里抽出来，
存到挂载卷 /app/data/config.json，重启不丢；文件为空时回退到环境变量。
Homebox 地址（HOMEBOX_URL）属于基础设施配置，仍只写在 docker-compose 里，
不在此处管理。
"""
import json
import os
import threading
from pathlib import Path

# 仅这些键可由界面设置；其余（HOMEBOX_URL 等）走 docker-compose
DEFAULTS = {
    "gs1_api_url": "",
    "gs1_secret_id": "",
    "gs1_secret_key": "",
    "vision_api_url": "",
    "vision_api_key": "",
    "vision_model": "gpt-4o-mini",
}

# 文件为空时的环境变量兜底
ENV_MAP = {
    "gs1_api_url": "GS1_API_URL",
    "gs1_secret_id": "GS1_SECRET_ID",
    "gs1_secret_key": "GS1_SECRET_KEY",
    "vision_api_url": "VISION_API_URL",
    "vision_api_key": "VISION_API_KEY",
    "vision_model": "VISION_MODEL",
}

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/app/data/config.json"))
_lock = threading.Lock()


def load() -> dict:
    """合并：文件优先，文件为空字段回退环境变量。"""
    merged = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k in DEFAULTS:
                if k in data and data[k] not in (None, ""):
                    merged[k] = data[k]
    except Exception:  # noqa: BLE001
        pass
    for k, envk in ENV_MAP.items():
        if not merged.get(k):
            v = os.getenv(envk)
            if v:
                merged[k] = v
    return merged


def save(cfg: dict) -> dict:
    """只持久化已知键；空值显式写为空串，便于「清空某配置」。"""
    data = {k: (cfg.get(k) or "") for k in DEFAULTS}
    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return load()
