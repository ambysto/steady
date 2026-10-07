# ADR-0017: The elevated helper validates every backup entry against the tweak's own domain before restoring it

- **Status:** Accepted (amended by [ADR-0018](0018-backups-in-hklm.md), which takes option 1)
- **Date:** 2026-10-07

## Context

The elevated helper ([ADR-0005](0005-unelevated-server-uac-writes.md)) restores original values from `backup.json` when a tweak is turned off, when failover switches back, and on uninstall (`restore-all`). `backup.json` lives in the user's data folder, which any process of that user can write **without Admin rights**. The helper therefore acted as a confused deputy: the user approves a UAC prompt for "turn off tweak X", and the helper writes whatever the file says.

- `RegistryDwordTweak.restore` wrote a DWORD to (or deleted a value from) an HKLM path and value name taken from the file: any HKLM DWORD on the machine.
- `DnsFastestTweak.restore` set the IPv4 DNS servers of any interface index, and DoH templates (URLs) for the six candidate addresses, from the file.
- The other kinds took the adapter name, property keyword, binding component, `netsh` / offload setting name, power-plan GUIDs and failover's interface index from the file too.

[ADR-0015](0015-tweak-value-from-measurement.md) and [ADR-0016](0016-measured-tweaks.md) already clamp calibration values the helper reads from user-writable files. Two fixes were considered:

1. **Store backups where only the elevated side can write** (an Admin-only ACL in ProgramData, or `HKLM\SOFTWARE`), and migrate existing `backup.json` entries.
2. **Validate every restore value** against what the tweak itself could have captured, right before restoring it.

## Decision

1. **Every tweak kind implements `check_original(system, original)`.** It raises `ValueError` unless `original` has exactly the shape its `capture()` produces **and** every field lies in the tweak's own domain, checked against the machine now and the tweak's declaration, never against the file:
   - fixed fields must equal the declaration: registry value name, power-plan subgroup and setting, binding component, `netsh` and offload setting names;
   - fields the tweak resolves on the machine must equal what it resolves now: the registry path (e.g. the Wi‑Fi card's class key), the Wi‑Fi adapter name, the advanced-property keyword (exactly the `RegistryKeyword` of the property the tweak resolves on this card, by keyword or by name; `WakeOnMagicPacket` and `*WakeOnMagicPacket` are two registry values);
   - values must be ones the setting accepts: a DWORD (or "not set"), a power index in the setting's range (`valid` per tweak), a registry value from the property's `ValidRegistryValues`, one of `TCP_GLOBAL_VALUES` / `OFFLOAD_VALUES`, booleans for flags;
   - `upload_shaping` ([ADR-0016](0016-measured-tweaks.md)): only the tool's own QoS policy name, at a rate within the bounds the tweak itself derives (1–1000 Mbps) or "no policy", and LAN exemptions only from its own `StableInternet-Upload-Local1`…`Local6` names, each at most once (backups from before the exemptions have none);
   - `mtu_pmtu`: the fields its capture writes (`guid`, `interface_index`, `alias`, `mtu`), a well-formed InterfaceGuid, and an MTU of 1280–1500, the only range `derive()` lowers from;
   - `dns_fastest`: DoH entries only for the six candidate addresses, with each provider's own template (any other URL is refused; restore always writes the built-in template); a static server list of 1–8 unicast IPv4 addresses (not unspecified, multicast, broadcast or reserved; loopback stays allowed for local resolvers); the interface GUID, when present (backups older than it have none), must be a well-formed InterfaceGuid (restore finds the interface by it), and the index a positive integer.
2. **`TweakManager.disable` calls it before `restore`.** A refused entry (or one that is not an object with an `original`) restores nothing, keeps the backup (the user may still want it) and is reported as `tweak.result.backup_rejected` with level `bad`. `adopt_backup` uses the same check instead of the old shape-only `validate_original`.
3. **Failover's `MetricSwitch.restore`** checks its entry the same way: the interface index comes from a `path:<digits>` key, the original is `{"automatic": true}` or a metric in 1–9999.
4. Option 1 is not taken now. It needs a migration that would have to trust the user-writable file once anyway (so it needs this validation regardless), and it adds machine state that the uninstaller must clean. It stays possible later on top of this ADR.

## Consequences

- ✅ A tampered `backup.json` can no longer make the helper write outside each tweak's own setting: no arbitrary HKLM path or value name, no other adapter or property, no other power setting, no DoH template pointing anywhere but the provider's own.
- ✅ No migration: existing backups that a real capture wrote pass the check unchanged.
- ⚠️ **Residual risk, `dns_fastest`:** the user's original static DNS servers can be any address, so a forged entry can still choose which unicast IPv4 servers come back (or DHCP) when the user approves turning `dns_fastest` off (or uninstalls). It can also choose **which interface** gets them: restore finds the interface by the backup's GUID, and only the GUID's format is checked.
- ⚠️ **Residual risk, `mtu_pmtu`:** the same holds for the interface, and the MTU can be anything in 1280–1500.
- Both residual risks need backups the user cannot write (option 1). ADR-0018 (proposed in ambysto/steady#33) does that for new backups.
- ⚠️ A backup made on another Wi‑Fi card (the card was replaced since) or for a property the driver no longer lists is refused instead of restored onto a card that is gone; the backup is kept and the message says why.
- ⚠️ Adding a tweak kind now means writing its `check_original`; the base class refuses everything, so a missing one fails closed.
