@echo off
rem Starts RepoHammer without a console window.
where pythonw >nul 2>&1 && (start "" pythonw "%~dp0app.py" & exit /b 0)
where pyw >nul 2>&1 && (start "" pyw "%~dp0app.py" & exit /b 0)
echo Python 3.10 or newer is needed: https://www.python.org/downloads/
pause
