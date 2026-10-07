# WSL-Windows Hotkey & Clipboard Bridge Helper
# Spawns automatically from NixOS WSL - ZERO setup or Python needed on Windows.
#
# -Binds carries the push-to-talk chords, e.g. "164,165;160,161|124": '|'
# separates binds, ';' separates the keys of one bind, ',' separates the
# alternative virtual keys for one key. See voice_transcriber.keybinds.
param(
    [string]$Binds = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Binds)) {
    # Old caller / missing flag: Alt+Shift, the chord this app always shipped
    # with. Must stay identical to voice_transcriber.keybinds.DEFAULT_VK_BINDS;
    # tests/shared pins the equality because this file cannot import Python.
    $Binds = "164,165;160,161"
}

$csharpCode = @"
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class WinInterop {
    [DllImport("user32.dll")]
    public static extern short GetAsyncKeyState(int vKey);

    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

    public const int VK_MBUTTON = 0x04;
    public const int VK_SHIFT = 0x10;
    public const int VK_CONTROL = 0x11;
    public const int VK_MENU = 0x12; // ALT key
    public const int VK_SPACE = 0x20;

    public const uint KEYEVENTF_KEYUP = 0x0002;
    public const byte VK_V = 0x56;

    private static System.Media.SoundPlayer startPlayer = null;
    private static System.Media.SoundPlayer donePlayer = null;

    public static void SetSoundTheme(string theme) {
        try {
            theme = (theme ?? "").Trim().ToLower();
            string startPath = "";
            string donePath = "";

            if (theme == "silent" || theme == "muted" || theme == "none") {
                startPlayer = null;
                donePlayer = null;
                return;
            } else if (theme == "speech") {
                startPath = @"C:\Windows\Media\Speech On.wav";
                donePath = @"C:\Windows\Media\Speech Off.wav";
            } else if (theme == "ding" || theme == "notify") {
                startPath = @"C:\Windows\Media\Windows Notify.wav";
                donePath = @"C:\Windows\Media\Windows Notify.wav";
            } else if (theme == "subtle" || theme == "navigation") {
                startPath = @"C:\Windows\Media\Windows Navigation Start.wav";
                donePath = @"C:\Windows\Media\Windows Navigation Start.wav";
            } else if (theme == "classic" || theme == "tada") {
                startPath = @"C:\Windows\Media\chimes.wav";
                donePath = @"C:\Windows\Media\tada.wav";
            } else if (System.IO.File.Exists(theme)) {
                startPath = theme;
                donePath = theme;
            } else {
                // Default: proximity
                startPath = @"C:\Windows\Media\Windows Proximity Notification.wav";
                donePath = @"C:\Windows\Media\Windows Proximity Notification.wav";
            }

            if (!System.IO.File.Exists(startPath)) startPath = @"C:\Windows\Media\Speech On.wav";
            if (!System.IO.File.Exists(donePath)) donePath = @"C:\Windows\Media\Speech Off.wav";

            if (System.IO.File.Exists(startPath)) {
                startPlayer = new System.Media.SoundPlayer(startPath);
                startPlayer.LoadAsync();
            }
            if (System.IO.File.Exists(donePath)) {
                donePlayer = (startPath == donePath) ? startPlayer : new System.Media.SoundPlayer(donePath);
                if (startPath != donePath) donePlayer.LoadAsync();
            }
        } catch { }
    }

    public static void InitSounds() {
        SetSoundTheme("proximity");
    }

    public static void PlayStartSound() {
        try { if (startPlayer != null) startPlayer.Play(); } catch { }
    }

    public static void PlayDoneSound() {
        try { if (donePlayer != null) donePlayer.Play(); } catch { }
    }

    public static bool MiddleClickEnabled = true;
    public static void SetMiddleClickEnabled(bool enabled) {
        MiddleClickEnabled = enabled;
    }

    public static bool IsBindPressed(string binds) {
        // '|' separates binds, ';' separates keys of one bind, ',' separates
        // the alternative VK codes for one key. A bind is satisfied when every
        // key group has at least one code down; any bind fires the trigger.
        if (string.IsNullOrEmpty(binds)) {
            return false;
        }
        foreach (string bind in binds.Split('|')) {
            if (bind.Length == 0) {
                continue;
            }
            bool bindDown = true;
            foreach (string group in bind.Split(';')) {
                bool keyDown = false;
                foreach (string code in group.Split(',')) {
                    int vk;
                    if (int.TryParse(code.Trim(), out vk) &&
                        (GetAsyncKeyState(vk) & 0x8000) != 0) {
                        keyDown = true;
                        break;
                    }
                }
                if (!keyDown) {
                    bindDown = false;
                    break;
                }
            }
            if (bindDown) {
                return true;
            }
        }
        return false;
    }

    public static bool IsSpacePressed() {
        return (GetAsyncKeyState(VK_SPACE) & 0x8000) != 0;
    }

    public static bool IsMButtonPressed() {
        return (GetAsyncKeyState(VK_MBUTTON) & 0x8000) != 0;
    }

    public static void SendCtrlV() {
        // Send Ctrl+V using keybd_event for maximum compatibility across all apps
        keybd_event((byte)VK_CONTROL, 0, 0, UIntPtr.Zero);
        keybd_event(VK_V, 0, 0, UIntPtr.Zero);
        keybd_event(VK_V, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
        keybd_event((byte)VK_CONTROL, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
        PlayDoneSound();
    }

    public static void SendCtrlShiftV() {
        // Send Ctrl+Shift+V for terminal paste
        keybd_event((byte)VK_CONTROL, 0, 0, UIntPtr.Zero);
        keybd_event((byte)VK_SHIFT, 0, 0, UIntPtr.Zero);
        keybd_event(VK_V, 0, 0, UIntPtr.Zero);
        keybd_event(VK_V, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
        keybd_event((byte)VK_SHIFT, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
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
                        } else if (line == "PASTE_TERMINAL") {
                            SendCtrlShiftV();
                        } else if (line == "PLAY_DONE") {
                            PlayDoneSound();
                        } else if (line != null && line.StartsWith("SET_SOUND:")) {
                            SetSoundTheme(line.Substring(10));
                        } else if (line != null && line.StartsWith("SET_MCLICK:")) {
                            SetMiddleClickEnabled(line.Substring(11).Trim() == "1");
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
$wasSpaceDown = $false
# Hands-free latch state, mirroring BaseHotkeyManager.latch_release on
# Linux/Windows. Space tapped while the record trigger is held means "let go of
# the keys and keep recording"; the next trigger press finishes the dictation.
$latched = $false
$recording = $false
$mButtonDownTime = [DateTime]::MinValue
$mButtonActive = $false

while ($true) {
    # Check Middle Mouse button (Hold >= 250ms Trigger)
    $isMButtonDown = if ([WinInterop]::MiddleClickEnabled) { [WinInterop]::IsMButtonPressed() } else { $false }
    if ($isMButtonDown) {
        if ($mButtonDownTime -eq [DateTime]::MinValue) {
            $mButtonDownTime = [DateTime]::UtcNow
        } elseif (-not $mButtonActive -and (([DateTime]::UtcNow - $mButtonDownTime).TotalMilliseconds -ge 250)) {
            $mButtonActive = $true
        }
    } else {
        $mButtonDownTime = [DateTime]::MinValue
        $mButtonActive = $false
    }

    # Check the configured bind(s) or Middle Click Hold (Record Trigger)
    $isBindDown = [WinInterop]::IsBindPressed($Binds)
    $isHotkeyDown = $isBindDown -or $mButtonActive
    if ($isHotkeyDown -and -not $wasHotkeyDown) {
        $wasHotkeyDown = $true
        if ($recording) {
            # Already recording hands-free: this press concludes the dictation.
            # Deliberately no start chime and no HOTKEY_DOWN.
            $recording = $false
            $latched = $false
            [Console]::WriteLine("HOTKEY_UP")
        } else {
            $recording = $true
            $latched = $false
            [WinInterop]::PlayStartSound()
            [Console]::WriteLine("HOTKEY_DOWN")
        }
        [Console]::Out.Flush()
    } elseif (-not $isHotkeyDown -and $wasHotkeyDown) {
        $wasHotkeyDown = $false
        if ($recording) {
            if ($latched) {
                # Hands-free: the keys are up but the recording stays open.
                $latched = $false
                [Console]::WriteLine("LATCH_HOLD")
            } else {
                $recording = $false
                [Console]::WriteLine("HOTKEY_UP")
            }
            [Console]::Out.Flush()
        }
    }

    # Space tapped while the trigger is held = engage the hands-free latch.
    $isSpaceDown = [WinInterop]::IsSpacePressed()
    if ($isSpaceDown -and -not $wasSpaceDown -and $wasHotkeyDown -and -not $latched) {
        $latched = $true
        [Console]::WriteLine("LATCH_DOWN")
        [Console]::Out.Flush()
    }
    $wasSpaceDown = $isSpaceDown

    [System.Threading.Thread]::Sleep(10)
}
