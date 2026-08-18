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

    private static System.Media.SoundPlayer startPlayer = null;
    private static System.Media.SoundPlayer donePlayer = null;

    public static void InitSounds() {
        try {
            string onPath = @"C:\Windows\Media\Speech On.wav";
            if (!System.IO.File.Exists(onPath)) onPath = @"C:\Windows\Media\Windows Navigation Start.wav";
            if (System.IO.File.Exists(onPath)) {
                startPlayer = new System.Media.SoundPlayer(onPath);
                startPlayer.LoadAsync();
            }

            string donePath = @"C:\Windows\Media\Windows Proximity Notification.wav";
            if (!System.IO.File.Exists(donePath)) donePath = @"C:\Windows\Media\Speech Off.wav";
            if (!System.IO.File.Exists(donePath)) donePath = @"C:\Windows\Media\Windows Notify.wav";
            if (System.IO.File.Exists(donePath)) {
                donePlayer = new System.Media.SoundPlayer(donePath);
                donePlayer.LoadAsync();
            }
        } catch { }
    }

    public static void PlayStartSound() {
        try { if (startPlayer != null) startPlayer.Play(); } catch { }
    }

    public static void PlayDoneSound() {
        try { if (donePlayer != null) donePlayer.Play(); } catch { }
    }

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
        PlayDoneSound();
    }

    public static void StartStdinListener() {
        System.Threading.Thread t = new System.Threading.Thread(() => {
            try {
                using (var reader = new System.IO.StreamReader(Console.OpenStandardInput())) {
                    while (true) {
                        string line = reader.ReadLine();
                        if (line == "EXIT") {
                            Environment.Exit(0);
                        } else if (line == "PASTE") {
                            SendCtrlV();
                        } else if (line == "PLAY_DONE") {
                            PlayDoneSound();
                        } else if (line == null) {
                            System.Threading.Thread.Sleep(50);
                        }
                    }
                }
            } catch {
                // ignore
            }
        });
        t.IsBackground = true;
        t.Start();
    }
}
"@

Add-Type -TypeDefinition $csharpCode -ReferencedAssemblies "System.Windows.Forms"

[WinInterop]::InitSounds()
[Console]::WriteLine("READY")
[Console]::Out.Flush()

[WinInterop]::StartStdinListener()

$wasHotkeyDown = $false
$wasConfigDown = $false

while ($true) {
    # Check Alt+Shift (Record Trigger)
    $isHotkeyDown = [WinInterop]::IsAltShiftPressed()
    if ($isHotkeyDown -and -not $wasHotkeyDown) {
        $wasHotkeyDown = $true
        [WinInterop]::PlayStartSound()
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

    [System.Threading.Thread]::Sleep(10)
}
