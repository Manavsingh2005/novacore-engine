param(
    [string]$Message = "update: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
)

& .\.venv\Scripts\python.exe push_update.py "$Message"
