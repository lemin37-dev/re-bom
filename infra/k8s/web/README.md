# infra/k8s/web — 웹 화면 + 검색 API (k3s)

| 파일 | 내용 |
|---|---|
| `namespace.yaml` | 네임스페이스 `rebom-web` |
| `api.yaml` | 검색 API(FastAPI) Deployment·Service. `/health` 로 상태 확인, 지금은 샘플 데이터 모드 |
| `web.yaml` | 웹 화면(Next.js standalone) Deployment·Service |
| `ingress.yaml` | k3s 기본 Traefik. `/api/...` 는 `/api` 를 떼고(StripPrefix) 검색 API 로, 나머지는 웹 화면으로 |
| `kustomization.yaml` | 위 파일 묶음. **이미지 주소·태그는 여기 `images` 에서만** 관리한다 (`api.yaml`·`web.yaml` 의 `rebom-api`·`rebom-web` 은 자리표시 이름) |

- 웹 화면은 검색 API 를 같은 주소의 `/api` 로 부른다(`frontend/src/lib/api.ts` 기본값). 그래서 CORS 설정이 필요 없고, 나중에 CloudFront 의 `/api/*` 도 같은 길로 들어온다.
- 두 파드 모두 노드 `rebom/pool=main`(k3s 서버)에 뜬다. 이미지는 노드의 EC2 역할로 ECR 에서 받는다(pull secret 없음, `infra/terraform/compute`).
- 이미지: `infra/docker/api.Dockerfile`(컨텍스트 레포 루트), `infra/docker/web.Dockerfile`(컨텍스트 `frontend/`).

## 배포 흐름 (Argo CD)

**이 폴더는 직접 `kubectl apply` 하지 않는다.** Argo CD 가 `deploy/dev` 브랜치의 이 폴더대로 맞추고, 손으로 바꾼 것은 되돌린다(selfHeal).

1. `develop` 에 머지 → `.github/workflows/build-images.yml` 이 바뀐 쪽(api/web) 이미지를 빌드해 ECR 에 올린다 (`develop`, `sha-<7자리>` 태그)
2. 같은 워크플로의 deploy job 이 `deploy/dev` 브랜치의 `infra/k8s/web` 을 develop 과 맞추고, 빌드한 쪽만 `newTag` 를 `sha-<7자리>` 로 바꿔 커밋한다 (`.github/scripts/update_deploy_branch.sh`)
3. Argo CD 가 `deploy/dev` 를 최대 2~3분 안에 확인하고 배포한다 (`infra/k8s/argocd`)

- `develop` 의 이 폴더는 태그가 `develop` 인 채로 둔다. 실제 배포 태그는 `deploy/dev` 에만 있다.
- **`deploy/dev` 는 CI 만 쓰는 브랜치다.** 사람은 커밋하지 않는다 (예외: 아래 되돌리기). 매니페스트를 바꾸려면 develop 으로 PR 을 올린다 → 머지되면 deploy job 이 반영한다.
- 지금 떠 있는 버전: `deploy/dev` 의 `infra/k8s/web/kustomization.yaml` 또는 Argo CD 화면.

## 처음 한 번 (사람이 직접, AGENTS.md 5번)

1. 테라폼: `infra/terraform/registry`(ECR) 와 `infra/terraform/compute`(k3s, `install_k3s = true`) apply
2. 저장소 Actions **Variable** `AWS_ECR_PUSH_ROLE_ARN` = registry 출력 `github_push_role_arn` (비밀값 아님)
3. Argo CD 설치와 Application 등록: `infra/k8s/argocd/README.md`
4. develop 에 머지 → Actions "이미지 빌드 (ECR)" 성공 → `deploy/dev` 브랜치가 생기고 Argo CD 가 배포

수동 실행: Actions → "이미지 빌드 (ECR)" → Run workflow → 브랜치 **develop** → target `all` (기본 브랜치가 develop 이라 버튼이 보인다).
AWS 역할은 develop 브랜치 워크플로만 맡을 수 있어서, 다른 브랜치로 돌리면 로그인 단계에서 실패한다(정상). deploy job 도 develop 에서만 돈다.

## 내 PC 에서 확인 (Traefik 80 포트포워딩)

밖으로 여는 포트는 없다. 서버의 80(Traefik)을 SSM 으로 내 PC 8080 에 붙인다.

```bash
aws ssm start-session --profile rebom --region ap-northeast-2 --target <server_instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=80,localPortNumber=8080
```

다른 터미널에서:

```bash
curl http://localhost:8080/api/health      # {"status":"ok",...}
curl -I http://localhost:8080/             # 200
```

브라우저에서 `http://localhost:8080`.

상태 보기(읽기)는 kubectl 로 해도 된다: `kubectl -n rebom-web get pods`, `kubectl -n rebom-web logs deploy/api`.

## 되돌리기

- 특정 배포를 되돌리기: `deploy/dev` 에서 그 배포 커밋(`deploy(dev): ...`)을 `git revert` 해서 push → Argo CD 가 이전 태그로 맞춘다. 명령은 `infra/k8s/argocd/README.md`.
- 다음 develop 머지 때 deploy job 이 다시 새 태그로 바꾼다. 문제가 develop 코드에 있으면 develop 에서도 고친다.
- 전부 내리기: Application `rebom-web` 을 지운다 (`kubectl -n argocd delete application rebom-web`, 사람이 실행). finalizer 가 있어 `rebom-web` 리소스도 같이 지워진다.

## 문제가 생기면

- `ImagePullBackOff`: 이미지가 ECR 에 있는지(`aws ecr list-images --repository-name rebom/web --profile rebom`), 노드의 ECR 로그인 플러그인이 잡혔는지(`infra/terraform/compute` README 주의).
- `Pending`: 노드 라벨 확인 `kubectl get nodes --show-labels | grep rebom/pool`.
- 화면은 뜨는데 동물 목록이 안 나옴: `curl http://localhost:8080/api/animals?size=1` 로 Traefik → api 경로 확인. `kubectl -n rebom-web get middleware,ingress`.
- 머지했는데 안 바뀜: Actions 의 deploy job 로그 → `deploy/dev` 커밋 → Argo CD 화면(Sync 상태) 순서로 본다.
- `/api/docs` 는 `/openapi.json` 을 루트에서 찾아서 이 경로로는 열리지 않는다. API 문서는 `kubectl -n rebom-web port-forward svc/api 8000:80` 후 `http://localhost:8000/docs`.
