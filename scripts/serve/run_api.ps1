# Re:봄 검색 API 실행 (Windows PowerShell). 레포 어디서 실행해도 루트 기준으로 동작한다.
#   .\scripts\serve\run_api.ps1                 # sample 모드, http://localhost:8000/docs
#   .\scripts\serve\run_api.ps1 -Port 8080 -Mode sample
#   .\scripts\serve\run_api.ps1 -NoReload       # 자동 재시작 끄기
# 실행 정책 오류가 나면: powershell -ExecutionPolicy Bypass -File scripts\serve\run_api.ps1
param(
    [int]$Port = 8000,
    [string]$BindHost = "0.0.0.0",
    [ValidateSet("sample", "db")][string]$Mode = "",
    [switch]$NoReload
)
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "가상환경(.venv) 만드는 중..."
    python -m venv .venv
}
& $py -c "import fastapi, uvicorn, pydantic_settings, rebom" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "패키지 설치 중..."
    & $py -m pip install -q -e ".[serve,dev]"
}

# -Mode 를 주면 .env 보다 우선한다. 안 주면 .env 또는 기본값(sample)
if ($Mode) { $env:REBOM_DATA_MODE = $Mode }

$uvArgs = @("-m", "uvicorn", "rebom.serving.app:create_app", "--factory", "--host", $BindHost, "--port", $Port)
if (-not $NoReload) { $uvArgs += @("--reload", "--reload-dir", "src") }
Write-Host "검색 API: http://${BindHost}:${Port}/docs"
& $py @uvArgs
