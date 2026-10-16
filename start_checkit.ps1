$ErrorActionPreference = 'Stop'
$runtimePath = Join-Path $PSScriptRoot '.checkit-tools\python\python.exe'
$projectPath = Join-Path $PSScriptRoot 'CheckIT_MVP'
if (-not (Test-Path -LiteralPath $runtimePath)) {
    throw 'Local Python runtime was not found. Follow CheckIT_MVP/README.md to install Python.'
}
Set-Location -LiteralPath $projectPath
& $runtimePath -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
