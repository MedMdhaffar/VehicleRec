#!/usr/bin/env bash
# USER-RUN ONLY: privileged native package provisioning, never run by the agent.
set -euo pipefail

. /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 22.04 || "$(dpkg --print-architecture)" != amd64 ]]; then
    echo 'This reviewed script is for Ubuntu 22.04 amd64 only.' >&2
    exit 1
fi
if [[ -e /etc/apt/sources.list.d/pgdg.sources ]]; then
    echo 'pgdg.sources already exists; inspect it before choosing installation commands.' >&2
    exit 1
fi

# postgresql-common installs the postgres account and system service infrastructure.
# No server package is installed at this stage, so there is no new main cluster.
sudo apt-get update
sudo apt-get install --no-install-recommends curl ca-certificates postgresql-common
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl --fail --show-error --location \
    --output /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc
sudo tee /etc/apt/sources.list.d/pgdg.sources >/dev/null <<'EOF'
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: jammy-pgdg
Architectures: amd64
Components: main
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
EOF
sudo apt-get update
apt-cache policy postgresql-16 postgresql-client-16

# Suppress package-triggered 16/main initialization. Restore this file on exit;
# don't stop, mask or change existing cluster/service configuration.
p4_pg_config=/etc/postgresql-common/createcluster.conf
p4_pg_backup=$(mktemp /tmp/db1p4-createcluster.XXXXXXXX)
sudo cp -- "$p4_pg_config" "$p4_pg_backup"
p4_restore_config() {
    if sudo cp -- "$p4_pg_backup" "$p4_pg_config"; then
        rm -f -- "$p4_pg_backup"
    else
        echo "Configuration restoration failed; backup retained at $p4_pg_backup" >&2
        return 1
    fi
}
trap p4_restore_config EXIT
sudo sed -i -E '/^[[:space:]]*create_main_cluster[[:space:]]*=/d' "$p4_pg_config"
printf '\ncreate_main_cluster = false\n' | sudo tee -a "$p4_pg_config" >/dev/null
sudo apt-get install --no-install-recommends postgresql-16 postgresql-client-16
p4_restore_config
trap - EXIT

/usr/lib/postgresql/16/bin/postgres --version
/usr/lib/postgresql/16/bin/initdb --version
/usr/lib/postgresql/16/bin/pg_ctl --version
# Inspection only; do not start a system-managed cluster.
pg_lsclusters
