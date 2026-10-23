#!/usr/bin/env python3
"""Single fixed SSH relay: RUS SSH through the fleet monitor's existing route."""
import os
import socket
import threading


def main():
    connection = socket.create_connection(("93.77.168.140", 49322), 10)
    connection.settimeout(90)
    def upload():
        try:
            while True:
                data = os.read(0, 65536)
                if not data:
                    break
                connection.sendall(data)
        finally:
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    threading.Thread(target=upload, daemon=True).start()
    while True:
        data = connection.recv(65536)
        if not data:
            break
        os.write(1, data)


if __name__ == "__main__":
    main()
