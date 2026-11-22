@echo off
cd /d "%~dp0"
set "PY=E:\anaconda\python.exe"
if not exist "%PY%" (
  echo Could not find Python at %PY%
  echo Open Anaconda Prompt and run:  python -m streamlit run app.py
  pause
  exit /b
)
"%PY%" -m streamlit run "%~dp0app.py" --server.address localhost --server.port 8501
pause
