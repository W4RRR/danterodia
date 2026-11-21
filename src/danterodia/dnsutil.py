"""Consultas DNS asíncronas (dnspython). Sustituye a ``pydns``/``py3DNS`` del original."""

from __future__ import annotations

import logging

import dns.asyncresolver
import dns.exception
import dns.resolver
import dns.reversename

log = logging.getLogger("danterodia.dns")


class DnsResolver:
    """Envoltorio fino: devuelve listas de texto y ``[]`` si el nombre o el registro no existen."""

    def __init__(self, timeout: float = 5.0, nameservers: list[str] | None = None) -> None:
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.lifetime = timeout
        if nameservers:
            self._resolver.nameservers = nameservers

    async def query(self, name: str, rdtype: str) -> list[str]:
        try:
            answer = await self._resolver.resolve(name, rdtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return []
        except dns.exception.Timeout:
            log.debug("Timeout DNS %s %s", rdtype, name)
            return []
        result = []
        for rr in answer:
            if rdtype == "TXT":
                result.append(b"".join(rr.strings).decode("utf-8", errors="replace"))
            elif rdtype == "MX":
                result.append(f"{rr.preference} {rr.exchange.to_text().rstrip('.')}")
            else:
                result.append(rr.to_text().rstrip("."))
        return result

    async def reverse(self, ip: str) -> list[str]:
        return await self.query(dns.reversename.from_address(ip).to_text(), "PTR")
