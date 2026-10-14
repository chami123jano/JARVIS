# JARVIS setup. Safe to run again at any time: each step checks before it changes
# anything, and the summary at the end says what passed and what did not.
#
#   .\install.ps1                  everything, including start at login
#   .\install.ps1 -NoAutostart     do not start JARVIS when Windows starts
#   .\install.ps1 -SkipModels      do not download the language model
#
# Kept to plain ASCII: Windows PowerShell 5.1 misreads a UTF-8 script without a BOM.

param(
    [switch]$NoAutostart,
    [switch]$SkipModels
)

$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$results = New-Object System.Collections.ArrayList

function Report($name, $ok, $detail) {
    [void]$results.Add([pscustomobject]@{ Step = $name; Ok = $ok; Detail = $detail })
    if ($ok) { $mark = '[ ok ]' } else { $mark = '[FAIL]' }
    Write-Host ("{0} {1,-28} {2}" -f $mark, $name, $detail)
}

Write-Host ''
Write-Host 'JARVIS setup' -ForegroundColor Cyan
Write-Host ''

# 1. Python
$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    $version = & py -3 --version 2>&1
    Report 'Python' $true "$version"
} else {
    Report 'Python' $false 'not found - install Python 3.13 from python.org'
}

# 2. Virtual environment
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    & py -3 -m venv .venv
}
Report 'Virtual environment' (Test-Path $venvPython) '.venv'

# 3. Packages
if (Test-Path $venvPython) {
    & $venvPython -m pip install --quiet --upgrade pip 2>&1 | Out-Null
    $pip = & $venvPython -m pip install --quiet -r requirements.txt 2>&1
    if ($LASTEXITCODE -eq 0) {
        Report 'Python packages' $true 'requirements.txt installed'
    } else {
        Report 'Python packages' $false (($pip | Select-Object -Last 1) -as [string])
    }
}

# 4. Ollama, recent enough for qwen3.5
$ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (Test-Path $ollama) {
    $raw = (& $ollama --version 2>&1 | Out-String)
    $match = [regex]::Match($raw, '(\d+)\.(\d+)\.(\d+)')
    if ($match.Success) {
        $major = [int]$match.Groups[1].Value
        $minor = [int]$match.Groups[2].Value
        $recent = ($major -gt 0) -or ($minor -ge 34)
        if ($recent) {
            Report 'Ollama' $true ('version ' + $match.Value)
        } else {
            Report 'Ollama' $false ('version ' + $match.Value + ' is too old - update from ollama.com')
        }
    } else {
        Report 'Ollama' $false 'installed, version unreadable'
    }
} else {
    Report 'Ollama' $false 'not installed - get it from ollama.com/download'
}

# 5. Ollama settings. An existing model folder is kept, never overwritten.
$defaults = [ordered]@{
    'OLLAMA_MODELS'          = 'D:\ollama'
    'OLLAMA_FLASH_ATTENTION' = '1'
    'OLLAMA_KV_CACHE_TYPE'   = 'q8_0'
    'OLLAMA_KEEP_ALIVE'      = '30m'
}
foreach ($name in $defaults.Keys) {
    $current = [Environment]::GetEnvironmentVariable($name, 'User')
    if (-not $current) {
        [Environment]::SetEnvironmentVariable($name, $defaults[$name], 'User')
    }
}
$modelsPath = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
Report 'Ollama settings' $true ('models in ' + $modelsPath)

# 6. Ollama running, from the right folder. The service owns this from now on.
if (Test-Path $venvPython) {
    $repair = & $venvPython -c "import jarvis_service as s; print('ok' if s.ensure_ollama() else 'failed')" 2>&1 | Select-Object -Last 1
    Report 'Ollama running' ($repair -eq 'ok') ([string]$repair)
}

# 7. The language model
if ($SkipModels) {
    Report 'Language model' $true 'skipped'
} elseif (Test-Path $ollama) {
    $list = (& $ollama list 2>&1 | Out-String)
    if ($list -match 'qwen3\.5:9b') {
        Report 'Language model' $true 'qwen3.5:9b present'
    } else {
        Write-Host '  downloading qwen3.5:9b (6.6 GB) - this takes a while on a slow connection'
        & $ollama pull qwen3.5:9b
        $list = (& $ollama list 2>&1 | Out-String)
        Report 'Language model' ($list -match 'qwen3\.5:9b') 'qwen3.5:9b'
    }
}

# 8. Start at login
if (Test-Path $venvPython) {
    if ($NoAutostart) {
        & $venvPython jarvis_service.py --disable-autostart | Out-Null
        Report 'Start at login' $true 'off (as asked)'
    } else {
        $auto = & $venvPython jarvis_service.py --enable-autostart 2>&1 | Out-String
        Report 'Start at login' ($auto -match "'enabled': True") 'Task Manager > Startup apps > JARVIS'
    }
}

# 9. The health check
if (Test-Path $venvPython) {
    Write-Host ''
    Write-Host 'Running the health check...' -ForegroundColor Cyan
    $env:PYTHONIOENCODING = 'utf-8'
    $verify = & $venvPython verify.py --quick 2>&1 | Out-String
    $summary = ($verify -split "`n" | Where-Object { $_ -match 'passed,' } | Select-Object -Last 1)
    Report 'Health check' ($verify -match ' 0 failed') ([string]$summary).Trim()
}

# Summary
$failed = @($results | Where-Object { -not $_.Ok })
Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host 'Everything is ready.' -ForegroundColor Green
    Write-Host ''
    Write-Host 'Start JARVIS now:'
    Write-Host ('  & "' + (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe') + '" jarvis_service.py')
    Write-Host 'Look for the green dot in the system tray. Ctrl+Alt+J mutes.'
} else {
    Write-Host ($failed.Count.ToString() + ' step(s) need attention:') -ForegroundColor Yellow
    foreach ($item in $failed) { Write-Host ('  - ' + $item.Step + ': ' + $item.Detail) }
}
Write-Host ''
