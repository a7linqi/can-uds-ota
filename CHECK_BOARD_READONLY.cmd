@echo off
setlocal EnableExtensions DisableDelayedExpansion
title STM32 read-only connection check

rem Run this file from your normal Windows desktop account.
rem The only device operations are probe enumeration, HOTPLUG SWD connection,
rem and two memory reads. No erase, firmware write, reset or unlock is requested.
set "PROBE_CLI=D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
set "PROBE_LOG_DIR=%~dp0build-temp\board-probe"
set "PROBE_LOG=%PROBE_LOG_DIR%\read-only-connection.log"
set "PROBE_RESULT=1"

if /I "%~1"=="--self-check" goto self_check
if not exist "%PROBE_CLI%" goto missing_tool
if not exist "%PROBE_LOG_DIR%" mkdir "%PROBE_LOG_DIR%"
if not exist "%PROBE_LOG_DIR%" goto missing_log_dir

echo Checking ST-LINK and reading the target chip information...
echo Leave the board powered and connected through SWD.
echo.
>> "%PROBE_LOG%" echo.
>> "%PROBE_LOG%" echo RUN START: %date% %time%
>> "%PROBE_LOG%" echo Read-only check. SWD HOTPLUG, 1000 kHz.
>> "%PROBE_LOG%" echo Probe enumeration:
"%PROBE_CLI%" -l st-link-only >> "%PROBE_LOG%" 2>&1
>> "%PROBE_LOG%" echo Target connection and identification-register reads:
>> "%PROBE_LOG%" echo DBGMCU_IDCODE at 0xE0042000; STM32F407 Flash size at 0x1FFF7A22.
"%PROBE_CLI%" -c port=SWD mode=HOTPLUG freq=1000 -r32 0xE0042000 0x4 -r16 0x1FFF7A22 0x2 >> "%PROBE_LOG%" 2>&1
set "PROBE_RESULT=%ERRORLEVEL%"
>> "%PROBE_LOG%" echo CLI EXIT CODE: %PROBE_RESULT%
>> "%PROBE_LOG%" echo RUN END: %date% %time%
type "%PROBE_LOG%"
echo.
echo Log saved to:
echo %PROBE_LOG%
echo.
echo Tell the assistant that this check has finished; it can read the log.
goto finish

:missing_tool
echo STM32CubeProgrammer CLI was not found:
echo %PROBE_CLI%
goto finish

:missing_log_dir
echo Cannot create the log folder:
echo %PROBE_LOG_DIR%
goto finish

:self_check
if not exist "%PROBE_CLI%" exit /b 1
echo CLI found. The normal run will execute only these device commands:
echo "%PROBE_CLI%" -l st-link-only
echo "%PROBE_CLI%" -c port=SWD mode=HOTPLUG freq=1000 -r32 0xE0042000 0x4 -r16 0x1FFF7A22 0x2
echo Self-check only: no device was opened and no log was created.
exit /b 0

:finish
pause
exit /b %PROBE_RESULT%
