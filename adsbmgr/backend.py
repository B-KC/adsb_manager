"""SSH backend for the ADS-B stack. No UI — returns data, logs via callback."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from typing import Any

import paramiko

from adsbmgr.catalog import (
    ADSB_PACKAGES,
    JSON_DIRS,
    KNOWN_SERVICES,
    MAP_PROBES,
    OK_HTTP,
    UNIT_LABELS,
    UNIT_TO_MAP,
    WEB_PORTS,
)
from adsbmgr.config import SSH_KEY_PATHS, SSH_PORT, SSH_TIMEOUT, SSH_USER

LogFn = Callable[[str, str], None]


class AdsBBackend:
    def __init__(self, log: LogFn | None = None) -> None:
        self._log = log or (lambda _msg, _level="info": None)
        self.client: paramiko.SSHClient | None = None
        self.host: str = ""
        self.user: str = SSH_USER
        self.port: int = SSH_PORT
        self._lock = threading.Lock()
        self._tail_stop = threading.Event()
        self._tail_channel: paramiko.Channel | None = None

        # Filled by discover() after connect — per-server, not hardcoded
        self.services: list[dict[str, str]] = []
        self.maps: dict[str, str] = {}
        self.unit_to_map: dict[str, str] = {}
        self.stats_dir: str | None = None

    def log(self, msg: str, level: str = "info") -> None:
        self._log(msg, level)

    def _reset_inventory(self) -> None:
        self.services = []
        self.maps = {}
        self.unit_to_map = {}
        self.stats_dir = None

    # ── connection ─────────────────────────────────────────────────────────

    def resolve_host(self, preferred: str | None = None) -> str:
        host = (preferred or "").strip()
        if not host:
            raise ConnectionError("Enter a hostname or IP address")
        try:
            socket.getaddrinfo(host, self.port)
        except (socket.gaierror, OSError) as exc:
            self.log(f"Cannot resolve '{host}' ({exc}) — still trying to connect", "warn")
        return host

    @staticmethod
    def _new_client() -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return client

    def _try_connect(self, client: paramiko.SSHClient, host: str, username: str, **kwargs: Any) -> bool:
        try:
            client.connect(
                host,
                port=self.port,
                username=username,
                timeout=SSH_TIMEOUT,
                **kwargs,
            )
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(60)
            return True
        except paramiko.AuthenticationException:
            client.close()
            return False
        except paramiko.SSHException as exc:
            client.close()
            msg = str(exc).lower()
            if "no authentication methods" in msg or "authentication" in msg:
                return False
            raise

    def is_connected(self) -> bool:
        if self.client is None:
            return False
        transport = self.client.get_transport()
        return bool(transport and transport.is_active())

    def connect(
        self,
        username: str | None = None,
        password: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> str:
        """Authenticate (keys → agent → password). Returns a short success message."""
        self.disconnect()
        if port:
            self.port = port
        user = (username or SSH_USER).strip() or SSH_USER
        resolved = self.resolve_host(host)
        self.log(f"Connecting → {user}@{resolved}:{self.port}", "cmd")

        for key_path in SSH_KEY_PATHS:
            if not key_path.exists():
                continue
            client = self._new_client()
            if self._try_connect(
                client, resolved, user,
                key_filename=str(key_path), look_for_keys=False, allow_agent=False,
            ):
                self.client, self.host, self.user = client, resolved, user
                msg = f"Authenticated with key: {key_path.name}"
                self.log(msg, "ok")
                return msg

        client = self._new_client()
        if self._try_connect(client, resolved, user, look_for_keys=True, allow_agent=True):
            self.client, self.host, self.user = client, resolved, user
            msg = "Authenticated via SSH agent"
            self.log(msg, "ok")
            return msg

        if password:
            client = self._new_client()
            if self._try_connect(
                client, resolved, user,
                password=password, look_for_keys=False, allow_agent=False,
            ):
                self.client, self.host, self.user = client, resolved, user
                msg = "Authenticated with password"
                self.log(msg, "ok")
                return msg
            raise PermissionError("Authentication failed — check username / password")

        raise PermissionError("Key auth failed — enter a password and connect again")

    def disconnect(self) -> None:
        self.stop_journal_tail()
        self._reset_inventory()
        with self._lock:
            if self.client is not None:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None
                self.log("SSH connection closed", "dim")

    def _ensure(self) -> paramiko.SSHClient:
        if not self.is_connected() or self.client is None:
            raise ConnectionError("Not connected")
        return self.client

    # ── commands ───────────────────────────────────────────────────────────

    def run(self, cmd: str, sudo: bool = False, timeout: int = 30) -> tuple[int, str, str]:
        if sudo:
            cmd = f"sudo {cmd}"
        with self._lock:
            client = self._ensure()
            _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            code = stdout.channel.recv_exit_status()
            return (
                code,
                stdout.read().decode(errors="replace"),
                stderr.read().decode(errors="replace"),
            )

    def _label(self, unit: str) -> str:
        for svc in self.services:
            if svc["unit"] == unit:
                return svc["name"]
        return UNIT_LABELS.get(unit, unit)

    # ── discovery ──────────────────────────────────────────────────────────

    def discover(self) -> dict[str, Any]:
        """Probe this feeder for installed units, decoder JSON, and local maps."""
        self.log("Discovering services on this feeder…", "cmd")
        services = self._discover_services()
        self.services = services
        units = {s["unit"] for s in services}
        self.stats_dir = self._discover_json_dir()
        self.maps, self.unit_to_map = self._discover_maps(units)

        if services:
            names = ", ".join(f"{s['name']} ({s['state']})" for s in services)
            self.log(f"Found {len(services)} service(s): {names}", "ok")
        else:
            self.log("No known ADS-B systemd units found on this host", "warn")

        if self.stats_dir:
            self.log(f"Decoder JSON: {self.stats_dir}", "ok")
        else:
            self.log("No aircraft.json found under /run/dump1090-fa, /run/readsb, …", "warn")

        if self.maps:
            self.log(f"Map UIs: {', '.join(self.maps)}", "ok")
        else:
            self.log("No local map UIs responded on this host", "warn")

        return {
            "services": services,
            "maps": dict(self.maps),
            "unit_to_map": dict(self.unit_to_map),
            "stats_dir": self.stats_dir,
        }

    def _discover_services(self) -> list[dict[str, str]]:
        units = [u for u, _ in KNOWN_SERVICES]
        _, out, _ = self.run(f"systemctl show -p Id -p LoadState -p ActiveState -- {' '.join(units)}")
        by_id: dict[str, dict[str, str]] = {}
        block: dict[str, str] = {}

        def _take(current: dict[str, str]) -> None:
            raw_id = current.get("Id", "")
            unit = raw_id.removesuffix(".service")
            if unit and current.get("LoadState") not in ("not-found", "error", ""):
                by_id[unit] = current

        for line in out.splitlines():
            if not line.strip():
                if block:
                    _take(block)
                    block = {}
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                block[key] = val
        if block:
            _take(block)

        found: list[dict[str, str]] = []
        for unit, label in KNOWN_SERVICES:
            info = by_id.get(unit)
            if not info:
                continue
            found.append({
                "name": label,
                "unit": unit,
                "state": info.get("ActiveState") or "unknown",
            })
        return found

    def _discover_json_dir(self) -> str | None:
        checks = " ".join(f"{d}/aircraft.json" for d in JSON_DIRS)
        _, out, _ = self.run(f"ls -1 {checks} 2>/dev/null || true")
        for line in out.splitlines():
            path = line.strip()
            if path.endswith("/aircraft.json"):
                return path.rsplit("/", 1)[0]
        return None

    def _discover_maps(self, installed: set[str]) -> tuple[dict[str, str], dict[str, str]]:
        maps: dict[str, str] = {}
        unit_to_map: dict[str, str] = {}

        parts = [
            (
                f'c=$(curl -sS -o /dev/null -m 2 -w "%{{http_code}}" "{probe["probe"]}" 2>/dev/null || echo 000); '
                f'echo "{probe["name"]}|{probe["public"]}|$c"'
            )
            for probe in MAP_PROBES
        ]
        _, out, _ = self.run(
            "command -v curl >/dev/null && { " + " ; ".join(parts) + "; } || echo NOCURL"
        )

        probed = False
        if "NOCURL" not in out:
            for line in out.splitlines():
                bits = line.strip().split("|")
                if len(bits) != 3:
                    continue
                name, public, code_s = bits
                try:
                    code = int(code_s)
                except ValueError:
                    continue
                probed = True
                if code in OK_HTTP:
                    maps[name] = public

        if not probed or not maps:
            for unit in installed:
                name = UNIT_TO_MAP.get(unit)
                if not name:
                    continue
                probe = next((p for p in MAP_PROBES if p["name"] == name), None)
                if probe:
                    maps[name] = str(probe["public"])

        for probe in MAP_PROBES:
            name = str(probe["name"])
            if name not in maps:
                continue
            for unit in probe["units"]:  # type: ignore[union-attr]
                if unit in installed and unit not in unit_to_map:
                    unit_to_map[unit] = name

        return maps, unit_to_map

    # ── status / stats ─────────────────────────────────────────────────────

    def fetch_status(self) -> dict[str, Any]:
        services: list[dict[str, str]] = []
        if self.services:
            units = [s["unit"] for s in self.services]
            _, out, _ = self.run(f"systemctl is-active {' '.join(units)}")
            states = out.strip().splitlines()
            for i, svc in enumerate(self.services):
                state = states[i] if i < len(states) else "unknown"
                services.append({**svc, "state": state})
                svc["state"] = state

        _, uptime, _ = self.run("uptime -p")
        _, temp_raw, _ = self.run(
            "vcgencmd measure_temp 2>/dev/null || "
            "awk '{printf \"temp=%.1f°C\", $1/1000}' /sys/class/thermal/thermal_zone0/temp"
        )
        _, mem, _ = self.run("free -h | awk '/^Mem:/{print $3\"/\"$2}'")
        _, disk, _ = self.run("df -h / | awk 'NR==2{print $3\"/\"$2\" (\"$5\" used)\"}'")
        return {
            "services": services,
            "uptime": uptime.strip(),
            "temp": temp_raw.strip(),
            "memory": mem.strip(),
            "disk": disk.strip(),
        }

    def fetch_aircraft_stats(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "aircraft_total": None,
            "aircraft_with_pos": None,
            "msg_per_sec": None,
            "max_range": None,
            "cpr": None,
            "total_msgs": None,
            "error": None,
        }
        if not self.stats_dir:
            result["error"] = "No decoder JSON found on this host (dump1090-fa / readsb)"
            return result

        _, raw_stats, _ = self.run(f"cat {self.stats_dir}/stats.json 2>/dev/null")
        _, raw_ac, _ = self.run(f"cat {self.stats_dir}/aircraft.json 2>/dev/null")
        if not raw_stats and not raw_ac:
            result["error"] = f"Could not read JSON in {self.stats_dir} — is the decoder running?"
            return result

        if raw_ac:
            try:
                ac_data = json.loads(raw_ac)
                aircraft = ac_data.get("aircraft", [])
                result["aircraft_total"] = len(aircraft)
                result["aircraft_with_pos"] = sum(
                    1 for a in aircraft if "lat" in a and "lon" in a
                )
            except json.JSONDecodeError:
                self.log("Could not parse aircraft.json", "warn")

        if raw_stats:
            try:
                stats = json.loads(raw_stats)
                last1 = stats.get("last1min", {})
                total = stats.get("total", {})

                msg_rate = last1.get("local", {}).get("accepted", [None])
                if isinstance(msg_rate, list) and msg_rate:
                    msg_rate = msg_rate[0]
                if isinstance(msg_rate, (int, float)):
                    result["msg_per_sec"] = msg_rate / 60

                max_range_m = last1.get("local", {}).get("max_distance")
                if max_range_m:
                    result["max_range"] = (
                        f"{max_range_m / 1852:.1f} nm  ({max_range_m / 1000:.1f} km)"
                    )

                result["cpr"] = (
                    last1.get("cpr", {}).get("surface", 0)
                    + last1.get("cpr", {}).get("airborne", 0)
                )

                total_msgs = total.get("local", {}).get("accepted", [None])
                if isinstance(total_msgs, list) and total_msgs:
                    total_msgs = total_msgs[0]
                if isinstance(total_msgs, int):
                    result["total_msgs"] = f"{total_msgs:,}"
            except (json.JSONDecodeError, TypeError):
                self.log("Could not parse stats.json", "warn")
        return result

    # ── services ───────────────────────────────────────────────────────────

    def service_action(self, unit: str, action: str) -> tuple[int, str]:
        display = self._label(unit)
        self.log(f"{action.capitalize()}ing {display}…", "cmd")
        code, _, err = self.run(f"systemctl {action} {unit}", sudo=True)
        if code == 0:
            self.log(f"{action.capitalize()} OK — {display}", "ok")
        else:
            self.log(f"Failed (exit {code}) — {err.strip() or display}", "err")
        return code, err.strip()

    def action_all(self, action: str) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []
        for svc in self.services:
            display, unit = svc["name"], svc["unit"]
            code, _, err = self.run(f"systemctl {action} {unit}", sudo=True)
            ok = code == 0
            detail = "ok" if ok else err.strip() or f"exit {code}"
            results.append((display, ok, detail))
            self.log(f"  {display}: {detail}", "ok" if ok else "err")
        return results

    def fetch_logs(self, unit: str, lines: int = 60) -> str:
        display = self._label(unit)
        self.log(f"Last {lines} lines — {display}", "cmd")
        _, out, err = self.run(f"journalctl -u {unit} -n {int(lines)} --no-pager", timeout=20)
        text = out or err or "(no output)"
        for line in text.splitlines():
            self.log(line, "dim")
        return text

    def start_journal_tail(self, unit: str, emit: Callable[[str], None]) -> None:
        """Blocking. Call from a worker thread. emit() once per line."""
        self.stop_journal_tail()
        self._tail_stop.clear()
        display = self._label(unit)
        self.log(f"Tailing {display} — stop tail to end", "cmd")

        with self._lock:
            client = self._ensure()
            transport = client.get_transport()
            if transport is None:
                raise ConnectionError("No SSH transport")
            channel = transport.open_session()
            self._tail_channel = channel

        channel.exec_command(f"journalctl -u {unit} -f --no-pager")
        channel.settimeout(0.4)
        buf = b""
        try:
            while not self._tail_stop.is_set():
                try:
                    chunk = channel.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        emit(line.decode(errors="replace"))
                except socket.timeout:
                    continue
        finally:
            try:
                channel.close()
            except Exception:
                pass
            self._tail_channel = None
            self.log("Tail stopped", "dim")

    def stop_journal_tail(self) -> None:
        self._tail_stop.set()
        channel = self._tail_channel
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass

    # ── diagnostics ────────────────────────────────────────────────────────

    def fetch_network(self, web_host: str | None = None) -> dict[str, Any]:
        _, ifaces, _ = self.run("ip -br addr show")
        port_pat = "|".join(f":{p}" for p in WEB_PORTS)
        _, ports, _ = self.run(f"ss -tlnp | grep -E '{port_pat}' || true")
        self.log("Interfaces:", "cmd")
        self.log(ifaces.strip() or "(none)", "dim")
        self.log("ADS-B-related open ports:", "cmd")
        self.log(ports.strip() or "(none detected)", "dim")
        host = (web_host or self.host).strip()
        maps = {name: tmpl.format(host=host) for name, tmpl in self.maps.items()}
        if maps:
            self.log("Discovered maps:", "cmd")
            for name, url in maps.items():
                self.log(f"  {name}: {url}", "dim")
        return {
            "ifaces": ifaces.strip(),
            "ports": ports.strip(),
            "maps": maps,
        }

    def check_sdr(self) -> dict[str, Any]:
        _, out, _ = self.run("lsusb")
        rtl_lines = [
            line for line in out.splitlines()
            if any(kw in line.lower() for kw in ("rtl", "realtek", "2838", "0bda"))
        ]
        self.log("USB devices (RTL-SDR filter):", "cmd")
        if rtl_lines:
            for line in rtl_lines:
                self.log(f"  {line}", "ok")
        else:
            self.log("  No RTL-SDR device found in lsusb output", "err")
            self.log("Full lsusb output:", "dim")
            self.log(out or "  (no output)", "dim")

        _, rtl_out, _ = self.run(
            "which rtl_test && timeout 3 rtl_test 2>&1 | head -6 || true"
        )
        if rtl_out.strip():
            self.log("rtl_test (3 s probe):", "cmd")
            self.log(rtl_out.strip(), "dim")
        return {
            "rtl_lines": rtl_lines,
            "lsusb": out,
            "rtl_test": rtl_out.strip(),
        }

    def preview_updates(self) -> dict[str, Any]:
        self.log("Running apt-get update…", "cmd")
        code, _out, err = self.run("apt-get update -qq", sudo=True, timeout=120)
        if code != 0:
            self.log(f"apt-get update failed:\n{err.strip()}", "err")
            return {"ok": False, "output": err.strip(), "has_upgrades": False}

        self.log("Package lists updated", "ok")
        pkg_list = " ".join(ADSB_PACKAGES)
        _, apt_out, _ = self.run(
            f"apt-get --simulate upgrade {pkg_list} 2>/dev/null"
            " | grep '^Inst' || echo 'No upgrades available'",
            sudo=True,
            timeout=30,
        )
        text = apt_out.strip()
        self.log("Upgradeable ADS-B packages:", "cmd")
        self.log(text, "dim")
        return {
            "ok": True,
            "output": text,
            "has_upgrades": "No upgrades" not in text,
        }

    def install_updates(self) -> dict[str, Any]:
        pkg_list = " ".join(ADSB_PACKAGES)
        self.log("Installing — this may take a minute…", "cmd")
        code, out, err = self.run(
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg_list} 2>&1",
            sudo=True,
            timeout=300,
        )
        combined = (out + err).strip()
        if code == 0:
            self.log("Upgrade complete", "ok")
        else:
            self.log(f"Upgrade failed (exit {code}):\n{combined}", "err")
        return {"ok": code == 0, "output": combined}

    def reboot(self) -> None:
        self.log("Sending reboot command…", "warn")
        try:
            self.run("reboot", sudo=True)
        except Exception:
            pass
        self.log("Reboot sent — connection will drop", "warn")
        self.disconnect()
