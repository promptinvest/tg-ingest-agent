"""Fixed legacy wire vectors shared by the task agent, worker and canary."""
import copy
import hashlib
import unittest

import cara_worker
import improvement
import task_json
import task_runner
import verify_task_runtime
import worker_client


CONSUMERS = (worker_client, cara_worker, verify_task_runtime, task_runner, improvement)


class TaskJSONCompatibilityTests(unittest.TestCase):
    def assert_vector(self, value, expected, digest=None):
        before = copy.deepcopy(value)
        for consumer in (task_json, *CONSUMERS):
            encode = consumer.canonical if consumer is task_json else consumer._canonical
            with self.subTest(consumer=consumer.__name__):
                rendered = encode(value)
                self.assertIsInstance(rendered, str)
                self.assertEqual(rendered, expected)
                if digest is not None:
                    self.assertEqual(hashlib.sha256(rendered.encode("utf-8")).hexdigest(), digest)
        self.assertEqual(value, before)

    def test_original_hash_bytes_are_stable_across_all_task_participants(self):
        # Literal wire data and independently recorded SHA256, not another encoder.
        self.assert_vector(
            {"b": 2, "a": 1}, '{"a":1,"b":2}',
            "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777")
        self.assertEqual(task_runner._input_hash({"b": 2, "a": 1}),
                         "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777")
        self.assertEqual(improvement._hash({"b": 2, "a": 1}),
                         "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777")

    def test_unicode_escaping_nested_order_and_tuple_shape_keep_exact_bytes(self):
        self.assert_vector(
            {"z": (True, None, 3), "a": {"é": "å", "x": 'line\nquote"\\'}},
            r'{"a":{"x":"line\nquote\"\\","é":"å"},"z":[true,null,3]}',
            "cc3382b15e0df2d1ee5f77e6d106240ca2cf6a6630be43223a178f0605287ec1")

    def test_scalar_values_preserve_existing_json_spellings(self):
        for value, expected in ((None, "null"), (False, "false"),
                                (-0.0, "-0.0"), ("å", '"å"'),
                                ({2: "two", 1: "one"}, '{"1":"one","2":"two"}')):
            with self.subTest(value=value):
                self.assert_vector(value, expected)

    def test_nonfinite_numbers_keep_legacy_task_contract(self):
        for consumer in (task_json, *CONSUMERS):
            encode = consumer.canonical if consumer is task_json else consumer._canonical
            with self.subTest(consumer=consumer.__name__):
                self.assertEqual(
                    encode({"positive": float("inf"), "not": float("nan"),
                            "negative": -float("inf")}),
                    '{"negative":-Infinity,"not":NaN,"positive":Infinity}')

    def test_unsupported_values_and_cycles_still_fail_instead_of_silently_coercing(self):
        cycle = []
        cycle.append(cycle)
        for consumer in (task_json, *CONSUMERS):
            encode = consumer.canonical if consumer is task_json else consumer._canonical
            with self.subTest(consumer=consumer.__name__):
                with self.assertRaises(TypeError):
                    encode({"unsupported": {1, 2}})
                with self.assertRaises(TypeError):
                    encode({1: "integer", "two": "string"})
                with self.assertRaisesRegex(ValueError, "Circular reference"):
                    encode(cycle)


if __name__ == "__main__":
    unittest.main()
