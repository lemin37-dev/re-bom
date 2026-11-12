FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY pyproject.toml ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install ".[storage]"

FROM python:3.12-slim

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_DEFAULT_REGION=ap-northeast-2

RUN useradd --system --uid 10002 --no-create-home rebom-storage

COPY --from=build /opt/venv /opt/venv
COPY scripts/storage/rebom_run_aws_real_silver_test.py /app/scripts/storage/rebom_run_aws_real_silver_test.py

WORKDIR /app
USER 10002

ENTRYPOINT ["python"]
