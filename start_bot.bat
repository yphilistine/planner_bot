@echo off
chcp 65001 > nul
title Telegram Planner Bot
echo ========================================
echo     Бот-планировщик встреч
echo ========================================
echo.
echo Запуск бота...
python planner_bot.py
pause