# infra/k8s/argocd — Argo CD (개발 k3s)

`deploy/dev` 브랜치를 보고 `infra/k8s/web`(웹 화면 + 검색 API)을 클러스터에 맞춘다. pull 방식이라 클러스터에 들어오는 길이 없어도 된다.

| 파일 | 내용 |
|---|---|
| `values.yaml` | 공식 Helm 차트 `argo/argo-cd` 최소 구성. HA·SSO(dex)·알림 끔, ApplicationSet 0대, 서버는 ClusterIP, 노드 `rebom/pool=main` |
| `apps/rebom-web.yaml` | Application `rebom-web`: `lemin37-dev/re-bom` 의 `deploy/dev` 브랜치 `infra/k8s/web` → 네임스페이스 `rebom-web`. 자동 동기화(prune, selfHeal), 네임스페이스 자동 생성 |
| `apps/airflow.yaml` | Application `airflow`: Airflow Helm 차트 1.22.0 + develop 의 `infra/k8s/airflow/values.yaml` + `infra/k8s/ingest`. 설치·Secret 은 `infra/k8s/airflow/README.md` |

- 차트 버전 **10.11.1** (Argo CD v3.5.6, 2026-10-23 배포)으로 고정한다. 올릴 때는 나온 지 2주 넘은 버전으로, `helm show values` 로 바뀐 값 이름을 확인한다.
- 레포가 public 이라 레포 자격 증명(repository secret)은 필요 없다. private 으로 바꾸면 읽기 전용 deploy key 를 등록해야 한다.
- 배포 흐름과 `deploy/dev` 규칙은 `infra/k8s/web/README.md`.

## 메모리

| 구성 요소 | requests | limits |
|---|---|---|
| application-controller | 256Mi | 768Mi |
| repo-server | 128Mi | 512Mi |
| server | 64Mi | 256Mi |
| redis | 32Mi | 128Mi |
| 합계 | 약 0.5GB | 약 1.6GB |

평소에는 0.5~1GB 정도 쓴다. 서버(t3.xlarge, 16GB)에 Airflow·임베딩 배치와 같이 올라간다. 설치 뒤 `kubectl top pods -n argocd` 로 실제 값을 보고 조정한다.

## 설치 (사람이 직접, AGENTS.md 5번)

내 PC 에서 kubectl 이 되는 상태(SSM 6443 포트포워딩, `infra/terraform/compute` README)에서. PowerShell 이면 줄 끝 `\` 를 백틱(`` ` ``)으로 바꾼다.

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update argo
helm template argocd argo/argo-cd --version 10.11.1 -n argocd -f infra/k8s/argocd/values.yaml > /dev/null   # 값 확인만

helm upgrade --install argocd argo/argo-cd --version 10.11.1 \
  -n argocd --create-namespace -f infra/k8s/argocd/values.yaml
kubectl -n argocd get pods -w     # application-controller, repo-server, server, redis 가 Running
```

`values.yaml` 을 바꾼 뒤에도 같은 `helm upgrade --install` 명령을 쓴다.

## 화면 접속

1. SSM 으로 서버 80(Traefik)을 내 PC 8080 에 붙인다 (kubectl 필요 없음)

```powershell
.\scripts\infra\dev-tunnel.ps1        # bash: scripts/infra/dev-tunnel.sh
```

2. 브라우저에서 `http://argocd.localhost:8080` (`*.localhost` 는 브라우저가 내 PC 로 보낸다, hosts 파일 수정 불필요)
   - Traefik 호스트 규칙 `argocd.localhost` → `argocd-server`(values `server.ingress`). Traefik 이 HTTP 로 넘기므로 `server.insecure: true` 다. 내 PC ~ 서버 구간은 SSM 터널이 암호화한다
   - CloudFront 는 Host 를 origin 주소로 바꿔 보내서 밖에서는 이 규칙에 닿지 않는다 (`infra/terraform/edge`)
   - kubectl 로 붙여도 된다: `kubectl -n argocd port-forward svc/argocd-server 8081:80` → `http://localhost:8081`
3. 사용자 `admin`, 비밀번호는 처음 설치 때 만들어진 값

```powershell
$p = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p))
```

bash: `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d`

4. 로그인 뒤 User Info → Update Password 로 바꾸고, 초기 비밀번호 Secret 을 지운다: `kubectl -n argocd delete secret argocd-initial-admin-secret`. 비밀번호는 팀 비밀번호 관리 도구에만 둔다 (레포·노션·채팅에 적지 않음).

## Application 등록

```bash
kubectl apply -f infra/k8s/argocd/apps/rebom-web.yaml
kubectl -n argocd get application rebom-web     # SYNC STATUS Synced, HEALTH Healthy
```

`deploy/dev` 브랜치가 아직 없으면 `ComparisonError`(브랜치 없음)가 보인다. 브랜치가 생기면 다음 확인(최대 2~3분) 때 저절로 배포한다.

## deploy/dev 처음 만들기

- 보통은 할 일이 없다. deploy job 이 처음 돌 때 `deploy/dev` 가 없으면 이번 develop 커밋에서 만들고, 빌드한 이미지 태그를 `sha-<7자리>` 로 커밋한다. `build-images.yml` 이 바뀐 머지라면 api·web 이 둘 다 빌드된다.
- 빌드 없이 먼저 만들어야 할 때만 (태그는 `develop` 그대로, 사람이 실행):

```bash
git fetch origin develop
git push origin origin/develop:refs/heads/deploy/dev
```

- 권한: `deploy/dev` 에는 브랜치 규칙(Ruleset)이 없다 (2026-11-09 조회: `RS_Develop_Restrict`·`RS_Main_Restrict` 는 각각 `develop`·`main` 에만). 저장소 기본 워크플로 권한은 read 지만 deploy job 이 `permissions: contents: write` 를 따로 받으므로 `GITHUB_TOKEN` 으로 push 할 수 있다. 나중에 `deploy/dev` 에 규칙을 걸면 GitHub Actions 를 우회(bypass) 대상으로 넣어야 한다.
- `GITHUB_TOKEN` 으로 한 push 는 다른 워크플로를 다시 띄우지 않는다 (반복 실행 없음).

## 되돌리기

배포 커밋은 `deploy(dev): api=sha-xxxxxxx web=sha-yyyyyyy (zzzzzzz)` 모양이다. 되돌릴 커밋을 골라 revert 한다 (`deploy/dev` 에 사람이 커밋하는 유일한 경우).

```bash
git fetch origin deploy/dev
git switch -c revert-deploy origin/deploy/dev
git log --oneline -5                 # 되돌릴 deploy(dev) 커밋 확인
git revert --no-edit <커밋>
git push origin HEAD:deploy/dev
git switch - && git branch -D revert-deploy
```

Argo CD 가 이전 태그로 맞춘다. 급하면 Argo CD 화면에서 `rebom-web` → Refresh. 다음 develop 머지 때 deploy job 이 다시 새 태그를 쓰므로, 원인이 코드에 있으면 develop 에서도 고친다.

Argo CD 화면의 History and Rollback 은 자동 동기화가 켜져 있으면 막힌다. 그래서 git revert 로 한다.

## 손으로 apply 했던 리소스 넘겨주기

전에 `kubectl apply -k infra/k8s/web` 로 손으로 올린 리소스가 있어도 지우지 않아도 된다.

1. Application 을 등록하면 Argo CD 가 같은 이름의 리소스를 찾아 자기 것으로 표시하고(`argocd.argoproj.io/instance` 라벨), `deploy/dev` 내용과 다르면 맞춘다. 이미지가 `develop` → `sha-` 태그로 바뀌면서 파드가 한 번 새로 뜬다
2. 확인: `kubectl -n rebom-web get deploy -o wide` 의 이미지가 `sha-` 태그, Argo CD 에서 `rebom-web` 이 Synced·Healthy
3. 그 뒤로는 손으로 apply 하지 않는다. selfHeal 이 켜져 있어 손으로 바꾼 것은 곧 되돌려진다

## 지우기 (사람이 실행)

```bash
kubectl -n argocd delete application rebom-web   # rebom-web 리소스도 같이 지워진다 (finalizer)
helm uninstall argocd -n argocd
```

CRD(`applications.argoproj.io` 등)는 helm 이 남겨 둔다. 완전히 지우려면 `kubectl get crd | grep argoproj` 로 보고 지운다.
