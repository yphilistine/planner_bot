@echo off
chcp 65001
pip install "python-telegram-bot[job-queue]"
echo.
if %errorlevel% == 0 (
    echo Зависимости успешно установлены
) else (
    echo Ошибка при установке зависимостей
)

pause

