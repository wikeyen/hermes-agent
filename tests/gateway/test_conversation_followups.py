from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import ConversationFollowupConfig, GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource, SessionStore


def _make_source(chat_type: str = "dm") -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        user_name="mike",
        chat_id="c1",
        chat_type=chat_type,
    )


def _make_entry(*, reason: str | None = None, pending: bool = False, due_at: datetime | None = None) -> SessionEntry:
    now = datetime.now()
    return SessionEntry(
        session_key="agent:main:telegram:dm:c1",
        session_id="sess-1",
        created_at=now,
        updated_at=now,
        origin=_make_source(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
        followup_pending=pending,
        followup_reason=reason,
        followup_due_at=due_at,
        followup_context="the thing you mentioned",
    )


def _make_runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
        conversation_followups=ConversationFollowupConfig(
            enabled=True,
            check_interval_seconds=1,
            min_minutes_between_followups=240,
            question_min_delay_minutes=120,
            question_max_delay_minutes=240,
            emotional_min_delay_minutes=240,
            emotional_max_delay_minutes=480,
            task_min_delay_minutes=360,
            task_max_delay_minutes=720,
            checkout_min_delay_minutes=1440,
            checkout_max_delay_minutes=2880,
            min_turns_for_checkout=4,
        ),
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._running = True
    runner._draining = False
    runner._running_agents = {}
    runner._background_tasks = set()
    runner.session_store = MagicMock()
    return runner


class TestConversationFollowupConfig:
    def test_roundtrip_preserves_random_windows_and_minimum_interval(self):
        original = ConversationFollowupConfig(
            enabled=True,
            min_minutes_between_followups=360,
            checkout_min_delay_minutes=1500,
            checkout_max_delay_minutes=2880,
        )

        restored = ConversationFollowupConfig.from_dict(original.to_dict())

        assert restored.enabled is True
        assert restored.min_minutes_between_followups == 360
        assert restored.checkout_min_delay_minutes == 1500
        assert restored.checkout_max_delay_minutes == 2880


class TestSessionStoreConversationFollowups:
    def test_schedule_and_mark_followup_roundtrip(self, tmp_path):
        store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
        source = _make_source()
        entry = store.get_or_create_session(source)
        due_at = datetime.now() + timedelta(hours=8)

        store.schedule_followup(
            entry.session_key,
            reason="checkout",
            due_at=due_at,
            context="rough day",
        )

        pending = store._entries[entry.session_key]
        assert pending.followup_pending is True
        assert pending.followup_reason == "checkout"
        assert pending.followup_due_at == due_at
        assert pending.followup_context == "rough day"

        store.mark_followup_sent(entry.session_key, sent_at=due_at)
        sent = store._entries[entry.session_key]
        assert sent.followup_pending is False
        assert sent.followup_sent_at == due_at

    def test_get_due_followups_only_returns_pending_due_entries(self, tmp_path):
        store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
        source = _make_source()
        entry = store.get_or_create_session(source)
        due_at = datetime.now() - timedelta(minutes=1)

        store.schedule_followup(entry.session_key, reason="checkout", due_at=due_at, context="anchor")
        due_entries = store.get_due_followups(now=datetime.now())

        assert [item.session_key for item in due_entries] == [entry.session_key]

    def test_get_or_create_session_clears_pending_followup_when_user_returns(self, tmp_path):
        store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
        source = _make_source()
        entry = store.get_or_create_session(source)
        due_at = datetime.now() + timedelta(hours=6)
        store.schedule_followup(entry.session_key, reason="checkout", due_at=due_at, context="anchor")

        reused = store.get_or_create_session(source)

        assert reused.followup_pending is False
        assert reused.followup_reason is None
        assert reused.followup_due_at is None
        assert reused.followup_context is None


class TestConversationFollowupRunner:
    def test_classifier_detects_question_followup(self):
        runner = _make_runner()

        result = runner._classify_conversation_followup(
            source=_make_source(),
            latest_user_text="I can check that later.",
            latest_assistant_text="Want me to remind you tomorrow?",
            transcript=[],
        )

        assert result is not None
        assert result["reason"] == "task"

    def test_classifier_detects_checkout_for_meaningful_non_question_dm(self):
        runner = _make_runner()
        transcript = [
            {"role": "user", "content": "Tomorrow is a big day and I keep replaying how I want it to go."},
            {"role": "assistant", "content": "That makes sense. No need to overwork it tonight."},
            {"role": "user", "content": "Yeah. I mostly want to walk in clear and not overcomplicate it."},
            {"role": "assistant", "content": "Then keep tonight simple and leave yourself some room to breathe."},
        ]

        result = runner._classify_conversation_followup(
            source=_make_source(),
            latest_user_text=transcript[-2]["content"],
            latest_assistant_text=transcript[-1]["content"],
            transcript=transcript,
        )

        assert result is not None
        assert result["reason"] == "checkout"

    def test_schedule_uses_random_window_and_respects_minimum_interval(self, monkeypatch):
        runner = _make_runner()
        session_entry = _make_entry()
        session_entry.followup_sent_at = datetime.now() - timedelta(minutes=60)
        runner.session_store.schedule_followup = MagicMock()
        runner.session_store.clear_followup = MagicMock()
        monkeypatch.setattr("gateway.run.random.randint", lambda a, b: 1500)

        runner._maybe_schedule_conversation_followup(
            source=_make_source(),
            session_entry=session_entry,
            latest_user_text="I just want to get through tomorrow in one piece.",
            latest_assistant_text="Got it. Keep tonight light.",
            transcript=[
                {"role": "user", "content": "Big day tomorrow."},
                {"role": "assistant", "content": "Keep it simple tonight."},
                {"role": "user", "content": "I just want to get through tomorrow in one piece."},
                {"role": "assistant", "content": "Got it. Keep tonight light."},
            ],
        )

        _, kwargs = runner.session_store.schedule_followup.call_args
        due_at = kwargs["due_at"]
        minimum_due = session_entry.followup_sent_at + timedelta(
            minutes=runner.config.conversation_followups.min_minutes_between_followups
        )
        assert due_at >= minimum_due
        assert kwargs["reason"] == "task"

    def test_build_message_uses_checkout_context(self):
        runner = _make_runner()
        entry = _make_entry(reason="checkout", pending=True, due_at=datetime.now())

        message = runner._build_conversation_followup_message(entry, transcript=[])

        assert "checking in" in message.lower()
        assert "thing you mentioned" in message.lower()

    @pytest.mark.asyncio
    async def test_watcher_sends_due_followup_and_marks_sent(self):
        runner = _make_runner()
        entry = _make_entry(reason="checkout", pending=True, due_at=datetime.now() - timedelta(minutes=5))
        runner.session_store.get_due_followups.return_value = [entry]
        runner.session_store.load_transcript.return_value = []
        runner.session_store.mark_followup_sent = MagicMock()
        runner.session_store.append_to_transcript = MagicMock()

        await runner._conversation_followup_watcher(initial_delay=0, _max_loops=1)

        runner.adapters[Platform.TELEGRAM].send.assert_awaited_once()
        runner.session_store.mark_followup_sent.assert_called_once_with(entry.session_key)
        runner.session_store.append_to_transcript.assert_called_once()
