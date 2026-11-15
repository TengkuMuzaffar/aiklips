# claude-cut setup for Windows.  Usage: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-AI] [-Captions] [-All]
param([switch]$AI, [switch]$Captions, [switch]$All)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Installing FFmpeg..."
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv..."
  winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
}
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

$extras = @()
if ($AI -or $All) { $extras += "--extra", "ai" }
if ($Captions -or $All) { $extras += "--extra", "captions" }
if ($All) { $extras += "--extra", "bg-quality" }
uv sync @extras
uv run vedit --help | Out-Null
Write-Host "claude-cut is ready. Open this folder in Claude Code (run: claude) and say what you want to edit."
