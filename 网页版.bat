@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ============================================
echo    三汇蔡徐坤 - 网页版
echo    正在启动，浏览器会自动打开...
echo  ============================================
echo.
python webui.py
pause
