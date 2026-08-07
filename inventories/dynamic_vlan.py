#!/usr/bin/env python3

import json
import os
import sys
import socket
import ipaddress
import subprocess
import re

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# KONFIGURACJA Z SEMAPHORE
# ============================================================
#
# NETWORKS:
#
# 25=10.10.25.0/24,26=10.10.26.0/24,PROD238=10.238.40.0/24
#
#
# VLANS:
#
# 25,26
#
# albo:
#
# 25,PROD238
#
# ============================================================

NETWORKS_ENV = os.environ.get(
    "NETWORKS",
    ""
)

VLANS_ENV = os.environ.get(
    "VLANS",
    ""
)


# ============================================================
# DANE SSH DO ROZPOZNAWANIA RASPBERRY
# ============================================================
#
# Semaphore:
#
# RPI_SSH_USER=pi
#
# RPI_SSH_KEY=/home/semaphore/.ssh/raspberry_id
#
# ============================================================

RPI_SSH_USER = os.environ.get(
    "RPI_SSH_USER",
    "pi"
)

RPI_SSH_KEY = os.environ.get(
    "RPI_SSH_KEY",
    "/home/semaphore/.ssh/raspberry_id"
)


# ============================================================
# USTAWIENIA SKANOWANIA
# ============================================================

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

MAX_HOSTS_PER_NETWORK = int(
    os.environ.get(
        "MAX_HOSTS_PER_NETWORK",
        "1024"
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
            f"[dynamic_inventory] {message}",
            file=sys.stderr
        )


# ============================================================
# WCZYTANIE DEFINICJI SIECI Z SEMAPHORE
# ============================================================

def load_networks():

    networks = {}

    items = NETWORKS_ENV.split(",")

    for item in items:

        item = item.strip()

        if not item:
            continue

        if "=" not in item:

            debug(
                f"Pominięto błędną definicję: {item}"
            )

            continue


        name, cidr = item.split(
            "=",
            1
        )


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
                f"Nieprawidłowa sieć: {name}={cidr}"
            )

            continue


        networks[name] = str(
            network
        )


    return networks


# ============================================================
# SIECI WYBRANE DO SKANOWANIA
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
                f"Brak definicji sieci dla: {name}"
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
# BEZPIECZNA NAZWA GRUPY ANSIBLE
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
# SPRAWDZENIE SSH
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
# SPRAWDZENIE CZY HOST TO RASPBERRY PI
# ============================================================

def is_raspberry(ip):

    ip = str(ip)


    # --------------------------------------------------------
    # Najpierw szybki test portu SSH
    # --------------------------------------------------------

    if not ssh_port_open(ip):

        return None


    debug(
        f"SSH wykryte: {ip}"
    )


    # --------------------------------------------------------
    # Klucz SSH musi istnieć wewnątrz kontenera Semaphore
    # --------------------------------------------------------

    if not os.path.isfile(
        RPI_SSH_KEY
    ):

        debug(
            f"Brak klucza SSH: {RPI_SSH_KEY}"
        )

        return None


    # --------------------------------------------------------
    # Komenda SSH
    # --------------------------------------------------------

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
        "LogLevel=ERROR",

        "-o",
        f"ConnectTimeout={SSH_TIMEOUT}",

        "-o",
        "ConnectionAttempts=1",

        # ----------------------------------------------------
        # Starsze urządzenia SSH
        # ----------------------------------------------------

        "-o",
        "KexAlgorithms=+diffie-hellman-group14-sha1",

        "-o",
        "HostKeyAlgorithms=+ssh-rsa",

        "-o",
        "PubkeyAcceptedAlgorithms=+ssh-rsa",

        # ----------------------------------------------------

        f"{RPI_SSH_USER}@{ip}",

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


    # --------------------------------------------------------
    # Nie udało się zalogować
    # --------------------------------------------------------

    if result.returncode != 0:

        debug(
            f"Nie można zalogować SSH: {ip}"
        )

        return None


    model = result.stdout.strip()


    if not model:

        debug(
            f"Brak modelu urządzenia: {ip}"
        )

        return None


    debug(
        f"{ip} -> {model}"
    )


    # --------------------------------------------------------
    # TYLKO RASPBERRY PI
    # --------------------------------------------------------

    if "Raspberry Pi" not in model:

        debug(
            f"{ip} nie jest Raspberry Pi"
        )

        return None


    return {
        "ip": ip,
        "model": model
    }


# ============================================================
# SKANOWANIE SIECI
# ============================================================

def scan_network(network):

    hosts = list(
        network.hosts()
    )


    # --------------------------------------------------------
    # Zabezpieczenie przed przypadkowym skanowaniem np. /16
    # --------------------------------------------------------

    if len(hosts) > MAX_HOSTS_PER_NETWORK:

        debug(
            f"Sieć {network} ma "
            f"{len(hosts)} hostów - pomijam"
        )

        return []


    found = []


    # --------------------------------------------------------
    # Równoległe skanowanie
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        futures = {

            executor.submit(
                is_raspberry,
                ip
            ): ip

            for ip in hosts

        }


        for future in as_completed(
            futures
        ):


            try:

                result = future.result()


                if result is not None:

                    found.append(
                        result
                    )


            except Exception as exception:

                debug(
                    f"Błąd skanowania: {exception}"
                )


    # --------------------------------------------------------
    # Sortowanie IP
    # --------------------------------------------------------

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

        # ----------------------------------------------------
        # Wszystkie znalezione Raspberry
        # ----------------------------------------------------

        "raspberry": {
            "hosts": []
        }

    }


    requested_networks = get_requested_networks()


    # ========================================================
    # KAŻDA WYBRANA SIEĆ
    # ========================================================

    for network_name, network_cidr in requested_networks:


        debug(
            f"Skanuję {network_name}: {network_cidr}"
        )


        try:

            network = ipaddress.ip_network(
                network_cidr,
                strict=False
            )


        except ValueError:

            debug(
                f"Błędna sieć: {network_cidr}"
            )

            continue


        # ----------------------------------------------------
        # Nazwa grupy Ansible
        # ----------------------------------------------------

        group_name = make_group_name(
            network_name
        )


        inventory[group_name] = {
            "hosts": []
        }


        # ----------------------------------------------------
        # Skanowanie
        # ----------------------------------------------------

        raspberry_hosts = scan_network(
            network
        )


        # ----------------------------------------------------
        # Dodanie znalezionych hostów
        # ----------------------------------------------------

        for raspberry in raspberry_hosts:


            ip = raspberry["ip"]

            model = raspberry["model"]


            # ------------------------------------------------
            # grupa VLAN / sieć
            # ------------------------------------------------

            inventory[
                group_name
            ][
                "hosts"
            ].append(ip)


            # ------------------------------------------------
            # globalna grupa Raspberry
            # ------------------------------------------------

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


            # ------------------------------------------------
            # HOSTVARS
            # ------------------------------------------------

            inventory[
                "_meta"
            ][
                "hostvars"
            ][ip] = {

                "ansible_host":
                    ip,

                "raspberry_model":
                    model,

                "network_name":
                    network_name,

                "network_cidr":
                    network_cidr

            }


    # ========================================================
    # SORTOWANIE GLOBALNEJ LISTY
    # ========================================================

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
