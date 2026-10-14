"""Run with:  python -m unittest discover -s tests -v

Every tool ships with a self-test against fake servers that behave in a known
way, so these tests need no GPU, model or network.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import demo           # noqa: E402
import lbproxy        # noqa: E402
import loadtest       # noqa: E402
import model_router   # noqa: E402


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


class SelfTests(unittest.TestCase):
    def test_loadtest_measures_a_known_server(self):
        self.assertEqual(quiet(loadtest.selftest), 0)

    def test_lbproxy_round_robins_and_streams_unbuffered(self):
        self.assertEqual(quiet(lbproxy.selftest), 0)

    def test_model_router_routes_and_normalizes_stream(self):
        self.assertEqual(quiet(model_router.selftest), 0)


class Demo(unittest.TestCase):
    def test_demo_completes_and_second_engine_adds_throughput(self):
        with mock.patch.object(sys, "argv", ["demo.py", "--users", "8"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = demo.main()
        self.assertEqual(rc, 0, out.getvalue())
        ratio = float(out.getvalue().split("Second engine: ")[1].split("x")[0])
        self.assertGreater(ratio, 1.5, out.getvalue())


if __name__ == "__main__":
    unittest.main()
