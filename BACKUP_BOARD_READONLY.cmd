@echo off
setlocal EnableExtensions DisableDelayedExpansion
title STM32 internal Flash read-only backup

rem User-launched backup for the physically identified 1 MiB board.
rem Device operations: HOTPLUG SWD connections and internal Flash reads only.
rem Two complete reads must have equal SHA256 before a manifest is accepted.
set "BACKUP_CLI=D:\Keil\CubProgram\bin\STM32_Programmer_CLI.exe"
set "BACKUP_RESULT=1"
set "BACKUP_STAMP="
set "BACKUP_DIR="

if /I "%~1"=="--self-check" goto self_check
if not exist "%BACKUP_CLI%" goto missing_tool
for /f %%T in ('powershell.exe -NoLogo -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss_fff"') do set "BACKUP_STAMP=%%T"
if not defined BACKUP_STAMP goto missing_timestamp
set "BACKUP_DIR=%~dp0backups\board-original\%BACKUP_STAMP%"
if exist "%BACKUP_DIR%" goto existing_directory
mkdir "%BACKUP_DIR%"
if not exist "%BACKUP_DIR%" goto missing_directory
set "BACKUP_LOG=%BACKUP_DIR%\backup.log"
set "BACKUP_FIRST=%BACKUP_DIR%\internal-flash-original.bin"
set "BACKUP_SECOND=%BACKUP_DIR%\internal-flash-readback.bin"
set "BACKUP_MANIFEST=%BACKUP_DIR%\backup-manifest.json"

echo Backing up the original 1 MiB internal Flash through ST-LINK SN I.
echo Keep the board connected and powered until both reads finish.
echo No firmware programming, erase, reset or unlock command is requested.
echo.
> "%BACKUP_LOG%" echo RUN START: %date% %time%
>> "%BACKUP_LOG%" echo Read-only internal Flash backup. SWD HOTPLUG, 1000 kHz, ST-LINK SN I.
>> "%BACKUP_LOG%" echo Address 0x08000000, size 0x100000 bytes. NOR and EEPROM are not included.
>> "%BACKUP_LOG%" echo First full read:
"%BACKUP_CLI%" -c port=SWD sn=I mode=HOTPLUG freq=1000 -u 0x08000000 0x100000 "%BACKUP_FIRST%" >> "%BACKUP_LOG%" 2>&1
if not "%ERRORLEVEL%"=="0" goto failed
>> "%BACKUP_LOG%" echo Second full read:
"%BACKUP_CLI%" -c port=SWD sn=I mode=HOTPLUG freq=1000 -u 0x08000000 0x100000 "%BACKUP_SECOND%" >> "%BACKUP_LOG%" 2>&1
if not "%ERRORLEVEL%"=="0" goto failed

powershell.exe -NoLogo -NoProfile -Command "$ErrorActionPreference='Stop'; try { $first=Get-Item -LiteralPath $env:BACKUP_FIRST; $second=Get-Item -LiteralPath $env:BACKUP_SECOND; if ($first.Length -ne 1048576 -or $second.Length -ne 1048576) { throw 'Unexpected file length: each file must contain 1048576 bytes.' }; function Get-BackupHash([string]$path) { $stream=[IO.File]::OpenRead($path); $sha=[Security.Cryptography.SHA256]::Create(); try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant() } finally { $stream.Dispose(); $sha.Dispose() } }; $a=Get-BackupHash $first.FullName; $b=Get-BackupHash $second.FullName; if ($a -ne $b) { throw 'Two reads differ. Preserve both files and do not accept this backup.' }; $record=[ordered]@{ captured_at=(Get-Date).ToString('o'); read_only=$true; scope='Internal Flash only; NOR and EEPROM not backed up'; probe_serial='I'; connection_mode='HOTPLUG'; flash_base='0x08000000'; length_bytes=1048576; first_file=$first.Name; second_file=$second.Name; sha256=$a.ToLowerInvariant(); two_reads_match=$true }; $record | ConvertTo-Json | Set-Content -LiteralPath $env:BACKUP_MANIFEST -Encoding UTF8; Write-Output ('BACKUP VERIFIED: 1048576 bytes, SHA256 '+$a) } catch { Write-Output ('BACKUP FAILED: '+$_.Exception.Message); exit 1 }" >> "%BACKUP_LOG%" 2>&1
if not "%ERRORLEVEL%"=="0" goto failed
set "BACKUP_RESULT=0"
>> "%BACKUP_LOG%" echo RUN END: %date% %time%
type "%BACKUP_LOG%"
echo.
echo Backup files and checksum manifest:
echo %BACKUP_DIR%
echo Tell the assistant that the backup has finished; it can inspect the files.
goto finish

:failed
>> "%BACKUP_LOG%" echo BACKUP NOT ACCEPTED. Files retained for inspection. RUN END: %date% %time%
type "%BACKUP_LOG%"
echo.
echo Backup did not pass. Preserve the log and files for the assistant.
goto finish

:missing_tool
echo STM32CubeProgrammer CLI was not found:
echo %BACKUP_CLI%
goto finish

:missing_timestamp
echo Could not obtain a timestamp. No backup was attempted.
goto finish

:existing_directory
echo Backup directory already exists. Existing files will be preserved:
echo %BACKUP_DIR%
goto finish

:missing_directory
echo Cannot create the backup directory:
echo %BACKUP_DIR%
goto finish

:self_check
if not exist "%BACKUP_CLI%" exit /b 1
echo CLI found. Normal execution reads 0x08000000..0x080FFFFF twice.
echo "%BACKUP_CLI%" -c port=SWD sn=I mode=HOTPLUG freq=1000 -u 0x08000000 0x100000 [new-output-file.bin]
echo Two 1048576-byte files must have equal SHA256 before a manifest is saved.
echo Self-check only: no device was opened and no backup directory was created.
exit /b 0

:finish
pause
exit /b %BACKUP_RESULT%
