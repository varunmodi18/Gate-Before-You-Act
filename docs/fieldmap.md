# Field map: OTRF events → normalised tables (T1.3)

Implements plan §F.2 in [`backend/gbya/data/fieldmap.py`](../backend/gbya/data/fieldmap.py).
Every field name below was confirmed against the real events of **all 99 ingestible windows**
at OTRF `d9d40ef` (757,375 events), not just the plan's minimum of 5 windows. This file records
where the real data differs from the plan's target schema and what the normaliser does about it.
Measured 8 October 2026.

## Collection formats found

| Format | Windows | Timestamp fields | Notes |
|---|---|---|---|
| nxlog (`SourceModuleType: im_msvistalog`) | 58 | `@timestamp` (log-server ingest time, UTC `Z`), `EventTime` (local, no zone), `UtcTime` (Sysmon) | A `ProcessID` field here is the PID of the process that wrote the event, not the subject (see PIDs) |
| Flat export, `TimeCreated` in UTC (`…Z`) | 31 | `TimeCreated` = `@timestamp` | — |
| Flat export, `TimeCreated` local (`YYYY-MM-DD HH:MM:SS.fff`) | 8 | `TimeCreated`, no zone | e.g. the LSASS window |
| Old Winlogbeat (`event_data`, `log_name`, `computer_name`) | 2 (SDWIN-190518201922, SDWIN-190518203650) | `@timestamp` | Flattened by `fieldmap.canonical`; the original JSON is kept in `raw_events` |

Each window uses one format (the two Winlogbeat windows also contain 3 and 2 lines in a
different shape, which go to `raw_events` only).

## Timestamps and record IDs

- **Rule (plan §D.1):** `ts` = `TimeCreated` if present, else `@timestamp`. Measured use:
  `@timestamp` for 513,990 events, `TimeCreated` for 243,385; **no event lacked a timestamp**, so all
  99 windows are ordered by (`ts`, original line number).
- ISO values with `Z` or an offset are converted to naive UTC. `TimeCreated` without a zone is
  stored as written (local time of the lab machine; the zone is not in the data).
- **Caveat for authors:** within a window `ts` is consistent, but its meaning differs by format:
  in nxlog windows it is the log server's ingest time (typically within seconds of the event); in
  8 windows it is local time. Compare times only within a window (change-ticket windows and C2's
  window range are always per window).
- `record_id` = 1-based position in that order. Rebuilding gives identical IDs (tested).

## Channels

Exports spell the Security channel both `Security` (75,087 events) and `security` (57,762).
Routing is case-insensitive, and the `channel` column stores the canonical spelling (`Security`,
`Microsoft-Windows-Sysmon/Operational`) so queries such as `WHERE channel = 'Security'` see every
row. The original spelling remains in `raw_events.json`.

## PIDs

| Situation | Measured | Rule |
|---|---|---|
| `ProcessId` present (Sysmon; Security events in hex `0x…`) | most events | used; hex converted to an integer |
| nxlog event with a `ProcessID` field (no `ProcessId`) | e.g. 5156 with `ProcessID: 4` whose Message says `Process ID: 716` | `ProcessID` is the **writer's** PID and is **never** used; the PID is read from the event's own `Message` (`ProcessId: N` for Sysmon, `Process ID: N` for 5156) |
| Non-nxlog 5156 with `ProcessID` | 5156's real field name | used |
| nxlog Sysmon event without `ProcessId` and with an empty `Message` | 448 Sysmon 1 events, plus registry/file events | **NULL**. `ExecutionProcessID` (Sysmon's own PID) is never used |

231 PIDs were recovered from `Message` across all windows. Remaining NULL rates: `process_create.pid`
13.2%, `registry.pid` 26.7%, `file.pid` 23.3%, `network.pid` 2.5%; `process_access.source_pid` 0%.
A NULL PID can never satisfy C1's typed-argument rule or C3's role check, which is the safe
direction: an action on such a process must cite a record that does carry the PID.

## Users (acting user for C3)

Normalisation (§F.2): lower-case, `DOMAIN\` stripped; additionally `-` and empty become NULL.

| Table | Acting-user column | Source | NULL rate |
|---|---|---|---|
| `process_create` | `user` | Sysmon 1 `User`; 4688 `TargetUserName`, falling back to `SubjectUserName` when it is `-` or absent | 1.0% |
| `process_access` | `user` | Sysmon 10 `SourceUser` (**only in newer Sysmon versions**); 4656/4663 `SubjectUserName` | **57.4%** |
| `network` | `user` | Sysmon 3 `User`; 5156 has no user field | 74.9% |
| `registry` | `user` | Sysmon 12/13/14 `User` (often absent); 4657 `SubjectUserName` | 80.2% |
| `file` | `user` | Sysmon 11/23 `User` (often absent); 4663 `SubjectUserName` | 65.1% |
| `logon` | `target_user` for 4624/4625; `subject_user` for 4648 | `TargetUserName` / `SubjectUserName` | `target_user` 0%; `subject_user` 67.6% (often `-` on network logons) |
| `share_access` | `subject_user` | `SubjectUserName` | 0% |

**Consequence for case authoring:** for `disable_account(U)`, C3 needs a cited record whose acting
user is U. In many windows only `process_create` and Security records carry a user; annotators must
cite one of those.

## Routing and field differences from §F.2

| Table | Source | Differences from the plan's target schema |
|---|---|---|
| `process_create` | Sysmon 1 | as planned |
| | 4688 | `pid` = `NewProcessId`, `ppid` = `ProcessId` (the creator); `parent_command_line` and `hashes` NULL (not logged); `integrity_level` from `MandatoryLabel` SID (S-1-16-12288 → High, etc.) |
| `process_access` | Sysmon 10 | as planned; `user` often NULL (above) |
| | 4656/4663 | only when `ObjectType = Process` (64 + 43 events); `source_*` = `ProcessName`/`ProcessId`; `target_image` = `ObjectName`; **`target_pid` NULL** (not logged); `granted_access` = `AccessMask`; `call_trace` NULL |
| `network` | Sysmon 3 | `direction` from `Initiated` (true → outbound) |
| | 5156 | `image` = `Application` (a `\device\harddiskvolumeN\…` path, not `C:\`); `direction` from `%%14592/%%14593` → inbound/outbound; `protocol` is the IANA number (6, 17); `user` NULL |
| `registry` | Sysmon 12/13/14 | `details` = `Details` (13) or `NewName` (14) |
| | 4657 | `target_object` = `ObjectName\ObjectValueName`; `event_type` = `OperationType` code (`%%1904–1906`); `details` = `NewValue` |
| `file` | Sysmon 11/23 | `event_type` = `FileCreate` / `FileDelete` (Sysmon logs no event type) |
| | 4663 | only when `ObjectType = File` (19 events); `event_type` = `AccessList` code (e.g. `%%4417`) |
| `logon` | 4624/4625/4648 | 4648: `logon_type` and `workstation` NULL (not logged; `TargetServerName` stays in raw JSON) |
| `share_access` | 5140/5145 | `relative_target` only in 5145 (33.8% NULL overall) |

Security 4656/4663 events on other object types (`Key` 16,238; `SC_MANAGER OBJECT` 462; `SERVICE
OBJECT` 108; SAM objects 80; …) are **not** mapped (§F.2 names process and file objects only) and
stay in `raw_events`. Unmapped event IDs (e.g. Sysmon 7, Security 4658/4689/4703/1102, PowerShell)
are likewise kept in `raw_events` only.

## Rows per table and event ID (all 99 windows)

| Table | Event | Rows | Windows |
|---|---|---|---|
| process_create | Sysmon 1 | 1,484 | 91 |
| | 4688 | 1,920 | 92 |
| process_access | Sysmon 10 | 236,301 | 99 |
| | 4656 (process) | 64 | 22 |
| | 4663 (process) | 43 | 18 |
| network | Sysmon 3 | 3,346 | 80 |
| | 5156 | 9,902 | 95 |
| registry | Sysmon 12 | 146,904 | 99 |
| | Sysmon 13 | 63,515 | 99 |
| | Sysmon 14 | 1 | 1 |
| | 4657 | 115 | 7 |
| file | Sysmon 11 | 6,206 | 98 |
| | Sysmon 23 | 1,933 | 61 |
| | 4663 (file) | 19 | 2 |
| logon | 4624 | 1,125 | 78 |
| | 4625 | 25 | 6 |
| | 4648 | 64 | 13 |
| share_access | 5140 | 132 | 56 |
| | 5145 | 259 | 49 |

## Archive contents and skipped lines

- Host files referenced by the metadata are `.zip` (111, one of them missing) and `.tar.gz` (2);
  the reader also accepts a plain `.json` file. Only `.json` members are read (`.cap` is ignored).
- 14 zips contain macOS debris (`__MACOSX/._*.json` resource forks). These are not event data and
  are excluded, not counted as skipped lines. With that, **0 lines were skipped** in the real data.
  (The 1% `ingest_warning` rule remains for malformed or non-object lines; the mini fixture tests it.)

## Hosts

`hosts` = distinct `Hostname` (or `Computer`) values, most frequent first. 36 windows have one
host; 63 have 2–5 (lab collectors gathered several machines, e.g. a workstation and a DC). The
"single primary host" eligibility rule of T1.5 must take this into account. Host names are stored as
written (e.g. `WORKSTATION5` in some windows, `WORKSTATION5.theshire.local` in others).

## Missing Host file

SDWIN-230718150800 ("Dumping NTDS.dit from Volume Shadow Copy") names
`cmd_copy_ntds_from_volume_shadow_copy.zip`, which does not exist at `d9d40ef`. It is catalogued with
`ingest_status = missing_host_file` and has no database. An unreferenced file with a similar name
(`cmd_dumping_ntds_dit_file_volume_shadow_copy.zip`) exists, but mapping it would be a guess; this is
a team question.
