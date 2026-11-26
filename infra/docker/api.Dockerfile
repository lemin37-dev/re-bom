# 검색 API (FastAPI, src/rebom/serving) 이미지. 빌드 컨텍스트는 레포 루트 (.dockerignore).
#   docker build -f infra/docker/api.Dockerfile -t rebom-api .
#   docker run --rm -p 8000:8000 rebom-api   → http://localhost:8000/health

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv  && /opt/venv/bin/pip install ".[serve]"

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH     PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     REBOM_DATA_MODE=sample     REBOM_SAMPLE_PATH=/app/data/sample/rescue_sample.json
RUN useradd --system --uid 10001 --no-create-home rebom
COPY --from=build /opt/venv /opt/venv
# 샘플 데이터 (개인정보 제거본). DB 모드가 생기면 빠진다
COPY data/sample /app/data/sample
WORKDIR /app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "rebom.serving.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
