@echo off
cd /d "%~dp0"
python -m streamlit run "%~dp0app.py" --server.address localhost --server.port 8501
pause
