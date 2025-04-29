# TODO
- [ ] add an interrupt during transcription in case it's wrong
- [x] add a test for the transcriber
- [x] use `faster-whisper` for faster transcriptions
- [ ] add some global shortcut/gui branch
- [ ] try it out on wsl/hyper v
- [ ] fix breaking terminal output when quitting using 'q'
    - this only breaks on windows i think
- [ ] make something to cache the nixpkgs so it works totally offline...
    > maybe done...
    > nope not yet these are trying to be downloaded: 
      - /nix/store/x9d49vaqlrkw97p9ichdwrnbh013kq7z-bash-interactive-5.2p37
      - /nix/store/k3a7dzrqphj9ksbb43i24vy6inz8ys51-ncurses-6.4.20221231
      - /nix/store/8yff66km6d5mav6i2101kmvp78vgqfcc-readline-8.2p13

# voice-transcriber
- to run from nixos use: 
```shell
nix-shell --run "python t2.py"
```


## tips:
### to get copy/paste notification 
- on gnome: use the `pano` extension

