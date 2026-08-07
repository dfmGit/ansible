#!/usr/bin/env python3

import json
import os
import sys
import socket
import ipaddress
import subprocess

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# KONFIGURACJA
# ============================================================

# np.
# VLANS=238_27=10.237.24.0/24
VLANS_ENV = os.environ.get("VLANS", "")

# SSH
SSH_USER = os.environ.get("RPI_SSH_USER", "pi")
SSH_PASSWORD = os.environ.get("RPI_SSH_PASSWORD", "")

SSH_PORT = 22
PORT_TIMEOUT = float(os.environ.get("PORT_TIMEOUT", "0.3"))
SSH_TIMEOUT = int(os.environ.get("SSH_TIMEOUT", "3"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "80"))

DEBUG = os.environ.get("DEBUG_INVENTORY", "0") == "1"


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
# VLAN
# ============================================================

def load_networks():

    networks = []

    for item in VLANS_ENV.split(","):

        item = item.strip()

        if not item:
            continue

        if "=" not in item:
            debug(f"Błędna definicja VLAN: {item}")
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
            debug(f"Błędna sieć: {name}={cidr}")
            continue

        networks.append(
            (name, network)
        )

    return networks


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
# SPRAWDZENIE RASPBERRY
# ============================================================

def check_raspberry(ip):

    ip = str(ip)

    if not ssh_port_open(ip):
        return None

    debug(f"SSH dostępne: {ip}")

    if not SSH_PASSWORD:
        debug("Brak sekretu RPI_SSH_PASSWORD")
        return None

    env = os.environ.copy()

    # sshpass -e czyta hasło z SSHPASS
    env["SSHPASS"] = SSH_PASSWORD

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

        "tr -d '\\0' < /proc/device-tree/model 2>/dev/null"
    ]

    try:

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=SSH_TIMEOUT + 3,
            env=env
        )

    except subprocess.TimeoutExpired:

        debug(f"Timeout SSH: {ip}")
        return None

    except FileNotFoundError:

        debug("Brak programu sshpass w kontenerze")
        return None

    except Exception as exception:

        debug(
            f"Błąd SSH {ip}: {exception}"
        )
        return None


    if result.returncode != 0:

        debug(
            f"Logowanie SSH nieudane: {ip}; "
            f"kod={result.returncode}; "
            f"blad={result.stderr.strip()}"
        )

        return None


    model = result.stdout.strip()

    debug(
        f"{ip}: model={model}"
    )


    if "Raspberry Pi" not in model:

        debug(
            f"{ip}: nie jest Raspberry Pi"
        )

        return None


    debug(
        f"Raspberry Pi znalezione: {ip} - {model}"
    )


    return {
        "ip": ip,
        "model": model
    }


# ============================================================
# SKAN SIECI
# ============================================================

def scan_network(network):

    found = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                check_raspberry,
                ip
            ): ip

            for ip in network.hosts()
        }


        for future in as_completed(futures):

            try:

                result = future.result()

                if result:
                    found.append(result)

            except Exception as exception:

                debug(
                    f"Błąd skanowania: {exception}"
                )


    found.sort(
        key=lambda item:
        ipaddress.ip_address(
            item["ip"]
        )
    )

    return found


# ============================================================
# INVENTORY
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


    if not SSH_PASSWORD:

        debug(
            "Brak sekretu RPI_SSH_PASSWORD"
        )

        return inventory


    debug(
        f"Użytkownik SSH: {SSH_USER}"
    )


    for vlan_name, network in networks:

        debug(
            f"Skanowanie {vlan_name} -> {network}"
        )


        group_name = (
            "vlan_"
            + vlan_name
            .replace(".", "_")
            .replace("-", "_")
        )


        inventory[group_name] = {
            "hosts": []
        }


        hosts = scan_network(network)


        for host in hosts:

            ip = host["ip"]
            model = host["model"]


            inventory[
                group_name
            ][
                "hosts"
            ].append(ip)


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

                "ansible_host": ip,

                "ansible_user": SSH_USER,

                "raspberry_model": model,

                "vlan_name": vlan_name,

                "network_cidr": str(network)
            }


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
