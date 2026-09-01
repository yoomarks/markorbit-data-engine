# Docker Desktop SIGBUS after WSL no-arg unmount — 2026-09-01

Status: active safety incident; Full Acceptance V3 remains frozen.

## Target-host evidence

- Docker Desktop WSL data root is customized to `D:\DockerData\DockerDesktopWSL` (`CustomWslDistroDir` / `wslDataFolder`).
- Default `%LOCALAPPDATA%\Docker\wsl\...` VHDX candidates are absent, consistent with the custom location.
- Immediately after the guarded no-argument WSL unmount recovery, the prior `mo_hot_cn_spike` 1 GiB orphan is no longer present in Ubuntu `lsblk`.
- Docker Desktop then reported `service containerd failed` with `fatal error: unexpected signal during runtime execution` and `SIGBUS: bus error` at 2026-09-01T13:24:55Z.
- Historical Docker backend logs from 2026-08-31 reported `WSL2 disk exists` and the same custom `wslDataFolder`.

## Safety interpretation

The prior recovery gate proved there were no `/mnt/wsl/*` external mountpoints, but that is not sufficient evidence that no Docker-managed WSL block device is attached. A no-argument `wsl --unmount` has broader detach semantics than the gate modeled. Until the Docker VHDX is located, cold-inspected, and Docker Desktop is recovered without data loss, no further no-argument WSL unmount and no Full Acceptance V3 are authorized.

## Prohibited until incident close

- no `wsl --unmount` without an explicit disk path;
- no `wsl --shutdown` as a recovery shortcut;
- no Docker Desktop factory reset or reinstall;
- no Docker prune/volume deletion;
- no VHDX mount/dismount/format/delete;
- no CN corpus replay or 751 GB recopy;
- no Full Acceptance V3.

Next evidence gate: enumerate `D:\DockerData\DockerDesktopWSL`, identify all VHDX files and sizes/timestamps/attachment state, then take a cold backup before attempting Docker Desktop restart if practical.