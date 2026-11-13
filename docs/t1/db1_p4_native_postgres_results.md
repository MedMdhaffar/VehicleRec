# P4 native PostgreSQL validation — provisioning pending, 2026-10-10

**No real PostgreSQL test has executed in this task.** Native server binaries are
missing; user-run privileged installation is required before dedicated cluster
setup. No fixture skip is claimed as success. No native cluster/database/roles have
been created or started, so no server stop or database cleanup was necessary.

Inspected actual adapter/documentation, proposed receipt SQL, gated fixture/README,
prior P4 report, and supervisor archive read-only. The supervisor checker was not
invoked. No Docker/container/socket access was attempted. No privileged installation,
repository/service configuration change or shared/production connection occurred.

## Executed environment inspection

- OS: Ubuntu **22.04.5 LTS**, codename **jammy**; architecture **amd64**.
- User: **intern**, UID/GID 1001, suitable for a separately owned native cluster.
- Existing VM Python **3.11.15**, psycopg **3.3.4**, environment `/home/intern/.venv`.
- `postgres`, `psql`, `initdb`, `pg_ctl` not available on PATH. Standard server
  directories `/usr/lib/postgresql`, `/usr/local/pgsql`, `/opt/postgresql` absent.
  No binaries found in the user's local tooling/project file search.
- `apt-cache policy postgresql-16 postgresql-client-16 postgresql-common`: no
  candidate for version 16 packages in configured repositories; postgresql-common
  is not installed, available candidate 238 from Ubuntu Jammy.
- No PostgreSQL/PGDG APT source found in current sources.

The [official PostgreSQL Ubuntu instructions](https://www.postgresql.org/download/linux/ubuntu/)
confirm Jammy/amd64 support and the signed PGDG repository configuration. The
[postgresql-common manual](https://manpages.debian.org/bookworm/postgresql-common/pg_createcluster.1.en.html)
documents package installation's default main-cluster creation and the
`create_main_cluster=false` control.

## Prepared exact user-run installation

Review `scripts/install_p4_native_postgres16.sh`, then run as intern:

```bash
cd /home/intern/part-time/aziz/pipeline
bash scripts/install_p4_native_postgres16.sh
```

The script contains the exact native sudo/apt/curl/repository commands for this VM.
It adds the official signed `jammy-pgdg` APT source, refreshes package metadata,
prints the version 16 candidates and installs `postgresql-16`/`postgresql-client-16`.
It installs curl/CA certificates/postgresql-common as prerequisites. It uses a private
backup of createcluster.conf, suppresses automatic main-cluster creation during
server installation and restores that config afterward, including ordinary failure
exit. A failed restore retains its backup and reports its path. No password or
other credential is included in the script or command invocation.

Side effects requiring user execution: APT repository/key/metadata changes, package
and dependency installation/updates, postgres system account and service units from
postgresql-common/server packaging. Packaging can activate the generic PostgreSQL
service infrastructure, but automatic **16/main data-directory initialization is
suppressed**. The repository remains for future package updates. The script does
not explicitly start/stop/mask existing services or clusters. It inspects versions
and pg_lsclusters afterward. A hard interruption/power loss can prevent the shell
trap from restoring the configuration; its scoped backup can then be restored
manually. No installation command in this script has been executed by the agent.

## Resume scope after binaries are available

Use `/usr/lib/postgresql/16/bin/initdb`, `pg_ctl` and `postgres` explicitly. Create a
new intern-owned directory under scoped `local_output/p4_native_postgres/`, with
private data/socket/log directories and an available nonstandard loopback TCP port.
No existing data directory or system-managed cluster will be used. Authentication
will be confined to the new cluster, with privately generated credentials passed
through protected files/process environment, not command arguments or report/logs.

Verify server major, data directory/cluster identity and loopback endpoint before
setting fixture opt-in. Inspect effective fsync/full_page_writes/synchronous_commit
and retain them enabled. Set log redaction so intentional test errors cannot expose
raw plate text or credentials. Run the exact user-requested command with privately
provided DB1_P4_ISOLATED and DB1_P4_ADMIN_DSN, verify normal-role application execution
and scoped database/role cleanup, resolve reproduced failures, run affected checks,
and stop only this dedicated server cleanly. Retain scoped logs and update this
report with actual results/versions/commands once execution is possible.

The proposed supporting schema remains unapproved for deployment. No Redis,
manufacturer production, model execution, uploads, production connections, commit
or push. P3a fixes and P3b1 format/compatibility behavior remain unchanged.

## Checks executed before the installation gate

`bash -n scripts/install_p4_native_postgres16.sh` passed (syntax only, no commands
executed). Whitespace checks of both new files and `git diff --check` passed.
`sha256sum -c /tmp/p4-supervisor.sha256` verified the unchanged archive;
`sha256sum -c /tmp/p4-fixtures.sha256` verified all eight fixture files unchanged.
Archive SHA256 is
`62736bf6550fd892e5065b9f0f14536bbefa51428b9a08c3c73c94a251e41c91`.
The integration command was not invoked: **0 tests executed**, with no test-run
pass/failure/skip outcome. Server versions, effective durability, normal-role/RLS/
concurrency outcomes and fixture cleanup remain untested pending installation.
