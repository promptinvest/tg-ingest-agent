#!/usr/bin/env python3
"""Offline unittest gate with optional named targets, shuffle and timing evidence."""
import argparse
import random
import time
import unittest


class TimedResult(unittest.TextTestResult):
    timings = None

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append((time.monotonic() - self.started, test.id()))
        super().stopTest(test)


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--shuffle", type=int)
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(args.targets) if args.targets else loader.discover(".", "test_*.py")
    if args.targets:
        print("SUBSET ONLY: " + " ".join(args.targets), flush=True)
    if args.shuffle is not None:
        items = list(cases(suite))
        random.Random(args.shuffle).shuffle(items)
        suite = unittest.TestSuite(items)
    TimedResult.timings = []
    result = unittest.TextTestRunner(resultclass=TimedResult).run(suite)
    print("Slowest tests (seconds):", flush=True)
    for elapsed, name in sorted(result.timings, reverse=True)[:20]:
        print(f"  {elapsed:.3f} {name}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
