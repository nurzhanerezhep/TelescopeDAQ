"""LAN viewing is opt-in; hardware control is always loopback-only."""

from __future__ import annotations

import ipaddress
import socket

import psutil

PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def address(host):
    try:
        value = ipaddress.ip_address(host)
        return getattr(value, "ipv4_mapped", None) or value
    except ValueError:
        return None


def local_client(host):
    # TestClient's synthetic peer is never supplied by a real TCP connection.
    value = address(host)
    return host == "testclient" or (value is not None and value.is_loopback)


def private_client(host):
    value = address(host)
    return value is not None and any(value in network for network in PRIVATE_NETWORKS)


def lan_addresses():
    return sorted(
        {
            entry.address
            for entries in psutil.net_if_addrs().values()
            for entry in entries
            if entry.family == socket.AF_INET and private_client(entry.address)
        }
    )
