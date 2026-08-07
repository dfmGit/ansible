#!/usr/bin/env python3

import json
import os
import sys
import socket
import ipaddress
import subprocess
import shutil

from concurrent.futures import ThreadPoolExecutor, as_completed


# VLAN-y, np.:
# VLANS=50
# VLANS=50,55,60
VLANS = os.environ.get("VLANS", "50")

# VLAN 50 -> 10.10.50.0/24
NETWORK_TEMPLATE = "10.10.{}.0/24"

# Porty używane dodatkowo do wykrywania aktywnych urządzeń
CHECK_PORTS = [
    22,     # SSH
    445,    # SMB / Windows
    3389,   # RDP
    5985,   # WinRM HTTP
    5986,   # WinRM HTTPS
]

TCP_TIMEOUT = 0.20
PING_TIMEOUT = 1
MAX_WORKERS = 100


def ping_host(ip):
    """
    Sprawdza ICMP ping.
    Jeśli polecenia ping nie ma w kontenerze, zwraca False.
    """

    if shutil.which("ping") is None:
        return False

    try:
        result = subprocess.run(
            [
                "ping",
                "-c", "1",
                "-W", str(PING_TIMEOUT),
                str(ip)
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PING_TIMEOUT + 1
        )

        return result.returncode == 0

    except Exception:
        return False


def check_port(ip, port):
    try:
        with socket.create_connection(
            (str(ip), port),
            timeout=TCP_TIMEOUT
        ):
            return True

    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_host(ip):
    ip = str(ip)

    # Najpierw ping
    ping_ok = ping_host(ip)

    open_ports = []

    # Sprawdzamy typowe porty
    for port in CHECK_PORTS:
        if check_port(ip, port):
            open_ports.append(port)

    active = ping_ok or len(open_ports) > 0

    if not active:
        return None

    # Orientacyjne rozpoznanie typu hosta
    host_type = "unknown"

    if 5985 in open_ports or 5986 in open_ports:
        host_type = "windows"

    elif 22 in open_ports:
        host_type = "linux"

    elif 445 in open_ports or 3389 in open_ports:
        host_type = "windows"

    return {
        "ip": ip,
        "ping": ping_ok,
        "ports": open_ports,
        "type": host_type
    }


def scan_network(network):
    found = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        futures = {
            executor.submit(check_host, ip): ip
            for ip in network.hosts()
        }

        for future in as_completed(futures):

            try:
                result = future.result()

                if result:
                    found.append(result)

            except Exception:
                pass

    return sorted(
        found,
        key=lambda x: ipaddress.ip_address(x["ip"])
    )


def build_inventory():

    inventory = {
        "_meta": {
            "hostvars": {}
        },

        "active": {
            "hosts": []
        },

        "linux": {
            "hosts": []
        },

        "windows": {
            "hosts": []
        },

        "unknown": {
            "hosts": []
        }
    }

    vlan_list = [
        vlan.strip()
        for vlan in VLANS.split(",")
        if vlan.strip()
    ]

    for vlan in vlan_list:

        network = ipaddress.ip_network(
            NETWORK_TEMPLATE.format(vlan),
            strict=False
        )

        vlan_group = f"vlan_{vlan}"

        inventory[vlan_group] = {
            "hosts": []
        }

        hosts = scan_network(network)

        for host in hosts:

            ip = host["ip"]
            host_type = host["type"]

            inventory[vlan_group]["hosts"].append(ip)
            inventory["active"]["hosts"].append(ip)

            inventory[host_type]["hosts"].append(ip)

            inventory["_meta"]["hostvars"][ip] = {
                "ansible_host": ip,
                "detected_type": host_type,
                "ping_available": host["ping"],
                "detected_ports": host["ports"],
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
