#!/usr/bin/env python3

import json
import os
import sys
import socket
import ipaddress
import subprocess

from concurrent.futures import ThreadPoolExecutor, as_completed


# ---------------------------------------------------------
# MAPOWANIE NAZW -> VLAN
# ---------------------------------------------------------

ENVIRONMENTS = {
    "produkcja": 20,
    "magazyn": 25,
    "logistyka": 50,
}


# np. ENVIRONMENT=produkcja
ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "produkcja"
).lower()


# Dane potrzebne tylko do ROZPOZNANIA Raspberry
RPI_SSH_USER = os.environ.get(
    "RPI_SSH_USER",
    "pi"
)

RPI_SSH_KEY = os.environ.get(
    "RPI_SSH_KEY",
    "/home/semaphore/.ssh/raspberry_id"
)


SSH_TIMEOUT = 2
MAX_WORKERS = 60


def port_open(ip, port=22):
    try:
        with socket.create_connection(
            (str(ip), port),
            timeout=0.3
        ):
            return True

    except OSError:
        return False


def is_raspberry(ip):

    ip = str(ip)

    # Bez SSH nie próbujemy dalej
    if not port_open(ip, 22):
        return False

    command = [
        "ssh",

        "-i",
        RPI_SSH_KEY,

        "-o",
        "BatchMode=yes",

        "-o",
        "StrictHostKeyChecking=no",

        "-o",
        "UserKnownHostsFile=/dev/null",

        "-o",
        f"ConnectTimeout={SSH_TIMEOUT}",

        # kompatybilność ze starszym SSH
        "-o",
        "KexAlgorithms=+diffie-hellman-group14-sha1",

        "-o",
        "HostKeyAlgorithms=+ssh-rsa",

        "-o",
        "PubkeyAcceptedAlgorithms=+ssh-rsa",

        f"{RPI_SSH_USER}@{ip}",

        "tr -d '\\0' < /proc/device-tree/model 2>/dev/null"
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=SSH_TIMEOUT + 2
        )

        if result.returncode != 0:
            return False

        model = result.stdout.strip()

        return "Raspberry Pi" in model

    except Exception:
        return False


def scan_network(network):

    raspberry_hosts = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(is_raspberry, ip): ip
            for ip in network.hosts()
        }

        for future in as_completed(futures):

            ip = futures[future]

            try:
                if future.result():
                    raspberry_hosts.append(
                        str(ip)
                    )
            except Exception:
                pass

    return sorted(
        raspberry_hosts,
        key=ipaddress.ip_address
    )


def build_inventory():

    if ENVIRONMENT not in ENVIRONMENTS:

        # Nie wypisujemy błędu tekstowego,
        # bo inventory musi zwrócić poprawny JSON
        return {
            "_meta": {
                "hostvars": {}
            },
            "raspberry": {
                "hosts": []
            }
        }


    vlan = ENVIRONMENTS[ENVIRONMENT]

    network = ipaddress.ip_network(
        f"10.10.{vlan}.0/24",
        strict=False
    )

    hosts = scan_network(network)

    inventory = {

        "_meta": {
            "hostvars": {}
        },

        "raspberry": {
            "hosts": []
        },

        ENVIRONMENT: {
            "hosts": []
        },

        f"vlan_{vlan}": {
            "hosts": []
        }
    }


    for ip in hosts:

        inventory["raspberry"]["hosts"].append(ip)

        inventory[ENVIRONMENT]["hosts"].append(ip)

        inventory[f"vlan_{vlan}"]["hosts"].append(ip)

        inventory["_meta"]["hostvars"][ip] = {

            "ansible_host": ip,

            "environment": ENVIRONMENT,

            "vlan": vlan
        }


    return inventory


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
