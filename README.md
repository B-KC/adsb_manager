# ADS-B Stack Manager

A small desktop app for managing an ADS-B feeder over SSH. Connect to a Pi (or any Linux feeder), and the app **discovers what is actually installed on that host** — services, decoder JSON, and local map URLs.

Built for people who just stood up a feeder and want a friendly way to start/stop services, read logs, and open the local map without memorizing `systemctl` and URLs.

## What it does

- SSH connect (key, agent, or password — password is never saved)
- Discover systemd units on **that** server
- Start / stop / restart each discovered service
- Live decoder stats (aircraft, msg/s, range) from dump1090-fa or readsb
- Open whichever map UIs the feeder actually serves
- Logs, live journal tail, SDR check, apt updates, reboot

Nothing is hardcoded to one person's LAN. Host, username, and map host are remembered locally in `~/.adsb_manager/settings.json`.

## Requirements

**The feeder**

- SSH enabled
- A user that can `sudo systemctl` (typical `pi` / `adsb` image accounts)
- Common setups work out of the box: PiAware, ADSB Exchange image, wiedehopf/readsb + tar1090, FR24, RadarBox, Plane Finder, airplanes.live, adsb.fi, …

**Your PC** — pick one:

| How you run it | What you need |
|---|---|
| Windows `.exe` | Nothing else. Download `ADSB-Stack-Manager.exe` and double-click. |
| Python package | Python 3.10+ with Tkinter (official Windows/macOS installers include it) |

## Run the Windows exe

1. Grab `ADSB-Stack-Manager.exe` from [Releases](../../releases) (or build it — see below).
2. Double-click. No install, no Python.
3. Enter the feeder hostname/IP, username, and password (or use an SSH key in `~/.ssh`).

Windows SmartScreen may warn on an unsigned exe the first time — *More info* → *Run anyway*. That is expected until someone codesigns a release.

## Install as a Python package

```bash
pip install .
adsb-manager
```

From a checkout without installing:

```bash
pip install paramiko
python adsb_manager.py
# or
python -m adsbmgr
```

Publishable later with:

```bash
powershell -File scripts\build_wheel.ps1
# dist/adsb_stack_manager-*.whl
pip install dist/adsb_stack_manager-*.whl
```

## How discovery works

On connect the app asks the feeder:

1. **Services** — `systemctl show` for a catalog of known ADS-B units. Only loaded units appear.
2. **Decoder JSON** — first hit among `/run/dump1090-fa`, `/run/readsb`, `/run/dump1090`, `/run/dump978-fa`.
3. **Maps** — HTTP probe of common local URLs (`:8080`, `/tar1090/`, `/skyaware/`, `/adsbx/`, `/graphs1090/`, `:8754`, …). Only URLs that respond are listed.

The **map host** defaults to the SSH host you typed. Change it if maps are served through another IP or reverse proxy (for example `http://192.168.17.5/adsbx/`).

To teach the app about another feeder or URL, add it to `adsbmgr/catalog.py`.

## Build the Windows exe (maintainers)

On a Windows machine with Python 3.10+:

```powershell
powershell -File scripts\build_exe.ps1
```

Output: `dist\ADSB-Stack-Manager.exe` (one file, no console window).

Rebuild whenever you change code. Do not commit `dist/` or `build/`.

## License

MIT
