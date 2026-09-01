# Docker Desktop SIGBUS after WSL no-arg unmount — 2026-09-01

Status: operationally recovered; no-argument WSL unmount remains permanently prohibited. Full Acceptance V3 may resume only after the kill-switch PR is merged and a fresh read-only preflight confirms production safety.

## Target-host evidence

- Docker Desktop WSL data root is customized to `D:\DockerData\DockerDesktopWSL` (`CustomWslDistroDir` / `wslDataFolder`).
- Default `%LOCALAPPDATA%\Docker\wsl\...` VHDX candidates are absent, consistent with the custom location.
- Immediately after the guarded no-argument WSL unmount recovery, the prior `mo_hot_cn_spike` 1 GiB orphan disappeared from Ubuntu `lsblk`.
- Docker Desktop then reported `service containerd failed` with `fatal error: unexpected signal during runtime execution` and `SIGBUS: bus error` at 2026-09-01T13:24:55Z.
- Historical Docker backend logs from 2026-08-31 reported `WSL2 disk exists` and the same custom `wslDataFolder`.

## Cold-backup evidence

Docker processes were fully stopped before backup. The following source VHDX files were all detached from the Windows DiskImage view and copied byte-for-byte by file length to `E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery`:

- `disk\docker_data.empty.vhdx`: 1,617,952,768 bytes;
- `disk\docker_data.vhdx`: 852,253,212,672 bytes;
- `main\ext4.vhdx`: 109,051,904 bytes.

`robocopy` completed with exit code 1, 3 files copied, 0 failed, 795.331 GiB total copied. Source and backup file lengths matched for all three VHDX files.

## Recovery evidence

After the cold backup, Docker Desktop was started normally without reset, reinstall, prune, VHDX mutation, or manual mount/unmount.

- `docker-desktop` returned to `Running` under WSL2;
- Docker Desktop 4.85.0 / Engine 29.6.2 responded normally;
- containerd v2.2.5 responded normally;
- production `markorbit-data-engine-clickhouse-1` returned healthy;
- production `markorbit-data-engine-postgres-1` returned healthy;
- accepted volume `markorbit-data-engine_clickhouse_data` remained present at `/var/lib/docker/volumes/markorbit-data-engine_clickhouse_data/_data`;
- ClickHouse `SELECT 1` returned `1`;
- ClickHouse version remained `24.8.14.39`;
- database inventory still contained `markorbit_facts` with 44 tables.

Windows `Get-DiskImage` still reported the Docker data VHDX as `Attached=False` while Docker Desktop was healthy and using its data. Therefore Windows DiskImage attachment state is not an authoritative ownership/safety probe for Docker Desktop WSL storage and must not be used to authorize a global WSL detach operation.

## Safety interpretation

The prior recovery gate proved there were no `/mnt/wsl/*` external mountpoints, but that was insufficient evidence that no Docker-managed WSL block device was in use. The temporal sequence is consistent with the no-argument `wsl --unmount` detaching storage that containerd was using, followed by SIGBUS. Operational recovery without data loss strongly supports a transient detach incident rather than persistent Docker data corruption, but the exact internal Docker attachment mechanism is not treated as proven.

The architectural conclusion is permanent: no `wsl --unmount` without an explicit disk path is authorized by this repository. The former recovery operator is retained only as a fail-closed receipt/kill-switch and must contain no executable no-argument unmount primitive.

## Permanent prohibitions

- no `wsl --unmount` without an explicit disk path;
- no `wsl --shutdown` as a disk-recovery shortcut;
- no Docker Desktop factory reset or reinstall as part of #410;
- no Docker prune/volume deletion;
- no destructive VHDX mutation;
- no CN corpus replay or 751 GB recopy for this incident.

## Resume gate for #410

Full Acceptance V3 is no longer blocked by Docker data recovery itself. It may resume only after:

1. the no-argument unmount kill-switch is merged to exact `main`;
2. Docker Desktop / production ClickHouse / accepted volume are rechecked read-only on that exact main;
3. the V3 operator uses only path-specific, ownership-proven spike-disk mount/unmount primitives and cannot invoke a global WSL detach.
