#!/usr/bin/env python3
import ipaddress

# Ustawienie limitu hostów do wygenerowania dla danego zakresu (dla celów demonstracyjnych)
host_limit = 10

# Definicja grup i odpowiadających im zakresów IP
# Dla zakresu 10.239.* używamy podsieci /16, dla innych /24
groups = {
    "szwalnia_dm": [ipaddress.ip_network("10.10.21.0/24")],
    "szwalnia_re": [ipaddress.ip_network("10.238.21.0/24")],
    "szwalnia_ol": [ipaddress.ip_network("10.239.0.0/16")],
    "montownia": [
        ipaddress.ip_network("10.10.24.0/24"),
        ipaddress.ip_network("10.10.25.0/24")
    ]
}

def generate_hosts(network, limit):
    """
    Generuje listę hostów z danego zakresu sieci, pomijając adres sieciowy i broadcast.
    Zwraca nie więcej niż 'limit' hostów.
    """
    hosts = list(network.hosts())
    return hosts[:limit]

# Generowanie pliku inwentaryzacji w formacie INI
inventory_lines = []

for group, networks in groups.items():
    inventory_lines.append(f"[{group}]")
    for network in networks:
        hosts = generate_hosts(network, host_limit)
        for host in hosts:
            inventory_lines.append(str(host))
    inventory_lines.append("")  # pusta linia oddzielająca grupy

# Zapis do pliku lub wyświetlenie na ekranie
inventory_text = "\n".join(inventory_lines)
print(inventory_text)

# Jeśli chcesz zapisać wynik do pliku, odkomentuj poniższe linie:
# with open("hosts.ini", "w") as f:
#     f.write(inventory_text)
