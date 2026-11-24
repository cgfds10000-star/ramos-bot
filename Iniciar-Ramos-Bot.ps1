$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
        else { throw 'Instale Python 3.11 ou superior e abra este arquivo novamente.' }
        if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel preparar o Python.' }
    }
    & $pythonExe -m pip install -r backend\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar o modulo de Instagram. Confira sua conexao com a internet.' }
    if (-not (Test-Path -LiteralPath 'dist\client\index.html')) {
        if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Instale Node.js 22 ou superior para compilar a interface.' }
        & npm.cmd install
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar as dependencias da interface.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar a interface.' }
    }
    Write-Host 'Abra http://127.0.0.1:8765 no navegador. Para encerrar, pressione Ctrl+C.' -ForegroundColor Magenta
    & $pythonExe backend\app.py
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
