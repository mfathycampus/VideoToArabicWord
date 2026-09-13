@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title تثبيت محرّك Cohere Transcribe Arabic

REM المفسّر: يُكتشف من الجهاز، ولا يُكتب هنا مسارٌ ثابت.
REM كان هنا مسارٌ يخصّ جهاز المطوّر وحده — يعمل عنده ويفشل عند غيره،
REM ويسرّب اسم مستخدمه في ملفٍ عامّ.
REM
REM إن أردتَ مفسّرًا بعينه (بيئة معزولة مثلًا) اضبط VIDEO_AI_PYTHON
REM قبل تشغيل هذا الملف:  set VIDEO_AI_PYTHON=C:\path\to\python.exe

set "PY="

if defined VIDEO_AI_PYTHON (
  if exist "%VIDEO_AI_PYTHON%" set "PY=%VIDEO_AI_PYTHON%"
)

REM البيئة المعزولة داخل المشروع إن وُجدت — هي التي يعمل بها التطبيق
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

REM مُطلق Python الرسمي على ويندوز: يعرف المفسّرات المثبَّتة كلها.
REM الكتلة صريحة لا سطرٌ واحد بـ&&: ‏cmd يفصل عند && على مستوى الجملة
REM لا داخل if، فيصير الإسناد يقع أحيانًا رغم أن الشرط كاذب.
if not defined PY (
  where py >nul 2>&1
  if !ERRORLEVEL!==0 set "PY=py -3"
)

if not defined PY (
  where python >nul 2>&1
  if !ERRORLEVEL!==0 set "PY=python"
)

if not defined PY (
  echo لم يُعثر على Python على هذا الجهاز.
  echo ثبّته من https://www.python.org/downloads/ ثم أعد تشغيل هذا الملف.
  echo.
  pause
  exit /b 1
)

echo المفسّر المستعمَل: !PY!
echo.

cd /d "%~dp0"
!PY! tools\install_engine.py cohere
set RC=!ERRORLEVEL!

echo.
if !RC!==0 (
  echo تم التثبيت بنجاح. افتح البرنامج واختر المحرّك.
) else (
  echo فشل التثبيت — راجع الرسائل أعلاه.
)
echo.
pause
