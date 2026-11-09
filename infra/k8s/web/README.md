# infra/k8s/web — 웹 화면 + 검색 API (k3s)

| 파일 | 내용 |
|---|---|
| `namespace.yaml` | 네임스페이스 `rebom-web` |
| `api.yaml` | 검색 API(FastAPI) Deployment·Service. 이미지 `rebom/api:develop`, `/health` 로 상태 확인, 지금은 샘플 데이터 모드 |
| `web.yaml` | 웹 화면(Next.js standalone) Deployment·Service. 이미지 `rebom/web:develop` |
| `ingress.yaml` | k3s 기본 Traefik. `/api/...` 는 `/api` 를 떼고(StripPrefix) 검색 API 로, 나머지는 웹 화면으로 |
| `kustomization.yaml` | 위 파일 묶음. 이미지 태그를 여기서 바꾼다 |

- 웹 화면은 검색 API 를 같은 주소의 `/api` 로 부른다(`frontend/src/lib/api.ts` 기본값). 그래서 CORS 설정이 필요 없고, 나중에 CloudFront 의 `/api/*` 도 같은 길로 들어온다.
- 두 파드 모두 노드 `rebom/pool=main`(k3s 서버)에 뜬다. 이미지는 노드의 EC2 역할로 ECR 에서 받는다(pull secret 없음, `infra/terraform/compute`).
- 이미지: `infra/docker/api.Dockerfile`(컨텍스트 레포 루트), `infra/docker/web.Dockerfile`(컨텍스트 `frontend/`). 빌드는 `.github/workflows/build-images.yml`.

## 순서 (모두 사람이 직접, AGENTS.md 5번)

### 1. 테라폼 apply

`infra/terraform/registry`(ECR) 와 `infra/terraform/compute`(k3s 서버, `install_k3s = true`) 를 apply 한다. 각 폴더 README 참고.

### 2. GitHub 저장소 Variable 등록

registry 출력 `github_push_role_arn` 을 저장소 Settings → Secrets and variables → Actions → **Variables** 에 `AWS_ECR_PUSH_ROLE_ARN` 으로 넣는다. 비밀값이 아니다.

```bash
cd infra/terraform/registry
terraform output -raw github_push_role_arn
gh variable set AWS_ECR_PUSH_ROLE_ARN --repo lemin37-dev/re-bom --body "<위 값>"
```

### 3. 첫 이미지 올리기

- 이 워크플로가 `develop` 에 처음 머지되면 워크플로 파일이 바뀐 것으로 잡혀 api·web 둘 다 빌드된다.
- 그 뒤 수동 실행: Actions → "이미지 빌드 (ECR)" → Run workflow → 브랜치 **develop** 선택 → target `all`.
  (수동 실행 버튼은 워크플로 파일이 기본 브랜치 `main` 에도 있어야 보인다. main 에 아직 없으면 위의 머지 빌드를 쓴다)
- AWS 역할은 develop 브랜치 워크플로만 맡을 수 있어서, 다른 브랜치로 수동 실행하면 로그인 단계에서 실패한다(정상).
- 확인: `aws ecr list-images --repository-name rebom/web --profile rebom`

### 4. 배포

내 PC 에서 kubectl 이 되는 상태(SSM 6443 포트포워딩, `infra/terraform/compute` README)에서:

```bash
kubectl kustomize infra/k8s/web        # 적용될 내용 미리 보기
kubectl apply -k infra/k8s/web
kubectl -n rebom-web get pods -w       # api, web 이 Running·READY 1/1
```

새 이미지가 올라온 뒤 다시 띄우기 (`develop` 태그를 덮어쓰므로 재시작하면 새로 받는다):

```bash
kubectl -n rebom-web rollout restart deploy/api deploy/web
```

특정 커밋으로 고정·되돌리기: `kustomization.yaml` 의 `newTag` 를 `sha-<커밋 7자리>` 로 바꾸고 다시 `kubectl apply -k`.

### 5. 내 PC 에서 확인 (Traefik 80 포트포워딩)

밖으로 여는 포트는 없다. 서버의 80(Traefik)을 SSM 으로 내 PC 8080 에 붙인다.

```bash
aws ssm start-session --profile rebom --target <server_instance_id>   --document-name AWS-StartPortForwardingSession   --parameters portNumber=80,localPortNumber=8080
```

다른 터미널에서:

```bash
curl http://localhost:8080/api/health      # {"status":"ok",...}
curl -I http://localhost:8080/             # 200
```

브라우저에서 `http://localhost:8080`.

## 문제가 생기면

- `ImagePullBackOff`: 이미지가 ECR 에 있는지, 노드의 ECR 로그인 플러그인이 잡혔는지(`infra/terraform/compute` README 주의).
- `Pending`: 노드 라벨 확인 `kubectl get nodes --show-labels | grep rebom/pool`.
- 화면은 뜨는데 동물 목록이 안 나옴: `curl http://localhost:8080/api/animals?size=1` 로 Traefik → api 경로 확인. `kubectl -n rebom-web get middleware,ingress`.
- `/api/docs` 는 `/openapi.json` 을 루트에서 찾아서 이 경로로는 열리지 않는다. API 문서는 `kubectl -n rebom-web port-forward svc/api 8000:80` 후 `http://localhost:8000/docs`.

## 되돌리기

```bash
kubectl delete -k infra/k8s/web
```
