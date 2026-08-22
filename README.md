# Steam Freebie Collector

A small Windows automation tool that reads the Steam section of the fixed Keylol giveaway index, extracts only authored ASF add-license commands, deduplicates them persistently, and can submit safe reconstructed commands through ASF's localhost IPC API.

The tool never uses GUI automation, never reads ASF account credentials, and never executes arbitrary commands copied from a webpage.

## Setup

From Windows PowerShell 5.1 in this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the offline tests first:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Then perform a live read-only scrape. Dry-run creates a JSONL run log but does not create the SQLite state database and does not contact ASF:

```powershell
.\.venv\Scripts\python.exe -m steam_freebie_collector run --mode dry-run
```

## Modes and review commands

```powershell
# Save candidates without sending them
.\.venv\Scripts\python.exe -m steam_freebie_collector run --mode review

# Automatically submit only new entries explicitly marked currently claimable
.\.venv\Scripts\python.exe -m steam_freebie_collector run --mode automatic

# One-off end-to-end deployment validation (bypasses only the cycle guard)
.\.venv\Scripts\python.exe -m steam_freebie_collector run --mode automatic --validate-once

# Review state
.\.venv\Scripts\python.exe -m steam_freebie_collector review list
.\.venv\Scripts\python.exe -m steam_freebie_collector review approve 1
.\.venv\Scripts\python.exe -m steam_freebie_collector review approve all
.\.venv\Scripts\python.exe -m steam_freebie_collector review reject 1
.\.venv\Scripts\python.exe -m steam_freebie_collector review retry 1
.\.venv\Scripts\python.exe -m steam_freebie_collector history --limit 20

# Read-only ASF health check
.\.venv\Scripts\python.exe -m steam_freebie_collector health
```

If an ASF IPC password is configured later, expose it only to the collector process:

```powershell
$IpcCredential = Get-Credential -UserName 'ASF IPC' -Message 'Enter the ASF IPC password'
$env:STEAM_FREEBIE_ASF_IPC_PASSWORD = $IpcCredential.GetNetworkCredential().Password
```

For persistent unattended use, set the environment variable through an appropriately protected user-level mechanism. Do not add it to `config.toml` or the task arguments.

## State and safety

- `data\collector.sqlite3` is the deduplication and attempt-history database.
- SQLite also owns the atomic per-cycle lease. A successful scheduled run marks its `YYYY-MM-DD` cycle completed; failures release the lease for a same-cycle retry.
- `logs\collector-YYYY-MM-DD.jsonl` is the append-only structured log.
- Dry-run does not create or change the database.
- An uncertain POST result is marked `unknown` and is never retried automatically.
- Only canonical single-license commands matching `!ALA a/<id>` or `!ALA s/<id>` can reach the IPC client.
- Authored add-license arguments may use ASF's typed forms (`a/`, `app/`, `s/`, `sub/`) or a positive bare ID; every bare ID is normalized independently as `sub`, and raw webpage commands are never forwarded.
- A narrowly recognized Keylol `复制ASF代码`/`複製ASF代碼` widget may supply an AppID only when the original post has no valid authored command. Its static href and fixed `setCopy` structure must agree; JavaScript is never executed or logged, and the result is rebuilt as a canonical `!ALA a/<id>` command.

## Task Scheduler

After tests, live dry-run, and read-only ASF health validation pass, register the tasks:

```powershell
.\scripts\Register-Tasks.ps1
```

This registers one Collector task with two triggers:

- The collector starts 30 seconds after logon and daily at 21:00 local time.

The operational day runs from 21:00 through the following 20:59:59. A daytime logon therefore belongs to the cycle that began at 21:00 on the preceding calendar day. The scheduled command is:

```powershell
.\.venv\Scripts\python.exe -m steam_freebie_collector run --mode automatic --scheduled
```

It acquires an atomic SQLite lease before any Keylol or ASF access. A later trigger in a successfully completed cycle logs `cycle_already_completed` and exits without fetching the site or starting ASF. Failed runs release the lease, and a stale lease from a crashed process can be recovered after the configured timeout.

ASF is no longer a standalone scheduled process. When a scheduled run has a new command to submit, the collector first checks IPC. It starts the configured ASF executable only if needed, remembers that exact process, and requests graceful shutdown afterward. A pre-existing/manual ASF instance is left running. The lifecycle-only `!exit` request is fixed in code and is isolated from the strict webpage-command allowlist.

The task uses `StartWhenAvailable`, `WakeToRun`, failure retries, and `MultipleInstances IgnoreNew`. A powered-off PC cannot wake, but the next logon trigger provides the catch-up run. Registration is idempotent and removes the legacy `ArchiSteamFarm` scheduled task if present. The collector task is deliberately disabled initially unless `-EnableCollector` is supplied. Inspect it with:

```powershell
.\scripts\Get-TaskStatus.ps1 -CheckAsf
```

Enable automatic mode only after inspection:

```powershell
.\scripts\Enable-Collector.ps1
```

Remove both tasks with:

```powershell
.\scripts\Unregister-Tasks.ps1
```

`Unregister-Tasks.ps1` also removes the legacy standalone ASF task if an older installation still has it. Manual `dry-run`, `review`, `automatic`, approval, retry, history, health, and cycle diagnostic commands do not use the scheduled cycle guard.

## One-off validation

Run this exact command from the project directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m steam_freebie_collector run --mode automatic --validate-once
```

`--validate-once` is an explicit deployment check. It starts and verifies ASF before fetching Keylol, then performs ordinary automatic processing with the same strict command validation and persistent license deduplication. It never reads or writes operational-cycle records, so it cannot consume or suppress a 21:00 run. It shuts down only an ASF process that it started; a pre-existing ASF instance remains running.
