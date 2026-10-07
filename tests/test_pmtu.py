"""Path MTU search (app/pmtu.py) and its diagnostic (#16), against fake paths - no packet leaves."""
import random
import unittest

from app import diagnostics, i18n, pmtu
from app.pmtu import LOST, PASSED, TOO_BIG


def path(mtu, silent=False, lose=()):
    """A fake path: packets up to `mtu` bytes pass. Larger ones get "too big" back, or vanish when
    PMTUD is blackholed (`silent`). The n-th packets listed in `lose` are lost whatever their size."""
    sent = []

    def send(size):
        sent.append(size)
        if len(sent) in lose:
            return LOST
        if size <= mtu:
            return PASSED
        return LOST if silent else TOO_BIG
    send.sent = sent
    return send


class ProbeTests(unittest.TestCase):
    def test_full_mtu_takes_two_packets(self):
        send = path(1500)
        self.assertEqual(pmtu.probe(send, 1500), (1500, 2, False))
        self.assertEqual(send.sent, [pmtu.MIN_MTU, 1500])

    def test_finds_pppoe_when_routers_answer_too_big(self):
        self.assertEqual(pmtu.probe(path(1492), 1500)[::2], (1492, True))

    def test_finds_pppoe_when_large_packets_vanish(self):
        mtu, sent, too_big = pmtu.probe(path(1492, silent=True), 1500)
        self.assertEqual((mtu, too_big), (1492, False))
        self.assertLessEqual(sent, 24)   # silent drops are retried once each

    def test_finds_any_mtu_in_range(self):
        for mtu in (pmtu.MIN_MTU, 577, 1280, 1400, 1420, 1460, 1480, 1499):
            for silent in (False, True):
                with self.subTest(mtu=mtu, silent=silent):
                    self.assertEqual(pmtu.probe(path(mtu, silent), 1500)[0], mtu)

    def test_ceiling_is_the_interface_mtu(self):
        self.assertEqual(pmtu.probe(path(9000), 1492)[0], 1492)   # never probes above the interface

    def test_jumbo_interfaces_are_probed_up_to_1500_only(self):
        send = path(9000, silent=True)
        self.assertEqual(pmtu.probe(send, 9000), (1500, 2, False))
        self.assertEqual(max(send.sent), pmtu.MAX_MTU)
        self.assertLessEqual(pmtu.probe(path(1492, silent=True), 65535)[1], 24)

    def test_one_lost_packet_does_not_shrink_the_result(self):
        self.assertEqual(pmtu.probe(path(1500, lose={2}), 1500), (1500, 3, False))
        self.assertEqual(pmtu.probe(path(1492, silent=True, lose={4}), 1500)[0], 1492)

    def test_target_that_never_answers(self):
        self.assertEqual(pmtu.probe(lambda size: LOST, 1500), (None, pmtu.TRIES, False))
        self.assertEqual(pmtu.probe(path(1500), 500), (None, 0, False))   # nonsense ceiling: nothing sent

    def test_random_paths(self):
        rng = random.Random(86)
        for _ in range(200):
            ceiling = rng.randint(pmtu.MIN_MTU, pmtu.MAX_MTU)
            mtu = rng.randint(pmtu.MIN_MTU, ceiling)
            self.assertEqual(pmtu.probe(path(mtu, rng.random() < 0.5), ceiling)[0], mtu)


class MeasureTests(unittest.TestCase):
    def test_each_target_gets_its_own_sender_and_is_closed(self):
        paths = {"192.0.2.1": path(1492), "192.0.2.2": path(1500), "192.0.2.3": lambda size: LOST}
        closed = []

        def sender(target):
            return paths[target], lambda: closed.append(target)

        results = pmtu.measure(1500, paths, sender)
        self.assertEqual([(r.target, r.mtu, r.too_big) for r in results],
                         [("192.0.2.1", 1492, True), ("192.0.2.2", 1500, False), ("192.0.2.3", None, False)])
        self.assertEqual(sorted(closed), sorted(paths))


R = pmtu.PathResult
WIFI = {"alias": "Wi-Fi", "mtu": 1500}


class EvaluateTests(unittest.TestCase):
    def test_full_mtu_is_ok(self):
        r = diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1500, 2), R("8.8.8.8", 1500, 2)])
        self.assertEqual((r.id, r.key, r.status), (16, "path_mtu", diagnostics.OK))
        self.assertEqual(r.summary, i18n.msg("diag.path_mtu.ok", mtu=1500))

    def test_pppoe_blackhole_behind_a_1500_interface_warns(self):
        r = diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1492, 24), R("8.8.8.8", 1492, 24)])
        self.assertEqual(r.status, diagnostics.WARN)
        self.assertEqual(r.summary, i18n.msg("diag.path_mtu.too_big", mtu=1500, path=1492))
        self.assertIn(i18n.msg("diag.path_mtu.pppoe"), r.details)
        self.assertIn(i18n.msg("diag.path_mtu.silent"), r.details)
        self.assertEqual(r.advice, i18n.msg("diag.path_mtu.advice", path=1492, name="Wi-Fi"))
        self.assertEqual(i18n.render(r.summary, "en"),
                         "The interface MTU (1500 bytes) is larger than the path allows (1492 bytes)")

    def test_working_pmtud_is_only_info(self):
        """Routers answer "too big" (or Windows learned it from them): connections adapt."""
        r = diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1492, 12, True), R("8.8.8.8", 1492, 24)])
        self.assertEqual(r.status, diagnostics.INFO)
        self.assertIn(i18n.msg("diag.path_mtu.reported"), r.details)
        self.assertEqual(r.summary, i18n.msg("diag.path_mtu.too_big", mtu=1500, path=1492))

    def test_a_single_silent_target_is_not_enough_to_warn(self):
        r = diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", None, 2), R("8.8.8.8", None, 2), R("9.9.9.9", 1499, 24)])
        self.assertEqual(r.status, diagnostics.INFO)
        self.assertIn(i18n.msg("diag.path_mtu.one_target"), r.details)

    def test_the_largest_path_counts(self):
        """One destination behind a tunnel does not make the access link look small."""
        r = diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1400, 12), R("8.8.8.8", 1500, 2), R("9.9.9.9", None, 2)])
        self.assertEqual(r.status, diagnostics.OK)
        self.assertIn(i18n.msg("diag.path_mtu.target_silent", target="9.9.9.9"), r.details)

    def test_other_tunnels_warn_without_the_pppoe_hint(self):
        r = diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1480, 24), R("8.8.8.8", 1480, 24)])
        self.assertEqual(r.status, diagnostics.WARN)
        self.assertNotIn(i18n.msg("diag.path_mtu.pppoe"), r.details)

    def test_already_lowered_interface_is_ok(self):
        r = diagnostics.evaluate_path_mtu({"alias": "Wi-Fi", "mtu": 1492}, [R("1.1.1.1", 1492, 2)])
        self.assertEqual(r.status, diagnostics.OK)

    def test_jumbo_interface_with_a_1500_path_is_ok(self):
        r = diagnostics.evaluate_path_mtu({"alias": "Ethernet", "mtu": 9000}, [R("1.1.1.1", 1500, 2)])
        self.assertEqual((r.status, r.summary), (diagnostics.OK, i18n.msg("diag.path_mtu.ok_capped", cap=1500)))
        self.assertIn(i18n.msg("diag.path_mtu.capped", cap=1500), r.details)

    def test_no_reply_no_interface_or_no_ipv4_is_info_never_ok(self):
        r = diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", None, 2), R("8.8.8.8", None, 2)])
        self.assertEqual((r.status, r.summary), (diagnostics.INFO, i18n.msg("diag.path_mtu.no_reply")))
        r = diagnostics.evaluate_path_mtu(None, [])
        self.assertEqual((r.status, r.summary), (diagnostics.INFO, i18n.msg("diag.path_mtu.no_interface")))
        r = diagnostics.evaluate_path_mtu(None, [], ipv4_route=False)
        self.assertEqual((r.status, r.summary), (diagnostics.INFO, i18n.msg("diag.path_mtu.no_ipv4")))

    def test_check_reads_the_context(self):
        paths = [R("1.1.1.1", 1492, 24), R("8.8.8.8", 1492, 24)]
        ctx = diagnostics.Context(now=0, loaders={"path_mtu": lambda: (WIFI, paths, True)})
        (r,) = diagnostics.run_all(ctx, only={16}).results
        self.assertEqual((r.id, r.status), (16, diagnostics.WARN))

    def test_renders_in_every_language(self):
        cases = [diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1492, 24), R("8.8.8.8", 1492, 24)]),
                 diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1492, 12, True), R("8.8.8.8", None, 2)]),
                 diagnostics.evaluate_path_mtu(WIFI, [R("1.1.1.1", 1499, 24)]),
                 diagnostics.evaluate_path_mtu({"alias": "Ethernet", "mtu": 9000}, [R("1.1.1.1", 1500, 2)]),
                 diagnostics.evaluate_path_mtu(None, [], ipv4_route=False)]
        for code in [entry["code"] for entry in i18n.available()]:
            for r in cases:
                text = r.localized(code)
                for line in [text.title, text.summary, text.advice, *text.details]:
                    self.assertNotIn("diag.", line, code)
                    self.assertNotRegex(line, r"\{[a-z_]+\}", code)


if __name__ == "__main__":
    unittest.main()
