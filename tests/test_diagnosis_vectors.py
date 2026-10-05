"""The shared diagnosis test vectors (spec/diagnosis/*.json, ADR-0010) hold for the Python rules.

The same files run against the Swift port (apple/SteadyKit) and later the Kotlin one, so every
platform reaches the same verdict from the same measurements. Changing a rule means changing its
vectors and every implementation together."""
import json
import math
import unittest
from pathlib import Path

from app import bufferbloat, diagnostics, dnsprobe

SPEC = Path(__file__).resolve().parent.parent / "spec" / "diagnosis"


def _measurement(data: dict) -> bufferbloat.Measurement:
    """Vector phases list the targets in order ([{label, samples}]); Python keeps them in a dict."""
    return bufferbloat.Measurement(*(
        bufferbloat.Phase(name, {t["label"]: t["samples"] for t in data[name]["rtts"]},
                          data[name]["mbps"], data[name]["error"])
        for name in ("idle", "download", "upload")))


# rule name in the vector file -> function computing the result from the case's "input"
RULES = {
    "ping": lambda data: diagnostics.evaluate_ping(data["rows_1h"], data["rows_5m"]),
    "dns": lambda data: diagnostics.evaluate_dns([dnsprobe.ServerBenchmark(**b) for b in data["bench"]],
                                                 data["in_use"], data["labels"]),
    "bufferbloat": lambda data: diagnostics.evaluate_bufferbloat(_measurement(data)),
}
COMPARED = ("status", "summary", "details", "advice")


def same(expected, actual) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(expected, actual, rel_tol=0, abs_tol=1e-9)
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(same(expected[k], actual[k]) for k in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(same(e, a) for e, a in zip(expected, actual))
    return expected == actual


class DiagnosisVectorTests(unittest.TestCase):
    def test_every_vector_file_has_a_rule(self):
        names = {json.loads(p.read_text(encoding="utf-8"))["rule"] for p in SPEC.glob("*.json")}
        self.assertTrue(names, "no vector files found")
        self.assertEqual(names - RULES.keys(), set())

    def test_python_rules_match_the_vectors(self):
        for path in sorted(SPEC.glob("*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            for case in spec["cases"]:
                with self.subTest(file=path.name, case=case["name"]):
                    result = RULES[spec["rule"]](case["input"]).to_dict()
                    actual = {k: json.loads(json.dumps(result[k])) for k in COMPARED}
                    self.assertTrue(same(case["expected"], actual),
                                    f"expected {case['expected']}\n  actual {actual}")

    def test_comparison_tolerates_float_noise_only(self):
        self.assertTrue(same({"a": [1.0, {"b": 0.1 + 0.2}]}, {"a": [1, {"b": 0.3}]}))
        self.assertFalse(same({"a": 1.0}, {"a": 1.01}))
        self.assertFalse(same({"a": 1}, {"a": 1, "b": 2}))
        self.assertFalse(same("", None))


if __name__ == "__main__":
    unittest.main()
