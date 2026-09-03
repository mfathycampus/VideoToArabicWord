@echo off
chcp 65001 >nul
setlocal
title تثبيت محرّك Cohere Transcribe Arabic

REM المفسّر الذي يشغّل التطبيق فعليًا — كما أبلغ عنه فحص البرنامج.
REM غيّره هنا فقط إن نقلت Python إلى مكان آخر.
set "PY=C:\Users\IBM\AppData\Local\Programs\Python\Python312\python.exe"

if not exist "%PY%" (
  echo لم يُعثر على المفسّر: %PY%
  echo جارٍ استخدام python من PATH بدلًا منه...
  set "PY=python"
)

cd /d "%~dp0"
"%PY%" tools\install_engine.py cohere
set RC=%ERRORLEVEL%

echo.
if %RC%==0 (
  echo تم التثبيت بنجاح. افتح البرنامج واختر المحرّك.
) else (
  echo فشل التثبيت — راجع الرسائل أعلاه.
)
echo.
pause
