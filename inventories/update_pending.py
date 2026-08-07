#!/usr/bin/env python3

import json
import os
import socket
import subprocess
import sys
from datetime import datetime


BASE_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        ".."
    )
)

JOBS_FILE = os.environ.get(
    "RPI_UPDATE_JOB",
    "/data/rpi_inventory/jobs/update_system.json"
)

INVENTORY = os.path.join(
    BASE_DIR,
    "inventories",
    "dynamic_vlan2.py"
)

PLAYBOOK = os.path.join(
    BASE_DIR,
    "playbooks",
    "update_system.yml"
)

SSH_TIMEOUT = 2


def now():
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def port_open(ip):

    try:
        with socket.create_connection(
            (ip, 22),
            timeout=SSH_TIMEOUT
        ):
            return True

    except OSError:
        return False


def load_job():

    if not os.path.isfile(JOBS_FILE):
        return None

    with open(
        JOBS_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def save_job(job):

    os.makedirs(
        os.path.dirname(JOBS_FILE),
        exist_ok=True
    )

    with open(
        JOBS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            job,
            file,
            indent=2,
            ensure_ascii=False
        )


def run_update(inventory_host):

    command = [
        "ansible-playbook",
        "-i",
        INVENTORY,
        PLAYBOOK,
        "--limit",
        inventory_host
    ]

    print(
        f"Uruchamiam aktualizację: {inventory_host}",
        flush=True
    )

    return subprocess.run(
        command,
        cwd=BASE_DIR
    ).returncode


def main():

    job = load_job()

    if not job:

        print(
            "Brak aktywnego zadania UPDATE_SYSTEM."
        )
        return 0


    hosts = job.get(
        "hosts",
        {}
    )


    pending = [
        (host_id, data)
        for host_id, data in hosts.items()
        if data.get("status") == "PENDING"
    ]


    print(
        f"PENDING: {len(pending)}"
    )


    if not pending:

        print(
            "Brak hostów oczekujących."
        )
        return 0


    for host_id, data in pending:

        ip = data.get(
            "ip",
            ""
        )

        inventory_host = (
            "rpi_" + host_id.lower()
        )


        if not ip:

            print(
                f"{inventory_host}: brak IP"
            )
            continue


        # ----------------------------------------------
        # Szybko sprawdzamy tylko TCP/22
        # ----------------------------------------------

        if not port_open(ip):

            print(
                f"{inventory_host} ({ip}) nadal OFFLINE"
            )

            data[
                "last_try"
            ] = now()

            data[
                "attempts"
            ] = data.get(
                "attempts",
                0
            ) + 1

            continue


        print(
            f"{inventory_host} ({ip}) ONLINE"
        )


        data[
            "status"
        ] = "RUNNING"

        data[
            "last_try"
        ] = now()

        data[
            "attempts"
        ] = data.get(
            "attempts",
            0
        ) + 1

        save_job(job)


        result = run_update(
            inventory_host
        )


        if result == 0:

            data[
                "status"
            ] = "DONE"

            data[
                "completed"
            ] = now()

            print(
                f"{inventory_host}: DONE"
            )

        else:

            #
            # Host odpowiadał, ale playbook się nie udał.
            # Nie traktujemy tego jak OFFLINE.
            #
            data[
                "status"
            ] = "ERROR"

            data[
                "error_time"
            ] = now()

            print(
                f"{inventory_host}: ERROR"
            )


        save_job(job)


    # ----------------------------------------------
    # PODSUMOWANIE
    # ----------------------------------------------

    counts = {
        "PENDING": 0,
        "RUNNING": 0,
        "DONE": 0,
        "ERROR": 0
    }


    for data in hosts.values():

        status = data.get(
            "status",
            "PENDING"
        )

        if status in counts:
            counts[status] += 1


    print()
    print(
        "==============================="
    )
    print(
        f"DONE:    {counts['DONE']}"
    )
    print(
        f"PENDING: {counts['PENDING']}"
    )
    print(
        f"ERROR:   {counts['ERROR']}"
    )
    print(
        "==============================="
    )


    return 0


if __name__ == "__main__":
    sys.exit(main())
