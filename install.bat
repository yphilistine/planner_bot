@echo off
chcp 65001
echo Устанавливаем python-telegram-bot с поддержкой Job Queue...
pip install "python-telegram-bot[job-queue]"
echo.
if %errorlevel% == 0 (
    echo Зависимости успешно установлены
) else (
    echo Ошибка при установке зависимостей
)

pause
