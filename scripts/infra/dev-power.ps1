<#
.SYNOPSIS
  개발 인프라 전체 끄기/켜기/상태 (k3s EC2 + RDS). 스케줄러가 생기기 전까지 손으로 쓴다.

.DESCRIPTION
  status : 상태만 본다 (아무것도 바꾸지 않음)
  stop   : EC2 추가 노드(agent) → 서버(server) → RDS 순서로 끄고, 각각 멈출 때까지 기다린다
  start  : RDS 를 먼저 켜고 available 까지 기다린 뒤 서버 → 추가 노드 순서로 켠다
           (Airflow 가 메타 DB 를 찾으므로). 켠 뒤 SSM 연결이 될 때까지 기다리고 6443 포트포워딩 명령을 보여 준다

  대상: 태그 Project=rebom, Stage=dev 인 EC2 전부, RDS(기본 rebom-dev-pg, infra/terraform/database 출력이 있으면 그 값)
  꺼질 대상이 아닌 것: S3, ECR, Secrets Manager, VPC(NAT 없음, S3 게이트웨이 엔드포인트는 무료)
  사람이 직접 실행한다 (AGENTS.md 5번).

.EXAMPLE
  .\scripts\infra\dev-power.ps1 status
  .\scripts\infra\dev-power.ps1 stop
  .\scripts\infra\dev-power.ps1 start -Yes
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("status", "stop", "start")]
  [string]$Action,

  [string]$Profile = "rebom",
  [string]$Region = "ap-northeast-2",
  [string]$DbInstance = "",
  [string]$AccountId = "559987919299",

  # 확인 질문 없이 진행
  [switch]$Yes
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$common = @("--profile", $Profile, "--region", $Region)

function Invoke-Aws {
  param([string[]]$Arguments)
  $out = & aws @Arguments @common 2>&1
  if ($LASTEXITCODE -ne 0) {
    $text = ($out | Out-String)
    if ($text -match "expired|ExpiredToken|Token has expired|InvalidClientTokenId|login|SSO") {
      Write-Host "AWS 로그인이 만료된 것 같습니다. 콘솔에 rebom-admin 으로 들어간 상태에서: aws login --profile rebom-login" -ForegroundColor Yellow
    }
    throw "aws $($Arguments -join ' ') 실패: $text"
  }
  return $out
}

function Assert-Account {
  $acct = (Invoke-Aws @("sts", "get-caller-identity", "--query", "Account", "--output", "text") | Out-String).Trim()
  if ($acct -ne $AccountId) { throw "지금 자격 증명 계정이 $acct 입니다. $AccountId 가 아니라서 멈춥니다 (프로필 $Profile 확인)." }
}

function Resolve-DbInstance {
  if ($DbInstance) { return $DbInstance }
  $dir = Join-Path $RepoRoot "infra\terraform\database"
  if ((Get-Command terraform -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $dir ".terraform"))) {
    $id = (& terraform "-chdir=$dir" output -raw identifier 2>$null)
    if ($LASTEXITCODE -eq 0 -and $id) { return ($id | Out-String).Trim() }
  }
  return "rebom-dev-pg"
}

function Get-Nodes {
  $json = Invoke-Aws @(
    "ec2", "describe-instances",
    "--filters", "Name=tag:Project,Values=rebom", "Name=tag:Stage,Values=dev",
    "Name=instance-state-name,Values=pending,running,stopping,stopped",
    "--query", "Reservations[].Instances[].{Id:InstanceId,State:State.Name,Role:Tags[?Key=='Role']|[0].Value,Name:Tags[?Key=='Name']|[0].Value}",
    "--output", "json")
  return @(($json | Out-String) | ConvertFrom-Json)
}

function Get-DbState([string]$Id) {
  $out = & aws rds describe-db-instances --db-instance-identifier $Id --query "DBInstances[0].DBInstanceStatus" --output text @common 2>&1
  if ($LASTEXITCODE -ne 0) { return "없음" }
  return ($out | Out-String).Trim()
}

function Get-SsmPing([string]$Id) {
  $out = & aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$Id" --query "InstanceInformationList[0].PingStatus" --output text @common 2>&1
  if ($LASTEXITCODE -ne 0) { return "?" }
  return ($out | Out-String).Trim()
}

function Show-Status([string]$Db) {
  $rows = @()
  foreach ($n in (Get-Nodes)) {
    $ping = if ($n.State -eq "running") { Get-SsmPing $n.Id } else { "-" }
    $rows += [pscustomobject]@{ 종류 = "EC2"; 이름 = $n.Name; 역할 = $n.Role; 상태 = $n.State; SSM = $ping; ID = $n.Id }
  }
  $rows += [pscustomobject]@{ 종류 = "RDS"; 이름 = $Db; 역할 = "postgres"; 상태 = (Get-DbState $Db); SSM = "-"; ID = $Db }
  $rows | Format-Table -AutoSize | Out-String | Write-Host
}

function Confirm-Action([string]$Message) {
  if ($Yes) { return }
  $answer = Read-Host "$Message 계속할까요? (y/N)"
  if ($answer -notin @("y", "Y", "yes")) { Write-Host "취소했습니다."; exit 1 }
}

function Wait-DbState([string]$Id, [string]$Wanted, [int]$TimeoutMinutes = 25) {
  $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
  while ($true) {
    $state = Get-DbState $Id
    if ($state -eq $Wanted) { Write-Host "  RDS $Id : $state"; return }
    if ((Get-Date) -gt $deadline) { throw "RDS 가 $TimeoutMinutes 분 안에 $Wanted 가 되지 않았습니다 (지금 $state)" }
    Write-Host "  RDS $Id : $state ... 기다리는 중"
    Start-Sleep -Seconds 30
  }
}

function Stop-Nodes([object[]]$Nodes, [string]$Label) {
  $targets = @($Nodes | Where-Object { $_.State -in @("running", "pending") })
  if ($targets.Count -eq 0) { Write-Host "EC2 $Label : 꺼져 있음 (건너뜀)"; return }
  $ids = @($targets.Id)
  Write-Host "EC2 $Label 끄기: $($ids -join ', ')"
  Invoke-Aws (@("ec2", "stop-instances", "--instance-ids") + $ids + @("--query", "StoppingInstances[].InstanceId", "--output", "text")) | Out-Null
  Invoke-Aws (@("ec2", "wait", "instance-stopped", "--instance-ids") + $ids) | Out-Null
  Write-Host "  stopped"
}

function Start-Nodes([object[]]$Nodes, [string]$Label) {
  $targets = @($Nodes | Where-Object { $_.State -in @("stopped", "stopping") })
  if ($targets.Count -eq 0) { Write-Host "EC2 $Label : 켜져 있음 (건너뜀)"; return }
  $ids = @($targets.Id)
  Write-Host "EC2 $Label 켜기: $($ids -join ', ')"
  Invoke-Aws (@("ec2", "wait", "instance-stopped", "--instance-ids") + $ids) | Out-Null # stopping 이면 끝날 때까지
  Invoke-Aws (@("ec2", "start-instances", "--instance-ids") + $ids + @("--query", "StartingInstances[].InstanceId", "--output", "text")) | Out-Null
  Invoke-Aws (@("ec2", "wait", "instance-running", "--instance-ids") + $ids) | Out-Null
  foreach ($id in $ids) {
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-SsmPing $id) -ne "Online") {
      if ((Get-Date) -gt $deadline) { Write-Warning "$id SSM 이 10분 안에 Online 이 되지 않았습니다. 조금 뒤 status 로 다시 보세요."; break }
      Write-Host "  $id SSM 연결 기다리는 중..."
      Start-Sleep -Seconds 15
    }
  }
  Write-Host "  running"
}

Assert-Account
$db = Resolve-DbInstance

switch ($Action) {
  "status" {
    Show-Status $db
  }

  "stop" {
    Show-Status $db
    Confirm-Action "EC2(추가 노드 → 서버)와 RDS 를 끕니다."
    $nodes = Get-Nodes
    Stop-Nodes @($nodes | Where-Object { $_.Role -ne "k3s-server" }) "추가 노드"
    Stop-Nodes @($nodes | Where-Object { $_.Role -eq "k3s-server" }) "서버"

    $state = Get-DbState $db
    if ($state -eq "available") {
      Write-Host "RDS $db 끄기"
      Invoke-Aws @("rds", "stop-db-instance", "--db-instance-identifier", $db, "--query", "DBInstance.DBInstanceStatus", "--output", "text") | Out-Null
      Wait-DbState $db "stopped"
    } elseif ($state -in @("stopped", "stopping")) {
      Write-Host "RDS $db : $state (건너뜀)"
    } elseif ($state -eq "없음") {
      Write-Host "RDS $db : 없음 (건너뜀)"
    } else {
      Write-Warning "RDS $db 가 '$state' 상태라 끄지 않았습니다. 잠시 뒤 다시 실행하세요."
    }

    Write-Host ""
    Write-Host "끝. 남는 요금: EBS 디스크, RDS 스토리지, S3·ECR 저장."
    Write-Host "주의: RDS 는 꺼도 7일 뒤 AWS 가 저절로 켠다. 오래 쉴 때는 7일 안에 다시 stop 한다." -ForegroundColor Yellow
  }

  "start" {
    Show-Status $db
    Confirm-Action "RDS → 서버 → 추가 노드 순서로 켭니다."
    $state = Get-DbState $db
    if ($state -eq "stopped") {
      Write-Host "RDS $db 켜기"
      Invoke-Aws @("rds", "start-db-instance", "--db-instance-identifier", $db, "--query", "DBInstance.DBInstanceStatus", "--output", "text") | Out-Null
      Wait-DbState $db "available"
    } elseif ($state -eq "available") {
      Write-Host "RDS $db : available (건너뜀)"
    } elseif ($state -eq "없음") {
      Write-Warning "RDS $db 가 없습니다. EC2 만 켭니다."
    } else {
      Write-Host "RDS $db 가 '$state' 입니다. available 까지 기다립니다."
      Wait-DbState $db "available"
    }

    $nodes = Get-Nodes
    Start-Nodes @($nodes | Where-Object { $_.Role -eq "k3s-server" }) "서버"
    Start-Nodes @($nodes | Where-Object { $_.Role -ne "k3s-server" }) "추가 노드"

    $server = @(Get-Nodes | Where-Object { $_.Role -eq "k3s-server" }) | Select-Object -First 1
    Write-Host ""
    Write-Host "끝. k3s·Argo CD·Airflow 파드가 다 뜨기까지 몇 분 더 걸린다."
    Write-Host "퍼블릭 IP 는 바뀌었을 수 있지만 SSM 은 인스턴스 ID 로 붙으므로 상관없다."
    if ($server) {
      Write-Host "kubectl 용 6443 포트포워딩 (다른 창에서 켜 둔다):"
      Write-Host "  aws ssm start-session --profile $Profile --region $Region --target $($server.Id) --document-name AWS-StartPortForwardingSession --parameters portNumber=6443,localPortNumber=6443"
      Write-Host "  `$env:KUBECONFIG = `"`$HOME\.kube\rebom-dev.yaml`"; kubectl get pods -A"
    }
  }
}
