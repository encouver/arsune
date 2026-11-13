import socket


def free_port() -> int:
    """Ask the OS for an unused local port, so parallel test runs do not collide."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
