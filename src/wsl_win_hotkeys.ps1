# WSL-Windows Hotkey & Clipboard Bridge Helper
# Spawns automatically from NixOS WSL - ZERO setup or Python needed on Windows.

$ErrorActionPreference = "Stop"

$csharpCode = @"
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class WinInterop {
    [DllImport("user32.dll")]
    public static extern short GetAsyncKeyState(int vKey);

    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

    public const int VK_SHIFT = 0x10;
    public const int VK_CONTROL = 0x11;
    public const int VK_MENU = 0x12; // ALT key
    public const int VK_KEY_I = 0x49; // 'I' key

    public const uint KEYEVENTF_KEYUP = 0x0002;
    public const byte VK_V = 0x56;

    public static bool IsAltShiftPressed() {
        bool alt = (GetAsyncKeyState(VK_MENU) & 0x8000) != 0;
        bool shift = (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0;
        return alt && shift;
    }

    public static bool IsCtrlAltIPressed() {
        bool ctrl = (GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0;
        bool alt = (GetAsyncKeyState(VK_MENU) & 0x8000) != 0;
        bool keyI = (GetAsyncKeyState(VK_KEY_I) & 0x8000) != 0;
        return ctrl && alt && keyI;
    }

    public static void SendCtrlV() {
        // Send Ctrl+V using keybd_event for maximum compatibility across all apps
        keybd_event((byte)VK_CONTROL, 0, 0, UIntPtr.Zero);
        keybd_event(VK_V, 0, 0, UIntPtr.Zero);
        keybd_event(VK_V, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
        keybd_event((byte)VK_CONTROL, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
    }
}
"@

Add-Type -TypeDefinition $csharpCode -ReferencedAssemblies "System.Windows.Forms"

[Console]::WriteLine("READY")
[Console]::Out.Flush()

# Stdin listener thread for PASTE commands
[System.Threading.ThreadPool]::QueueUserWorkItem({
    try {
        $reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput())
        while ($true) {
            $line = $reader.ReadLine()
            if ($line -eq "EXIT") {
                [System.Environment]::Exit(0)
            } elseif ($line -eq "PASTE") {
                [WinInterop]::SendCtrlV()
            } elseif ($null -eq $line) {
                # Null means end of stream or no data yet, do NOT exit immediately
                [System.Threading.Thread]::Sleep(200)
            }
        }
    } catch {
        # ignore read errors
    }
}) | Out-Null

$wasHotkeyDown = $false
$wasConfigDown = $false

while ($true) {
    # Check Alt+Shift (Record Trigger)
    $isHotkeyDown = [WinInterop]::IsAltShiftPressed()
    if ($isHotkeyDown -and -not $wasHotkeyDown) {
        $wasHotkeyDown = $true
        [Console]::WriteLine("HOTKEY_DOWN")
        [Console]::Out.Flush()
    } elseif (-not $isHotkeyDown -and $wasHotkeyDown) {
        $wasHotkeyDown = $false
        [Console]::WriteLine("HOTKEY_UP")
        [Console]::Out.Flush()
    }

    # Check Ctrl+Alt+I (Config Trigger)
    $isConfigDown = [WinInterop]::IsCtrlAltIPressed()
    if ($isConfigDown -and -not $wasConfigDown) {
        $wasConfigDown = $true
        [Console]::WriteLine("CONFIG_DOWN")
        [Console]::Out.Flush()
    } elseif (-not $isConfigDown -and $wasConfigDown) {
        $wasConfigDown = $false
    }

    [System.Threading.Thread]::Sleep(20)
}
