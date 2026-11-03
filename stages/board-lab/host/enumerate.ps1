# enumerate.ps1 - read-only host inventory for board-lab Phase 3 (group A)
# Enumerates serial ports, PnP USB/debug devices and any CAN adapter candidate.
# Does NOT install drivers, change permissions or flash any adapter.
$ErrorActionPreference = 'Stop'
$outDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outFile = Join-Path $outDir 'host-inventory.json'

$inventory = [ordered]@{
    captured_at_local = (Get-Date).ToString('o')
    timezone          = (Get-TimeZone).Id
    computer          = $env:COMPUTERNAME
    user              = $env:USERNAME
    os                = (Get-CimInstance Win32_OperatingSystem).Caption
}

# 1. Serial ports visible to Win32
$inventory.com_ports = @(
    Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue |
        ForEach-Object { [ordered]@{ device = $_.DeviceID; description = $_.Description; pnp = $_.PNPDeviceID } }
)

# 2. All present PnP devices of class Ports
$inventory.pnp_ports = @(
    Get-PnpDevice -PresentOnly -Class Ports -ErrorAction SilentlyContinue |
        ForEach-Object { [ordered]@{ name = $_.FriendlyName; status = $_.Status; instance = $_.InstanceId } }
)

# 3. ST-Link / STM32 debug probe
$inventory.stlink = @(
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
        Where-Object { $_.FriendlyName -match 'ST-Link|STM32' } |
        ForEach-Object { [ordered]@{ name = $_.FriendlyName; status = $_.Status; instance = $_.InstanceId } }
)

# 4. All present USB devices (for VID/PID visibility)
$inventory.usb_devices = @(
    Get-PnpDevice -PresentOnly -Class USB -ErrorAction SilentlyContinue |
        ForEach-Object { [ordered]@{ name = $_.FriendlyName; status = $_.Status; instance = $_.InstanceId } }
)

# 5. CAN adapter candidates by name
$inventory.can_candidates = @(
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
        Where-Object { $_.FriendlyName -match 'CAN|USBtin|PEAK|Vector|Kvaser|Lawicel|ZLG|USBCAN|PCAN' } |
        ForEach-Object { [ordered]@{ name = $_.FriendlyName; status = $_.Status; instance = $_.InstanceId } }
)

$inventory.has_ch340_uart = [bool](Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -match 'CH340|WCH|USB\\\\VID_1A86' })

$json = $inventory | ConvertTo-Json -Depth 6
$json | Set-Content -Path $outFile -Encoding UTF8
Write-Output $json
Write-Output ("WROTE: " + $outFile)
