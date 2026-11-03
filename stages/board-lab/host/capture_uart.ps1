# capture_uart.ps1 - 有限时长采集板载 UART 115200/8N1，记录原始字节与时间
# 用途：复位前后采集日志串口原始输出。仅读，不改驱动/权限。
# 用法：capture_uart.ps1 -Port COM9 -Seconds 15 -OutFile capture.bin
param(
    [Parameter(Mandatory=$true)][string]$Port,
    [int]$Seconds = 15,
    [Parameter(Mandatory=$true)][string]$OutFile,
    [int]$Baud = 115200
)
$ErrorActionPreference = 'Stop'
$sp = $null
try {
    $sp = New-Object System.IO.Ports.SerialPort($Port, $Baud, 'None', 8, 'One')
    $sp.ReadTimeout = 500
    $sp.Open()
    $start = Get-Date
    $stream = [System.IO.File]::Open($OutFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    $writer = New-Object System.IO.BinaryWriter($stream)
    $meta = New-Object System.IO.StreamWriter(($OutFile + '.meta'), $false, [System.Text.Encoding]::UTF8)
    try {
        $buf = New-Object byte[] 4096
        while (((Get-Date) - $start).TotalSeconds -lt $Seconds) {
            if ($sp.BytesToRead -gt 0) {
                $n = $sp.Read($buf, 0, [Math]::Min($buf.Length, $sp.BytesToRead))
                $now = (Get-Date).ToString('o')
                $writer.Write($buf, 0, $n)
                $meta.WriteLine(("BYTES {0} AT {1} HEX {2}" -f $n, $now, (($buf[0..([Math]::Max(0,$n-1))] | ForEach-Object { $_.ToString('X2') }) -join '')))
            } else {
                Start-Sleep -Milliseconds 20
            }
        }
    } finally {
        $meta.Close(); $writer.Close(); $stream.Close()
    }
    Write-Output ("CAPTURED_OK port=" + $Port + " seconds=" + $Seconds + " out=" + $OutFile)
} catch {
    Write-Output ("CAPTURE_FAILED port=" + $Port + " error=" + $_.Exception.Message)
    if ($_.Exception.Message -match 'denied|not exist|does not exist|Access') {
        Write-Output "HINT: no such COM port or it is busy. Record as a missing item; do not install drivers."
    }
} finally {
    if ($sp) { try { $sp.Close() } catch {} }
}
