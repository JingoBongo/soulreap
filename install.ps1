<#
  Install soulreap into a harness skills directory (Windows / PowerShell).

    ./install.ps1 claude          -> ~/.claude/skills/soulreap
    ./install.ps1 pi              -> ~/.pi/agent/skills/soulreap
    ./install.ps1 opencode        -> ~/.config/opencode/skills/soulreap
    ./install.ps1 C:\path\to\dir  -> that directory

  Copies SKILL.md, scripts/ and references/. The engine is path-independent.
#>
param([Parameter(Mandatory = $true)][string]$Target)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$home_ = $env:USERPROFILE

switch ($Target) {
  'claude'   { $dest = Join-Path $home_ '.claude\skills\soulreap' }
  'pi'       { $dest = Join-Path $home_ '.pi\agent\skills\soulreap' }
  'opencode' { $dest = Join-Path $home_ '.config\opencode\skills\soulreap' }
  default    { $dest = $Target }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Warning "'uv' not found on PATH — soulreap needs it at runtime (https://docs.astral.sh/uv/)"
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $here 'SKILL.md') $dest -Force
Copy-Item (Join-Path $here 'scripts') $dest -Recurse -Force
Copy-Item (Join-Path $here 'references') $dest -Recurse -Force
Get-ChildItem $dest -Recurse -Force -Include '__pycache__', '.pytest_cache', '.ruff_cache', '*.pyc' -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Output "installed soulreap -> $dest"
Write-Output "in SKILL.md, `$SKILL = $dest"
