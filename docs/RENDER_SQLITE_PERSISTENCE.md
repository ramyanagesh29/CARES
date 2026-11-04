# CARES SQLite persistence on Render

CARES stores accounts, sessions, guardian contacts, monitoring history, and location records in SQLite.
The server supports the `CARES_DB_PATH` environment variable. If this points to Render's ephemeral
filesystem, the database can disappear after a restart/redeploy, and users may then see
`Invalid credentials` because their account record is no longer present.

## Render configuration

1. In Render, open the CARES Web Service.
2. Add a **Persistent Disk** (available only on plans that support it).
3. Choose a mount path such as `/var/data`.
4. In Environment, set:
   - `CARES_DB_PATH=/var/data/cares.sqlite3`
5. Save and redeploy.
6. Register a test account, log out, and log in again.
7. Restart/redeploy once and verify the same account still exists.

Do not point `CARES_DB_PATH` at a persistent disk until the disk is mounted at that exact path.
Do not delete or replace the existing database to troubleshoot login. Back it up first.

If the current Render plan does not provide persistent disks, use an external persistent database
or a hosting plan with persistent storage. A code change alone cannot preserve SQLite data on an
ephemeral filesystem.

## Local testing

PowerShell:
```powershell
$env:CARES_DB_PATH = "data/cares.sqlite3"
python -m backend.server
```
