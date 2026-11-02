"""Checkout-only coverage of the hand-maintained ADR-0027 documentation maps.

These guards read bounded current maps, not historical release mentions. The
table inventory comes from a freshly migrated synthetic database, including
schema fragments imported by store; no live database, model or Telegram is used.
Runtime-only deployment payloads omit the three specs and skip the checkout
checks explicitly. A partial checkout with only some specs is an error.
"""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import store


ROOT = Path(__file__).resolve().parent
SPECS = ("CARA.md", "SOLUTION.md", "CLAUDE.md")
INSTALLER = "install-tg-ingest-agent-pilot-remote.sh"


def contract_block(text, kind):
    """Require one current map; scattered historical mentions cannot satisfy it."""
    start = f"<!-- documentation-contract:{kind} -->"
    end = f"<!-- /documentation-contract:{kind} -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"expected exactly one {kind} documentation-contract block")
    before, remainder = text.split(start)
    body, after = remainder.split(end)
    if end in before or start in after:
        raise ValueError(f"invalid {kind} documentation-contract block order")
    return body


def module_inventory(installer):
    assignments = re.findall(r'^MODULES="([^"]+)"\s*$', installer, re.MULTILINE)
    if len(assignments) != 1:
        raise ValueError("expected one literal installer MODULES assignment")
    names = assignments[0].split()
    if len(names) != len(set(names)) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", n) for n in names):
        raise ValueError("installer MODULES must contain unique Python filenames")
    return set(names) | {"tg_ingest_agent.py"}


def documented_modules(text):
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*\.py)`", contract_block(text, "modules")))


def first_cell_map(text, kind):
    """Read exact names and a nonempty policy/responsibility from map rows."""
    rows = {}
    for line in contract_block(text, kind).splitlines():
        if not line.startswith("|"):
            continue
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        names = re.findall(r"`([^`]+)`", cells[0])
        if not names:
            continue  # Heading and separator rows have no code-formatted name.
        if len(cells) < 2 or not cells[1] or cells[1] in {"-", "TBD", "TODO"}:
            raise ValueError(f"{kind} map has no policy/responsibility: {cells[0]}")
        for name in names:
            if name in rows:
                raise ValueError(f"duplicate {kind} map entry: {name}")
            rows[name] = cells[1]
    return rows


def service_inventory(root):
    return {path.relative_to(root).as_posix() for path in root.rglob("*.service")
            if ".git" not in path.relative_to(root).parts}


def require_coverage(actual, documented, label):
    missing = set(actual) - set(documented)
    stale = set(documented) - set(actual)
    if missing or stale:
        raise AssertionError(f"{label}: missing={sorted(missing)}, stale={sorted(stale)}")


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        available = {name for name in SPECS if (ROOT / name).is_file()}
        if not available and not (ROOT / ".git").exists():
            raise unittest.SkipTest("checkout-only documentation maps are absent from runtime payload")
        missing = set(SPECS) - available
        if missing:
            raise AssertionError(f"partial documentation checkout: missing {sorted(missing)}")
        cls.docs = {name: (ROOT / name).read_text(encoding="utf-8") for name in SPECS}
        cls.modules = module_inventory((ROOT / INSTALLER).read_text(encoding="utf-8"))
        cls.services = service_inventory(ROOT)
        with tempfile.TemporaryDirectory(prefix="cara-doc-schema-") as temp:
            # Schema initialization is local. Refuse accidental external effects
            # if a future migration starts doing more than SQLite work.
            with mock.patch("socket.socket", side_effect=AssertionError("schema attempted network")), \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("schema started process")):
                conn = store.open_db(Path(temp) / "synthetic.db")
                try:
                    cls.tables = {row["name"] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                finally:
                    conn.close()

    def test_cara_module_map_covers_installer(self):
        require_coverage(self.modules, documented_modules(self.docs["CARA.md"]), "CARA modules")

    def test_solution_module_map_covers_installer(self):
        require_coverage(self.modules, documented_modules(self.docs["SOLUTION.md"]), "SOLUTION modules")

    def test_agent_snapshot_covers_installer(self):
        require_coverage(self.modules, documented_modules(self.docs["CLAUDE.md"]), "CLAUDE modules")

    def test_cara_service_map_covers_checkout(self):
        require_coverage(self.services, first_cell_map(self.docs["CARA.md"], "services"), "CARA services")

    def test_solution_service_map_covers_checkout(self):
        require_coverage(self.services, first_cell_map(self.docs["SOLUTION.md"], "services"), "SOLUTION services")

    def test_agent_snapshot_covers_checkout_services(self):
        require_coverage(self.services, first_cell_map(self.docs["CLAUDE.md"], "services"), "CLAUDE services")

    def test_cara_retention_map_covers_migrated_schema(self):
        require_coverage(self.tables, first_cell_map(self.docs["CARA.md"], "tables"), "CARA tables")

    def test_solution_retention_map_covers_migrated_schema(self):
        require_coverage(self.tables, first_cell_map(self.docs["SOLUTION.md"], "tables"), "SOLUTION tables")


class DocumentationGuardSensitivityTests(unittest.TestCase):
    def test_historical_module_mention_cannot_cover_current_map_omission(self):
        source = 'MODULES="store.py future_module.py"\n'
        doc = ("Historical release: `future_module.py`.\n"
               "<!-- documentation-contract:modules -->\n`store.py` `tg_ingest_agent.py`\n"
               "<!-- /documentation-contract:modules -->")
        with self.assertRaisesRegex(AssertionError, "future_module.py"):
            require_coverage(module_inventory(source), documented_modules(doc), "modules")

    def test_recursive_new_service_requires_a_map_row(self):
        with tempfile.TemporaryDirectory(prefix="cara-doc-service-") as temp:
            root = Path(temp)
            (root / "ops").mkdir()
            (root / "ops" / "future.service").write_text("[Service]\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "ops/future.service"):
                require_coverage(service_inventory(root), {}, "services")

    def test_new_live_table_requires_a_retention_class(self):
        with tempfile.TemporaryDirectory(prefix="cara-doc-table-") as temp:
            conn = store.open_db(Path(temp) / "synthetic.db")
            try:
                before = {row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                conn.execute("CREATE TABLE future_evidence(id INTEGER PRIMARY KEY)")
                after = {row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                with self.assertRaisesRegex(AssertionError, "future_evidence"):
                    require_coverage(after, before, "tables")
            finally:
                conn.close()

    def test_missing_retention_class_is_refused(self):
        doc = ("<!-- documentation-contract:tables -->\n| `evidence` | | purpose |\n"
               "<!-- /documentation-contract:tables -->")
        with self.assertRaisesRegex(ValueError, "no policy/responsibility"):
            first_cell_map(doc, "tables")

    def test_duplicate_and_stale_rows_are_refused(self):
        doc = ("<!-- documentation-contract:tables -->\n"
               "| `evidence` | durable | purpose |\n| `evidence` | durable | purpose |\n"
               "<!-- /documentation-contract:tables -->")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            first_cell_map(doc, "tables")
        with self.assertRaisesRegex(AssertionError, "stale=.*retired"):
            require_coverage({"evidence"}, {"evidence", "retired"}, "tables")

    def test_duplicate_or_reversed_current_maps_are_refused(self):
        start = "<!-- documentation-contract:modules -->"
        end = "<!-- /documentation-contract:modules -->"
        for text in (start + end + start + end, end + start):
            with self.subTest(text=text), self.assertRaises(ValueError):
                contract_block(text, "modules")


if __name__ == "__main__":
    unittest.main()
