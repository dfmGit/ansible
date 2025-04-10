#!/usr/bin/env python3
import json
import subprocess
from collections import defaultdict

# Funkcja sprawdzająca, czy host odpowiada na ping
def ping(host):
    try:
        # Pingowanie hosta (timeout 1 sekunda, 1 pakiet)
        response = subprocess.run(['ping', '-c', '1', '-W', '1', host], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return response.returncode == 0
    except Exception as e:
        return False

# Generator hostów z danego prefixu i listy podsieci
def generuj_hosty(prefix):
    for subnet in [24, 25, 23, 21, 67]:
        for i in range(1, 255):
            yield f"{prefix}.{subnet}.{i}"

# Prefiksy do wygenerowania
prefixy = ["10.10","10.238","10.237"]

# Inicjalizacja grup jako defaultdict z listami
grupy = defaultdict(list)

# Przetwarzamy hosty na bieżąco
for prefix in prefixy:
    for host in generuj_hosty(prefix):
        try:
            subnet = int(host.split('.')[-2])
        except ValueError:
            continue

        # Sprawdzanie, czy host odpowiada na ping
        if ping(host):
            # Grupy VLAN
            if subnet in [24, 25]:
                grupy["Montownia"].append(host)
            elif subnet == 21:
                grupy["Szwalnia"].append(host)
            elif subnet == 22:
                grupy["Pianka"].append(host)
            elif subnet == 67:
                grupy["Metal"].append(host)

            # Grupy nadrzędne
            if host.startswith("10.10."):
                grupy["DM"].append(host)
            elif host.startswith("10.238.") or host.startswith("10.239."):
                grupy["RE"].append(host)
            elif host.startswith("10.237.") or host.startswith("10.237."):
                grupy["OL"].append(host)

# Budowanie inwentarza
inventory = {
    "all": {
        "children": ["Montownia", "DM", "RE", "Szwalnia", "Pianka", "Metal"]
    },
    "Montownia": {"hosts": grupy["Montownia"]},
    "Szwalnia":  {"hosts": grupy["Szwalnia"]},
    "Pianka":    {"hosts": grupy["Pianka"]},
    "Metal":     {"hosts": grupy["Metal"]},
    "DM":        {"hosts": grupy["DM"]},
    "RE":        {"hosts": grupy["RE"]},
    "OL":        {"hosts": grupy["OL"]},
}

# Wydruk JSON
print(json.dumps(inventory, indent=4))
