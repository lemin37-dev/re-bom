#!/usr/bin/env bash
# deploy/dev 브랜치의 infra/k8s/web 을 이번 develop 커밋 기준으로 맞추고,
# 이번에 빌드한 이미지만 sha-<7자리> 태그로 바꿔 커밋·push 한다. Argo CD 가 deploy/dev 를 보고 배포한다.
# .github/workflows/build-images.yml 의 deploy job 에서만 실행한다.
#
# 환경 변수: SHA(이번 커밋), API_BUILT/WEB_BUILT(true|false), REGISTRY(ECR 주소)
# 다시 돌려도 결과가 같다: 바뀐 게 없으면 커밋하지 않고, push 가 밀리면 최신 deploy/dev 위에서 다시 한다.
set -euo pipefail

: "${SHA:?}" "${REGISTRY:?}"
API_BUILT="${API_BUILT:-false}"
WEB_BUILT="${WEB_BUILT:-false}"
BRANCH=deploy/dev
DIR=infra/k8s/web
NEW_TAG="sha-${SHA::7}"

command -v kustomize >/dev/null || { echo "kustomize 가 없습니다" >&2; exit 1; }
command -v yq >/dev/null || { echo "yq 가 없습니다" >&2; exit 1; }

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

current_tag() { # $1 = rebom-api | rebom-web. deploy/dev 에 지금 적힌 태그 (없으면 develop)
  local t
  t="$(yq -r ".images[] | select(.name == \"$1\") | .newTag" "$DIR/kustomization.yaml" 2>/dev/null || true)"
  if [ -z "$t" ] || [ "$t" = "null" ]; then t=develop; fi
  echo "$t"
}

for attempt in 1 2 3 4; do
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null; then
    git fetch --quiet origin "$BRANCH"
    git checkout --quiet -B "$BRANCH" "origin/$BRANCH"
  else
    echo "$BRANCH 가 없어서 이번 커밋에서 만듭니다"
    git checkout --quiet -B "$BRANCH" "$SHA"
  fi

  # 빌드하지 않은 쪽은 deploy/dev 에 있던 태그를 그대로 쓴다
  api_tag="$(current_tag rebom-api)"
  web_tag="$(current_tag rebom-web)"
  [ "$API_BUILT" = true ] && api_tag="$NEW_TAG"
  [ "$WEB_BUILT" = true ] && web_tag="$NEW_TAG"

  # 매니페스트는 이번 develop 커밋의 것으로 (지운 파일도 반영)
  rm -rf "$DIR"
  git checkout --quiet "$SHA" -- "$DIR"
  (
    cd "$DIR"
    kustomize edit set image \
      "rebom-api=$REGISTRY/rebom/api:$api_tag" \
      "rebom-web=$REGISTRY/rebom/web:$web_tag"
  )

  git add -A "$DIR"
  if git diff --cached --quiet; then
    echo "바뀐 것이 없습니다 (api=$api_tag web=$web_tag)"
    exit 0
  fi
  git commit --quiet -m "deploy(dev): api=$api_tag web=$web_tag (${SHA::7})"

  if git push --quiet origin "$BRANCH"; then
    echo "push 완료: api=$api_tag web=$web_tag"
    exit 0
  fi
  echo "push 실패 (다른 실행과 겹침?). 최신 $BRANCH 위에서 다시 합니다 ($attempt/4)" >&2
  sleep $((attempt * 5))
done

echo "deploy/dev push 를 4번 실패했습니다" >&2
exit 1
