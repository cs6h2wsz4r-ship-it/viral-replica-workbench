from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import tempfile
from typing import Mapping, Sequence


DEFAULT_HOST_IPS: dict[str, tuple[str, ...]] = {
    "api.viraltok.ai": ("104.21.80.217", "172.67.187.70"),
    "cdn.viraltok.ai": ("104.21.80.217", "172.67.187.70"),
}


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _sitecustomize_source(host_ips: Mapping[str, Sequence[str]]) -> str:
    normalized = {
        str(host).rstrip(".").lower(): tuple(str(ip) for ip in ips)
        for host, ips in sorted(host_ips.items())
    }
    return f'''"""Per-run DNS shim for Jimmy video generation tools.

Some local networks return 198.18.x.x fake-IP records for Jimmy API or CDN
hosts. This run-local shim supplies known public A records without changing
system DNS.
"""

from __future__ import annotations

import socket


_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_HOST_IPS = {normalized!r}


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    normalized_host = str(host).rstrip(".").lower()
    ips = _HOST_IPS.get(normalized_host)
    if ips:
        socktype = type or socket.SOCK_STREAM
        protocol = proto or socket.IPPROTO_TCP
        return [
            (socket.AF_INET, socktype, protocol, "", (ip, port))
            for ip in ips
        ]
    return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)


socket.getaddrinfo = _patched_getaddrinfo
'''


def _normalized_host_ips(
    host_ips: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    return {
        str(host).rstrip(".").lower(): tuple(str(ip) for ip in ips)
        for host, ips in sorted((host_ips or DEFAULT_HOST_IPS).items())
    }


def install_runtime_dns_patch(
    host_ips: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Install the same Jimmy host DNS shim in the current Python process."""
    if getattr(socket.getaddrinfo, "_jimmy_runtime_dns_patch", False):
        return

    original_getaddrinfo = socket.getaddrinfo
    normalized = _normalized_host_ips(host_ips)

    def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        normalized_host = str(host).rstrip(".").lower()
        ips = normalized.get(normalized_host)
        if ips:
            socktype = type or socket.SOCK_STREAM
            protocol = proto or socket.IPPROTO_TCP
            return [
                (socket.AF_INET, socktype, protocol, "", (ip, port))
                for ip in ips
            ]
        return original_getaddrinfo(host, port, family, type, proto, flags)

    _patched_getaddrinfo._jimmy_runtime_dns_patch = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _patched_getaddrinfo


def write_runtime_dns_patch(
    run_dir: Path,
    host_ips: Mapping[str, Sequence[str]] | None = None,
) -> Path:
    patch_dir = Path(run_dir).expanduser() / "runtime_patch"
    patch_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(patch_dir, 0o700)
    except OSError:
        pass
    _atomic_write_text(
        patch_dir / "sitecustomize.py",
        _sitecustomize_source(host_ips or DEFAULT_HOST_IPS),
    )
    return patch_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write a per-run Python DNS shim for Jimmy hosts.",
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    print(write_runtime_dns_patch(args.run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
