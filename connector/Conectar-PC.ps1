$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
        else { throw 'Instale Python 3.11 ou superior e abra o conector novamente.' }
        if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel preparar o Python.' }
    }
    $requirementsHash = (Get-FileHash 'backend\requirements.txt' -Algorithm SHA256).Hash
    $stamp = Join-Path $PSScriptRoot '.venv\ramos-requirements.txt'
    if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $requirementsHash) {
        Write-Host 'Preparando o conector. Na primeira vez pode levar alguns minutos.' -ForegroundColor Magenta
        & $pythonExe -m pip install --disable-pip-version-check -r backend\requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar as dependencias. Confira a conexao com a internet.' }
        Set-Content -LiteralPath $stamp -Value $requirementsHash
    }
    & $pythonExe pc_bridge.py
    if ($LASTEXITCODE -ne 0) { throw 'O conector nao foi iniciado. Confira as mensagens acima.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
