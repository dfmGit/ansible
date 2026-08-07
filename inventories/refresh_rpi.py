#!/usr/bin/env python3

import json
import os
import sys
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

REFRESH_MODE = os.environ.get(
    "RPI_REFRESH_MODE",
    "MERGE"
).strip().upper()

SSH_PORT = 22

PORT_TIMEOUT = float(
    os.environ.get(
        "PORT_TIMEOUT",
        "0.3"
    )
)

SSH_TIMEOUT = int(
    os.environ.get(
        "SSH_TIMEOUT",
        "3"
    )
)

MAX_WORKERS = int(
    os.environ.get(
        "MAX_WORKERS",
        "80"
    )
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
# KONFIGURACJA JSON
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


    if not isinstance(config, dict):

        raise RuntimeError(
            "RPI_NETWORK_CONFIG musi być obiektem JSON"
        )


    if "ZAKLADY" not in config:

        raise RuntimeError(
            "Brak ZAKLADY w RPI_NETWORK_CONFIG"
        )


    if "WYDZIALY" not in config:

        raise RuntimeError(
            "Brak WYDZIALY w RPI_NETWORK_CONFIG"
        )


    return config


# ============================================================
# GENEROWANIE SIECI
# ============================================================

def build_network_list(config):

    zaklady = config.get(
        "ZAKLADY",
        {}
    )

    wydzialy = config.get(
        "WYDZIALY",
        {}
    )

    networks = []


    for zaklad, prefix in zaklady.items():

        prefix = str(prefix).strip()


        for wydzial, vlans in wydzialy.items():

            if not isinstance(vlans, list):

                debug(
                    f"{wydzial}: VLAN-y nie są listą"
                )

                continue


            for vlan in vlans:

                vlan = str(vlan).strip()

                #
                # Wszystkie VLAN-y /24
                #
                cidr = (
                    f"{prefix}."
                    f"{vlan}.0/24"
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


                networks.append({
                    "zaklad": str(zaklad),
                    "wydzial": str(wydzial),
                    "vlan": vlan,
                    "network": network
                })


    return networks


# ============================================================
# PORT SSH
# ============================================================

def ssh_port_open(ip):

    try:

        with socket.create_connection(
            (
                str(ip),
                SSH_PORT
            ),
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


    except subprocess.TimeoutExpired:

        debug(
            f"{ip}: timeout SSH"
        )

        return None


    except FileNotFoundError:

        raise RuntimeError(
            "Brak programu sshpass"
        )


    except Exception as exception:

        debug(
            f"{ip}: błąd SSH: {exception}"
        )

        return None


# ============================================================
# SPRAWDZENIE RASPBERRY
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


    debug(
        f"{ip}: SSH dostępne"
    )


    passwords = []


    if SSH_PASSWORD:

        passwords.append(
            (
                "PRIMARY",
                SSH_PASSWORD
            )
        )


    if (
        SSH_PASSWORD_ALT
        and SSH_PASSWORD_ALT != SSH_PASSWORD
    ):

        passwords.append(
            (
                "ALTERNATIVE",
                SSH_PASSWORD_ALT
            )
        )


    if not passwords:

        return None


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


    # ========================================================
    # HASŁO PODSTAWOWE / ALTERNATYWNE
    # ========================================================

    for auth_name, password in passwords:


        debug(
            f"{ip}: próba {auth_name}"
        )


        current = ssh_command(
            ip,
            password,
            remote_command
        )


        if current is None:

            continue


        if current.returncode == 0:

            result = current
            auth = auth_name

            debug(
                f"{ip}: logowanie OK ({auth_name})"
            )

            break


        debug(
            f"{ip}: logowanie NIEUDANE ({auth_name})"
        )


    if result is None:

        debug(
            f"{ip}: żadne hasło nie działa"
        )

        return None


    output = result.stdout.strip()


    if not output:

        return None


    parts = output.split(
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


    # ========================================================
    # TYLKO RASPBERRY PI
    # ========================================================

    if "Raspberry Pi" not in model:

        debug(
            f"{ip}: nie jest Raspberry Pi"
        )

        return None


    # ========================================================
    # STAŁE ID
    # ========================================================

    device_id = serial or machine_id


    if not device_id:

        debug(
            f"{ip}: brak serial i machine-id"
        )

        return None


    debug(
        f"RPI: {ip} "
        f"serial={serial} "
        f"hostname={hostname} "
        f"{zaklad}/{wydzial} "
        f"VLAN={vlan} "
        f"AUTH={auth}"
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

        "network_cidr": str(
            network
        ),

        "auth": auth,

        "online": True,

        "last_seen": now()
    }


# ============================================================
# SKAN JEDNEJ SIECI
# ============================================================

def scan_network(item):

    network = item[
        "network"
    ]

    found = []


    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        futures = {

            executor.submit(
                check_raspberry,
                ip,
                item["zaklad"],
                item["wydzial"],
                item["vlan"],
                network
            ): ip

            for ip in network.hosts()

        }


        for future in as_completed(
            futures
        ):

            try:

                result = future.result()


                if result:

                    found.append(
                        result
                    )


            except Exception as exception:

                debug(
                    f"Błąd skanowania: {exception}"
                )


    return found


# ============================================================
# WCZYTANIE STAREJ BAZY
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

            data = json.load(
                file
            )


        if isinstance(
            data,
            dict
        ):

            return data


    except Exception as exception:

        debug(
            f"Błąd odczytu hosts.json: "
            f"{exception}"
        )


    return {}


# ============================================================
# ZAPIS ATOMOWY
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

    mode = REFRESH_MODE


    if mode not in (
        "MERGE",
        "REBUILD"
    ):

        raise RuntimeError(
            f"Nieprawidłowy RPI_REFRESH_MODE: {mode}"
        )


    print(
        f"Tryb aktualizacji: {mode}"
    )


    config = load_network_config()


    networks = build_network_list(
        config
    )


    print(
        f"Liczba sieci: {len(networks)}"
    )


    # ========================================================
    # MERGE
    # ========================================================

    if mode == "MERGE":

        hosts = load_hosts()


        #
        # stare hosty zostają,
        # ale na początku oznaczamy je offline
        #
        for device in hosts.values():

            device[
                "online"
            ] = False


    # ========================================================
    # REBUILD
    # ========================================================

    else:

        hosts = {}


    found_count = 0
    new_count = 0
    updated_count = 0


    # ========================================================
    # SKAN
    # ========================================================

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


        print(
            f"  Raspberry Pi: "
            f"{len(discovered)}"
        )


        for device in discovered:


            found_count += 1


            device_id = device[
                "id"
            ]


            # =================================================
            # ISTNIEJĄCY HOST
            # =================================================

            if device_id in hosts:


                previous_ip = hosts[
                    device_id
                ].get(
                    "ip"
                )


                hosts[
                    device_id
                ].update(
                    device
                )


                updated_count += 1


                if (
                    previous_ip
                    and previous_ip != device["ip"]
                ):

                    print(
                        f"  IP zmienione: "
                        f"{device_id}: "
                        f"{previous_ip} "
                        f"-> {device['ip']}"
                    )


            # =================================================
            # NOWY HOST
            # =================================================

            else:


                hosts[
                    device_id
                ] = device


                new_count += 1


                print(
                    f"  NOWY RPI: "
                    f"{device_id} "
                    f"{device['ip']} "
                    f"{device['hostname']}"
                )


    # ========================================================
    # ZAPIS
    # ========================================================

    save_hosts(
        hosts
    )


    online = sum(
        1
        for device in hosts.values()
        if device.get(
            "online"
        ) is True
    )


    offline = (
        len(hosts)
        - online
    )


    # ========================================================
    # PODSUMOWANIE
    # ========================================================

    print()
    print(
        "========================================"
    )

    print(
        "ODŚWIEŻANIE RASPBERRY PI"
    )

    print(
        "========================================"
    )

    print(
        f"Tryb:              {mode}"
    )

    print(
        f"Znaleziono teraz:  {found_count}"
    )

    print(
        f"Nowe hosty:        {new_count}"
    )

    print(
        f"Zaktualizowane:    {updated_count}"
    )

    print(
        f"W bazie razem:     {len(hosts)}"
    )

    print(
        f"ONLINE:            {online}"
    )

    print(
        f"OFFLINE:           {offline}"
    )

    print(
        f"Plik:              {HOSTS_FILE}"
    )

    print(
        "========================================"
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
