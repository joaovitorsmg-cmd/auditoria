@echo off
echo Instalando dependencias Python...
pip install playwright openpyxl requests xlrd
playwright install chromium
echo.
echo Pronto! Agora copie config.exemplo.ini para config.ini e preencha suas credenciais.
pause
