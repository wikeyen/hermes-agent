from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import (
    ConversationFollowupConfig,
    GatewayConfig,
    Platform,
    PlatformConfig,
)
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource


class _FakeAdapter:
    def __init__(self):
        self.send = AsyncMock()


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
        conversation_followups=ConversationFollowupConfig(
            enabled=True,
            check_interval_seconds=30,
            question_delay_minutes=45,
            emotional_delay_minutes=15,
            task_delay_minutes=120,
        ),
    )
    runner.adapters = {Platform.TELEGRAM: _FakeAdapter()}
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner.session_store = MagicMock()
    return runner


def _make_entry(reason="question", context="Do you want me to handle it?"):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type="dm",
        user_id="u1",
        user_name="Mike",
    )
    return SessionEntry(
        session_key="sess-key",
        session_id="sess-id",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        followup_pending=True,
        followup_reason=reason,
        followup_due_at=datetime.now() - timedelta(minutes=1),
        followup_context=context,
    )


def test_classify_followup_question():
    runner = _make_runner()
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="c1", chat_type="dm")

    reason, delay, context = runner._classify_conversation_followup(
        source=source,
        user_text="I am not sure yet",
        assistant_text="What do you want to do next?",
    )

    assert reason == "question"
    assert delay == 45
    assert "What do you want to do next" in context


def test_classify_followup_emotional_beats_question():
    runner = _make_runner()
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="c1", chat_type="dm")

    reason, delay, _ = runner._classify_conversation_followup(
        source=source,
        user_text="I feel overwhelmed and tired",
        assistant_text="Do you want to talk about it?",
    )

    assert reason == "emotional"
    assert delay == 15


def test_build_followup_message_uses_reason_and_context():
    runner = _make_runner()
    entry = _make_entry(reason="task", context="send the investor update")

    msg = runner._build_conversation_followup_message(entry, [])

    assert "send the investor update" in msg
    assert "nudge" in msg.lower()


@pytest.mark.asyncio
async def test_schedule_followup_calls_session_store():
    runner = _make_runner()
    session_entry = _make_entry()

    await runner._maybe_schedule_conversation_followup(
        session_entry=session_entry,
        source=session_entry.origin,
        user_text="I need to send this later",
        assistant_text="Want me to remind you about it?",
    )

    runner.session_store.schedule_followup.assert_called_once()
    kwargs = runner.session_store.schedule_followup.call_args.kwargs
    assert kwargs["reason"] == "task"
    assert "send this later" in kwargs["context"]


@pytest.mark.asyncio
async def test_followup_watcher_sends_due_message_and_marks_sent(monkeypatch):
    runner = _make_runner()
    entry = _make_entry(reason="question", context="Want me to handle it?")
    runner.session_store.get_due_followups.side_effect = [[entry], []]
    runner.session_store._is_session_expired.return_value = False
    runner.session_store.load_transcript.return_value = [
        {"role": "user", "content": "I need to sort this out"},
        {"role": "assistant", "content": "Want me to handle it?"},
    ]

    sleep_calls = {"count": 0}

    async def _controlled_sleep(_seconds):
        sleep_calls["count"] += 1
        if sleep_calls["count"] > 1:
            runner._running = False

    monkeypatch.setattr("gateway.run.asyncio.sleep", _controlled_sleep)

    await runner._conversation_followup_watcher()

    runner.adapters[Platform.TELEGRAM].send.assert_awaited_once()
    sent_text = runner.adapters[Platform.TELEGRAM].send.await_args.args[1]
    assert "Want me to handle it" in sent_text
    runner.session_store.mark_followup_sent.assert_called_once_with("sess-key")
