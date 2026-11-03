@echo off
rem Live writes: Send to keyboard really writes to the AULA F75 Max.
cd /d "%~dp0"
set AULA_LIVE_WRITES=1
npx electron .
