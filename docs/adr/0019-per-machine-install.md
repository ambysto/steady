# ADR-0019: Install for all users under Program Files

- **Status:** Accepted
- **Date:** 2026-10-07
- **Related:** [ADR-0005](0005-unelevated-server-uac-writes.md), [ADR-0018](0018-backups-in-hklm.md), [SECURITY.md](../SECURITY.md), SIC-97

## Context

Until 0.5.0 the app installed for the current user, without Admin rights, into `%LOCALAPPDATA%\Programs\Ambysto Steady` (SIC-32). Every elevated operation (ADR-0005) runs the packaged exe from that folder, and so does the monitor task when the user gave it "highest privileges" (`HighestAvailable`, which then runs elevated at every logon without a prompt). A process of the user without Admin rights can replace any file in that folder, for example a module in `_internal`, and its code then runs elevated at the next UAC prompt, or at the next logon with no prompt at all. ADR-0017 and ADR-0018 protect the backups the helper restores, but not the helper itself; ADR-0018 lists this as the remaining risk.

Only a folder that a process without Admin rights cannot write closes that. `Program Files` is such a folder (its ACL grants Users read and execute only).

## Decision

1. **The app installs for all users under `Program Files`** (`<FOLDERID_ProgramFiles>\Ambysto Steady`, found with `SHGetKnownFolderPath`, not from an environment variable a user can set). Installing needs one UAC prompt.
2. **Install is split by who owns what.** With "over-the-shoulder" UAC (a standard user, an administrator's password) the elevated process runs as *another account*, with another profile, so it must not touch per-user things.
   - Elevated, through the helper (`install-machine <caller pid>`): stop copies running from the target (sparing the installer that asked), end and remove a monitor task that runs anything but the exe in the target (a per-user install's task may have highest privileges and point at a folder anyone can write), replace the program folder with the copy the helper itself runs from (the helper copies *its own* folder; it never takes a source path from its caller), the "Apps & features" entry in `HKLM`, the Start menu shortcut for all users.
   - Not elevated, in the installer the user started: the monitor task, registered for the new exe *before* an earlier copy's folder is deleted, cleaning up a per-user install of an earlier version, the sign-in (tray) shortcut in this user's Startup folder, starting the monitor and the window from the new folder.
3. **Upgrading from a per-user install.** When the user's registry still has the old "Apps & features" entry, the installer stops that copy, removes its entry and Start menu shortcut, and deletes its folder (only one that holds the app's exe, as before). The data folder (`%LOCALAPPDATA%\StableInternet`) and the backup store (ADR-0018) are not touched, so measurements, settings and backups carry over.
4. **Uninstall** (from "Apps & features", whose entry now lives in `HKLM`): not elevated, it stops this user's copies; one elevated operation (`uninstall-machine <caller pid>`) stops the copies of other users too, restores every tweak and failover metric (as `restore-all` did), and only then removes the `HKLM` entry and the all-users shortcut and deletes the program folder once both the helper and the uninstaller have exited. Back in the uninstaller, the monitor task and this user's sign-in shortcut are removed. If restoring fails or UAC is declined, nothing is removed and the monitor is started again.
5. **The elevated paths only run from a protected folder.** In the packaged build, an exe that is not in `<Program Files>\Ambysto Steady` does not ask for UAC to change settings (it says the app must be installed first): `run_elevated` refuses every operation except `install-machine`, and the helper refuses them too. The monitor task is only registered with highest privileges for an exe in that folder. Running from source is unchanged (development).
6. **PowerShell run by the app loads Windows' modules only.** `-NoProfile` does not stop module autoloading, and `PSModulePath` starts with the user's `Documents\WindowsPowerShell\Modules`, which a process without Admin rights can fill; a module there exporting a cmdlet the app uses would run with the caller's rights. Every script the app runs therefore sets `PSModulePath` to `$PSHOME\Modules` and the all-users `Program Files\WindowsPowerShell\Modules` as its first statement, and `powershell.exe` is started by its full path in the System folder.

## Consequences

- ✅ A process without Admin rights can no longer change the code that runs elevated, at a UAC prompt or at logon. With ADR-0017 and ADR-0018, the elevated side trusts nothing a standard user can write.
- ✅ Measurements, settings and backups survive the move from a per-user install.
- ⚠️ Installing now needs Admin approval once. A user with no administrator at hand can still run the app from the unzipped folder to monitor and diagnose, but not to change Windows settings.
- ⚠️ Other users of the PC see the app in their Start menu, but there is one monitor task per PC (by name), running for the user who installed (or reinstalled) last, and the tray shortcut exists only for that user; another user who starts the app gets the window, not the 24/7 monitor. Uninstalling removes the task and the uninstalling user's shortcut.
- ⚠️ A per-user install's task with highest privileges is removed by the elevated install step and registered again without them (raising it needs Admin); the user turns it on again where the app offers it.
- ⚠️ The UAC prompt of `install-machine` runs the exe from the folder the user unzipped it to, which any process of the user can change. That is true of every installer that is started from a download; what this ADR closes is everything *after* installing.
- ⚠️ Not changed here: the helper writes its result to `%LOCALAPPDATA%\StableInternet\results` of the account it runs as. With over-the-shoulder UAC that is not the caller's profile, so the caller gets no result (ADR-0005). The installer therefore does not rely on the result for `install-machine`: when the result is missing it checks the effect itself (the `HKLM` entry names the target folder and the exe is there) and carries on with the per-user part. Other elevated operations keep reporting "no result" in that setup.
