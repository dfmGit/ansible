import os
import sys
import json
import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_env(name):
    value = os.environ.get(name)
    if not value:
        print("ERROR: Brak zmiennej środowiskowej: " + name)
        sys.exit(1)
    return value


GLPI_URL = get_env("GLPI_URL").rstrip("/")
USER_TOKEN = get_env("GLPI_USER_TOKEN")
ONLY_PREFIX = os.environ.get("GLPI_ONLY_PREFIX", "rpi-")


def glpi_url(path):
    return GLPI_URL.rstrip("/") + "/apirest.php" + path


def init_session():
    headers = {
        "Authorization": "user_token " + USER_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    url = glpi_url("/initSession")

    response = requests.get(
        url,
        headers=headers,
        timeout=30,
        verify=False
    )

    print("URL:", url)
    print("HTTP STATUS:", response.status_code)
    print("ODPOWIEDZ GLPI:")
    print(response.text)

    if response.status_code >= 400:
        sys.exit(1)

    data = response.json()

    if "session_token" not in data:
        print("ERROR: Brak session_token")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        sys.exit(1)

    return data["session_token"]

def kill_session(session_token):
    headers = {
        "Session-Token": session_token
    }

    try:
        requests.get(
            glpi_url("/killSession"),
            headers=headers,
            timeout=30,
            verify=False
        )
    except Exception:
        pass


def get_all_computers(session_token):
    headers = {
        "Session-Token": session_token
    }

    all_items = []
    start = 0
    step = 100

    while True:
        end = start + step - 1

        response = requests.get(
            glpi_url("/Computer"),
            headers=headers,
            params={"range": str(start) + "-" + str(end)},
            timeout=60,
            verify=False
        )

        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            print("ERROR: GLPI nie zwróciło listy komputerów")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            sys.exit(1)

        if len(data) == 0:
            break

        all_items.extend(data)

        if len(data) < step:
            break

        start += step

    return all_items


def name_to_ip(name):
    if not name.lower().startswith(ONLY_PREFIX.lower()):
        return ""

    raw = name[len(ONLY_PREFIX):]
    ip = raw.replace("-", ".")

    parts = ip.split(".")
    if len(parts) != 4:
        return ""

    for part in parts:
        if not part.isdigit():
            return ""

        number = int(part)
        if number < 0 or number > 255:
            return ""

    return ip


def main():
    session_token = init_session()

    try:
        computers = get_all_computers(session_token)

        result = []

        for computer in computers:
            name = str(computer.get("name", "")).strip()

            if not name.lower().startswith(ONLY_PREFIX.lower()):
                continue

            result.append({
                "id": computer.get("id", ""),
                "name": name,
                "ip": name_to_ip(name),
                "serial": computer.get("serial", ""),
                "otherserial": computer.get("otherserial", "")
            })

        print("Raspberry znalezione w GLPI:")
        print("--------------------------------")

        for item in result:
            print("ID: " + str(item["id"]))
            print("NAZWA: " + item["name"])
            print("IP: " + item["ip"])
            print("SERIAL: " + str(item["serial"]))
            print("OTHER_SERIAL: " + str(item["otherserial"]))
            print("--------------------------------")

        print("Razem Raspberry: " + str(len(result)))

        print("JSON:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    finally:
        kill_session(session_token)


if __name__ == "__main__":
    main()
