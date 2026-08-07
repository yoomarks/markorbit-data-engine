# M1.5.3.1 Deterministic Validation

M1.5.3.1 removes the worker race from development validation and adds a non-empty two-package runtime fixture.

Validation order is frozen:

1. reset database volumes with the worker stopped;
2. empty-package contract preflight;
3. non-empty runtime fixture;
4. real ZIP import;
5. start the scheduled worker only after the real import is accepted.

The runtime fixture executes production `_publish()` twice. The second package changes the owner of an existing case so the party touched/supersession path runs with real rows rather than merely compiling on an empty package.

Fixture coverage:

- normal direct CN case;
- multi-class case;
- owner and co-owner;
- agent and priority data;
- active and inactive goods observations;
- Madrid-designation CN root and `A` derived case;
- derived-case relation and scope carve-out;
- party replacement/supersession history and event;
- permanent lineage values.

Commands:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\reset-m15.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-cn-contract.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-cn-fixture.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-cn.ps1
```

After the real import is accepted:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\start-worker.ps1
```
