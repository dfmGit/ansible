#!/usr/bin/env python3

import json
import os
import sys
import socket
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed


# VLAN-y podawane z Semaphore:
# np. VLANS=55,56,60
VLANS = os.environ.get("VLANS", "55")

# Jeżeli VLAN 55 = 192.168.55.0/24
NETWORK_TEMPLATE = "192.168.{}.0/24"

# Sprawdzamy czy działa SSH
CHECK_PORT = 22

# timeout pojedynczego połączenia
TIMEOUT = 0.3


def check_host(ip):
    try:
        with socket.create_connection(
            (str(ip), CHECK_PORT),
            timeout=TIMEOUT
        ):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def scan_network(network):
    found = []

    addresses = list(network.hosts())

    with ThreadPoolExecutor(max_workers=100) as executor:

        futures = {
            executor.submit(check_host, ip): ip
            for ip in addresses
        }

        for future in as_completed(futures):

            ip = futures[future]

            try:
                if future.result():
                    found.append(str(ip))
            except Exception:
                pass

    return found


def build_inventory():

    inventory = {
        "_meta": {
            "hostvars": {}
        },
        "all": {
            "children": []
        }
    }

    vlan_list = [
        x.strip()
        for x in VLANS.split(",")
        if x.strip()
    ]

    for vlan in vlan_list:

        network_string = NETWORK_TEMPLATE.format(vlan)

        network = ipaddress.ip_network(
            network_string,
            strict=False
        )

        group_name = f"vlan_{vlan}"

        inventory[group_name] = {
            "hosts": []
        }

        inventory["all"]["children"].append(group_name)

        hosts = scan_network(network)

        for ip in hosts:

            inventory[group_name]["hosts"].append(ip)

            inventory["_meta"]["hostvars"][ip] = {
                "ansible_host": ip
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
