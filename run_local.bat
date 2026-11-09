@echo off
cd /d %~dp0
python -m pip install -q -r requirements.txt
echo Etched L6 MES API Server: http://127.0.0.1:5050
python app.py
