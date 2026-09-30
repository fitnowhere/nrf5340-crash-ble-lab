FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils-arm-none-eabi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY service /app/service

RUN groupadd --system diagnostics \
    && useradd --system --gid diagnostics --home-dir /app diagnostics \
    && mkdir -p /data \
    && chown -R diagnostics:diagnostics /app /data

ENV DIAG_SERVICE_DATA=/data
ENV DIAG_ADDR2LINE=arm-none-eabi-addr2line
EXPOSE 8765
USER diagnostics
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8765"]
