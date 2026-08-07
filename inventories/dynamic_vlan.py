#!/usr/bin/env python3

import json
import os
import sys
import socket
import ipaddress
import subprocess
import tempfile

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# KONFIGURACJA
# ============================================================

# Semaphore -> zmienna środowiskowa:
#
# VLANS=25=10.10.25.0/24,238_24=10.238.24.0/24
#
VLANS_ENV = os.environ.get("VLANS", "")


# Semaphore -> SECRET:
#
# RPI_SSH_PRIVATE_KEY=
# -----BEGIN OPENSSH PRIVATE KEY-----
# ...
# -----END OPENSSH PRIVATE KEY-----
#
SSH_PRIVATE_KEY = os.environ.get(
    "RPI_SSH_PRIVATE_KEY",
    ""
)


# Użytkownik Raspberry
# Jeżeli na Raspberry masz innego użytkownika, zmień tutaj.
SSH_USER = "pi"


PORT_TIMEOUT = 0.30
SSH_TIMEOUT = 3
MAX_WORKERS = 80

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
            f"[dynamic_inventory] {message}",
            file=sys.stderr
        )


# ============================================================
# ODCZYT VLAN-ÓW
# ============================================================

def load_networks():

    networks = []

    for item in VLANS_ENV.split(","):

        item = item.strip()

        if not item:
            continue

        if "=" not in item:
            debug(
                f"Błędna definicja VLAN: {item}"
            )
            continue

        name, cidr = item.split("=", 1)

        name = name.strip()
        cidr = cidr.strip()

        try:

            network = ipaddress.ip_network(
                cidr,
                strict=False
            )

        except ValueError:

            debug(
                f"Błędna sieć: {name}={cidr}"
            )

            continue

        networks.append(
            (
                name,
                network
            )
        )

    return networks


# ============================================================
# TYMCZASOWY KLUCZ SSH
# ============================================================

def create_private_key_file():

    if not SSH_PRIVATE_KEY:

        debug(
            "Brak sekretu RPI_SSH_PRIVATE_KEY"
        )

        return None

    try:

        fd, path = tempfile.mkstemp(
            prefix="rpi_inventory_",
            suffix=".key"
        )

        os.write(
            fd,
            SSH_PRIVATE_KEY.encode("utf-8")
        )

        os.close(fd)

        os.chmod(
            path,
            0o600
        )

        return path

    except Exception as exception:

        debug(
            f"Nie można utworzyć klucza SSH: {exception}"
        )

        return None


# ============================================================
# SPRAWDZENIE PORTU 22
# ============================================================

def ssh_port_open(ip):

    try:

        with socket.create_connection(
            (
                str(ip),
                22
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
# SPRAWDZENIE RASPBERRY
# ============================================================

def check_raspberry(ip, key_file):

    ip = str(ip)

    # Najpierw szybki test TCP/22.
    if not ssh_port_open(ip):
        return None

    debug(
        f"SSH dostępne: {ip}"
    )

    command = [

        "ssh",

        "-i",
        key_file,

        "-o",
        "BatchMode=yes",

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

        # obsługa starszego SSH
        "-o",
        "KexAlgorithms=+diffie-hellman-group14-sha1",

        "-o",
        "HostKeyAlgorithms=+ssh-rsa",

        "-o",
        "PubkeyAcceptedAlgorithms=+ssh-rsa",

        f"{SSH_USER}@{ip}",

        "tr -d '\\0' < /proc/device-tree/model 2>/dev/null"
    ]

    try:

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=SSH_TIMEOUT + 2
        )

    except subprocess.TimeoutExpired:

        debug(
            f"Timeout SSH: {ip}"
        )

        return None

    except Exception as exception:

        debug(
            f"Błąd SSH {ip}: {exception}"
        )

        return None


    if result.returncode != 0:

        debug(
            f"Logowanie SSH nieudane: {ip}"
        )

        return None


    model = result.stdout.strip()


    if not model:

        debug(
            f"Brak /proc/device-tree/model: {ip}"
        )

        return None


    debug(
        f"{ip}: {model}"
    )


    # ========================================================
    # TYLKO RASPBERRY PI
    # ========================================================

    if "Raspberry Pi" not in model:

        debug(
            f"{ip}: nie jest Raspberry Pi"
        )

        return None


    return {
        "ip": ip,
        "model": model
    }


# ============================================================
# SKANOWANIE SIECI
# ============================================================

def scan_network(network, key_file):

    found = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {

            executor.submit(
                check_raspberry,
                ip,
                key_file
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
                    f"Błąd podczas skanowania: {exception}"
                )


    found.sort(
        key=lambda item:
        ipaddress.ip_address(
            item["ip"]
        )
    )

    return found


# ============================================================
# BUDOWANIE INVENTORY
# ============================================================

def build_inventory():

    inventory = {

        "_meta": {
            "hostvars": {}
        },

        "raspberry": {
            "hosts": []
        }

    }


    networks = load_networks()


    if not networks:

        debug(
            "Brak poprawnych sieci w VLANS"
        )

        return inventory


    key_file = create_private_key_file()


    if not key_file:

        return inventory


    try:

        for vlan_name, network in networks:

            debug(
                f"Skanowanie {vlan_name} -> {network}"
            )


            # grupa np. vlan_25
            group_name = (
                "vlan_"
                + vlan_name
                .replace(".", "_")
                .replace("-", "_")
            )


            inventory[group_name] = {
                "hosts": []
            }


            hosts = scan_network(
                network,
                key_file
            )


            for host in hosts:

                ip = host["ip"]
                model = host["model"]


                # konkretna sieć
                inventory[
                    group_name
                ][
                    "hosts"
                ].append(ip)


                # wszystkie Raspberry
                if ip not in inventory[
                    "raspberry"
                ][
                    "hosts"
                ]:

                    inventory[
                        "raspberry"
                    ][
                        "hosts"
                    ].append(ip)


                inventory[
                    "_meta"
                ][
                    "hostvars"
                ][ip] = {

                    "ansible_host":
                        ip,

                    "raspberry_model":
                        model,

                    "vlan_name":
                        vlan_name,

                    "network_cidr":
                        str(network)

                }


    finally:

        try:

            os.unlink(
                key_file
            )

        except Exception:
            pass


    inventory[
        "raspberry"
    ][
        "hosts"
    ].sort(
        key=ipaddress.ip_address
    )


    return inventory


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    if "--host" in sys.argv:

        print("{}")

    else:

        print(
            json.dumps(
                build_inventory(),
                indent=2
            )
        )
