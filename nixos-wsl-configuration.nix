# NixOS-WSL Configuration for Voice Transcriber (VT)
# Copy or import into /etc/nixos/configuration.nix inside NixOS-WSL

{ config, lib, pkgs, ... }:

{
  imports = [
    # Include the default WSL module provided by NixOS-WSL
    <nixos-wsl/modules>
  ];

  wsl = {
    enable = true;
    defaultUser = "nixos";
    
    # Automatically configure interop and paths
    interop.includePath = true;
    useWindowsDriver = true;
  };

  # Enable Nix Flakes and modern CLI tools
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    auto-optimise-store = true;
  };

  # Audio and Microphone Passthrough:
  # WSLg automatically routes the Windows default microphone and speakers
  # through a PulseAudio UNIX socket at /mnt/wslg/runtime-dir/pulse/native
  hardware.pulseaudio.enable = false; # We use WSLg's built-in PulseAudio bridge

  environment.variables = {
    PULSE_SERVER = "unix:/mnt/wslg/runtime-dir/pulse/native";
    # Display configuration for overlays/GUI
    WAYLAND_DISPLAY = "wayland-0";
    DISPLAY = ":0";
  };

  # System packages needed for VT and audio diagnostics
  environment.systemPackages = with pkgs; [
    git
    pulseaudio      # Provides pactl, parec, paplay, pacmd
    alsa-utils      # Provides arecord, aplay
    sox
    python3
    pciutils
    usbutils
    wget
    curl
  ];

  # System state version
  system.stateVersion = "26.05";
}
