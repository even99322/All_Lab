"""Local IPv4 networks (for the same-subnet rule and Host availability)."""

from __future__ import annotations

import ipaddress


def local_networks(include_loopback: bool = False) -> list[ipaddress.IPv4Network]:
    from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface

    networks = []
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        if not flags & QNetworkInterface.InterfaceFlag.IsUp or not flags & QNetworkInterface.InterfaceFlag.IsRunning:
            continue
        loopback = bool(flags & QNetworkInterface.InterfaceFlag.IsLoopBack)
        if loopback and not include_loopback:
            continue
        for entry in interface.addressEntries():
            ip = entry.ip()
            if ip.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            try:
                networks.append(ipaddress.IPv4Network(f"{ip.toString()}/{entry.netmask().toString()}", strict=False))
            except ValueError:
                continue
    return networks


def same_subnet(peer: str, include_loopback: bool = False) -> bool:
    try:
        address = ipaddress.IPv4Address(peer)
    except ValueError:
        return False
    if address.is_loopback:
        return include_loopback
    return any(address in network for network in local_networks(include_loopback))


def has_lan(include_loopback: bool = False) -> bool:
    return bool(local_networks(include_loopback))
