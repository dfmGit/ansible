#!/usr/bin/env python3

import json
import os
import sys
import socket
import ipaddress
import re

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# ZMIENNE ŚRODOWISKOWE Z SEMAPHORE
# ============================================================
#
# NETWORKS:
# 25=10.10.25.0/24,26=10.10.26.0/24,238_24=10.238.24.0/24
#
# VLANS:
# 25,238_24
#
# ============================================================

NETWORKS_ENV = os.environ.get("NETWORKS", "")
VLANS_ENV = os.environ.get("VLANS", "")


# ============================================================
# USTAWIENIA SKANOWANIA
# ============================================================

SSH_PORT = 22

PORT_TIMEOUT = float(
    os.environ.get("PORT_TIMEOUT", "0.3")
)

MAX_WORKERS = int(
    os.environ.get("MAX_WORKERS", "100")
)

MAX_HOSTS_PER_NETWORK = int(
    os.environ.get("MAX_HOSTS_PER_NETWORK", "1024")
)

DEBUG = os.environ.get(
    "DEBUG_INVENTORY",
    "0"
) == "1"


def debug(message):
    """
    Debug idzie na STDERR.
    Nie może trafiać do STDOUT,
    ponieważ STDOUT musi zawierać wyłącznie JSON inventory.
    """

    if DEBUG:
        print(
            f"[dynamic_inventory] {message}",
            file=sys.stderr
        )


# ============================================================
# WCZYTANIE NETWORKS
# ============================================================

def load_networks():
    """
    Przykład:

    NETWORKS=
    25=10.10.25.0/24,
    26=10.10.26.0/24,
    238_24=10.238.24.0/24

    Zwraca:

    {
        "25": "10.10.25.0/24",
        "26": "10.10.26.0/24",
        "238_24": "10.238.24.0/24"
    }
    """

    networks = {}

    for item in NETWORKS_ENV.split(","):

        item = item.strip()

        if not item:
            continue

        if "=" not in item:
            debug(
                f"Błędna definicja NETWORKS: {item}"
            )
            continue

        name, cidr = item.split("=", 1)

        name = name.strip()
        cidr = cidr.strip()

        if not name or not cidr:
            continue

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

        networks[name] = str(network)

    return networks


# ============================================================
# POBRANIE SIECI DO SKANOWANIA
# ============================================================

def get_requested_networks():

    networks = load_networks()

    requested = [
        item.strip()
        for item in VLANS_ENV.split(",")
        if item.strip()
    ]

    result = []

    for name in requested:

        if name not in networks:

            debug(
                f"VLANS zawiera '{name}', "
                f"ale brak go w NETWORKS"
            )

            continue

        result.append(
            (
                name,
                networks[name]
            )
        )

    return result


# ============================================================
# NAZWA GRUPY ANSIBLE
# ============================================================

def make_group_name(name):

    safe = re.sub(
        r"[^a-zA-Z0-9_]",
        "_",
        name
    )

    if safe.isdigit():
        return f"vlan_{safe}"

    return f"network_{safe.lower()}"


# ============================================================
# SPRAWDZENIE PORTU SSH
# ============================================================

def check_ssh(ip):

    ip = str(ip)

    try:

        with socket.create_connection(
            (
                ip,
                SSH_PORT
            ),
            timeout=PORT_TIMEOUT
        ):
            return ip

    except (
        socket.timeout,
        ConnectionRefusedError,
        OSError
    ):
        return None


# ============================================================
# SKANOWANIE JEDNEJ SIECI
# ============================================================

def scan_network(network):

    hosts = list(
        network.hosts()
    )

    if len(hosts) > MAX_HOSTS_PER_NETWORK:

        debug(
            f"Sieć {network} zawiera "
            f"{len(hosts)} hostów. Pomijam."
        )

        return []

    found = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                check_ssh,
                ip
            ): ip

            for ip in hosts
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
        key=ipaddress.ip_address
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

        # Wszystkie znalezione urządzenia posiadające SSH.
        #
        # To NIE oznacza jeszcze, że są Raspberry.
        #
        # Raspberry zostanie sprawdzone później przez
        # playbook i credential z Semaphore Key Store.

        "candidates": {
            "hosts": []
        }

    }

    requested_networks = get_requested_networks()

    for network_name, network_cidr in requested_networks:

        debug(
            f"Skanowanie "
            f"{network_name} -> {network_cidr}"
        )

        try:

            network = ipaddress.ip_network(
                network_cidr,
                strict=False
            )

        except ValueError:

            debug(
                f"Błędny CIDR: {network_cidr}"
            )

            continue


        group_name = make_group_name(
            network_name
        )


        inventory[group_name] = {
            "hosts": []
        }


        hosts = scan_network(
            network
        )


        for ip in hosts:

            # --------------------------------------------
            # Grupa konkretnego VLAN-u / sieci
            # --------------------------------------------

            inventory[
                group_name
            ][
                "hosts"
            ].append(ip)


            # --------------------------------------------
            # Globalna grupa kandydatów
            # --------------------------------------------

            if ip not in inventory[
                "candidates"
            ][
                "hosts"
            ]:

                inventory[
                    "candidates"
                ][
                    "hosts"
                ].append(ip)


            # --------------------------------------------
            # Dane hosta
            # --------------------------------------------

            inventory[
                "_meta"
            ][
                "hostvars"
            ][ip] = {

                "ansible_host": ip,

                "network_name":
                    network_name,

                "network_cidr":
                    network_cidr

            }


    inventory[
        "candidates"
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
