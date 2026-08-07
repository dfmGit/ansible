#!/usr/bin/env python3

import json
import os
import sys
import re


HOSTS_FILE = os.environ.get(
    "RPI_HOSTS_FILE",
    "/data/rpi_inventory/hosts.json"
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


def safe_name(value):

    value = str(
        value
    ).strip().lower()

    return re.sub(
        r"[^a-zA-Z0-9_]",
        "_",
        value
    )


def add_host(
    inventory,
    group,
    host
):

    group = safe_name(
        group
    )

    if not group:
        return

    if group not in inventory:

        inventory[group] = {
            "hosts": []
        }

    if host not in inventory[
        group
    ]["hosts"]:

        inventory[
            group
        ]["hosts"].append(
            host
        )


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

            return json.load(
                file
            )

    except Exception:

        return {}


def build_inventory():

    inventory = {

        "_meta": {
            "hostvars": {}
        },

        "raspberry": {
            "hosts": []
        },

        "rpi_online": {
            "hosts": []
        },

        "rpi_offline": {
            "hosts": []
        }
    }


    hosts = load_hosts()


    for device_id, device in hosts.items():

        ip = device.get(
            "ip",
            ""
        )

        if not ip:
            continue


        # ====================================================
        # STAŁA NAZWA HOSTA
        # ====================================================

        inventory_host = (
            "rpi_"
            + safe_name(
                device_id
            )
        )


        auth = device.get(
            "auth",
            "PRIMARY"
        )


        if auth == "ALTERNATIVE":

            password = (
                SSH_PASSWORD_ALT
                or SSH_PASSWORD
            )

        else:

            password = (
                SSH_PASSWORD
                or SSH_PASSWORD_ALT
            )


        hostvars = {

            "ansible_host":
                ip,

            "ansible_user":
                SSH_USER,

            "rpi_serial":
                device.get(
                    "serial",
                    ""
                ),

            "rpi_machine_id":
                device.get(
                    "machine_id",
                    ""
                ),

            "rpi_hostname":
                device.get(
                    "hostname",
                    ""
                ),

            "raspberry_model":
                device.get(
                    "model",
                    ""
                ),

            "zaklad":
                device.get(
                    "zaklad",
                    ""
                ),

            "wydzial":
                device.get(
                    "wydzial",
                    ""
                ),

            "vlan":
                device.get(
                    "vlan",
                    ""
                ),

            "network_cidr":
                device.get(
                    "network_cidr",
                    ""
                ),

            "rpi_online":
                device.get(
                    "online",
                    False
                ),

            "rpi_last_seen":
                device.get(
                    "last_seen",
                    ""
                ),

            "rpi_ssh_auth":
                auth
        }


        # hasło istnieje tylko w runtime,
        # NIE jest zapisane w hosts.json
        if password:

            hostvars[
                "ansible_password"
            ] = password


        inventory[
            "_meta"
        ][
            "hostvars"
        ][
            inventory_host
        ] = hostvars


        # ====================================================
        # WSZYSTKIE RPI
        # ====================================================

        add_host(
            inventory,
            "raspberry",
            inventory_host
        )


        # ====================================================
        # ONLINE / OFFLINE
        # ====================================================

        if device.get(
            "online",
            False
        ):

            add_host(
                inventory,
                "rpi_online",
                inventory_host
            )

        else:

            add_host(
                inventory,
                "rpi_offline",
                inventory_host
            )


        zaklad = device.get(
            "zaklad",
            ""
        )

        wydzial = device.get(
            "wydzial",
            ""
        )

        vlan = device.get(
            "vlan",
            ""
        )


        # ====================================================
        # DM / RE
        # ====================================================

        if zaklad:

            add_host(
                inventory,
                zaklad,
                inventory_host
            )


        # ====================================================
        # MONTOWNIA / SZWALNIA...
        # ====================================================

        if wydzial:

            add_host(
                inventory,
                wydzial,
                inventory_host
            )


        # ====================================================
        # DM_MONTOWNIA
        # ====================================================

        if zaklad and wydzial:

            add_host(
                inventory,
                f"{zaklad}_{wydzial}",
                inventory_host
            )


        # ====================================================
        # VLAN
        # ====================================================

        if vlan:

            add_host(
                inventory,
                f"vlan_{vlan}",
                inventory_host
            )


        # ====================================================
        # DM_VLAN_24
        # ====================================================

        if zaklad and vlan:

            add_host(
                inventory,
                f"{zaklad}_vlan_{vlan}",
                inventory_host
            )


    for group, data in inventory.items():

        if group == "_meta":
            continue

        if "hosts" in data:
            data["hosts"].sort()


    return inventory


if __name__ == "__main__":

    if "--host" in sys.argv:

        print("{}")

    else:

        print(
            json.dumps(
                build_inventory(),
                indent=2,
                ensure_ascii=False
            )
        )
