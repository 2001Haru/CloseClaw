"""Windows restricted-token shell execution backend."""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Windows token / process constants.
TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_DUPLICATE = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_DEFAULT = 0x0080
TOKEN_ADJUST_SESSIONID = 0x0100
TOKEN_ALL_NEEDED = (
    TOKEN_ASSIGN_PRIMARY
    | TOKEN_DUPLICATE
    | TOKEN_QUERY
    | TOKEN_ADJUST_DEFAULT
    | TOKEN_ADJUST_SESSIONID
)

DISABLE_MAX_PRIVILEGE = 0x1
LUA_TOKEN = 0x4

SecurityImpersonation = 2
TokenPrimary = 1
TokenIntegrityLevel = 25
SE_GROUP_INTEGRITY = 0x00000020
SE_GROUP_INTEGRITY_ENABLED = 0x00000040

LOGON_WITH_PROFILE = 0x00000001
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", wintypes.LPVOID),
        ("Attributes", wintypes.DWORD),
    ]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _failure_result(message: str) -> dict[str, Any]:
    return {
        "returncode": -1,
        "stdout": "",
        "stderr": message,
        "executed": False,
        "sandbox_backend": "windows_restricted_token",
    }


def _decode_result_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _failure_result("Restricted worker did not produce output file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _failure_result(f"Restricted worker output parse failed: {exc}")
    if not isinstance(payload, dict):
        return _failure_result("Restricted worker returned non-object payload")
    payload.setdefault("sandbox_backend", "windows_restricted_token")
    return payload


def _open_process_token() -> wintypes.HANDLE:
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    token = wintypes.HANDLE()
    ok = advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ALL_NEEDED, ctypes.byref(token))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return token


def _create_restricted_primary_token() -> wintypes.HANDLE:
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32

    current = _open_process_token()
    restricted = wintypes.HANDLE()
    duplicated = wintypes.HANDLE()
    sid_ptr = wintypes.LPVOID()

    try:
        ok = advapi32.CreateRestrictedToken(
            current,
            DISABLE_MAX_PRIVILEGE | LUA_TOKEN,
            0,
            None,
            0,
            None,
            0,
            None,
            ctypes.byref(restricted),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

        ok = advapi32.DuplicateTokenEx(
            restricted,
            TOKEN_ALL_NEEDED,
            None,
            SecurityImpersonation,
            TokenPrimary,
            ctypes.byref(duplicated),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

        # Set low integrity mandatory label (MIC).
        ok = advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid_ptr))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

        label = TOKEN_MANDATORY_LABEL()
        label.Label.Sid = sid_ptr
        label.Label.Attributes = SE_GROUP_INTEGRITY | SE_GROUP_INTEGRITY_ENABLED

        sid_len = ctypes.windll.advapi32.GetLengthSid(sid_ptr)
        buf_len = ctypes.sizeof(TOKEN_MANDATORY_LABEL) + sid_len
        ok = advapi32.SetTokenInformation(
            duplicated,
            TokenIntegrityLevel,
            ctypes.byref(label),
            buf_len,
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

        return duplicated
    finally:
        if sid_ptr:
            ctypes.windll.kernel32.LocalFree(sid_ptr)
        if restricted:
            kernel32.CloseHandle(restricted)
        if current:
            kernel32.CloseHandle(current)


def _assign_kill_on_close_job(process_handle: wintypes.HANDLE) -> Optional[wintypes.HANDLE]:
    kernel32 = ctypes.windll.kernel32
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        kernel32.CloseHandle(job)
        return None

    ok = kernel32.AssignProcessToJobObject(job, process_handle)
    if not ok:
        kernel32.CloseHandle(job)
        return None
    return job


def _launch_worker_with_restricted_token(
    *,
    command: str,
    timeout: int,
    cwd: Optional[str],
    env: dict[str, str],
    output_file: Path,
    env_file: Path,
) -> tuple[int, str]:
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32

    token = _create_restricted_primary_token()
    process_info = PROCESS_INFORMATION()
    startup_info = STARTUPINFOW()
    startup_info.cb = ctypes.sizeof(STARTUPINFOW)

    cmd_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    worker_args = [
        sys.executable,
        "-m",
        "closeclaw.sandbox.restricted_worker",
        "--command-b64",
        cmd_b64,
        "--timeout",
        str(max(1, int(timeout))),
        "--output",
        str(output_file),
        "--env-file",
        str(env_file),
    ]
    if cwd:
        worker_args.extend(["--cwd", cwd])

    env_file.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    command_line = subprocess.list2cmdline(worker_args)

    job_handle = None
    try:
        ok = advapi32.CreateProcessAsUserW(
            token,
            None,
            ctypes.c_wchar_p(command_line),
            None,
            None,
            False,
            CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            None,
            ctypes.c_wchar_p(cwd) if cwd else None,
            ctypes.byref(startup_info),
            ctypes.byref(process_info),
        )
        if not ok:
            # Fallback path on some systems where CreateProcessAsUserW is restricted.
            ok = advapi32.CreateProcessWithTokenW(
                token,
                LOGON_WITH_PROFILE,
                None,
                ctypes.c_wchar_p(command_line),
                CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                None,
                ctypes.c_wchar_p(cwd) if cwd else None,
                ctypes.byref(startup_info),
                ctypes.byref(process_info),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())

        job_handle = _assign_kill_on_close_job(process_info.hProcess)
        wait_ms = max(1, int(timeout)) * 1000 + 3000
        wait_result = kernel32.WaitForSingleObject(process_info.hProcess, wait_ms)
        if wait_result == WAIT_TIMEOUT:
            kernel32.TerminateProcess(process_info.hProcess, 1)
            return 124, "Restricted worker timed out"
        if wait_result != WAIT_OBJECT_0:
            return 1, "Restricted worker wait failed"

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
            return 1, "Restricted worker exit code unavailable"
        return int(exit_code.value), ""
    finally:
        if process_info.hThread:
            kernel32.CloseHandle(process_info.hThread)
        if process_info.hProcess:
            kernel32.CloseHandle(process_info.hProcess)
        if job_handle:
            kernel32.CloseHandle(job_handle)
        if token:
            kernel32.CloseHandle(token)


def run_restricted_shell_windows(
    *,
    command: str,
    timeout: int,
    cwd: Optional[str],
    env: dict[str, str],
    fail_closed: bool,
) -> Optional[dict[str, Any]]:
    """Run shell command under restricted token + MIC + JobObject on Windows."""
    if os.name != "nt":
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="closeclaw_sbx_") as tmpdir:
            output_file = Path(tmpdir) / "result.json"
            env_file = Path(tmpdir) / "env.json"
            code, launcher_err = _launch_worker_with_restricted_token(
                command=command,
                timeout=timeout,
                cwd=cwd,
                env=env,
                output_file=output_file,
                env_file=env_file,
            )
            if code == 124:
                return _failure_result(f"Command timed out after {timeout} seconds (restricted sandbox)")
            if code != 0 and launcher_err:
                return _failure_result(launcher_err)
            return _decode_result_file(output_file)
    except Exception as exc:
        logger.warning("Windows restricted sandbox failed: %s", exc)
        if fail_closed:
            return _failure_result(f"OS sandbox enforcement failed (blocked): {exc}")
        return None

