"""Linux clock-domain provenance, including explicitly absent namespace APIs."""
import errno
import hashlib
from pathlib import Path

import pytest

from tradebot_delta import clock

BOOT_PATH = "/proc/sys/kernel/random/boot_id"
TIME_PATH = "/proc/self/ns/time"
OFFSET_PATH = "/proc/self/timens_offsets"
BOOT = "0ee4e4a4-d01b-4979-b9ae-25bc50d63573"


@pytest.fixture(autouse=True)
def clear_domain_cache():
    clock.monotonic_domain.cache_clear()
    yield
    clock.monotonic_domain.cache_clear()


def declared_proc(monkeypatch, *, boot=BOOT, namespace="time:[4026531834]", offsets="monotonic 0 0\nboottime 0 0\n"):
    """Controlled proc observations; never overwrite actual kernel files."""
    original_read = Path.read_text
    original_link = clock.os.readlink

    def read_text(path, *args, **kwargs):
        value = boot if str(path) == BOOT_PATH else offsets if str(path) == OFFSET_PATH else None
        if str(path) not in (BOOT_PATH, OFFSET_PATH):
            return original_read(path, *args, **kwargs)
        if isinstance(value, Exception):
            raise value
        return value

    def readlink(path, *args, **kwargs):
        if str(path) != TIME_PATH:
            return original_link(path, *args, **kwargs)
        if isinstance(namespace, Exception):
            raise namespace
        return namespace

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(clock.os, "readlink", readlink)


def expected(boot, namespace):
    return "linux-monotonic:"+hashlib.sha256((boot+":"+namespace).encode()).hexdigest()


def test_available_time_namespace_has_its_own_boot_bound_domain(monkeypatch):
    declared_proc(monkeypatch, boot=BOOT+"\n")
    first = clock.monotonic_domain()
    assert first == expected(BOOT, "time:[4026531834]")
    clock.monotonic_domain.cache_clear()
    declared_proc(monkeypatch, namespace="time:[4026531900]")
    assert clock.monotonic_domain() != first


def test_both_absent_namespace_entries_use_explicit_boot_bound_fallback(monkeypatch):
    declared_proc(monkeypatch, namespace=FileNotFoundError(errno.ENOENT, "Absent time namespace"),
                  offsets=FileNotFoundError(errno.ENOENT, "Absent time offsets"))
    assert clock.monotonic_domain() == expected(BOOT, "time-namespace-api-absent")


def test_new_boot_cannot_reuse_absent_namespace_domain(monkeypatch):
    absent = FileNotFoundError(errno.ENOENT, "No time namespace API")
    declared_proc(monkeypatch, namespace=absent, offsets=absent)
    first = clock.monotonic_domain()
    clock.monotonic_domain.cache_clear()
    declared_proc(monkeypatch, boot="c7169024-29bb-42b1-a1f1-ee50fcf1d610", namespace=absent, offsets=absent)
    assert clock.monotonic_domain() != first


@pytest.mark.parametrize("offsets", ["monotonic 0 0\nboottime 0 0\n", ""])
def test_missing_namespace_link_with_present_offsets_fails_closed(monkeypatch, offsets):
    declared_proc(monkeypatch, namespace=FileNotFoundError(errno.ENOENT, "Absent namespace link"), offsets=offsets)
    assert clock.monotonic_domain() is None


@pytest.mark.parametrize("error", [errno.EACCES, errno.EPERM, errno.EIO, errno.ENOTDIR])
@pytest.mark.parametrize("location", ["boot", "namespace", "offsets"])
def test_access_or_io_failure_never_enters_absence_fallback(monkeypatch, error, location):
    fault = OSError(error, "Declared proc read failure")
    values = {location: fault}
    if location == "offsets":
        values["namespace"] = FileNotFoundError(errno.ENOENT, "Absent namespace link")
    declared_proc(monkeypatch, **values)
    assert clock.monotonic_domain() is None


@pytest.mark.parametrize("boot", ["", "\n", FileNotFoundError(errno.ENOENT, "Missing boot identifier")])
def test_absent_or_empty_boot_identifier_cannot_establish_clock_domain(monkeypatch, boot):
    absent = FileNotFoundError(errno.ENOENT, "No time namespace API")
    declared_proc(monkeypatch, boot=boot, namespace=absent, offsets=absent)
    assert clock.monotonic_domain() is None


def test_empty_namespace_link_cannot_establish_clock_domain(monkeypatch):
    declared_proc(monkeypatch, namespace="")
    assert clock.monotonic_domain() is None
