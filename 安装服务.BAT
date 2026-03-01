@echo off
REM 取当前批处理所在目录
set "BASEDIR=%~dp0"

# "jxyDesk Service"系统服务中显示的文件名
#
#

REM 安装 RustDesk 服务，使用当前目录下的 rustdesk.exe
sc.exe create RustDesk binPath= "\"%BASEDIR%rustdesk.exe\" --service" start= auto DisplayName= "jxyDesk Service"
sc.exe start RustDesk

echo.
echo RustDesk   服务安装并已尝试启动，按任意键退出…
pause >nul

#
#
# sc.exe stop RustDesk
# sc.exe start RustDesk