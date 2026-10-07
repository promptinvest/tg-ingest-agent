import os
import tempfile
from pathlib import Path
from unittest import mock
import common

_TYPING_PATCH = None

def setUpModule():
    """Phase A (ADR-0007) sends a typing indicator at the top of every dispatch
    and of every suggest_row. In this offline suite that would be one real HTTPS
    call per dispatch, so it is neutralised ONCE here; tests that care about the
    indicator re-patch it on their own agent instance."""
    global _TYPING_PATCH
    import tg_ingest_agent
    _TYPING_PATCH = mock.patch.object(
        tg_ingest_agent.Agent, "send_chat_action",
        lambda self, chat_id, action="typing": None)
    _TYPING_PATCH.start()


def tearDownModule():
    if _TYPING_PATCH is not None:
        _TYPING_PATCH.stop()


def make_config(**overrides):
    runtime_root = Path(
        os.environ.get("CARA_TEST_RUNTIME_ROOT")
        or (Path(tempfile.gettempdir()) / f"cara-unit-{os.getpid()}"))
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_CHAT_IDS": "111",
        "DO_MODEL_ACCESS_KEY": "do-key",
        # Never let an offline/unit test inherit Cara's production persistence
        # defaults.  The Mentor runner supplies a root inside its scratch tree;
        # ordinary VPS/CI runs get a per-process /tmp tree.
        "DB_PATH": str(runtime_root / "tg-ingest-agent" / "ingest.db"),
        "MEDIA_DIR": str(runtime_root / "tg-ingest-agent" / "media"),
        "TASK_ARTIFACTS_DIR": str(
            runtime_root / "tg-ingest-agent" / "task-artifacts"),
        "TASK_WORKER_SPOOL": str(runtime_root / "cara-worker" / "spool"),
        "MENTOR_REVIEW_SPOOL": str(runtime_root / "cara-mentor" / "spool"),
        "MENTOR_RUNNER_SPOOL": str(
            runtime_root / "cara-mentor-runner" / "spool"),
    }
    env.update(overrides)
    return common.load_config(env)

