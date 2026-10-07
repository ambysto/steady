import copy
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
        self.assertFalse(st["wifi_prefer_5g"].supported)                 # no band property on this card
        for tid in ("wifi_power_saving", "wifi_wake_magic", "wifi_wake_pattern", "wifi_bw20_5g", "wifi_mode_ac",
                    "wifi_tx_power_max", "device_power_off", "power_wireless_max", "power_pcie_aspm_off",
                    "tcp_timedwait", "ipv6_off"):
            self.assertTrue(st[tid].supported, tid)
        # fresh machine: only the transmit power reads as on, because the driver default is already Highest
        self.assertEqual({tid for tid, s in st.items() if s.enabled}, {"wifi_tx_power_max"})

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
            if mgr.state(t.id).supported and t.id != "wifi_tx_power_max":   # already on: the tool never changed it
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

    def test_power_tweaks_without_backup_cannot_be_disabled_blindly(self):
        mgr, s = manager()
        s.power[(tweaks.SUB_PCIE, tweaks.SET_ASPM)] = (0, 0)             # turned on by hand earlier
        out = mgr.disable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.power[(tweaks.SUB_PCIE, tweaks.SET_ASPM)], (0, 0))


def card_with(*props):
    s = real_card_system()
    s.props["Wi-Fi"] = list(props)
    return s


# Property names and values as the vendors ship them (Intel AX2xx, Realtek RTL88xx, MediaTek MT79xx).
INTEL_BAND = prop("Preferred Band", "RoamingPreferredBandType", "0",
                  [("1. No Preference", "0"), ("2. Prefer 2.4GHz band", "1"), ("3. Prefer 5GHz band", "2")], "0")
INTEL_TX = prop("Transmit Power", "TransmitPower", "100",
                [("1. Lowest", "20"), ("3. Medium", "60"), ("5. Highest", "100")], "100")
REALTEK_BAND = prop("Band Preference", "BandPreference", "0",
                    [("Auto", "0"), ("Prefer 5GHz", "1"), ("5G Only", "2")], "0")
REALTEK_TX = prop("Tx Power Level", "TxPowerLevel", "1", [("Highest", "0"), ("Medium", "1"), ("Lowest", "2")], "0")
MEDIATEK_BAND = prop("Preferred Band", "PreferredBand", "0",
                     [("1. Auto", "0"), ("2. Prefer 2.4GHz", "1"), ("3. Prefer 5GHz", "2"), ("4. 5G Only", "3")], "0")
MEDIATEK_TX = prop("Transmit Power Level", "TxPowerLevel", "1", [("1. Highest", "0"), ("2. Medium", "1")], "0")


class PreferFiveGhzTests(unittest.TestCase):
    def test_each_vendor_gets_prefer_5ghz_and_never_the_5g_only_value(self):
        for band, keyword, shown in ((INTEL_BAND, "RoamingPreferredBandType", "3. Prefer 5GHz band"),
                                     (REALTEK_BAND, "BandPreference", "Prefer 5GHz"),
                                     (MEDIATEK_BAND, "PreferredBand", "3. Prefer 5GHz")):
            mgr, s = manager(card_with(copy.deepcopy(band)))
            out = mgr.enable("wifi_prefer_5g")
            self.assertTrue(out.ok and out.changed, (keyword, out.message))
            self.assertEqual(s._prop("Wi-Fi", keyword)["DisplayValue"], shown)

    def test_disable_restores_the_original_band(self):
        mgr, s = manager(card_with(copy.deepcopy(MEDIATEK_BAND)))
        mgr.enable("wifi_prefer_5g")
        out = mgr.disable("wifi_prefer_5g")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s._prop("Wi-Fi", "PreferredBand")["DisplayValue"], "1. Auto")

    def test_disable_without_a_backup_resets_to_the_driver_default(self):
        mgr, s = manager(card_with(copy.deepcopy(REALTEK_BAND)))
        s._prop("Wi-Fi", "BandPreference").update(RegistryValue=["1"], DisplayValue="Prefer 5GHz")   # set by hand
        out = mgr.disable("wifi_prefer_5g")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s._prop("Wi-Fi", "BandPreference")["DisplayValue"], "Auto")

    def test_not_supported_without_the_property(self):
        mgr, s = manager()
        st = mgr.state("wifi_prefer_5g")
        self.assertFalse(st.supported)
        self.assertIn("không có thuộc tính", i18n.render(st.reason, "vi"))
        self.assertFalse(mgr.enable("wifi_prefer_5g").ok)
        self.assertEqual(s.writes(), [])

    def test_a_card_with_only_5g_only_is_not_offered_that_value(self):
        only = prop("Preferred Band", "PreferredBand", "0", [("Auto", "0"), ("5G Only", "1")], "0")
        mgr, s = manager(card_with(only))
        self.assertFalse(mgr.state("wifi_prefer_5g").supported)
        self.assertFalse(mgr.enable("wifi_prefer_5g").ok)
        self.assertEqual(s.writes(), [])

    def test_offered_only_when_the_connected_ssid_has_a_5ghz_access_point(self):
        s = card_with(copy.deepcopy(INTEL_BAND))
        mgr, _ = manager(s)
        self.assertTrue(mgr.state("wifi_prefer_5g").supported)
        s.ssid_bands = ("HomeNet", frozenset({"2.4 GHz"}))
        st = mgr.state("wifi_prefer_5g")
        self.assertFalse(st.supported)
        self.assertIn("HomeNet", i18n.render(st.reason, "en"))
        self.assertIn("5 GHz", i18n.render(st.reason, "en"))
        self.assertFalse(mgr.enable("wifi_prefer_5g").ok)
        self.assertEqual(s.writes(), [])

    def test_reason_when_the_scan_does_not_list_the_ssid_or_wifi_is_down(self):
        s = card_with(copy.deepcopy(INTEL_BAND))
        mgr, _ = manager(s)
        s.ssid_bands = ("HomeNet", frozenset())
        st = mgr.state("wifi_prefer_5g")
        self.assertFalse(st.supported)
        self.assertIn("HomeNet", i18n.render(st.reason, "en"))
        s.ssid_bands = None
        st = mgr.state("wifi_prefer_5g")
        self.assertFalse(st.supported)
        self.assertIn("not connected", i18n.render(st.reason, "en").lower())

    def test_band_spelling_from_netsh_is_tolerated(self):
        s = card_with(copy.deepcopy(INTEL_BAND))
        s.ssid_bands = ("HomeNet", frozenset({"5GHz"}))
        self.assertTrue(manager(s)[0].state("wifi_prefer_5g").supported)

    def test_already_on_stays_switchable_off_when_the_network_has_no_5ghz(self):
        mgr, s = manager(card_with(copy.deepcopy(INTEL_BAND)))
        self.assertTrue(mgr.enable("wifi_prefer_5g").ok)
        s.ssid_bands = ("Cafe", frozenset({"2.4 GHz"}))       # moved to another network
        st = mgr.state("wifi_prefer_5g")
        self.assertTrue(st.supported and st.enabled)
        self.assertTrue(mgr.disable("wifi_prefer_5g").ok)
        self.assertEqual(s._prop("Wi-Fi", "RoamingPreferredBandType")["DisplayValue"], "1. No Preference")

    def test_listing_never_writes(self):
        mgr, s = manager(card_with(copy.deepcopy(INTEL_BAND), copy.deepcopy(INTEL_TX)))
        mgr.states()
        self.assertEqual(s.writes(), [])


class TransmitPowerTests(unittest.TestCase):
    def test_each_vendor_name_is_matched(self):
        for tx, keyword, low, shown in ((INTEL_TX, "TransmitPower", "20", "5. Highest"),
                                        (REALTEK_TX, "TxPowerLevel", "1", "Highest"),
                                        (MEDIATEK_TX, "TxPowerLevel", "1", "1. Highest")):
            s = card_with(copy.deepcopy(tx))
            s._prop("Wi-Fi", keyword).update(
                RegistryValue=[low], DisplayValue=tx["ValidDisplayValues"][tx["ValidRegistryValues"].index(low)])
            mgr, _ = manager(s)
            self.assertFalse(mgr.state("wifi_tx_power_max").enabled, keyword)
            out = mgr.enable("wifi_tx_power_max")
            self.assertTrue(out.ok and out.changed, (keyword, out.message))
            self.assertEqual(s._prop("Wi-Fi", keyword)["DisplayValue"], shown)
            self.assertTrue(mgr.disable("wifi_tx_power_max").ok)
            self.assertEqual(s._prop("Wi-Fi", keyword)["RegistryValue"], [low])

    def test_already_highest_is_reported_on_and_left_alone(self):
        mgr, s = manager(card_with(copy.deepcopy(INTEL_TX)))
        self.assertTrue(mgr.state("wifi_tx_power_max").enabled)
        out = mgr.enable("wifi_tx_power_max")
        self.assertTrue(out.ok and not out.changed)
        self.assertEqual(s.writes(), [])

    def test_not_supported_without_the_property(self):
        mgr, s = manager(card_with(copy.deepcopy(INTEL_BAND)))
        self.assertFalse(mgr.state("wifi_tx_power_max").supported)
        self.assertFalse(mgr.enable("wifi_tx_power_max").ok)
        self.assertEqual(s.writes(), [])

    def test_other_power_properties_are_not_confused_with_it(self):
        s = card_with(prop("Power Saving", "PowerSaving", "0", [("Disabled", "0"), ("Auto", "1")], "1"),
                      prop("Transmit Power Control", "TPC", "0", [("Off", "0"), ("Highest", "1")], "0"))
        self.assertFalse(manager(s)[0].state("wifi_tx_power_max").supported)


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
