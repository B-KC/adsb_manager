#!/usr/bin/env python3
"""
ADS-B Stack Manager
SSH management tool for Raspberry Pi (pi3server / 192.168.17.206).
Manages: dump1090-fa, tar1090, graphs1090, fr24feed, piaware,
         adsbexchange, airplanes.live

Dependencies: pip install paramiko rich
"""
import getpass
import json
import select
import socket
import sys
import time
from pathlib import Path

try:
    import paramiko
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
except ImportError:
    sys.exit("Missing dependencies — run:  pip install paramiko rich")

# ── Connection config ──────────────────────────────────────────────────────────
HOST_PRIMARY  = "pi3server"          # mDNS / hostname
HOST_FALLBACK = "192.168.17.206"     # Static IP fallback
SSH_PORT      = 22
SSH_USER      = "pi"                 # change if your login differs
SSH_TIMEOUT   = 10                   # seconds

# Key files tried in order before falling back to password
SSH_KEY_PATHS = [
    Path.home() / ".ssh" / "id_ed25519",
    Path.home() / ".ssh" / "id_rsa",
    Path.home() / ".ssh" / "id_ecdsa",
]

# ── ADS-B services: display name → systemd unit ───────────────────────────────
SERVICES = {
    "dump1090-fa":    "dump1090-fa",
    "tar1090":        "tar1090",
    "graphs1090":     "graphs1090",
    "fr24feed":       "fr24feed",
    "PiAware":        "piaware",
    "ADS-B Exchange": "adsbexchange-feed",
    "airplanes.live": "airplanes-feeder",
}

console = Console()


# ── SSH helpers ───────────────────────────────────────────────────────────────

def _resolve_host() -> str:
    """Return primary hostname if resolvable, else fall back to IP."""
    try:
        socket.getaddrinfo(HOST_PRIMARY, SSH_PORT)
        return HOST_PRIMARY
    except (socket.gaierror, OSError):
        console.print(
            f"[yellow]Cannot resolve '{HOST_PRIMARY}', using {HOST_FALLBACK}[/yellow]"
        )
        return HOST_FALLBACK


def connect() -> paramiko.SSHClient:
    host = _resolve_host()
    console.print(f"\n[bold cyan]Connecting → {SSH_USER}@{host}:{SSH_PORT}[/bold cyan]")

    client = paramiko.SSHClient()
    # AutoAddPolicy is acceptable for a known private LAN host on first connect.
    # For stricter security, copy the Pi's host key to ~/.ssh/known_hosts first.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 1. Try each key file
    for key_path in SSH_KEY_PATHS:
        if not key_path.exists():
            continue
        try:
            client.connect(
                host, port=SSH_PORT, username=SSH_USER,
                key_filename=str(key_path), timeout=SSH_TIMEOUT,
                look_for_keys=False, allow_agent=False,
            )
            console.print(f"[green]Authenticated with key:[/green] {key_path.name}")
            client.get_transport().set_keepalive(60)
            return client
        except (paramiko.AuthenticationException, paramiko.SSHException):
            pass

    # 2. Try SSH agent (picks up keys added via ssh-add)
    try:
        client.connect(
            host, port=SSH_PORT, username=SSH_USER,
            timeout=SSH_TIMEOUT, look_for_keys=True, allow_agent=True,
        )
        console.print("[green]Authenticated via SSH agent[/green]")
        client.get_transport().set_keepalive(60)
        return client
    except paramiko.AuthenticationException:
        pass

    # 3. Password fallback
    console.print("[yellow]Key auth failed — falling back to password[/yellow]")
    for attempt in range(1, 4):
        pwd = getpass.getpass(f"  Password for {SSH_USER}@{host} (attempt {attempt}/3): ")
        try:
            client.connect(
                host, port=SSH_PORT, username=SSH_USER,
                password=pwd, timeout=SSH_TIMEOUT,
                look_for_keys=False, allow_agent=False,
            )
            console.print("[green]Authenticated with password[/green]")
            client.get_transport().set_keepalive(60)
            return client
        except paramiko.AuthenticationException:
            console.print(f"[red]  Incorrect password[/red]")

    client.close()
    sys.exit("Authentication failed after 3 attempts.")


def run(
    client: paramiko.SSHClient,
    cmd: str,
    sudo: bool = False,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Execute a command; returns (exit_code, stdout, stderr)."""
    if sudo:
        cmd = f"sudo {cmd}"
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")


# ── Aircraft live stats ───────────────────────────────────────────────────────

# dump1090-fa writes stats JSON here; adjust path if using readsb
_STATS_PATH = "/run/dump1090-fa/stats.json"
_AIRCRAFT_PATH = "/run/dump1090-fa/aircraft.json"


def show_aircraft_stats(client: paramiko.SSHClient) -> None:
    _, raw_stats, _ = run(client, f"cat {_STATS_PATH} 2>/dev/null")
    _, raw_ac, _   = run(client, f"cat {_AIRCRAFT_PATH} 2>/dev/null")

    if not raw_stats and not raw_ac:
        console.print("[red]Could not read dump1090-fa JSON — is the service running?[/red]")
        return

    table = Table(title="Live ADS-B Receiver Stats", box=box.ROUNDED, show_lines=True)
    table.add_column("Metric", style="cyan", min_width=28)
    table.add_column("Value", min_width=14)

    # Aircraft currently tracked
    if raw_ac:
        try:
            ac_data = json.loads(raw_ac)
            aircraft = ac_data.get("aircraft", [])
            with_pos = sum(1 for a in aircraft if "lat" in a and "lon" in a)
            table.add_row("Aircraft tracked (total)", str(len(aircraft)))
            table.add_row("Aircraft with position", str(with_pos))
        except json.JSONDecodeError:
            pass

    # 1-minute rolling stats from stats.json
    if raw_stats:
        try:
            stats = json.loads(raw_stats)
            last1 = stats.get("last1min", {})
            total = stats.get("total", {})

            msg_rate = last1.get("local", {}).get("accepted", [None])
            if isinstance(msg_rate, list) and msg_rate:
                msg_rate = msg_rate[0]
            table.add_row("Messages/s (1-min avg)",
                          f"{msg_rate / 60:.1f}" if isinstance(msg_rate, (int, float)) else "—")

            max_range_m = last1.get("local", {}).get("max_distance", None)
            if max_range_m:
                table.add_row("Max range (1 min)",
                              f"{max_range_m / 1852:.1f} nm  ({max_range_m / 1000:.1f} km)")

            cpr_ok = last1.get("cpr", {}).get("surface", 0) + last1.get("cpr", {}).get("airborne", 0)
            table.add_row("CPR positions decoded (1 min)", str(cpr_ok))

            total_msgs = total.get("local", {}).get("accepted", [None])
            if isinstance(total_msgs, list) and total_msgs:
                total_msgs = total_msgs[0]
            table.add_row("Total messages (session)",
                          f"{total_msgs:,}" if isinstance(total_msgs, int) else "—")
        except (json.JSONDecodeError, TypeError):
            console.print("[yellow]Could not parse stats.json[/yellow]")

    console.print(table)


# ── SDR dongle check ───────────────────────────────────────────────────────────

def check_sdr(client: paramiko.SSHClient) -> None:
    console.print("\n[bold]USB devices (RTL-SDR filter):[/bold]")
    _, out, _ = run(client, "lsusb")
    rtl_lines = [l for l in out.splitlines() if any(
        kw in l.lower() for kw in ("rtl", "realtek", "2838", "0bda")
    )]
    if rtl_lines:
        for line in rtl_lines:
            console.print(f"  [green]{line}[/green]")
    else:
        console.print("  [red]No RTL-SDR device found in lsusb output[/red]")
        console.print("  [dim]Full lsusb output:[/dim]")
        console.print(out or "  (no output)")

    # Also check rtl_test briefly if available
    _, rtl_out, _ = run(client, "which rtl_test && timeout 3 rtl_test 2>&1 | head -6 || true")
    if rtl_out.strip():
        console.print("\n[bold]rtl_test (3 s probe):[/bold]")
        console.print(rtl_out.strip())


# ── Live log tail ──────────────────────────────────────────────────────────────

def live_log_tail(client: paramiko.SSHClient) -> None:
    result = _pick_service()
    if result is None:
        return
    display, unit = result
    console.print(f"\n[bold]Tailing {display} — press Ctrl+C to stop[/bold]\n")

    transport = client.get_transport()
    channel = transport.open_session()
    channel.exec_command(f"journalctl -u {unit} -f --no-pager")
    channel.settimeout(0.5)

    try:
        buf = b""
        while True:
            try:
                chunk = channel.recv(4096)
                if not chunk:
                    break
                buf += chunk
                # Flush complete lines
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    console.print(line.decode(errors="replace"))
            except socket.timeout:
                pass
    except KeyboardInterrupt:
        console.print("\n[dim]Tail stopped.[/dim]")
    finally:
        channel.close()


# ── Stop all services ──────────────────────────────────────────────────────────

def stop_all(client: paramiko.SSHClient) -> None:
    if not Confirm.ask("\n[red bold]Stop ALL ADS-B services?[/red bold]"):
        return
    for display, unit in SERVICES.items():
        code, _, err = run(client, f"systemctl stop {unit}", sudo=True)
        ok = "[green]stopped[/green]" if code == 0 else f"[red]FAILED — {err.strip()}[/red]"
        console.print(f"  {display}: {ok}")


# ── Package update ─────────────────────────────────────────────────────────────

# Known package names for the supported feeders
_ADSB_PACKAGES = [
    "dump1090-fa", "piaware", "fr24feed",
    "tar1090", "graphs1090", "readsb",
]


def update_packages(client: paramiko.SSHClient) -> None:
    console.print("\n[bold]Running apt-get update…[/bold]")
    code, out, err = run(client, "apt-get update -qq", sudo=True, timeout=120)
    if code != 0:
        console.print(f"[red]apt-get update failed:\n{err.strip()}[/red]")
        return
    console.print("[green]Package lists updated.[/green]")

    # Check which ADS-B packages have upgrades available
    pkg_list = " ".join(_ADSB_PACKAGES)
    _, apt_out, _ = run(
        client,
        f"apt-get --simulate upgrade {pkg_list} 2>/dev/null"
        " | grep '^Inst' || echo 'No upgrades available'",
        sudo=True, timeout=30,
    )
    console.print("\n[bold]Upgradeable ADS-B packages:[/bold]")
    console.print(apt_out.strip())

    if "No upgrades" in apt_out:
        return

    if not Confirm.ask("\nInstall the upgrades now?"):
        return

    console.print("[bold]Installing — this may take a minute…[/bold]")
    code, out, err = run(
        client,
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg_list} 2>&1",
        sudo=True, timeout=300,
    )
    if code == 0:
        console.print("[green]Upgrade complete.[/green]")
    else:
        console.print(f"[red]Upgrade failed (exit {code}):\n{(out + err).strip()}[/red]")


# ── Status ─────────────────────────────────────────────────────────────────────

def show_status(client: paramiko.SSHClient) -> None:
    console.print("\n[bold]Fetching service states…[/bold]")

    units = list(SERVICES.values())
    _, out, _ = run(client, f"systemctl is-active {' '.join(units)}")
    states = out.strip().splitlines()

    table = Table(title="ADS-B Stack Status", box=box.ROUNDED, show_lines=True)
    table.add_column("Service", style="cyan", min_width=18)
    table.add_column("Unit", style="dim")
    table.add_column("State", min_width=10)

    for i, (display, unit) in enumerate(SERVICES.items()):
        state = states[i] if i < len(states) else "unknown"
        color = "green" if state == "active" else (
            "yellow" if state in ("activating", "reloading") else "red"
        )
        table.add_row(display, unit, f"[{color}]{state}[/{color}]")

    console.print(table)

    # System stats bar
    _, uptime, _ = run(client, "uptime -p")
    _, temp_raw, _ = run(
        client,
        "vcgencmd measure_temp 2>/dev/null || "
        "awk '{printf \"temp=%.1f\\u00b0C\", $1/1000}' /sys/class/thermal/thermal_zone0/temp",
    )
    _, mem, _ = run(client, "free -h | awk '/^Mem:/{print $3\"/\"$2}'")
    _, disk, _ = run(client, "df -h / | awk 'NR==2{print $3\"/\"$2\" (\"$5\" used)\"}'")

    console.print(Panel(
        f"[b]Uptime:[/b] {uptime.strip()}   "
        f"[b]CPU Temp:[/b] {temp_raw.strip()}   "
        f"[b]Memory:[/b] {mem.strip()}   "
        f"[b]Disk (/):[/b] {disk.strip()}",
        title="System", box=box.ROUNDED,
    ))


# ── Service picker ─────────────────────────────────────────────────────────────

def _pick_service() -> tuple[str, str] | None:
    items = list(SERVICES.items())
    console.print("\n[bold]Select service:[/bold]")
    for i, (name, unit) in enumerate(items, 1):
        console.print(f"  [cyan]{i}[/cyan]. {name}  [dim]({unit})[/dim]")
    console.print("  [dim]0. Cancel[/dim]")

    choice = Prompt.ask("Number", default="0")
    if not choice.isdigit() or int(choice) == 0:
        return None
    idx = int(choice) - 1
    if not (0 <= idx < len(items)):
        console.print("[red]Invalid selection.[/red]")
        return None
    return items[idx]


def service_action(client: paramiko.SSHClient, action: str) -> None:
    result = _pick_service()
    if result is None:
        return
    display, unit = result
    console.print(f"\n[bold]{action.capitalize()}ing[/bold] {display}…")
    code, _, err = run(client, f"systemctl {action} {unit}", sudo=True)
    if code == 0:
        console.print(f"[green]{action.capitalize()} OK[/green]")
    else:
        console.print(f"[red]Failed (exit {code})[/red]")
        if err.strip():
            console.print(f"[red]{err.strip()}[/red]")


def restart_all(client: paramiko.SSHClient) -> None:
    if not Confirm.ask("\n[yellow]Restart ALL ADS-B services?[/yellow]"):
        return
    for display, unit in SERVICES.items():
        code, _, err = run(client, f"systemctl restart {unit}", sudo=True)
        ok = "[green]OK[/green]" if code == 0 else f"[red]FAILED — {err.strip()}[/red]"
        console.print(f"  {display}: {ok}")


# ── Logs ───────────────────────────────────────────────────────────────────────

def view_logs(client: paramiko.SSHClient) -> None:
    result = _pick_service()
    if result is None:
        return
    display, unit = result
    n = Prompt.ask("Lines to fetch", default="60")
    n = n if n.isdigit() else "60"
    console.print(f"\n[bold]Last {n} lines — {display}[/bold]")
    _, out, err = run(client, f"journalctl -u {unit} -n {n} --no-pager", timeout=20)
    console.print(out or err or "[dim](no output)[/dim]")


# ── Network info ───────────────────────────────────────────────────────────────

def show_network(client: paramiko.SSHClient) -> None:
    _, ifaces, _ = run(client, "ip -br addr show")
    console.print("\n[bold]Interfaces:[/bold]")
    console.print(ifaces or "[dim]none[/dim]")

    _, ports, _ = run(
        client,
        "ss -tlnp | grep -E ':8080|:8754|:30003|:30005|:31003|:10001'",
    )
    console.print("[bold]ADS-B-related open ports:[/bold]")
    console.print(ports or "[dim](none detected)[/dim]")

    # Quick URL hints
    host_disp = HOST_PRIMARY
    console.print(Panel(
        f"[link]http://{host_disp}:8080[/link]   tar1090 / SkyAware map\n"
        f"[link]http://{host_disp}:8080/graphs1090[/link]   graphs1090",
        title="Web interfaces", box=box.ROUNDED,
    ))


# ── Reboot ─────────────────────────────────────────────────────────────────────

def reboot_pi(client: paramiko.SSHClient) -> None:
    if not Confirm.ask("\n[red bold]Reboot the Raspberry Pi?[/red bold]"):
        return
    run(client, "reboot", sudo=True)
    console.print("[yellow]Reboot command sent — connection will drop.[/yellow]")


# ── Main menu ──────────────────────────────────────────────────────────────────

MENU: list[tuple[str, object]] = [
    ("Status (all services + system stats)", show_status),
    ("Live aircraft & receiver stats",       show_aircraft_stats),
    ("Start a service",                      lambda c: service_action(c, "start")),
    ("Stop a service",                       lambda c: service_action(c, "stop")),
    ("Restart a service",                    lambda c: service_action(c, "restart")),
    ("Restart ALL services",                 restart_all),
    ("Stop ALL services",                    stop_all),
    ("View service logs",                    view_logs),
    ("Tail service log (live)",              live_log_tail),
    ("Network info / open ports",            show_network),
    ("Check RTL-SDR dongle",                 check_sdr),
    ("Update ADS-B packages",                update_packages),
    ("Reboot Pi",                            reboot_pi),
]


def main() -> None:
    ssh = connect()
    try:
        while True:
            console.print(Panel(
                "[bold cyan]ADS-B Stack Manager[/bold cyan]\n"
                f"[dim]{SSH_USER}@{HOST_PRIMARY}  ({HOST_FALLBACK})[/dim]",
                box=box.DOUBLE_EDGE,
            ))
            for i, (label, _) in enumerate(MENU, 1):
                console.print(f"  [cyan]{i}[/cyan]. {label}")
            console.print("  [dim]0. Exit[/dim]\n")

            choice = Prompt.ask("Select", default="1")
            if not choice.isdigit():
                continue
            n = int(choice)
            if n == 0:
                break
            if 1 <= n <= len(MENU):
                try:
                    MENU[n - 1][1](ssh)
                except Exception as exc:
                    console.print(f"[red]Error: {exc}[/red]")
            else:
                console.print("[red]Invalid choice.[/red]")
            console.print()
    finally:
        ssh.close()
        console.print("[dim]SSH connection closed.[/dim]")


if __name__ == "__main__":
    main()
