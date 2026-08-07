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
- `logs\collector-YYYY-MM-DD.jsonl` is the append-only structured log.
- Dry-run does not create or change the database.
- An uncertain POST result is marked `unknown` and is never retried automatically.
- Only canonical single-license commands matching `!ALA a/<id>` or `!ALA s/<id>` can reach the IPC client.
- Generic commands embedded in Keylol Steam widget JavaScript are ignored.

## Task Scheduler

After tests, live dry-run, and read-only ASF health validation pass, register the tasks:

```powershell
.\scripts\Register-Tasks.ps1
```

This registers two triggers for each task:

- ASF starts at logon and daily at 08:55 local time.
- The collector starts 30 seconds after logon and daily at 09:00 local time.

Both tasks use `StartWhenAvailable` and `WakeToRun`, so a sleeping PC can wake for the daily run and a missed start is attempted when Windows can run it. A powered-off PC cannot wake, but the next logon trigger provides the catch-up run. The collector task is deliberately disabled initially. Inspect it with:

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
