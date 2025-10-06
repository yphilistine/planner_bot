@echo off
chcp 65001
echo Установка зависимостей для бота-планировщика...
echo Устанавливаем python-telegram-bot с поддержкой Job Queue...
pip install "python-telegram-bot[job-queue]"
echo.
if %errorlevel% == 0 (
    echo Зависимости успешно установлены!
    echo.
    echo Теперь отредактируйте файл planner_bot.py и укажите ваш BOT_TOKEN
    echo.
) else (
    echo Ошибка при установке зависимостей!
)
pause