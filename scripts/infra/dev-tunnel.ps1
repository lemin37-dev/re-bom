<#
.SYNOPSIS
  관리 화면(Argo CD, Airflow)과 웹을 내 PC 에서 연다. SSM 포트포워딩 한 번: 서버 80(Traefik) -> 내 PC 8080.

.DESCRIPTION
  켜 둔 동안 브라우저에서:
    http://argocd.localhost:8080    Argo CD
    http://airflow.localhost:8080   Airflow
    http://localhost:8080           웹 화면 (/api/... 는 검색 API)
  *.localhost 는 브라우저가 127.0.0.1 로 보낸다(hosts 파일 수정 불필요). Ctrl+C 로 끝낸다.
  kubectl 이 필요하면 6443 포워딩(infra/terraform/compute README)을 따로 켠다.
  서버가 꺼져 있으면 먼저: .\scripts\infra\dev-power.ps1 start

.EXAMPLE
  .\scripts\infra\dev-tunnel.ps1
  .\scripts\infra\dev-tunnel.ps1 -LocalPort 18080
#>
[CmdletBinding()]
param(
  [int]$LocalPort = 8080,
  [string]$Profile = "rebom",
  [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$common = @("--profile", $Profile, "--region", $Region)

$id = (& aws ec2 describe-instances @common `
    --filters "Name=tag:Project,Values=rebom" "Name=tag:Stage,Values=dev" "Name=tag:Role,Values=k3s-server" "Name=instance-state-name,Values=running" `
    --query "Reservations[0].Instances[0].InstanceId" --output text 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "서버를 찾지 못했습니다: $id (AWS 로그인 확인: aws login --profile rebom-login)" }
if (-not $id -or $id -eq "None") { throw "켜져 있는 k3s 서버가 없습니다. 먼저 .\scripts\infra\dev-power.ps1 start" }

Write-Host "서버 $id 의 80 -> localhost:$LocalPort"
Write-Host "  Argo CD : http://argocd.localhost:$LocalPort"
Write-Host "  Airflow : http://airflow.localhost:$LocalPort"
Write-Host "  웹      : http://localhost:$LocalPort"
Write-Host "Ctrl+C 로 끝냅니다."

& aws ssm start-session @common --target $id `
  --document-name AWS-StartPortForwardingSession `
  --parameters "portNumber=80,localPortNumber=$LocalPort"
