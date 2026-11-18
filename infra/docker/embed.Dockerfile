# 사진 임베딩 (src/rebom/embedding) 이미지. 빌드 컨텍스트는 레포 루트 (.dockerignore).
# Airflow DAG(dags/rescue_ingest.py)가 사진 수집이 끝난 뒤 KubernetesPodOperator 로 띄운다.
#   docker build -f infra/docker/embed.Dockerfile -t rebom-embed .
#   docker run --rm -e REBOM_S3_BUCKET=<버킷> rebom-embed embed --limit 0 --dry-run
# Gemini 키와 DB 접속 정보는 Secrets Manager(rebom/dev/gemini_api_key, rebom/dev/rebom-db)에서 노드 역할로 읽는다.
# 이미지·환경변수에 키를 넣지 않는다. 모델을 서버에 올리지 않으므로(Gemini API) 이미지가 가볍다.

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ".[embed]"

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10003 --no-create-home rebom-embed
COPY --from=build /opt/venv /opt/venv
WORKDIR /tmp
USER 10003
ENTRYPOINT ["python", "-m", "rebom.embedding.cli"]
