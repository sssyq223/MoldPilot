ARG PADDLE_BASE_IMAGE=m.daocloud.io/docker.io/nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04
FROM ${PADDLE_BASE_IMAGE}

ARG PADDLE_PACKAGE=paddlepaddle-gpu
ARG PADDLE_INDEX_URL=https://www.paddlepaddle.org.cn/packages/stable/cu126/
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_PDX_MODEL_SOURCE=BOS
WORKDIR /service

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates libgl1 libglib2.0-0 libgomp1 python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY services/paddleocr/requirements.txt /service/requirements.txt
RUN python3 -m pip install --no-cache-dir "${PADDLE_PACKAGE}==3.2.0" --index-url "${PADDLE_INDEX_URL}" \
    && python3 -m pip install --no-cache-dir -r /service/requirements.txt --index-url "${PYPI_INDEX_URL}"

COPY services/__init__.py /service/services/__init__.py
COPY services/paddleocr/__init__.py /service/services/paddleocr/__init__.py
COPY services/paddleocr/app.py /service/services/paddleocr/app.py

EXPOSE 8080
CMD ["python3", "-m", "uvicorn", "services.paddleocr.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--workers", "1"]
