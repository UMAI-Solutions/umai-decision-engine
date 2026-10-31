"""A local stack of real processes for measurements that need them (launch plan S0-2, S0-3): the
policy service, the engine API and the gateway as uvicorn processes sharing a policy directory,
a key-registry directory and an applications directory, as on the single VM (ADR-0032), plus one
stub process for the upstream model and the L1b provider (``evals/stubs.py``).

Production settings throughout (``UMAI_DE_ENV=production``): signed bundles and signed key
snapshots, the default key-registry poll interval, rate limit and failed-credential throttle,
access logs on. Everything lives in a temporary directory, with synthetic keys and data only; the
developer's ``.env`` and ``UMAI_DE_*`` variables are not read. Records are off unless asked for.

Optional CPU affinity pins the services to a subset of logical CPUs (to model a smaller VM) and
keeps the load generator and the stubs on the others.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evals.common import ROOT
from umai_decision_engine.policy.manifest import BundleManifest, sha256_hex, sign_manifest

TENANT = "loadtest"  # the stack's tenant: its default tenant, so root bundles are its own
APPLICATION = "load"
DEMO_BUNDLE = "gr-demo-data-protection"
L1B_BUNDLE = "gr-load-l1b"
# As on umai-beta (its customer's guardrail): two L1b policies enforce, the others run in shadow,
# with an L1b timeout of 1 400 ms.
L1B_ENFORCED = ("prompt-attack", "malicious-use")
L1B_TIMEOUT_MS = 1400
PROVIDER_KEY = "sk-stub-not-a-real-key"  # sent to the stub upstream only

SERVICES = {
    "policy": "umai_decision_engine.control_plane.app:app",
    "engine": "umai_decision_engine.api.app:app",
    "gateway": "umai_decision_engine.gateway.app:app",
    "stubs": "evals.stubs:app",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode(), base64.b64encode(public).decode()


# ------------------------------------------------------------------ process measurements


def set_affinity(pid: int, cpus: list[int]) -> bool:
    """Pin a process to logical CPUs; returns whether the platform allowed it."""

    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(pid, set(cpus))
        return True
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x0200 | 0x0400, False, pid)  # SET_INFORMATION | QUERY
        if not handle:
            return False
        try:
            mask = sum(1 << c for c in cpus)
            done = kernel32.SetProcessAffinityMask(ctypes.c_void_p(handle), ctypes.c_size_t(mask))
            return bool(done)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    return False


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    def seconds(self) -> float:
        return ((self.high << 32) | self.low) / 1e7


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def process_stats(pid: int) -> tuple[float, int] | None:
    """(CPU seconds, user + system, since start; resident memory in bytes), or ``None``."""

    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY | VM_READ
        if not handle:
            return None
        try:
            times = [_FileTime() for _ in range(4)]
            if not kernel32.GetProcessTimes(ctypes.c_void_p(handle), *map(ctypes.byref, times)):
                return None
            counters = _MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb)
            return times[2].seconds() + times[3].seconds(), counters.WorkingSetSize
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK")
        cpu = (int(fields[11]) + int(fields[12])) / ticks
        rss = int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
        return cpu, rss
    except (OSError, IndexError, ValueError):
        return None


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def interpreter_pid(pid: int) -> int:
    """The process that runs the code: on Windows a virtual environment's ``python.exe`` is a
    launcher that starts the base interpreter as its child; elsewhere ``pid`` itself."""

    if sys.platform != "win32":
        return pid
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return pid
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        while more:
            if entry.th32ParentProcessID == pid and entry.szExeFile.lower() == "python.exe":
                return entry.th32ProcessID
            more = kernel32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(snapshot))
    return pid


def performance_cpus() -> list[int]:
    """Logical CPUs of the most performant class, in order (hybrid CPUs have performance and
    efficiency cores; Windows reports the class per CPU set). Elsewhere: every CPU."""

    count = os.cpu_count() or 1
    if sys.platform != "win32":
        return list(range(count))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    size = ctypes.c_ulong(0)
    kernel32.GetSystemCpuSetInformation(None, 0, ctypes.byref(size), None, 0)
    buffer = (ctypes.c_byte * size.value)()
    if not size.value or not kernel32.GetSystemCpuSetInformation(
        buffer, size, ctypes.byref(size), None, 0
    ):
        return list(range(count))
    raw, offset, classes = bytes(buffer), 0, {}
    while offset < size.value:
        length = int.from_bytes(raw[offset : offset + 4], "little")
        # SYSTEM_CPU_SET_INFORMATION: LogicalProcessorIndex at 14, EfficiencyClass at 18
        classes[raw[offset + 14]] = raw[offset + 18]
        offset += length
    best = max(classes.values())
    return sorted(cpu for cpu, level in classes.items() if level == best)


class _HostTimes(ctypes.Structure):
    _fields_ = [("idle", _FileTime), ("kernel", _FileTime), ("user", _FileTime)]


def host_cpu_times() -> tuple[float, float] | None:
    """(busy, total) CPU seconds of the whole host since boot, summed over CPUs; two samples
    give how busy the host was in between (other work competes with a measurement)."""

    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        times = _HostTimes()
        if not kernel32.GetSystemTimes(
            ctypes.byref(times.idle), ctypes.byref(times.kernel), ctypes.byref(times.user)
        ):
            return None
        total = times.kernel.seconds() + times.user.seconds()  # kernel time includes idle
        return total - times.idle.seconds(), total
    try:
        values = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    except (OSError, ValueError):
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values) - idle, sum(values)


def host_busy(before: tuple[float, float] | None, after: tuple[float, float] | None) -> float:
    """Share of the host's CPU time that was busy between two samples (NaN if unknown)."""

    if before is None or after is None or after[1] <= before[1]:
        return float("nan")
    return (after[0] - before[0]) / (after[1] - before[1])


def own_cpu_seconds() -> float:
    times = os.times()
    return times.user + times.system


# ------------------------------------------------------------------ the stack


@dataclass
class Service:
    name: str
    port: int
    process: subprocess.Popen
    log: Path
    pid: int = 0  # the interpreter's (see interpreter_pid), set once the service is ready

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stats(self) -> tuple[float, int] | None:
        return process_stats(self.pid or self.process.pid)


@dataclass
class LocalStack:
    work: Path
    server_cpus: list[int] | None = None  # policy service, engine, gateway
    client_cpus: list[int] | None = None  # this process (the load generator) and the stubs
    stub_latency_ms: float = 0.0
    extra_env: dict[str, str] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)
    control_token: str = field(default_factory=lambda: f"ctl-{secrets.token_urlsafe(24)}")
    affinity: dict[str, bool] = field(default_factory=dict)  # process → pinned as asked

    def __post_init__(self) -> None:
        self.work = Path(self.work)
        self.ports = {name: free_port() for name in SERVICES}

    # -------------------------------------------------------------- files

    def prepare(self) -> None:
        """Signed bundles (the demo guardrail and an L1b variant), the signing key, the plans
        file and the tenant's applications file, under ``work``."""

        for sub in ("policies", "keys", "applications", "data"):
            (self.work / sub).mkdir(parents=True, exist_ok=True)
        self.private_key, self.public_key = _keypair()
        (self.work / "signing.key").write_text(self.private_key, encoding="utf-8")
        demo = ROOT / "policies" / DEMO_BUNDLE
        active = int((demo / "active").read_text(encoding="utf-8").strip())
        content = (demo / str(active) / "bundle.yaml").read_text(encoding="utf-8")
        self._write_bundle(DEMO_BUNDLE, active, content)
        self._write_bundle(L1B_BUNDLE, 1, self.l1b_bundle_yaml())
        shutil.copyfile(ROOT / "deploy" / "plans.yaml", self.work / "plans.yaml")
        stub = f"http://127.0.0.1:{self.ports['stubs']}/v1"
        content = {
            "providers": [
                {
                    "provider_id": "stub",
                    "kind": "openai",
                    "base_url": stub,
                    "credential": "pass_through",
                    "default": True,
                }
            ],
            "applications": [{"application_id": APPLICATION, "guardrail": DEMO_BUNDLE}],
        }
        (self.work / "applications" / f"{TENANT}.yaml").write_text(
            json.dumps(content, indent=2), encoding="utf-8"
        )

    @staticmethod
    def l1b_bundle_yaml() -> str:
        """The demo guardrail's L1b version (its highest) with the stub provider, two policies
        enforcing and the rest in shadow and beta's L1b timeout, as umai-beta runs its
        customer's guardrail."""

        demo = ROOT / "policies" / DEMO_BUNDLE
        latest = max(int(p.name) for p in demo.iterdir() if p.name.isdigit())
        text = (demo / str(latest) / "bundle.yaml").read_text(encoding="utf-8")
        text = text.replace(f"id: {DEMO_BUNDLE}\n", f"id: {L1B_BUNDLE}\n", 1)
        text = text.replace(f"version: {latest}\n", "version: 1\n", 1)
        text = text.replace("  provider: openai\n", "  provider: stub\n", 1)
        text = re.sub(r"(?m)^  timeout_ms: \d+$", f"  timeout_ms: {L1B_TIMEOUT_MS}", text, count=1)
        lines, policy = [], None
        for line in text.splitlines(keepends=True):
            if line.startswith("  - id: "):
                policy = line.split(":", 1)[1].strip()
            if line.strip() == "enforcement: shadow" and policy in L1B_ENFORCED:
                line = line.replace("shadow", "enforce")
            lines.append(line)
        return "".join(lines)

    def _write_bundle(self, bundle_id: str, version: int, content: str) -> None:
        directory = self.work / "policies" / bundle_id / str(version)
        directory.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        (directory / "bundle.yaml").write_bytes(data)
        manifest = BundleManifest(
            bundle_id=bundle_id,
            version=version,
            contract_version="0.9",
            engine=">=0.1.0",
            created_at=datetime.now(UTC),
            sha256=sha256_hex(data),
        )
        signed = sign_manifest(manifest, self.private_key, "eval")
        (directory / "manifest.json").write_text(
            signed.model_dump_json(exclude_none=True), encoding="utf-8"
        )
        (directory.parent / "active").write_text(str(version), encoding="utf-8")

    def env(self) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith(("UMAI_DE_", "UMAI_STUB_"))}
        stub = f"http://127.0.0.1:{self.ports['stubs']}"
        settings = {
            "UMAI_DE_ENV": "production",
            "UMAI_DE_LOG_LEVEL": "INFO",
            "UMAI_DE_POLICY_DIR": str(self.work / "policies"),
            "UMAI_DE_BUNDLE_PUBLIC_KEYS": json.dumps({"eval": self.public_key}),
            "UMAI_DE_REQUIRE_SIGNED_BUNDLES": "true",
            "UMAI_DE_KEY_REGISTRY_DIR": str(self.work / "keys"),
            "UMAI_DE_APPLICATIONS_DIR": str(self.work / "applications"),
            "UMAI_DE_DEFAULT_TENANT_ID": TENANT,
            "UMAI_DE_REGION": "eu",
            "UMAI_DE_TENANT_HMAC_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            "UMAI_DE_CONTROL_API_TOKEN": self.control_token,
            "UMAI_DE_CONTROL_DB_PATH": str(self.work / "data" / "control.sqlite"),
            "UMAI_DE_SIGNING_KEY_FILE": str(self.work / "signing.key"),
            "UMAI_DE_SIGNING_KEY_ID": "eval",
            "UMAI_DE_PLANS_FILE": str(self.work / "plans.yaml"),
            "UMAI_DE_GATEWAY_UPSTREAM_ALLOWLIST": json.dumps([stub]),
            "UMAI_DE_DECISION_PROVIDERS": json.dumps(
                {"stub": {"adapter": "openai_decisions", "base_url": f"{stub}/v1", "model": "stub"}}
            ),
            "UMAI_DE_DECISION_PROVIDER_KEYS": json.dumps({"stub": PROVIDER_KEY}),
            "UMAI_STUB_LATENCY_MS": str(self.stub_latency_ms),
            "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), base.get("PYTHONPATH")])),
        }
        return {**base, **settings, **self.extra_env}

    # -------------------------------------------------------------- processes

    def start(self, names: tuple[str, ...] = ("stubs", "policy", "engine", "gateway")) -> None:
        env = self.env()
        if self.client_cpus:
            self.affinity["client"] = set_affinity(os.getpid(), self.client_cpus)
        for name in names:
            log = self.work / f"{name}.log"
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                SERVICES[name],
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ports[name]),
                "--no-server-header",
                "--no-proxy-headers",  # as on the VM: X-Forwarded-For only from trusted proxies
            ]
            if name == "stubs":
                command += ["--no-access-log", "--log-level", "warning"]
            handle = log.open("wb")
            process = subprocess.Popen(
                command, cwd=self.work, env=env, stdout=handle, stderr=subprocess.STDOUT
            )
            service = Service(name, self.ports[name], process, log)
            self.services[name] = service
            self._wait_ready(service)
            service.pid = interpreter_pid(process.pid)
            cpus = self.client_cpus if name == "stubs" else self.server_cpus
            if cpus:
                self.affinity[name] = set_affinity(service.pid, cpus)

    def _wait_ready(self, service: Service, timeout_s: float = 120.0) -> None:
        path = "/readyz" if service.name in ("engine", "gateway") else "/healthz"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if service.process.poll() is not None:
                raise RuntimeError(f"{service.name} exited; see {service.log}")
            try:
                if httpx.get(service.url + path, timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"{service.name} not ready after {timeout_s:.0f} s; see {service.log}")

    def stop(self) -> None:
        for service in reversed(list(self.services.values())):
            service.process.terminate()
        for service in self.services.values():
            try:
                service.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                service.process.kill()
        self.services.clear()

    def __enter__(self) -> LocalStack:
        self.prepare()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -------------------------------------------------------------- policy service calls

    def control(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.services["policy"].url,
            headers={"Authorization": f"Bearer {self.control_token}"},
            timeout=30,
        )

    def create_tenant(self, overrides: dict[str, Any] | None = None) -> None:
        """The stack's tenant, with plan overrides (as the beta plan: a dedicated test tenant
        whose quota is lifted, so the run uses no customer's quota)."""

        with self.control() as control:
            created = control.post("/v1/tenants", json={"id": TENANT})
            if created.status_code not in (201, 409):
                raise RuntimeError(f"tenant: HTTP {created.status_code}")
            if overrides is not None:
                plan = control.put(
                    f"/v1/tenants/{TENANT}/plan",
                    json={"edition": "community", "overrides": overrides},
                )
                if plan.status_code != 200:
                    raise RuntimeError(f"plan: HTTP {plan.status_code}")

    def mint_key(self, name: str, scopes: list[str], **extra: Any) -> tuple[str, str]:
        """A new key of the stack's tenant: (secret, key id)."""

        with self.control() as control:
            created = control.post(
                f"/v1/tenants/{TENANT}/keys", json={"name": name, "scopes": scopes, **extra}
            )
        if created.status_code != 201 or not created.json().get("published"):
            raise RuntimeError(f"key: HTTP {created.status_code}")
        return created.json()["secret"], created.json()["key"]["key_id"]

    def revoke_key(self, key_id: str) -> None:
        with self.control() as control:
            revoked = control.delete(f"/v1/tenants/{TENANT}/keys/{key_id}")
        if revoked.status_code != 200 or not revoked.json().get("published"):
            raise RuntimeError(f"revoke: HTTP {revoked.status_code}")
