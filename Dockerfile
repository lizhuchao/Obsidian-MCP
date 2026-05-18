FROM python:3.12-slim

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1

COPY requirements.txt /app/requirements.txt

RUN pip install \
    --timeout 180 \
    --retries 10 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
    -r /app/requirements.txt

COPY app.py /app/app.py
COPY kb_seed.py /app/kb_seed.py
COPY scripts /app/scripts

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
