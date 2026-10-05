@echo off
cd /d "%~dp0"
python -m admin_server.local --connection-key
pause
