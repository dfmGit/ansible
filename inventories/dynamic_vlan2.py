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
# RPI_NETWORK_CONFIG
#
# Przykład:
#
# {
#   "ZAKLADY": {
#     "DM": "10.10",
#     "RE": "10.238"
#   },
#   "WYDZIALY": {
#     "SZWALNIA": ["20", "21"],
#     "MONTOWNIA": ["24", "25"],
#     "KJ": ["26"],
#     "LOGISTYKA": ["27"]
#   }
# }
#

NETWORK_CONFIG_ENV = os.environ.get(
    "RPI_NETWORK_CONFIG",
    ""
)


# ============================================================
# SSH
# ============================================================

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


# ============================================================
# PARAMETRY SKANOWANIA
# ============================================================

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
            f"[dynamic_inventory] {message}",
            file=sys.stderr
        )


# ============================================================
# NAZWA GRUPY ANSIBLE
# ============================================================

def safe_name(value):

    value = str(value).strip().lower()

    value = re.sub(
        r"[^a-zA-Z0-9_]",
        "_",
        value
    )

    return value


# ============================================================
# ODCZYT JSON
# ============================================================

def load_network_config():

    if not NETWORK_CONFIG_ENV:

        debug(
            "Brak zmiennej RPI_NETWORK_CONFIG"
        )

        return None

    try:

        config = json.loads(
            NETWORK_CONFIG_ENV
        )

    except json.JSONDecodeError as exception:

        debug(
            f"Błędny JSON RPI_NETWORK_CONFIG: "
            f"{exception}"
        )

        return None


    if not isinstance(config, dict):

        debug(
            "RPI_NETWORK_CONFIG nie jest obiektem JSON"
        )

        return None


    if "ZAKLADY" not in config:

        debug(
            "Brak ZAKLADY w RPI_NETWORK_CONFIG"
        )

        return None


    if "WYDZIALY" not in config:

        debug(
            "Brak WYDZIALY w RPI_NETWORK_CONFIG"
        )

        return None


    return config


# ============================================================
# BUDOWANIE LISTY SIECI
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

    seen = set()


    for zaklad, prefix in zaklady.items():

        prefix = str(prefix).strip()


        for wydzial, vlans in wydzialy.items():

            if not isinstance(vlans, list):

                debug(
                    f"WYDZIAL {wydzial}: "
                    f"lista VLAN nie jest tablicą"
                )

                continue


            for vlan in vlans:

                vlan = str(vlan).strip()


                # =================================================
                # Wszystkie sieci są /24
                #
                # DM + VLAN 24:
                # 10.10.24.0/24
                #
                # RE + VLAN 24:
                # 10.238.24.0/24
                # =================================================

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


                # ochrona przed przypadkowym
                # podwójnym wpisaniem tej samej sieci
                unique_key = (
                    str(network),
                    str(zaklad),
                    str(wydzial)
                )

                if unique_key in seen:
                    continue

                seen.add(
                    unique_key
                )


                networks.append({
                    "zaklad": str(zaklad),
                    "wydzial": str(wydzial),
                    "vlan": vlan,
                    "network": network
                })


    return networks


# ============================================================
# TEST PORTU 22
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
# POJEDYNCZE LOGOWANIE SSH
# ============================================================

def ssh_command(
    ip,
    password,
    remote_command
):

    env = os.environ.copy()

    #
    # sshpass -e pobiera hasło ze zmiennej SSHPASS
    #
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
            f"Timeout SSH: {ip}"
        )

        return None


    except FileNotFoundError:

        debug(
            "Brak programu sshpass w kontenerze"
        )

        return None


    except Exception as exception:

        debug(
            f"Błąd SSH {ip}: {exception}"
        )

        return None


# ============================================================
# SPRAWDZENIE RASPBERRY PI
# ============================================================

def check_raspberry(ip):

    ip = str(ip)


    # ========================================================
    # Najpierw szybki test TCP 22
    # ========================================================

    if not ssh_port_open(ip):
        return None


    debug(
        f"SSH dostępne: {ip}"
    )


    # ========================================================
    # Polecenie wykonywane na urządzeniu
    # ========================================================

    remote_command = (
        "MODEL=$(tr -d '\\0' "
        "</proc/device-tree/model 2>/dev/null); "

        "SERIAL=$(tr -d '\\0' "
        "</proc/device-tree/serial-number 2>/dev/null); "

        "HOSTNAME=$(hostname 2>/dev/null); "

        "printf '%s|%s|%s\\n' "
        "\"$MODEL\" "
        "\"$SERIAL\" "
        "\"$HOSTNAME\""
    )


    # ========================================================
    # LISTA HASEŁ
    # ========================================================

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
        and
        SSH_PASSWORD_ALT != SSH_PASSWORD
    ):

        passwords.append(
            (
                "ALTERNATIVE",
                SSH_PASSWORD_ALT
            )
        )


    if not passwords:

        debug(
            "Brak RPI_SSH_PASSWORD "
            "oraz RPI_SSH_PASSWORD_ALT"
        )

        return None


    result = None

    used_password = None
    used_password_name = None


    # ========================================================
    # PRÓBA LOGOWANIA
    # ========================================================

    for password_name, password in passwords:


        debug(
            f"{ip}: próba logowania "
            f"{password_name}"
        )


        current_result = ssh_command(
            ip,
            password,
            remote_command
        )


        if current_result is None:
            continue


        if current_result.returncode == 0:

            result = current_result

            used_password = password

            used_password_name = password_name


            debug(
                f"{ip}: logowanie OK "
                f"({password_name})"
            )

            break


        debug(
            f"{ip}: logowanie nieudane "
            f"({password_name})"
        )


    # ========================================================
    # ŻADNE HASŁO NIE DZIAŁA
    # ========================================================

    if result is None:

        debug(
            f"{ip}: żadne hasło SSH nie działa"
        )

        return None


    # ========================================================
    # ODCZYT ODPOWIEDZI
    # ========================================================

    output = result.stdout.strip()


    if not output:

        debug(
            f"{ip}: brak odpowiedzi"
        )

        return None


    parts = output.split(
        "|",
        2
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


    # ========================================================
    # TYLKO RASPBERRY PI
    # ========================================================

    if "Raspberry Pi" not in model:

        debug(
            f"{ip}: urządzenie nie jest Raspberry Pi "
            f"(model='{model}')"
        )

        return None


    debug(
        f"Raspberry Pi znalezione: "
        f"{ip} | "
        f"{model} | "
        f"serial={serial} | "
        f"hostname={hostname} | "
        f"auth={used_password_name}"
    )


    return {
        "ip": ip,
        "model": model,
        "serial": serial,
        "hostname": hostname,

        # hasło nie jest wyświetlane w DEBUG
        "password": used_password,

        "auth": used_password_name
    }


# ============================================================
# SKANOWANIE JEDNEJ SIECI
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


    found.sort(
        key=lambda item:
        ipaddress.ip_address(
            item["ip"]
        )
    )


    return found


# ============================================================
# DODAWANIE HOSTA DO GRUPY
# ============================================================

def add_host_to_group(
    inventory,
    group_name,
    host
):

    group_name = safe_name(
        group_name
    )


    if not group_name:
        return


    if group_name not in inventory:

        inventory[group_name] = {
            "hosts": []
        }


    if host not in inventory[
        group_name
    ][
        "hosts"
    ]:

        inventory[
            group_name
        ][
            "hosts"
        ].append(
            host
        )


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


    # ========================================================
    # KONFIGURACJA
    # ========================================================

    config = load_network_config()


    if not config:
        return inventory


    if not SSH_PASSWORD and not SSH_PASSWORD_ALT:

        debug(
            "Brak haseł SSH"
        )

        return inventory


    networks = build_network_list(
        config
    )


    debug(
        f"Liczba sieci do skanowania: "
        f"{len(networks)}"
    )

    debug(
        f"Użytkownik SSH: "
        f"{SSH_USER}"
    )


    # ========================================================
    # SKANOWANIE
    # ========================================================

    for network_data in networks:


        zaklad = network_data[
            "zaklad"
        ]

        wydzial = network_data[
            "wydzial"
        ]

        vlan = network_data[
            "vlan"
        ]

        network = network_data[
            "network"
        ]


        debug(
            f"Skanowanie: "
            f"{zaklad} / "
            f"{wydzial} / "
            f"VLAN {vlan} "
            f"-> {network}"
        )


        hosts = scan_network(
            network
        )


        debug(
            f"{network}: znaleziono "
            f"{len(hosts)} Raspberry Pi"
        )


        # ====================================================
        # HOSTY
        # ====================================================

        for host_data in hosts:


            ip = host_data[
                "ip"
            ]

            model = host_data[
                "model"
            ]

            serial = host_data[
                "serial"
            ]

            hostname = host_data[
                "hostname"
            ]

            password = host_data[
                "password"
            ]

            auth = host_data[
                "auth"
            ]


            # =================================================
            # NA RAZIE INVENTORY HOST = IP
            # =================================================

            inventory_host = ip


            # =================================================
            # HOSTVARS
            # =================================================

            inventory[
                "_meta"
            ][
                "hostvars"
            ][
                inventory_host
            ] = {

                # aktualny adres
                "ansible_host":
                    ip,

                # użytkownik SSH
                "ansible_user":
                    SSH_USER,

                # =================================================
                # WAŻNE:
                #
                # przekazujemy Ansible to hasło,
                # które faktycznie zadziałało
                # =================================================

                "ansible_password":
                    password,

                # dane Raspberry
                "rpi_serial":
                    serial,

                "rpi_hostname":
                    hostname,

                "raspberry_model":
                    model,

                # organizacja
                "zaklad":
                    zaklad,

                "wydzial":
                    wydzial,

                "vlan":
                    vlan,

                "network_cidr":
                    str(network),

                # tylko informacja PRIMARY / ALTERNATIVE
                # bez wartości hasła
                "rpi_ssh_auth":
                    auth
            }


            # =================================================
            # GRUPA: raspberry
            # =================================================

            add_host_to_group(
                inventory,
                "raspberry",
                inventory_host
            )


            # =================================================
            # GRUPA ZAKŁADU
            #
            # dm
            # re
            # =================================================

            add_host_to_group(
                inventory,
                zaklad,
                inventory_host
            )


            # =================================================
            # GRUPA WYDZIAŁU
            #
            # szwalnia
            # montownia
            # kj
            # logistyka
            # =================================================

            add_host_to_group(
                inventory,
                wydzial,
                inventory_host
            )


            # =================================================
            # ZAKŁAD + WYDZIAŁ
            #
            # dm_szwalnia
            # dm_montownia
            # re_szwalnia
            # =================================================

            add_host_to_group(
                inventory,
                f"{zaklad}_{wydzial}",
                inventory_host
            )


            # =================================================
            # VLAN
            #
            # vlan_20
            # vlan_24
            # vlan_25
            # =================================================

            add_host_to_group(
                inventory,
                f"vlan_{vlan}",
                inventory_host
            )


            # =================================================
            # ZAKŁAD + VLAN
            #
            # dm_vlan_24
            # re_vlan_24
            # =================================================

            add_host_to_group(
                inventory,
                f"{zaklad}_vlan_{vlan}",
                inventory_host
            )


    # ========================================================
    # SORTOWANIE
    # ========================================================

    for group_name, group_data in inventory.items():


        if group_name == "_meta":
            continue


        if "hosts" not in group_data:
            continue


        try:

            group_data[
                "hosts"
            ].sort(
                key=ipaddress.ip_address
            )

        except ValueError:

            group_data[
                "hosts"
            ].sort()


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
