@echo off
cd /d "%~dp0"
title 三汇蔡徐坤 - 开公网

REM 真程序就放在本文件旁边，写死路径，不猜、不找、不依赖 PATH
set CLOUDFLARED=%~dp0cloudflared.exe

if not exist "%CLOUDFLARED%" goto NO_CF

echo.
echo ============================================================
echo    三汇蔡徐坤 - 把搭子挂到公网上
echo ============================================================
echo.
echo   这个窗口一关，公网地址就失效了。
echo   想让别人一直能用，就把它最小化挂着，电脑别关。
echo.

echo   [1/2] 检查网页服务...
netstat -ano | findstr /R /C:"127.0.0.1:8848 .*LISTENING" >nul
if not errorlevel 1 goto WEB_UP

echo         没在跑，正在启动...
start "HypeBuddy 网页服务 - 别关这个" /min cmd /c "python webui.py"
timeout /t 5 /nobreak >nul
netstat -ano | findstr /R /C:"127.0.0.1:8848 .*LISTENING" >nul
if errorlevel 1 (
    echo         [!] 好像没起来，多半是 .env 里没填 API Key
    echo             双击 网页版.bat 单独跑一次，看它报啥错
) else (
    echo         起来了
)
goto TUNNEL

:WEB_UP
echo         已经在跑了，跳过

:TUNNEL
echo.
echo   [2/2] 开隧道，要等十来秒...
echo.
echo ------------------------------------------------------------
echo   下面日志里会有一行像这样的地址：
echo.
echo       https://xxxx-xxxx-xxxx.trycloudflare.com
echo.
echo   那一行就是公网地址，复制了发给别个，点开就能用。
echo ------------------------------------------------------------
echo.

"%CLOUDFLARED%" tunnel --url http://127.0.0.1:8848 --no-autoupdate

echo.
echo   隧道断了。重新双击本文件就是新地址。
pause
exit /b 0

:NO_CF
echo.
echo   [X] 找不到 cloudflared.exe
echo       它要跟本文件放在同一个文件夹里，路径是：
echo         %CLOUDFLARED%
echo.
pause
exit /b 1
