import re
import sys
import unittest
from pathlib import Path

from app import config, i18n, tweaks
from app.tweaks import TweakManager
from tests.test_tweaks import MemoryBackup, FakeSystem, prop

DOC = Path(config.ROOT, "docs", "TWEAKS.md").read_text(encoding="utf-8")
CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0011"


def doc_tweaks():
    """{id: risk} from the tables in sections 1-3 of docs/TWEAKS.md."""
    section = DOC.split("## 4.")[0]
    cell = r"(?:[^|\\]|\\.)*"   # a table cell may contain an escaped pipe (\|=)
    return {m.group(1): m.group(2) for m in re.finditer(rf"^\| `([a-z0-9_]+)` \|{cell}\|{cell}\|\s*(low|medium|experimental)\s*\|",
                                                      section, re.M)}


def real_card_system():
    """FakeSystem with the property list read from the dev PC's MediaTek MT7922 (2026-10-04)."""
    s = FakeSystem()
    s.props = {"Wi-Fi": [
        prop("2.4GHz channel bandwidth", "BWSelection24G", "0", [("1. Auto", "0"), ("2. 20MHz only", "1")], "0"),
        prop("5GHz channel bandwidth", "BWSelection5G", "0", [("1. Auto", "0"), ("2. 20MHz only", "1")], "0"),
        prop("802.11ax/ac/n/abg", "CurrPhyMode", "0",
             [("1. 802.11ax", "0"), ("2. 802.11ac", "1"), ("3. 802.11n", "2"), ("4. 802.11a/b/g", "3")], "0"),
        prop("Wake on Magic Packet", "DisableWakeOnMagic", "0", [("Disabled", "1"), ("Enabled", "0")], "0"),
        prop("Wake on Pattern Match", "DisableWakeOnPattern", "0", [("Disabled", "1"), ("Enabled", "0")], "0"),
        prop("Power Saving", "LowPowerEnable", "1", [("Disabled", "0"), ("Auto", "1")], "1"),
        prop("Transmit Power Level", "TxPowerLevel", "0", [("1. Highest", "0"), ("2. Medium", "1")], "0"),
        prop("Preferred Band", "PreferBand", "2",
             [("1. No Preference", "0"), ("2. Prefer 2.4GHz band", "1"), ("3. Prefer 5GHz band", "2")], "0"),
    ]}
    s.registry = {(CLASS_KEY, "PnPCapabilities"): 16}
    s.power = {(tweaks.SUB_PCIE, tweaks.SET_ASPM): (1, 2), (tweaks.SUB_WIRELESS, tweaks.SET_WIRELESS): (0, 2)}
    return s


def manager(system=None):
    system = system or real_card_system()
    backup = MemoryBackup()   # one object for both directions, like backup.json
    return TweakManager(system, tweaks.build_catalog(), load_backup=backup.load,
                        save_backup=backup.save, is_admin=lambda: True), system


class CatalogMatchesDocsTests(unittest.TestCase):
    def test_ids_and_risks_match_docs_tweaks_md(self):
        docs = doc_tweaks()
        self.assertEqual(len(docs), 13, docs)
        self.assertEqual({t.id: t.risk for t in tweaks.CATALOG}, docs)

    def test_ids_are_unique_and_names_vietnamese_present(self):
        ids = [t.id for t in tweaks.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        for t in tweaks.CATALOG:
            self.assertTrue(t.name and t.group, t.id)

    def test_flags_follow_the_doc_icons(self):
        by = {t.id: t for t in tweaks.CATALOG}
        self.assertTrue(by["tcp_timedwait"].needs_reboot)              # 🔁
        self.assertFalse(any(t.needs_reboot for t in tweaks.CATALOG if t.id != "tcp_timedwait"))
        for tid in ("wifi_power_saving", "wifi_wake_magic", "wifi_wake_pattern", "wifi_roaming", "wifi_bw20_5g",
                    "wifi_mode_ac", "wifi_prefer_5g", "wifi_tx_power_max", "device_power_off", "ipv6_off"):
            self.assertTrue(by[tid].disrupts_network, tid)             # 🔌
        for t in tweaks.CATALOG:
            self.assertTrue(t.needs_admin, t.id)                       # 🛡

    def test_powercfg_guids_match_the_manual_scripts(self):
        script = Path(config.ROOT, "scripts", "manual", "apply-lowrisk.ps1").read_text(encoding="utf-8")
        for guid in (tweaks.SUB_WIRELESS, tweaks.SET_WIRELESS, tweaks.SUB_PCIE, tweaks.SET_ASPM):
            self.assertIn(guid, script)

    def test_pnp_capabilities_has_no_blind_default(self):
        by = {t.id: t for t in tweaks.CATALOG}
        self.assertFalse(by["device_power_off"].has_default_restore)    # driver INF value: don't guess
        self.assertTrue(by["tcp_timedwait"].has_default_restore)        # Windows default is "not set"
        self.assertFalse(by["power_pcie_aspm_off"].has_default_restore)


class RealCardTests(unittest.TestCase):
    def test_states_on_the_real_card_layout(self):
        mgr, _ = manager()
        st = {s.id: s for s in mgr.states()}
        self.assertTrue(all(s.error is None for s in st.values()), [s.error for s in st.values() if s.error])
        self.assertFalse(st["wifi_roaming"].supported)                   # Intel-only property
        self.assertIn("không có thuộc tính", i18n.render(st["wifi_roaming"].reason, "vi"))
        for tid in ("wifi_power_saving", "wifi_wake_magic", "wifi_wake_pattern", "wifi_bw20_5g", "wifi_mode_ac",
                    "wifi_prefer_5g", "wifi_tx_power_max", "device_power_off", "power_wireless_max", "power_pcie_aspm_off", "tcp_timedwait", "ipv6_off"):
            self.assertTrue(st[tid].supported, tid)
        # The dev PC already prefers 5 GHz and sits at the highest transmit power (2026-10-07); nothing else is on.
        self.assertEqual({tid for tid, s in st.items() if s.enabled}, {"wifi_prefer_5g", "wifi_tx_power_max"})

    def test_prefixed_values_are_matched(self):
        mgr, s = manager()
        self.assertEqual(mgr.state("wifi_mode_ac").current, "1. 802.11ax")
        self.assertTrue(mgr.enable("wifi_mode_ac").ok)
        self.assertEqual(s._prop("Wi-Fi", "CurrPhyMode")["DisplayValue"], "2. 802.11ac")
        self.assertTrue(mgr.enable("wifi_bw20_5g").ok)
        self.assertEqual(s._prop("Wi-Fi", "BWSelection5G")["DisplayValue"], "2. 20MHz only")

    def test_exact_property_matching_does_not_touch_neighbours(self):
        mgr, s = manager()
        mgr.enable("wifi_bw20_5g")
        self.assertEqual(s._prop("Wi-Fi", "BWSelection24G")["DisplayValue"], "1. Auto")  # 2.4G untouched

    def test_power_saving_default_equals_the_original_on_this_pc(self):
        # Reset returns the driver default (Auto = 1), which is what the dev PC had before EXP-001.
        mgr, s = manager()
        s._prop("Wi-Fi", "LowPowerEnable").update(RegistryValue=["0"], DisplayValue="Disabled")  # on by hand
        out = mgr.disable("wifi_power_saving")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s._prop("Wi-Fi", "LowPowerEnable")["DisplayValue"], "Auto")

    def test_pnp_capabilities_keeps_other_bits(self):
        mgr, s = manager()
        s.registry[(CLASS_KEY, "PnPCapabilities")] = 0x100 | 0x10
        self.assertTrue(mgr.enable("device_power_off").ok)
        self.assertEqual(s.registry[(CLASS_KEY, "PnPCapabilities")], 0x100 | 0x18)
        self.assertTrue(mgr.disable("device_power_off").ok)
        self.assertEqual(s.registry[(CLASS_KEY, "PnPCapabilities")], 0x110)

    def test_round_trip_of_every_supported_tweak_restores_the_machine(self):
        mgr, s = manager()
        before = s.snapshot()
        for t in tweaks.build_catalog():
            if mgr.state(t.id).supported and not mgr.state(t.id).enabled:
                out = mgr.enable(t.id)
                self.assertTrue(out.ok and out.changed, (t.id, out.message))
        self.assertTrue(all(st.enabled for st in mgr.states() if st.supported))
        for t in tweaks.build_catalog():
            if mgr.state(t.id).supported and t.id not in ("wifi_prefer_5g", "wifi_tx_power_max"):
                out = mgr.disable(t.id)
                self.assertTrue(out.ok, (t.id, out.message))
        self.assertEqual(s.snapshot(), before)

    def test_intel_style_card_uses_the_alternative_names(self):
        s = real_card_system()
        s.props["Wi-Fi"] = [
            prop("Roaming Aggressiveness", "RoamingAggressiveness", "3",
                 [("1. Lowest", "1"), ("2. Medium-Low", "2"), ("3. Medium", "3"), ("5. Highest", "5")], "3"),
            prop("MIMO Power Save Mode", "MIMOPowerSaveMode", "3", [("Auto SMPS", "3"), ("No SMPS", "0")], "3")]
        mgr, _ = manager(s)
        self.assertTrue(mgr.enable("wifi_roaming").ok)
        self.assertTrue(mgr.enable("wifi_power_saving").ok)
        self.assertEqual(s._prop("Wi-Fi", "RoamingAggressiveness")["DisplayValue"], "1. Lowest")
        self.assertEqual(s._prop("Wi-Fi", "MIMOPowerSaveMode")["DisplayValue"], "No SMPS")

    def test_prefer_5g_and_tx_power_on_the_real_card_are_already_on(self):
        mgr, s = manager()
        self.assertTrue(mgr.state("wifi_prefer_5g").enabled)
        self.assertEqual(mgr.state("wifi_prefer_5g").current, "3. Prefer 5GHz band")
        self.assertTrue(mgr.state("wifi_tx_power_max").enabled)
        out = mgr.enable("wifi_prefer_5g")
        self.assertEqual(s.writes(), [], out.message)                   # nothing to change, nothing written

    def test_prefer_5g_round_trip_restores_the_original(self):
        mgr, s = manager()
        s._prop("Wi-Fi", "PreferBand").update(RegistryValue=["0"], DisplayValue="1. No Preference")
        s._prop("Wi-Fi", "TxPowerLevel").update(RegistryValue=["1"], DisplayValue="2. Medium")
        before = s.snapshot()
        for tid, kw, want in (("wifi_prefer_5g", "PreferBand", "3. Prefer 5GHz band"),
                              ("wifi_tx_power_max", "TxPowerLevel", "1. Highest")):
            self.assertFalse(mgr.state(tid).enabled)
            out = mgr.enable(tid)
            self.assertTrue(out.ok and out.changed, out.message)
            self.assertEqual(s._prop("Wi-Fi", kw)["DisplayValue"], want)
            self.assertTrue(mgr.disable(tid).ok)
        self.assertEqual(s.snapshot(), before)

    def test_power_tweaks_without_backup_cannot_be_disabled_blindly(self):
        mgr, s = manager()
        s.power[(tweaks.SUB_PCIE, tweaks.SET_ASPM)] = (0, 0)             # turned on by hand earlier
        out = mgr.disable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.power[(tweaks.SUB_PCIE, tweaks.SET_ASPM)], (0, 0))


VENDOR_BAND_LISTS = {
    "mediatek/intel": ("Preferred Band", [("1. No Preference", "0"), ("2. Prefer 2.4GHz band", "1"),
                                          ("3. Prefer 5GHz band", "2")], "2"),
    "realtek": ("Band Preference", [("Auto", "0"), ("Prefer 2.4G", "1"), ("Prefer 5G", "2")], "2"),
    "realtek-ghz": ("Band Preference", [("Auto", "0"), ("Prefer 2.4GHz", "1"), ("Prefer 5GHz", "2")], "2"),
    "realtek-52": ("Preferred Band", [("Auto", "0"), ("Prefer 2.4GHz", "1"), ("Prefer 5.2GHz", "2")], "2"),
    # A driver that offers band-only choices next to the preference: the preference must win.
    "with-only": ("Preferred Band", [("1. 5GHz only", "9"), ("2. 2.4GHz only", "8"), ("3. Prefer 6GHz band", "7"),
                                     ("4. Prefer 5GHz band", "2"), ("5. Prefer 2.4GHz band", "1")], "2"),
}


class PreferBandTests(unittest.TestCase):
    def card(self, name, values):
        s = real_card_system()
        start = next(r for _, r in values if r != "2")
        s.props["Wi-Fi"] = [prop(name, "PreferBand", start, values, start)]
        return manager(s), start

    def test_each_vendor_list_resolves_to_the_prefer_5_value(self):
        for label, (name, values, want) in VENDOR_BAND_LISTS.items():
            (mgr, s), start = self.card(name, values)
            self.assertFalse(mgr.state("wifi_prefer_5g").enabled, label)
            out = mgr.enable("wifi_prefer_5g")
            self.assertTrue(out.ok and out.changed, (label, out.message))
            self.assertEqual(s._prop("Wi-Fi", "PreferBand")["RegistryValue"], [want], label)
            self.assertTrue(mgr.disable("wifi_prefer_5g").ok, label)
            self.assertEqual(s._prop("Wi-Fi", "PreferBand")["RegistryValue"], [start], label)

    def test_the_target_never_matches_band_only_or_other_bands(self):
        tweak = next(t for t in tweaks.CATALOG if t.id == "wifi_prefer_5g")
        target = tweak.candidates[0][1]
        for ok in ("3. Prefer 5GHz band", "Prefer 5GHz", "Prefer 5 GHz", "Prefer 5G", "Prefer 5.2GHz",
                   "prefer 5ghz band"):
            self.assertTrue(target.fullmatch(ok), ok)
        for bad in ("5GHz only", "1. 5GHz only", "Only 5GHz", "Only 5G", "5G only", "Prefer 5GHz only",
                    "Prefer 5G only", "Prefer 2.4GHz band", "2. Prefer 2.4G", "Prefer 6GHz band", "Prefer 6G",
                    "Prefer 5.2GHz and 6GHz", "No Preference", "Auto", "Prefer 50GHz", "2.4GHz only"):
            self.assertIsNone(target.fullmatch(bad), bad)

    def test_a_band_only_list_without_a_preference_is_not_supported(self):
        (mgr, s), _ = self.card("Preferred Band", [("1. 5GHz only", "0"), ("2. 2.4GHz only", "1"),
                                                   ("3. Auto", "3")])
        self.assertFalse(mgr.state("wifi_prefer_5g").supported)
        self.assertFalse(mgr.enable("wifi_prefer_5g").ok)
        self.assertEqual(s.writes(), [])

    def test_card_without_the_property_is_not_supported(self):
        s = real_card_system()
        s.props["Wi-Fi"] = [p for p in s.props["Wi-Fi"]
                            if p["DisplayName"] not in ("Preferred Band", "Transmit Power Level")]
        mgr, _ = manager(s)
        self.assertFalse(mgr.state("wifi_prefer_5g").supported)
        self.assertFalse(mgr.state("wifi_tx_power_max").supported)


class TransmitPowerTests(unittest.TestCase):
    def test_vendor_names_and_values(self):
        cases = (("Transmit Power Level", [("1. Highest", "0"), ("2. Medium", "1")], "0"),
                 ("Transmit Power", [("1. Lowest", "1"), ("2. Medium-Low", "2"), ("3. Medium", "3"),
                                     ("4. Medium-High", "4"), ("5. Highest", "5")], "5"),
                 ("Transmit Power", [("Lowest", "0"), ("Highest", "1")], "1"))
        for name, values, want in cases:
            s = real_card_system()
            start = next(r for _, r in values if r != want)
            s.props["Wi-Fi"] = [prop(name, "TxPower", start, values, want)]
            mgr, _ = manager(s)
            self.assertTrue(mgr.enable("wifi_tx_power_max").ok, name)
            self.assertEqual(s._prop("Wi-Fi", "TxPower")["RegistryValue"], [want], name)
            self.assertTrue(mgr.disable("wifi_tx_power_max").ok, name)
            self.assertEqual(s._prop("Wi-Fi", "TxPower")["RegistryValue"], [start], name)

    def test_medium_high_is_not_mistaken_for_highest(self):
        s = real_card_system()
        s.props["Wi-Fi"] = [prop("Transmit Power", "TxPower", "4",
                                 [("4. Medium-High", "4"), ("5. Highest", "5")], "5")]
        mgr, _ = manager(s)
        self.assertFalse(mgr.state("wifi_tx_power_max").enabled)


@unittest.skipUnless(sys.platform == "win32", "reads the real machine")
class RealMachineReadOnlyTests(unittest.TestCase):
    """Reads the real PC through the real winsys layer. Nothing here writes."""

    def test_list_states_covers_every_tweak_and_never_errors(self):
        states = tweaks.default_manager().states()
        self.assertEqual({s.id for s in states}, set(doc_tweaks()))
        for s in states:
            self.assertIsNone(s.error, f"{s.id}: {s.error}")
        by = {s.id: s for s in states}
        self.assertFalse(by["wifi_roaming"].supported)                   # this card has no such property
        self.assertTrue(by["power_pcie_aspm_off"].supported)

    def test_diagnostics_check_10_now_has_data(self):
        states = tweaks.list_states()
        self.assertIsNotNone(states)
        self.assertEqual(set(states), set(doc_tweaks()))
        self.assertTrue(all({"risk", "enabled", "supported"} <= set(v) for v in states.values()))


if __name__ == "__main__":
    unittest.main()
