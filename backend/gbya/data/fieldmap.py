"""Routing and field maps from OTRF events to the 7 normalised tables (plan §F.2, T1.3).

Every choice here was checked against real events at d9d40ef; differences from the plan's target
schema are documented in ``docs/fieldmap.md``. Values are copied, not interpreted, except for the
documented normalisations: user names (lower-case, ``DOMAIN\\`` stripped, ``-`` → NULL), PIDs
(decimal or ``0x`` hex → integer), and a few Windows enumeration codes noted below.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

SYSMON = "microsoft-windows-sysmon/operational"
SECURITY = "security"

COMMON_COLUMNS = [
    ("record_id", "BIGINT PRIMARY KEY"),
    ("ts", "TIMESTAMP"),
    ("host", "VARCHAR"),
    ("channel", "VARCHAR"),
    ("event_id", "INTEGER"),
]

TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "process_create": [
        ("image", "VARCHAR"),
        ("command_line", "VARCHAR"),
        ("parent_image", "VARCHAR"),
        ("parent_command_line", "VARCHAR"),
        ("pid", "BIGINT"),
        ("ppid", "BIGINT"),
        ("user", "VARCHAR"),
        ("integrity_level", "VARCHAR"),
        ("hashes", "VARCHAR"),
    ],
    "process_access": [
        ("source_image", "VARCHAR"),
        ("source_pid", "BIGINT"),
        ("target_image", "VARCHAR"),
        ("target_pid", "BIGINT"),
        ("granted_access", "VARCHAR"),
        ("call_trace", "VARCHAR"),
        ("user", "VARCHAR"),
    ],
    "network": [
        ("image", "VARCHAR"),
        ("pid", "BIGINT"),
        ("src_ip", "VARCHAR"),
        ("src_port", "INTEGER"),
        ("dst_ip", "VARCHAR"),
        ("dst_port", "INTEGER"),
        ("protocol", "VARCHAR"),
        ("direction", "VARCHAR"),
        ("user", "VARCHAR"),
    ],
    "registry": [
        ("event_type", "VARCHAR"),
        ("image", "VARCHAR"),
        ("pid", "BIGINT"),
        ("target_object", "VARCHAR"),
        ("details", "VARCHAR"),
        ("user", "VARCHAR"),
    ],
    "file": [
        ("image", "VARCHAR"),
        ("pid", "BIGINT"),
        ("target_filename", "VARCHAR"),
        ("event_type", "VARCHAR"),
        ("user", "VARCHAR"),
    ],
    "logon": [
        ("subject_user", "VARCHAR"),
        ("target_user", "VARCHAR"),
        ("logon_type", "INTEGER"),
        ("src_ip", "VARCHAR"),
        ("workstation", "VARCHAR"),
        ("process_name", "VARCHAR"),
    ],
    "share_access": [
        ("subject_user", "VARCHAR"),
        ("share_name", "VARCHAR"),
        ("relative_target", "VARCHAR"),
        ("src_ip", "VARCHAR"),
        ("access_mask", "VARCHAR"),
    ],
}

TABLES = list(TABLE_COLUMNS)

# Acting-user column per table, for C3 (§F.2). For `logon` it depends on the event ID.
ACTING_USER = {
    "process_create": "user",
    "process_access": "user",
    "network": "user",
    "registry": "user",
    "file": "user",
    "share_access": "subject_user",
}


def acting_user_column(table: str, event_id: int) -> str:
    if table == "logon":
        return "subject_user" if event_id == 4648 else "target_user"
    return ACTING_USER[table]


def ddl(table: str) -> str:
    cols = COMMON_COLUMNS + TABLE_COLUMNS[table]
    return f'CREATE TABLE "{table}" (' + ", ".join(f'"{c}" {t}' for c, t in cols) + ")"


RAW_DDL = (
    "CREATE TABLE raw_events (record_id BIGINT PRIMARY KEY, channel VARCHAR, "
    "event_id INTEGER, json VARCHAR)"
)
META_DDL = "CREATE TABLE _meta (key VARCHAR PRIMARY KEY, value VARCHAR)"


# ---------------------------------------------------------------- value normalisation


def blank(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() in ("", "-"):
        return None
    return v


def as_str(v: Any) -> str | None:
    v = blank(v)
    return None if v is None else str(v)


def as_int(v: Any) -> int | None:
    """Decimal or 0x-hex integers (Security events log PIDs in hex)."""
    v = blank(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return None


def norm_user(v: Any) -> str | None:
    """Lower-case and strip a DOMAIN\\ prefix (§F.2); '-' and '' become NULL."""
    s = as_str(v)
    if s is None:
        return None
    s = s.split("\\")[-1].strip().lower()
    return s or None


INTEGRITY_SIDS = {
    "S-1-16-0": "Untrusted",
    "S-1-16-4096": "Low",
    "S-1-16-8192": "Medium",
    "S-1-16-8448": "MediumPlus",
    "S-1-16-12288": "High",
    "S-1-16-16384": "System",
}
# Windows Filtering Platform direction codes (event 5156).
WFP_DIRECTION = {"%%14592": "inbound", "%%14593": "outbound"}


def _direction_sysmon(initiated: Any) -> str | None:
    s = as_str(initiated)
    if s is None:
        return None
    return {"true": "outbound", "false": "inbound"}.get(s.lower(), s)


# ---------------------------------------------------------------- event canonicalisation

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


def parse_ts(value: Any) -> datetime | None:
    """ISO-8601 with ``Z``/offset → naive UTC; ``YYYY-MM-DD HH:MM:SS[.fff]`` → naive as written."""
    s = as_str(value)
    if s is None:
        return None
    s = s.strip()
    if "T" in s:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def canonical(event: dict[str, Any]) -> dict[str, Any]:
    """Flatten the old Winlogbeat layout (``event_data``, ``log_name``…) into the flat layout."""
    if "event_data" in event and isinstance(event["event_data"], dict):
        flat = dict(event["event_data"])
        flat.setdefault("Channel", event.get("log_name"))
        flat.setdefault("EventID", event.get("event_id"))
        flat.setdefault("Hostname", event.get("computer_name"))
        flat.setdefault("@timestamp", event.get("@timestamp"))
        return flat
    return event


def timestamp_source(event: dict[str, Any]) -> tuple[str | None, Any]:
    """Plan §D.1: ``TimeCreated`` if present, else ``@timestamp``."""
    for key in ("TimeCreated", "@timestamp"):
        if blank(event.get(key)) is not None:
            return key, event[key]
    return None, None


def host_of(event: dict[str, Any]) -> str | None:
    """``Hostname`` (or ``Computer``), as written in the event."""
    return as_str(event.get("Hostname")) or as_str(event.get("Computer"))


CANONICAL_CHANNELS = {
    SECURITY: "Security",
    SYSMON: "Microsoft-Windows-Sysmon/Operational",
}


def channel_of(event: dict[str, Any]) -> str | None:
    """Channel with canonical spelling: exports write both ``Security`` and ``security``.

    The original spelling stays in the raw JSON.
    """
    ch = as_str(event.get("Channel"))
    return None if ch is None else CANONICAL_CHANNELS.get(ch.lower(), ch)


def event_id_of(event: dict[str, Any]) -> int | None:
    return as_int(event.get("EventID"))


# ---------------------------------------------------------------- extractors

Extractor = Callable[[dict[str, Any]], dict[str, Any]]


_SYSMON_PID_MSG = re.compile(r"(?m)^ProcessId: (\d+)\s*$")
_WFP_PID_MSG = re.compile(r"Process ID:\s+(\d+)")


def is_nxlog(e: dict[str, Any]) -> bool:
    return "SourceModuleType" in e


def _pid(e: dict[str, Any], message_pattern: re.Pattern[str] = _SYSMON_PID_MSG) -> int | None:
    """PID of the process the event is about (see docs/fieldmap.md, "PIDs").

    In nxlog-collected events a ``ProcessID`` field is the PID of the process that *wrote* the
    event (e.g. 4 for System), and ``ExecutionProcessID`` is Sysmon's own PID — neither is ever
    used. When ``ProcessId`` is absent, the PID is read from the event's own rendered ``Message``;
    otherwise it is NULL. In the other export formats, ``ProcessID`` is the event's real field
    (e.g. 5156) and is used.
    """
    if blank(e.get("ProcessId")) is not None:
        return as_int(e["ProcessId"])
    if not is_nxlog(e) and blank(e.get("ProcessID")) is not None:
        return as_int(e["ProcessID"])
    m = message_pattern.search(str(e.get("Message") or ""))
    return int(m.group(1)) if m else None


def pid_from_message(e: dict[str, Any], pid: int | None) -> bool:
    """True when ``_pid`` had to read the PID from the event's ``Message`` text."""
    if pid is None or blank(e.get("ProcessId")) is not None:
        return False
    return is_nxlog(e) or blank(e.get("ProcessID")) is None


def _sysmon_process_create(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "image": as_str(e.get("Image")),
        "command_line": as_str(e.get("CommandLine")),
        "parent_image": as_str(e.get("ParentImage")),
        "parent_command_line": as_str(e.get("ParentCommandLine")),
        "pid": _pid(e),
        "ppid": as_int(e.get("ParentProcessId")),
        "user": norm_user(e.get("User")),
        "integrity_level": as_str(e.get("IntegrityLevel")),
        "hashes": as_str(e.get("Hashes")),
    }


def _sec_process_create(e: dict[str, Any]) -> dict[str, Any]:
    label = as_str(e.get("MandatoryLabel"))
    return {
        "image": as_str(e.get("NewProcessName")),
        "command_line": as_str(e.get("CommandLine")),
        "parent_image": as_str(e.get("ParentProcessName")),
        "parent_command_line": None,  # not in 4688
        "pid": as_int(e.get("NewProcessId")),
        "ppid": as_int(e.get("ProcessId")),  # in 4688 ProcessId is the creator (parent)
        # Account of the new process: TargetUserName when logged, else the creator.
        "user": norm_user(e.get("TargetUserName")) or norm_user(e.get("SubjectUserName")),
        "integrity_level": INTEGRITY_SIDS.get(label or "", label),
        "hashes": None,
    }


def _sysmon_process_access(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_image": as_str(e.get("SourceImage")),
        "source_pid": as_int(e.get("SourceProcessId")),
        "target_image": as_str(e.get("TargetImage")),
        "target_pid": as_int(e.get("TargetProcessId")),
        "granted_access": as_str(e.get("GrantedAccess")),
        "call_trace": as_str(e.get("CallTrace")),
        "user": norm_user(e.get("SourceUser")),  # only in newer Sysmon versions
    }


def _sec_process_access(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_image": as_str(e.get("ProcessName")),
        "source_pid": as_int(e.get("ProcessId")),
        "target_image": as_str(e.get("ObjectName")),
        "target_pid": None,  # 4656/4663 do not log the target PID
        "granted_access": as_str(e.get("AccessMask")),
        "call_trace": None,
        "user": norm_user(e.get("SubjectUserName")),
    }


def _sysmon_network(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "image": as_str(e.get("Image")),
        "pid": _pid(e),
        "src_ip": as_str(e.get("SourceIp")),
        "src_port": as_int(e.get("SourcePort")),
        "dst_ip": as_str(e.get("DestinationIp")),
        "dst_port": as_int(e.get("DestinationPort")),
        "protocol": as_str(e.get("Protocol")),
        "direction": _direction_sysmon(e.get("Initiated")),
        "user": norm_user(e.get("User")),
    }


def _sec_network(e: dict[str, Any]) -> dict[str, Any]:
    d = as_str(e.get("Direction"))
    return {
        "image": as_str(e.get("Application")),
        "pid": _pid(e, _WFP_PID_MSG),
        "src_ip": as_str(e.get("SourceAddress")),
        "src_port": as_int(e.get("SourcePort")),
        "dst_ip": as_str(e.get("DestAddress")),
        "dst_port": as_int(e.get("DestPort")),
        "protocol": as_str(e.get("Protocol")),
        "direction": WFP_DIRECTION.get(d or "", d),
        "user": None,  # 5156 carries no user
    }


def _sysmon_registry(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": as_str(e.get("EventType")),
        "image": as_str(e.get("Image")),
        "pid": _pid(e),
        "target_object": as_str(e.get("TargetObject")),
        "details": as_str(e.get("Details")) or as_str(e.get("NewName")),
        "user": norm_user(e.get("User")),
    }


def _sec_registry(e: dict[str, Any]) -> dict[str, Any]:
    key, value = as_str(e.get("ObjectName")), as_str(e.get("ObjectValueName"))
    return {
        "event_type": as_str(e.get("OperationType")),
        "image": as_str(e.get("ProcessName")),
        "pid": as_int(e.get("ProcessId")),
        "target_object": f"{key}\\{value}" if key and value else key,
        "details": as_str(e.get("NewValue")),
        "user": norm_user(e.get("SubjectUserName")),
    }


def _sysmon_file(e: dict[str, Any]) -> dict[str, Any]:
    eid = event_id_of(e)
    return {
        "image": as_str(e.get("Image")),
        "pid": _pid(e),
        "target_filename": as_str(e.get("TargetFilename")),
        "event_type": {11: "FileCreate", 23: "FileDelete"}.get(eid or 0),
        "user": norm_user(e.get("User")),
    }


def _sec_file(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "image": as_str(e.get("ProcessName")),
        "pid": as_int(e.get("ProcessId")),
        "target_filename": as_str(e.get("ObjectName")),
        "event_type": as_str(e.get("AccessList")),  # e.g. "%%4417" (WriteData)
        "user": norm_user(e.get("SubjectUserName")),
    }


def _sec_logon(e: dict[str, Any]) -> dict[str, Any]:
    eid = event_id_of(e)
    return {
        "subject_user": norm_user(e.get("SubjectUserName")),
        "target_user": norm_user(e.get("TargetUserName")),
        "logon_type": as_int(e.get("LogonType")),  # absent in 4648
        "src_ip": as_str(e.get("IpAddress")),
        "workstation": as_str(e.get("WorkstationName")) if eid != 4648 else None,
        "process_name": as_str(e.get("ProcessName")),
    }


def _sec_share(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_user": norm_user(e.get("SubjectUserName")),
        "share_name": as_str(e.get("ShareName")),
        "relative_target": as_str(e.get("RelativeTargetName")),  # 5145 only
        "src_ip": as_str(e.get("IpAddress")),
        "access_mask": as_str(e.get("AccessMask")),
    }


def _object_type(e: dict[str, Any]) -> str:
    return (as_str(e.get("ObjectType")) or "").lower()


def route(event: dict[str, Any]) -> tuple[str, Extractor] | None:
    """Return (table, extractor) for a mapped event, or None (kept in raw_events only)."""
    channel = (channel_of(event) or "").lower()
    eid = event_id_of(event)
    if channel == SYSMON:
        return {
            1: ("process_create", _sysmon_process_create),
            3: ("network", _sysmon_network),
            10: ("process_access", _sysmon_process_access),
            11: ("file", _sysmon_file),
            12: ("registry", _sysmon_registry),
            13: ("registry", _sysmon_registry),
            14: ("registry", _sysmon_registry),
            23: ("file", _sysmon_file),
        }.get(eid or -1)
    if channel == SECURITY:
        if eid in (4656, 4663) and _object_type(event) == "process":
            return ("process_access", _sec_process_access)
        if eid == 4663 and _object_type(event) == "file":
            return ("file", _sec_file)
        return {
            4688: ("process_create", _sec_process_create),
            5156: ("network", _sec_network),
            4657: ("registry", _sec_registry),
            4624: ("logon", _sec_logon),
            4625: ("logon", _sec_logon),
            4648: ("logon", _sec_logon),
            5140: ("share_access", _sec_share),
            5145: ("share_access", _sec_share),
        }.get(eid or -1)
    return None


def raw_json(event: dict[str, Any]) -> str:
    """The original event, unchanged, as compact JSON."""
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))
