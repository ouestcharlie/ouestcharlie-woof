"""Tests for IndexingSessionManager."""

from woof.indexing_session_manager import IndexingSessionManager


def test_start_returns_token_and_status_running():
    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "")
    s = mgr.get(sid)
    assert s["status"] == "running"
    assert s["library_name"] == "lib"
    assert s["partition"] == ""
    assert s["progress"] == 0.0


def test_update_writes_progress():
    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "2024")
    mgr.update(sid, 42.0, 100.0, "msg")
    s = mgr.get(sid)
    assert s["progress"] == 42.0
    assert s["total"] == 100.0
    assert s["message"] == "msg"


def test_update_unknown_session_is_noop():
    mgr = IndexingSessionManager()
    mgr.update("no-such", 1.0, 10.0, "x")  # must not raise


def test_complete_stores_summary():
    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "")
    mgr.complete(sid, {"photosIndexed": 99})
    s = mgr.get(sid)
    assert s["status"] == "completed"
    assert s["summary"]["photosIndexed"] == 99


def test_fail_stores_error():
    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "")
    mgr.fail(sid, "boom")
    s = mgr.get(sid)
    assert s["status"] == "failed"
    assert s["error"] == "boom"


def test_get_unknown_returns_none():
    mgr = IndexingSessionManager()
    assert mgr.get("no-such") is None


def test_eviction_at_capacity():
    mgr = IndexingSessionManager(max_sessions=2)
    sid1 = mgr.start("l", "")
    sid2 = mgr.start("l", "")
    sid3 = mgr.start("l", "")  # should evict sid1
    assert mgr.get(sid1) is None
    assert mgr.get(sid2) is not None
    assert mgr.get(sid3) is not None


def test_session_has_started_at():
    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "p")
    s = mgr.get(sid)
    assert "started_at" in s
    assert "2026" in s["started_at"]  # UTC ISO timestamp present


def test_cancel_transitions_to_cancelling_and_calls_task_cancel():
    import asyncio
    from unittest.mock import MagicMock

    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "")
    task = MagicMock(spec=asyncio.Task)
    mgr.register_task(sid, task)
    result = mgr.cancel(sid)
    assert result is True
    assert mgr.get(sid)["status"] == "cancelling"
    task.cancel.assert_called_once()


def test_cancel_returns_false_for_unknown_session():
    mgr = IndexingSessionManager()
    assert mgr.cancel("no-such") is False


def test_cancel_returns_false_when_already_completed():
    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "")
    mgr.complete(sid, {})
    assert mgr.cancel(sid) is False


def test_cancelled_transitions_to_cancelled():
    mgr = IndexingSessionManager()
    sid = mgr.start("lib", "")
    mgr.cancelled(sid)
    assert mgr.get(sid)["status"] == "cancelled"


def test_cancelled_unknown_session_is_noop():
    mgr = IndexingSessionManager()
    mgr.cancelled("no-such")  # must not raise


def test_eviction_cleans_up_task():
    import asyncio
    from unittest.mock import MagicMock

    mgr = IndexingSessionManager(max_sessions=1)
    sid1 = mgr.start("lib", "")
    task1 = MagicMock(spec=asyncio.Task)
    mgr.register_task(sid1, task1)
    # Adding a second session evicts sid1
    mgr.start("lib", "")
    assert mgr.get(sid1) is None
    assert sid1 not in mgr._tasks
