#!/usr/bin/env python3

import json
import os
import sys
import re
import socket
import ipaddress
import subprocess
import tempfile

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# KONFIGURACJA
# ============================================================

HOSTS_FILE = os.environ.get(
    "RPI_HOSTS_FILE",
    "/data/rpi_inventory/hosts.json"
)

NETWORK_CONFIG_ENV = os.environ.get(
    "RPI_NETWORK_CONFIG",
    ""
)

SSH_USER = os.environ.get(
    "RPI_SSH_USER",
    "pi"
)

SSH_PASSWORD = os.environ.get(
    "RPI_SSH_PASSWORD",
    ""
)

SSH_PASSWORD_ALT = os.environ.get(
    "RPI_SSH_PASSWORD_ALT",
    ""
)

SSH_PORT = 22

PORT_TIMEOUT = float(
    os.environ.get("PORT_TIMEOUT", "0.3")
)

SSH_TIMEOUT = int(
    os.environ.get("SSH_TIMEOUT", "3")
)

MAX_WORKERS = int(
    os.environ.get("MAX_WORKERS", "80")
)

DEBUG = os.environ.get(
    "DEBUG_INVENTORY",
    "0"
) == "1"


# ============================================================
# DEBUG
# ============================================================

def debug(message):
    if DEBUG:
        print(
            f"[refresh_rpi] {message}",
            file=sys.stderr
        )


def now():
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


# ============================================================
# TRYB MERGE / REBUILD
# ============================================================

def get_refresh_mode():

    mode = os.environ.get(
        "RPI_REFRESH_MODE",
        "MERGE"
    ).strip().upper()

    # pozwala również:
    #
    # refresh_rpi.py --mode REBUILD
    #
    if "--mode" in sys.argv:

        try:
            index = sys.argv.index("--mode")
            mode = sys.argv[index + 1].upper()
        except Exception:
            pass

    if mode not in (
        "MERGE",
        "REBUILD"
    ):
        mode = "MERGE"

    return mode


# ============================================================
# JSON SIECI
# ============================================================

def load_network_config():

    if not NETWORK_CONFIG_ENV:
        raise RuntimeError(
            "Brak RPI_NETWORK_CONFIG"
        )

    try:
        config = json.loads(
            NETWORK_CONFIG_ENV
        )
    except json.JSONDecodeError as exception:
        raise RuntimeError(
            f"Błędny RPI_NETWORK_CONFIG: {exception}"
        )

    if "ZAKLADY" not in config:
        raise RuntimeError(
            "Brak ZAKLADY"
        )

    if "WYDZIALY" not in config:
        raise RuntimeError(
            "Brak WYDZIALY"
        )

    return config


# ============================================================
# SIECI /24
# ============================================================

def build_network_list(config):

    result = []

    zaklady = config.get(
        "ZAKLADY",
        {}
    )

    wydzialy = config.get(
        "WYDZIALY",
        {}
    )

    for zaklad, prefix in zaklady.items():

        for wydzial, vlans in wydzialy.items():

            if not isinstance(vlans, list):
                continue

            for vlan in vlans:

                vlan = str(vlan).strip()

                cidr = (
                    f"{prefix}.{vlan}.0/24"
                )

                try:
                    network = ipaddress.ip_network(
                        cidr,
                        strict=False
                    )
                except ValueError:
                    debug(
                        f"Błędna sieć: {cidr}"
                    )
                    continue

                result.append({
                    "zaklad": str(zaklad),
                    "wydzial": str(wydzial),
                    "vlan": vlan,
                    "network": network
                })

    return result


# ============================================================
# PORT SSH
# ============================================================

def ssh_port_open(ip):

    try:
        with socket.create_connection(
            (str(ip), SSH_PORT),
            timeout=PORT_TIMEOUT
        ):
            return True

    except (
        socket.timeout,
        ConnectionRefusedError,
        OSError
    ):
        return False


# ============================================================
# SSH
# ============================================================

def ssh_command(
    ip,
    password,
    remote_command
):

    env = os.environ.copy()

    env["SSHPASS"] = password

    command = [
        "sshpass",
        "-e",

        "ssh",

        "-o",
        "PreferredAuthentications=password",

        "-o",
        "PubkeyAuthentication=no",

        "-o",
        "StrictHostKeyChecking=no",

        "-o",
        "UserKnownHostsFile=/dev/null",

        "-o",
        "LogLevel=ERROR",

        "-o",
        f"ConnectTimeout={SSH_TIMEOUT}",

        "-o",
        "ConnectionAttempts=1",

        f"{SSH_USER}@{ip}",

        remote_command
    ]

    try:

        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=SSH_TIMEOUT + 3,
            env=env
        )

    except Exception as exception:

        debug(
            f"SSH {ip}: {exception}"
        )

        return None


# ============================================================
# SPRAWDZENIE RPI
# ============================================================

def check_raspberry(
    ip,
    zaklad,
    wydzial,
    vlan,
    network
):

    ip = str(ip)

    if not ssh_port_open(ip):
        return None

    passwords = []

    if SSH_PASSWORD:
        passwords.append(
            ("PRIMARY", SSH_PASSWORD)
        )

    if (
        SSH_PASSWORD_ALT
        and SSH_PASSWORD_ALT != SSH_PASSWORD
    ):
        passwords.append(
            ("ALTERNATIVE", SSH_PASSWORD_ALT)
        )

    remote_command = (
        "MODEL=$(tr -d '\\0' "
        "</proc/device-tree/model 2>/dev/null); "

        "SERIAL=$(tr -d '\\0' "
        "</proc/device-tree/serial-number 2>/dev/null); "

        "HOSTNAME=$(hostname 2>/dev/null); "

        "MACHINE=$(cat /etc/machine-id 2>/dev/null); "

        "printf '%s|%s|%s|%s\\n' "
        "\"$MODEL\" "
        "\"$SERIAL\" "
        "\"$HOSTNAME\" "
        "\"$MACHINE\""
    )

    result = None
    auth = None

    for auth_name, password in passwords:

        current = ssh_command(
            ip,
            password,
            remote_command
        )

        if (
            current is not None
            and current.returncode == 0
        ):
            result = current
            auth = auth_name
            break

    if result is None:
        return None

    parts = result.stdout.strip().split(
        "|",
        3
    )

    model = (
        parts[0].strip()
        if len(parts) > 0
        else ""
    )

    serial = (
        parts[1].strip()
        if len(parts) > 1
        else ""
    )

    hostname = (
        parts[2].strip()
        if len(parts) > 2
        else ""
    )

    machine_id = (
        parts[3].strip()
        if len(parts) > 3
        else ""
    )

    if "Raspberry Pi" not in model:
        return None

    # serial jest podstawowym ID
    device_id = serial

    # awaryjny fallback
    if not device_id:
        device_id = machine_id

    if not device_id:
        debug(
            f"{ip}: RPi bez serial/machine-id"
        )
        return None

    debug(
        f"RPI {ip} "
        f"serial={device_id} "
        f"{zaklad}/{wydzial} "
        f"auth={auth}"
    )

    return {
        "id": device_id,
        "serial": serial,
        "machine_id": machine_id,
        "hostname": hostname,
        "ip": ip,
        "model": model,
        "zaklad": zaklad,
        "wydzial": wydzial,
        "vlan": vlan,
        "network_cidr": str(network),
        "auth": auth,
        "online": True,
        "last_seen": now()
    }


# ============================================================
# SKAN SIECI
# ============================================================

def scan_network(item):

    network = item["network"]

    found = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                check_raspberry,
                ip,
                item["zaklad"],
                item["wydzial"],
                item["vlan"],
                network
            )
            for ip in network.hosts()
        ]

        for future in as_completed(
            futures
        ):

            try:
                result = future.result()

                if result:
                    found.append(result)

            except Exception as exception:
                debug(
                    f"Błąd skanu: {exception}"
                )

    return found


# ============================================================
# STARA BAZA
# ============================================================

def load_hosts():

    if not os.path.isfile(
        HOSTS_FILE
    ):
        return {}

    try:
        with open(
            HOSTS_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

            if isinstance(data, dict):
                return data

    except Exception as exception:

        debug(
            f"Nie można odczytać hosts.json: "
            f"{exception}"
        )

    return {}


# ============================================================
# ATOMOWY ZAPIS
# ============================================================

def save_hosts(hosts):

    directory = os.path.dirname(
        HOSTS_FILE
    )

    os.makedirs(
        directory,
        exist_ok=True
    )

    fd, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=".hosts_",
        suffix=".json"
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                hosts,
                file,
                indent=2,
                ensure_ascii=False,
                sort_keys=True
            )

        os.replace(
            temporary,
            HOSTS_FILE
        )

    except Exception:

        try:
            os.unlink(
                temporary
            )
        except Exception:
            pass

        raise


# ============================================================
# REFRESH
# ============================================================

def refresh():

    mode = get_refresh_mode()

    print(
        f"Tryb aktualizacji: {mode}"
    )

    config = load_network_config()

    networks = build_network_list(
        config
    )

    if mode == "REBUILD":

        hosts = {}

    else:

        hosts = load_hosts()

        # wszystkie stare urządzenia
        # chwilowo oznacz offline
        for device in hosts.values():

            device["online"] = False


    found_count = 0
    new_count = 0
    updated_count = 0


    for item in networks:

        print(
            f"Skanuję: "
            f"{item['zaklad']} / "
            f"{item['wydzial']} / "
            f"VLAN {item['vlan']} "
            f"-> {item['network']}"
        )

        discovered = scan_network(
            item
        )

        for device in discovered:

            found_count += 1

            device_id = device[
                "id"
            ]

            if device_id in hosts:

                # zachowaj ewentualne przyszłe
                # dodatkowe pola ręczne
                existing = hosts[
                    device_id
                ]

                existing.update(
                    device
                )

                updated_count += 1

            else:

                hosts[
                    device_id
                ] = device

                new_count += 1


    save_hosts(
        hosts
    )


    online = sum(
        1
        for host in hosts.values()
        if host.get("online") is True
    )

    offline = len(hosts) - online


    print()
    print("====================================")
    print("ODŚWIEŻANIE RPI ZAKOŃCZONE")
    print("====================================")
    print(
        f"Tryb:             {mode}"
    )
    print(
        f"Znaleziono teraz: {found_count}"
    )
    print(
        f"Nowe hosty:       {new_count}"
    )
    print(
        f"Zaktualizowane:   {updated_count}"
    )
    print(
        f"W bazie razem:    {len(hosts)}"
    )
    print(
        f"Online:           {online}"
    )
    print(
        f"Offline:          {offline}"
    )
    print(
        f"Plik:             {HOSTS_FILE}"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:
        refresh()

    except Exception as exception:

        print(
            f"BŁĄD: {exception}",
            file=sys.stderr
        )

        sys.exit(1)
