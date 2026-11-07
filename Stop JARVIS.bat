@echo off
rem Stop everything in JARVIS: the service, the voice loop and the console.
rem Only Python run from this folder's own .venv, and the processes it started -- never
rem anything else that happens to be called server.py (the pharmacy's, for one).
rem Ollama is left running; it is a separate program.
powershell -NoProfile -Command ^
  "$home_ = '%~dp0'; $all = Get-CimInstance Win32_Process;" ^
  "$mine = @($all | Where-Object { $_.Name -match '^pythonw?\.exe$' -and (($_.ExecutablePath -and $_.ExecutablePath.StartsWith($home_, 'OrdinalIgnoreCase')) -or ($_.CommandLine -and $_.CommandLine.IndexOf($home_, [StringComparison]::OrdinalIgnoreCase) -ge 0)) -and $_.CommandLine -match '(jarvis_service|voice_loop|server)\.py' } | ForEach-Object { $_.ProcessId });" ^
  "do { $more = @($all | Where-Object { $mine -contains $_.ParentProcessId -and $mine -notcontains $_.ProcessId } | ForEach-Object { $_.ProcessId }); $mine += $more } while ($more.Count);" ^
  "$mine | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue };" ^
  "if ($mine.Count) { 'JARVIS stopped.' } else { 'JARVIS was not running.' }"
ping -n 3 127.0.0.1 >nul
