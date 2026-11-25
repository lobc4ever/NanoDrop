"""Windows USB-to-volume association for the third-generation iPod nano."""
import ctypes
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

QUERY = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$nodes=@(Get-CimInstance Win32_PnPEntity -Filter "PNPDeviceID LIKE 'USB%VID_05AC&PID_1262%'" |
    Where-Object { $_.PNPDeviceID -match '^USB\\VID_05AC&PID_1262\\[0-9A-F]{16}$' })
$result=@()
if ($nodes.Count -gt 0) {
    foreach ($disk in (Get-CimInstance Win32_DiskDrive | Where-Object { $_.Model -match 'iPod' })) {
        $id=$disk.PNPDeviceID
        $parent=$null
        for ($i=0; $i -lt 8 -and $id; $i++) {
            $parent=$nodes | Where-Object { $_.PNPDeviceID -eq $id } | Select-Object -First 1
            if ($parent) { break }
            $id=(Get-PnpDeviceProperty -InstanceId $id -KeyName DEVPKEY_Device_Parent -ErrorAction SilentlyContinue).Data
        }
        if (!$parent) {
            $parent=$nodes | Where-Object { $disk.PNPDeviceID.EndsWith((($_.PNPDeviceID -split '\\')[-1]+'&0'), [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
        }
        if (!$parent) { continue }
        $guid=($parent.PNPDeviceID -split '\\')[-1]
        foreach ($part in (Get-CimAssociatedInstance -InputObject $disk -Association Win32_DiskDriveToDiskPartition)) {
            foreach ($vol in (Get-CimAssociatedInstance -InputObject $part -Association Win32_LogicalDiskToPartition)) {
                if ($vol.DeviceID -match '^[D-ZA-B]:$' -and $vol.Size -gt 0) {
                    $result+=@{ drive=$vol.DeviceID; guid=$guid; label=$vol.VolumeName }
                }
            }
        }
    }
}
@{seen=$nodes.Count; volumes=@($result)} | ConvertTo-Json -Depth 4 -Compress
"""

DB_REL = Path('iPod_Control/iTunes/iTunesDB')

def serial(root):
    value = ctypes.c_ulong()
    success = ctypes.windll.kernel32.GetVolumeInformationW(
        str(root), None, 0, ctypes.byref(value), None, None, None, 0)
    if not success:
        raise OSError('Windows 已无法读取 iPod 卷，请检查数据线。')
    return value.value

@dataclass(frozen=True)
class Device:
    root: Path
    guid: bytes
    volume: int
    label: str

    def check(self):
        if serial(self.root) != self.volume:
            raise OSError('盘符对应的设备已更换，请重新连接 iPod。')

    @property
    def db(self):
        return self.root / DB_REL

def discover():
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    proc = subprocess.run([str(shell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', QUERY],
                          capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=18)
    if proc.returncode:
        raise OSError('Windows 设备查询失败，请稍后点击「重新检测」。')
    response = json.loads(proc.stdout.decode('utf-8-sig'))
    found = []
    for row in response['volumes']:
        if not re.fullmatch('[A-Z]:', row['drive'], re.I):
            continue
        root = Path(row['drive'] + '\\')
        found.append(Device(root, bytes.fromhex(row['guid']), serial(root), row['label'] or 'iPod nano'))
    return found, bool(response['seen'])
