# 扫码录入 Homebox - 后端镜像
FROM python:3.12-slim

# zbar 共享库：pyzbar 解码条码依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends libzbar0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --timeout 120 --retries 10 -r requirements.txt

COPY backend/ /app/
COPY frontend/ /app/frontend/

EXPOSE 8000
CMD ["python", "app.py"]
