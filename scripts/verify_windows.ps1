# Thin wrapper: runs the Python environment check with UTF-8 enabled.
# Usage (from the project root):  .\scripts\verify_windows.ps1
# NOTE: written without access to a Windows machine; not yet run on Windows.
$env:PYTHONUTF8 = "1"
$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
& $python scripts/verify_environment.py
exit $LASTEXITCODE
