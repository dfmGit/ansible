#!/usr/bin/env python3
import json

# Funkcja generująca hosty dla danej puli (np. 10.10, 10.238, 10.239)
def generuj_hosty(prefix):
    # Przyjmujemy podsieci 24 i 25
    return [f"{prefix}.24.{i}" for i in range(1, 255)] + [f"{prefix}.25.{i}" for i in range(1, 255)]

# Generowanie hostów dla poszczególnych pul
hosts_10_10   = generuj_hosty("10.10")
hosts_10_238  = generuj_hosty("10.238")
hosts_10_239  = generuj_hosty("10.239")

# Łączymy wszystkie hosty
all_hosts = hosts_10_10 + hosts_10_238 + hosts_10_239

# Inicjalizacja grup VLAN oraz nadrzędnych grup DM/RE
group_montownia = []
group_szwalnia = []
group_pianka   = []
group_metal    = []
group_dm       = []
group_re       = []

for host in all_hosts:
    # Pobranie ostatniego oktetu
    try:
        last_octet = int(host.split('.')[-1])
    except ValueError:
        continue
    if last_octet == 24:
        group_montownia.append(host)
    if last_octet == 25:
        group_montownia.append(host)    
    if last_octet == 21:
        group_szwalnia.append(host)
    if last_octet == 22:
        group_pianka.append(host)
    if last_octet == 67:
        group_metal.append(host)
    
    # Przypisanie do nadrzędnych grup
    if host.startswith("10.10."):
        group_dm.append(host)
    elif host.startswith("10.238.") or host.startswith("10.239."):
        group_re.append(host)

# Budowanie inwentarza
inventory = {
    "all": {
        "children": ["Montownia", "DM", "RE", "Szwalnia", "Pianka", "Metal"],
        "vars": {
            "ansible_user": "your_user",
            "ansible_ssh_private_key_file": "~/.ssh/id_rsa"
        }
    },
    "Montownia": {
        "hosts": group_montownia
    },
    "Szwalnia": {
        "hosts": group_szwalnia
    },
    "Pianka": {
        "hosts": group_pianka
    },
    "Metal": {
        "hosts": group_metal
    },
    "DM": {
        "hosts": group_dm
    },
    "RE": {
        "hosts": group_re
    }
}

# Wydrukowanie inwentarza w formacie JSON
print(json.dumps(inventory, indent=4))
