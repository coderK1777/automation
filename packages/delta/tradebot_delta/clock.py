"""Identify the Linux boot/time namespace for shared monotonic ordering."""
from functools import lru_cache
import hashlib
import os
from pathlib import Path


@lru_cache
def monotonic_domain() -> str | None:
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        try:
            namespace = os.readlink("/proc/self/ns/time")
        except FileNotFoundError:
            # Standard Linux procfs exposes neither entry when time
            # namespaces are unavailable. Use the shared kernel boot in
            # that case; deployments must not selectively hide these APIs.
            # An inaccessible namespace (e.g. EACCES) still fails closed.
            try:
                Path("/proc/self/timens_offsets").read_text()
            except FileNotFoundError:
                namespace = "time-namespace-api-absent"
            else:
                return None
        if not boot or not namespace:
            return None
    except OSError:
        return None
    # The opaque domain is public operational provenance, never key material.
    return "linux-monotonic:"+hashlib.sha256((boot+":"+namespace).encode()).hexdigest()
