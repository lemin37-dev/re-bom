# 사진 태깅 (src/rebom/tagging) 이미지. 빌드 컨텍스트는 레포 루트 (.dockerignore).
# Airflow DAG(dags/photo_tags.py)가 KubernetesPodOperator 로 띄운다 (네임스페이스 rebom-tag-img).
#   docker build -f infra/docker/tag-img.Dockerfile -t rebom-tag-img .
#   docker run --rm -e OPENROUTER_KEY_TAG_IMG=<키> rebom-tag-img select --dry-run
# OpenRouter 키는 실행할 때 환경변수로만 받는다(K8s Secret rebom-tag-img). 이미지에 넣지 않는다.
# 임베딩·Jev 태깅은 각자 이미지를 따로 만든다(2026-11-19 결정). OpenRouter 호출 코드(rebom.common.openrouter)만 같이 쓴다.

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ".[tag]"

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_DEFAULT_REGION=ap-northeast-2
RUN useradd --system --uid 10003 --no-create-home rebom-tag
COPY --from=build /opt/venv /opt/venv
WORKDIR /tmp
USER 10003
ENTRYPOINT ["python", "-m", "rebom.tagging.run_photo_tags"]
