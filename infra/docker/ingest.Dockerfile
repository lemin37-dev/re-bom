# 구조동물 증분 수집 (src/rebom/ingestion) 이미지. 빌드 컨텍스트는 레포 루트 (.dockerignore).
# Airflow DAG(dags/rescue_ingest.py)가 KubernetesPodOperator 로 띄운다.
#   docker build -f infra/docker/ingest.Dockerfile -t rebom-ingest .
#   docker run --rm -e DATA_GO_KR_SERVICE_KEY=<키> rebom-ingest --dry-run
# 인증키는 실행할 때 환경변수로만 받는다(K8s Secret rebom-ingest). 이미지에 넣지 않는다.

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ".[ingest]"

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home rebom
COPY --from=build /opt/venv /opt/venv
# 작업 폴더는 쓰기 가능한 /tmp (수집기가 임시 폴더를 만든다). 결과는 S3 로만 간다
WORKDIR /tmp
USER 10001
ENTRYPOINT ["python", "-m", "rebom.ingestion.rescue_incremental"]
