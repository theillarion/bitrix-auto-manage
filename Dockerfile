FROM python:3.12-slim AS sdk-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ARG BITRIX_SDK_REF=v0.2.0
RUN git clone --depth 1 --branch ${BITRIX_SDK_REF} \
    https://github.com/theillarion/sdk-bitrix-time-manager /opt/sdk

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/sdk/src

COPY --from=sdk-builder /opt/sdk/src /opt/sdk/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "bitrix_auto_manage"]
CMD ["daemon", "--config", "/app/config"]
