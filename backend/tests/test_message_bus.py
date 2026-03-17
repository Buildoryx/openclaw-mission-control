# ruff: noqa
"""Tests for the Redis Streams message bus — BusEvent envelope and MessageBus.

Covers:
- BusEvent construction with all fields
- BusEvent.to_stream_fields serialisation correctness
- BusEvent.from_stream_fields deserialisation (bytes and str keys)
- Round-trip serialise → deserialise fidelity
- Optional field handling (correlation_id, agent_id)
- Payload JSON encoding/decoding
- Stream key helpers
- MessageBus initialisation, default config, client lazy creation
- MessageBus.publish / subscribe / ack stubs with mocked Redis
- Consumer group creation (idempotent)
- Stream length, trim, read_history
- Edge cases: empty payloads, special characters, large payloads
- BusEvent timestamp ISO format compliance
- _async_sleep helper
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.memory.message_bus import (
    BusEvent,
    MessageBus,
    _async_sleep,
    _consumer_group,
    _stream_key,
    message_bus,
)


# ---------------------------------------------------------------------------
# Stream key helpers
# ---------------------------------------------------------------------------


class TestStreamKey:
    """Tests for the _stream_key helper function."""

    def test_default_channel(self) -> None:
        board_id = uuid4()
        key = _stream_key(board_id)
        assert key == f"mc:streams:{board_id}:deliberation"

    def test_custom_channel(self) -> None:
        board_id = uuid4()
        key = _stream_key(board_id, "custom-channel")
        assert key == f"mc:streams:{board_id}:custom-channel"

    def test_key_contains_board_id(self) -> None:
        board_id = uuid4()
        key = _stream_key(board_id)
        assert str(board_id) in key

    def test_key_has_prefix(self) -> None:
        key = _stream_key(uuid4())
        assert key.startswith("mc:streams:")

    def test_different_boards_produce_different_keys(self) -> None:
        key1 = _stream_key(uuid4())
        key2 = _stream_key(uuid4())
        assert key1 != key2

    def test_same_board_same_channel_produces_same_key(self) -> None:
        board_id = uuid4()
        key1 = _stream_key(board_id, "deliberation")
        key2 = _stream_key(board_id, "deliberation")
        assert key1 == key2

    def test_same_board_different_channels_produce_different_keys(self) -> None:
        board_id = uuid4()
        key1 = _stream_key(board_id, "deliberation")
        key2 = _stream_key(board_id, "notifications")
        assert key1 != key2


class TestConsumerGroup:
    """Tests for the _consumer_group helper."""

    def test_default_group(self) -> None:
        assert _consumer_group() == "mc-workers"

    def test_custom_purpose(self) -> None:
        assert _consumer_group("custom-group") == "custom-group"

    def test_returns_string(self) -> None:
        result = _consumer_group()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# BusEvent construction
# ---------------------------------------------------------------------------


class TestBusEventConstruction:
    """Tests for BusEvent dataclass instantiation."""

    def test_required_fields(self) -> None:
        board_id = uuid4()
        event = BusEvent(
            event_type="deliberation.started",
            board_id=board_id,
            payload={"key": "value"},
        )
        assert event.event_type == "deliberation.started"
        assert event.board_id == board_id
        assert event.payload == {"key": "value"}

    def test_auto_generated_event_id(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        assert isinstance(event.event_id, UUID)

    def test_auto_generated_timestamp(self) -> None:
        before = datetime.now(UTC)
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        after = datetime.now(UTC)
        assert before <= event.timestamp <= after

    def test_optional_correlation_id_default_none(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        assert event.correlation_id is None

    def test_optional_agent_id_default_none(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        assert event.agent_id is None

    def test_explicit_correlation_id(self) -> None:
        cid = uuid4()
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
            correlation_id=cid,
        )
        assert event.correlation_id == cid

    def test_explicit_agent_id(self) -> None:
        aid = uuid4()
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
            agent_id=aid,
        )
        assert event.agent_id == aid

    def test_explicit_event_id(self) -> None:
        eid = uuid4()
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
            event_id=eid,
        )
        assert event.event_id == eid

    def test_explicit_timestamp(self) -> None:
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
            timestamp=ts,
        )
        assert event.timestamp == ts

    def test_event_is_frozen(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        with pytest.raises(AttributeError):
            event.event_type = "modified"  # type: ignore[misc]

    def test_all_fields_set(self) -> None:
        eid = uuid4()
        bid = uuid4()
        cid = uuid4()
        aid = uuid4()
        ts = datetime(2026, 6, 15, 10, 30, 0, tzinfo=UTC)
        payload = {"deliberation_id": "abc", "topic": "test"}

        event = BusEvent(
            event_type="deliberation.entry_added",
            board_id=bid,
            payload=payload,
            event_id=eid,
            timestamp=ts,
            correlation_id=cid,
            agent_id=aid,
        )

        assert event.event_type == "deliberation.entry_added"
        assert event.board_id == bid
        assert event.payload == payload
        assert event.event_id == eid
        assert event.timestamp == ts
        assert event.correlation_id == cid
        assert event.agent_id == aid


# ---------------------------------------------------------------------------
# BusEvent.to_stream_fields
# ---------------------------------------------------------------------------


class TestBusEventToStreamFields:
    """Tests for BusEvent serialisation to Redis Stream field-value pairs."""

    def _make_event(self, **kwargs: Any) -> BusEvent:
        defaults: dict[str, Any] = {
            "event_type": "deliberation.started",
            "board_id": uuid4(),
            "payload": {"topic": "auth module"},
        }
        defaults.update(kwargs)
        return BusEvent(**defaults)

    def test_returns_dict(self) -> None:
        fields = self._make_event().to_stream_fields()
        assert isinstance(fields, dict)

    def test_all_values_are_strings(self) -> None:
        event = self._make_event(
            correlation_id=uuid4(),
            agent_id=uuid4(),
        )
        fields = event.to_stream_fields()
        for key, value in fields.items():
            assert isinstance(key, str), f"Key {key!r} is not a string"
            assert isinstance(value, str), f"Value for {key!r} is not a string"

    def test_contains_required_keys(self) -> None:
        fields = self._make_event().to_stream_fields()
        required = {"event_id", "event_type", "board_id", "payload", "timestamp"}
        assert required.issubset(fields.keys())

    def test_event_type_preserved(self) -> None:
        fields = self._make_event(
            event_type="deliberation.concluded"
        ).to_stream_fields()
        assert fields["event_type"] == "deliberation.concluded"

    def test_board_id_is_string_uuid(self) -> None:
        bid = uuid4()
        fields = self._make_event(board_id=bid).to_stream_fields()
        assert fields["board_id"] == str(bid)
        UUID(fields["board_id"])  # Should not raise

    def test_event_id_is_string_uuid(self) -> None:
        eid = uuid4()
        fields = self._make_event(event_id=eid).to_stream_fields()
        assert fields["event_id"] == str(eid)

    def test_payload_is_json_string(self) -> None:
        payload = {"deliberation_id": "abc-123", "count": 42}
        fields = self._make_event(payload=payload).to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded == payload

    def test_timestamp_is_iso_format(self) -> None:
        ts = datetime(2026, 3, 5, 18, 30, 0, tzinfo=UTC)
        fields = self._make_event(timestamp=ts).to_stream_fields()
        parsed = datetime.fromisoformat(fields["timestamp"])
        assert parsed.year == 2026
        assert parsed.month == 3

    def test_correlation_id_present_when_set(self) -> None:
        cid = uuid4()
        fields = self._make_event(correlation_id=cid).to_stream_fields()
        assert "correlation_id" in fields
        assert fields["correlation_id"] == str(cid)

    def test_correlation_id_absent_when_none(self) -> None:
        fields = self._make_event(correlation_id=None).to_stream_fields()
        assert "correlation_id" not in fields

    def test_agent_id_present_when_set(self) -> None:
        aid = uuid4()
        fields = self._make_event(agent_id=aid).to_stream_fields()
        assert "agent_id" in fields
        assert fields["agent_id"] == str(aid)

    def test_agent_id_absent_when_none(self) -> None:
        fields = self._make_event(agent_id=None).to_stream_fields()
        assert "agent_id" not in fields

    def test_empty_payload(self) -> None:
        fields = self._make_event(payload={}).to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded == {}

    def test_nested_payload(self) -> None:
        payload = {
            "outer": {
                "inner": [1, 2, 3],
                "nested_str": "hello",
            },
            "list": [{"a": 1}, {"b": 2}],
        }
        fields = self._make_event(payload=payload).to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded == payload

    def test_payload_with_uuid_values(self) -> None:
        """UUIDs in payload should be serialised as strings via default=str."""
        some_uuid = uuid4()
        payload = {"id": some_uuid}
        fields = self._make_event(payload=payload).to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["id"] == str(some_uuid)

    def test_payload_with_special_characters(self) -> None:
        payload = {"text": "Hello 'world' \"quotes\" <angle> & amp"}
        fields = self._make_event(payload=payload).to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["text"] == payload["text"]

    def test_payload_with_unicode(self) -> None:
        payload = {"text": "日本語 🎉 Ελληνικά"}
        fields = self._make_event(payload=payload).to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["text"] == payload["text"]

    def test_compact_json_separators(self) -> None:
        """Payload JSON should use compact separators (no spaces)."""
        fields = self._make_event(payload={"a": 1}).to_stream_fields()
        raw = fields["payload"]
        # Compact separators mean no spaces after : or ,
        assert " " not in raw or raw.count(" ") == 0


# ---------------------------------------------------------------------------
# BusEvent.from_stream_fields
# ---------------------------------------------------------------------------


class TestBusEventFromStreamFields:
    """Tests for BusEvent deserialisation from Redis Stream entries."""

    def _round_trip_fields(self, event: BusEvent) -> dict[str, str]:
        return event.to_stream_fields()

    def _round_trip_bytes_fields(self, event: BusEvent) -> dict[bytes, bytes]:
        str_fields = event.to_stream_fields()
        return {k.encode(): v.encode() for k, v in str_fields.items()}

    def test_round_trip_string_keys(self) -> None:
        original = BusEvent(
            event_type="deliberation.started",
            board_id=uuid4(),
            payload={"topic": "test"},
            correlation_id=uuid4(),
            agent_id=uuid4(),
        )
        fields = self._round_trip_fields(original)
        restored = BusEvent.from_stream_fields(fields)

        assert restored.event_id == original.event_id
        assert restored.event_type == original.event_type
        assert restored.board_id == original.board_id
        assert restored.correlation_id == original.correlation_id
        assert restored.agent_id == original.agent_id
        assert restored.payload == original.payload

    def test_round_trip_bytes_keys(self) -> None:
        original = BusEvent(
            event_type="deliberation.entry_added",
            board_id=uuid4(),
            payload={"entry_id": "abc"},
            agent_id=uuid4(),
        )
        fields = self._round_trip_bytes_fields(original)
        restored = BusEvent.from_stream_fields(fields)

        assert restored.event_id == original.event_id
        assert restored.event_type == original.event_type
        assert restored.board_id == original.board_id
        assert restored.agent_id == original.agent_id

    def test_without_optional_fields(self) -> None:
        original = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={"simple": True},
        )
        fields = self._round_trip_fields(original)
        restored = BusEvent.from_stream_fields(fields)

        assert restored.correlation_id is None
        assert restored.agent_id is None

    def test_payload_deserialized_correctly(self) -> None:
        payload = {"count": 42, "tags": ["a", "b"], "nested": {"x": 1}}
        original = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload=payload,
        )
        fields = self._round_trip_fields(original)
        restored = BusEvent.from_stream_fields(fields)
        assert restored.payload == payload

    def test_empty_payload_deserialized(self) -> None:
        original = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        fields = self._round_trip_fields(original)
        restored = BusEvent.from_stream_fields(fields)
        assert restored.payload == {}

    def test_timestamp_preserved(self) -> None:
        ts = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
        original = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
            timestamp=ts,
        )
        fields = self._round_trip_fields(original)
        restored = BusEvent.from_stream_fields(fields)
        assert restored.timestamp.year == 2026
        assert restored.timestamp.month == 7
        assert restored.timestamp.day == 4

    def test_missing_payload_key_uses_empty_dict(self) -> None:
        """If 'payload' key is missing from fields, default to empty dict."""
        fields: dict[str, str] = {
            "event_id": str(uuid4()),
            "event_type": "test",
            "board_id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        restored = BusEvent.from_stream_fields(fields)
        assert restored.payload == {}

    def test_mixed_bytes_and_string_keys(self) -> None:
        """Handle a mix of bytes and string keys/values."""
        event_id = uuid4()
        board_id = uuid4()
        fields: dict[bytes | str, bytes | str] = {
            b"event_id": str(event_id).encode(),
            "event_type": "test.mixed",
            b"board_id": str(board_id),
            "payload": b"{}",
            b"timestamp": datetime.now(UTC).isoformat().encode(),
        }
        restored = BusEvent.from_stream_fields(fields)
        assert restored.event_id == event_id
        assert restored.event_type == "test.mixed"
        assert restored.board_id == board_id

    def test_correlation_id_empty_string_treated_as_none(self) -> None:
        """Empty correlation_id string should result in None or raise."""
        fields: dict[str, str] = {
            "event_id": str(uuid4()),
            "event_type": "test",
            "board_id": str(uuid4()),
            "payload": "{}",
            "timestamp": datetime.now(UTC).isoformat(),
            "correlation_id": "",
        }
        # Empty string is falsy, so from_stream_fields should treat it as None
        restored = BusEvent.from_stream_fields(fields)
        assert restored.correlation_id is None

    def test_agent_id_empty_string_treated_as_none(self) -> None:
        fields: dict[str, str] = {
            "event_id": str(uuid4()),
            "event_type": "test",
            "board_id": str(uuid4()),
            "payload": "{}",
            "timestamp": datetime.now(UTC).isoformat(),
            "agent_id": "",
        }
        restored = BusEvent.from_stream_fields(fields)
        assert restored.agent_id is None


# ---------------------------------------------------------------------------
# BusEvent round-trip fidelity
# ---------------------------------------------------------------------------


class TestBusEventRoundTrip:
    """Verify that serialise → deserialise preserves all fields exactly."""

    def _round_trip(self, event: BusEvent) -> BusEvent:
        fields = event.to_stream_fields()
        return BusEvent.from_stream_fields(fields)

    def test_all_event_types(self) -> None:
        event_types = [
            "deliberation.started",
            "deliberation.entry_added",
            "deliberation.phase_advanced",
            "deliberation.synthesis_submitted",
            "deliberation.concluded",
            "deliberation.abandoned",
            "agent.position_changed",
            "memory.promoted",
        ]
        for et in event_types:
            original = BusEvent(
                event_type=et,
                board_id=uuid4(),
                payload={"type": et},
            )
            restored = self._round_trip(original)
            assert restored.event_type == et
            assert restored.payload["type"] == et

    def test_complex_payload(self) -> None:
        payload = {
            "deliberation_id": str(uuid4()),
            "entry_count": 42,
            "participants": [str(uuid4()) for _ in range(5)],
            "nested": {
                "tags": ["a", "b", "c"],
                "confidence": 0.95,
                "null_value": None,
            },
            "boolean": True,
        }
        original = BusEvent(
            event_type="deliberation.concluded",
            board_id=uuid4(),
            payload=payload,
            correlation_id=uuid4(),
            agent_id=uuid4(),
        )
        restored = self._round_trip(original)
        assert restored.payload == payload

    def test_uuid_fields_preserved(self) -> None:
        event_id = uuid4()
        board_id = uuid4()
        correlation_id = uuid4()
        agent_id = uuid4()

        original = BusEvent(
            event_type="test",
            board_id=board_id,
            payload={},
            event_id=event_id,
            correlation_id=correlation_id,
            agent_id=agent_id,
        )
        restored = self._round_trip(original)

        assert restored.event_id == event_id
        assert restored.board_id == board_id
        assert restored.correlation_id == correlation_id
        assert restored.agent_id == agent_id

    def test_bytes_round_trip(self) -> None:
        """Simulate Redis returning bytes instead of strings."""
        original = BusEvent(
            event_type="deliberation.started",
            board_id=uuid4(),
            payload={"topic": "test"},
            agent_id=uuid4(),
        )
        str_fields = original.to_stream_fields()
        byte_fields = {k.encode(): v.encode() for k, v in str_fields.items()}
        restored = BusEvent.from_stream_fields(byte_fields)

        assert restored.event_id == original.event_id
        assert restored.event_type == original.event_type
        assert restored.board_id == original.board_id
        assert restored.agent_id == original.agent_id

    def test_many_round_trips(self) -> None:
        """Multiple round trips should be fully lossless."""
        event = BusEvent(
            event_type="deliberation.entry_added",
            board_id=uuid4(),
            payload={"seq": 7, "type": "thesis"},
            correlation_id=uuid4(),
            agent_id=uuid4(),
        )
        for _ in range(10):
            event = self._round_trip(event)

        assert event.event_type == "deliberation.entry_added"
        assert event.payload["seq"] == 7
        assert event.payload["type"] == "thesis"


# ---------------------------------------------------------------------------
# MessageBus initialisation
# ---------------------------------------------------------------------------


class TestMessageBusInit:
    """Tests for MessageBus construction and configuration."""

    def test_default_init(self) -> None:
        bus = MessageBus()
        assert bus._client is None

    def test_custom_redis_url(self) -> None:
        bus = MessageBus(redis_url="redis://custom:6379/1")
        assert bus._redis_url == "redis://custom:6379/1"

    def test_default_redis_url_from_settings(self) -> None:
        from app.core.config import settings

        bus = MessageBus()
        assert bus._redis_url == settings.rq_redis_url

    def test_maxlen_from_settings(self) -> None:
        from app.core.config import settings

        bus = MessageBus()
        assert bus._maxlen == settings.memory_stream_maxlen

    def test_client_initially_none(self) -> None:
        bus = MessageBus()
        assert bus._client is None

    def test_has_publish_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "publish", None))

    def test_has_subscribe_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "subscribe", None))

    def test_has_close_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "close", None))

    def test_has_ensure_consumer_group_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "ensure_consumer_group", None))

    def test_has_consume_group_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "consume_group", None))

    def test_has_ack_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "ack", None))

    def test_has_stream_length_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "stream_length", None))

    def test_has_trim_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "trim", None))

    def test_has_read_history_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "read_history", None))

    def test_has_publish_many_method(self) -> None:
        bus = MessageBus()
        assert callable(getattr(bus, "publish_many", None))


# ---------------------------------------------------------------------------
# MessageBus.publish with mocked Redis
# ---------------------------------------------------------------------------


class TestMessageBusPublish:
    """Tests for the publish method with a mocked async Redis client."""

    @pytest.mark.asyncio
    async def test_publish_calls_xadd(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value=b"1234567890-0")
        bus._client = mock_client

        event = BusEvent(
            event_type="deliberation.started",
            board_id=uuid4(),
            payload={"topic": "test"},
        )
        result = await bus.publish(event)

        assert result == "1234567890-0"
        mock_client.xadd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_uses_correct_stream_key(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value=b"1-0")
        bus._client = mock_client

        board_id = uuid4()
        event = BusEvent(
            event_type="test",
            board_id=board_id,
            payload={},
        )
        await bus.publish(event)

        call_args = mock_client.xadd.call_args
        stream_name = call_args[0][0]
        expected_stream = _stream_key(board_id)
        assert stream_name == expected_stream

    @pytest.mark.asyncio
    async def test_publish_passes_maxlen(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value=b"1-0")
        bus._client = mock_client

        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        await bus.publish(event)

        call_kwargs = mock_client.xadd.call_args
        assert call_kwargs.kwargs.get("maxlen") == bus._maxlen
        assert call_kwargs.kwargs.get("approximate") is True

    @pytest.mark.asyncio
    async def test_publish_returns_none_on_exception(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(side_effect=ConnectionError("redis down"))
        bus._client = mock_client

        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        result = await bus.publish(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_publish_decodes_bytes_id(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value=b"9999999999-42")
        bus._client = mock_client

        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        result = await bus.publish(event)
        assert result == "9999999999-42"

    @pytest.mark.asyncio
    async def test_publish_handles_string_id(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value="1111111111-0")
        bus._client = mock_client

        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        result = await bus.publish(event)
        assert result == "1111111111-0"


# ---------------------------------------------------------------------------
# MessageBus.publish_many
# ---------------------------------------------------------------------------


class TestMessageBusPublishMany:
    """Tests for the publish_many method."""

    @pytest.mark.asyncio
    async def test_publish_many_empty_list(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        bus._client = mock_client
        count = await bus.publish_many([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_publish_many_all_succeed(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xadd = AsyncMock(return_value=b"1-0")
        bus._client = mock_client

        events = [
            BusEvent(event_type="test", board_id=uuid4(), payload={}) for _ in range(5)
        ]
        count = await bus.publish_many(events)
        assert count == 5

    @pytest.mark.asyncio
    async def test_publish_many_partial_failure(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        call_count = 0

        async def flaky_xadd(*args: Any, **kwargs: Any) -> bytes | None:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise ConnectionError("intermittent failure")
            return b"1-0"

        mock_client.xadd = flaky_xadd
        bus._client = mock_client

        events = [
            BusEvent(event_type="test", board_id=uuid4(), payload={}) for _ in range(4)
        ]
        count = await bus.publish_many(events)
        # Events 1, 3 succeed (odd call_count); events 2, 4 fail
        assert count == 2


# ---------------------------------------------------------------------------
# MessageBus.ack
# ---------------------------------------------------------------------------


class TestMessageBusAck:
    """Tests for the ack method."""

    @pytest.mark.asyncio
    async def test_ack_calls_xack(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xack = AsyncMock(return_value=1)
        bus._client = mock_client

        board_id = uuid4()
        result = await bus.ack(board_id, "mc-workers", "1234-0")
        assert result is True
        mock_client.xack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ack_returns_false_on_zero(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xack = AsyncMock(return_value=0)
        bus._client = mock_client

        result = await bus.ack(uuid4(), "mc-workers", "1234-0")
        assert result is False

    @pytest.mark.asyncio
    async def test_ack_returns_false_on_exception(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xack = AsyncMock(side_effect=Exception("boom"))
        bus._client = mock_client

        result = await bus.ack(uuid4(), "mc-workers", "1234-0")
        assert result is False

    @pytest.mark.asyncio
    async def test_ack_uses_correct_stream_key(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xack = AsyncMock(return_value=1)
        bus._client = mock_client

        board_id = uuid4()
        await bus.ack(board_id, "my-group", "555-0", channel="custom")
        call_args = mock_client.xack.call_args
        expected_stream = _stream_key(board_id, "custom")
        assert call_args[0][0] == expected_stream
        assert call_args[0][1] == "my-group"
        assert call_args[0][2] == "555-0"


# ---------------------------------------------------------------------------
# MessageBus.stream_length
# ---------------------------------------------------------------------------


class TestMessageBusStreamLength:
    """Tests for the stream_length method."""

    @pytest.mark.asyncio
    async def test_returns_length(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xlen = AsyncMock(return_value=42)
        bus._client = mock_client

        length = await bus.stream_length(uuid4())
        assert length == 42

    @pytest.mark.asyncio
    async def test_returns_zero_on_exception(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xlen = AsyncMock(side_effect=Exception("no stream"))
        bus._client = mock_client

        length = await bus.stream_length(uuid4())
        assert length == 0

    @pytest.mark.asyncio
    async def test_uses_correct_stream_key(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xlen = AsyncMock(return_value=10)
        bus._client = mock_client

        board_id = uuid4()
        await bus.stream_length(board_id, channel="notifications")
        call_args = mock_client.xlen.call_args
        expected = _stream_key(board_id, "notifications")
        assert call_args[0][0] == expected


# ---------------------------------------------------------------------------
# MessageBus.read_history
# ---------------------------------------------------------------------------


class TestMessageBusReadHistory:
    """Tests for the read_history method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_messages(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xrange = AsyncMock(return_value=[])
        bus._client = mock_client

        events = await bus.read_history(uuid4())
        assert events == []

    @pytest.mark.asyncio
    async def test_deserialises_stream_entries(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        original_event = BusEvent(
            event_type="deliberation.started",
            board_id=uuid4(),
            payload={"topic": "test"},
        )
        stream_fields = {
            k.encode(): v.encode() for k, v in original_event.to_stream_fields().items()
        }

        mock_client.xrange = AsyncMock(return_value=[(b"1234-0", stream_fields)])
        bus._client = mock_client

        events = await bus.read_history(original_event.board_id)
        assert len(events) == 1
        assert events[0].event_type == "deliberation.started"
        assert events[0].event_id == original_event.event_id

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_exception(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xrange = AsyncMock(side_effect=Exception("redis error"))
        bus._client = mock_client

        events = await bus.read_history(uuid4())
        assert events == []

    @pytest.mark.asyncio
    async def test_skips_malformed_entries(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        # One good entry, one malformed
        good_event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={},
        )
        good_fields = {
            k.encode(): v.encode() for k, v in good_event.to_stream_fields().items()
        }
        bad_fields = {b"garbage_key": b"garbage_value"}

        mock_client.xrange = AsyncMock(
            return_value=[
                (b"1-0", good_fields),
                (b"2-0", bad_fields),
            ]
        )
        bus._client = mock_client

        events = await bus.read_history(uuid4())
        assert len(events) == 1
        assert events[0].event_id == good_event.event_id


# ---------------------------------------------------------------------------
# MessageBus.trim
# ---------------------------------------------------------------------------


class TestMessageBusTrim:
    """Tests for the trim method."""

    @pytest.mark.asyncio
    async def test_trim_returns_removed_count(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xlen = AsyncMock(side_effect=[100, 50])
        mock_client.xtrim = AsyncMock()
        bus._client = mock_client

        removed = await bus.trim(uuid4(), maxlen=50)
        assert removed == 50

    @pytest.mark.asyncio
    async def test_trim_returns_zero_on_exception(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xlen = AsyncMock(side_effect=Exception("error"))
        bus._client = mock_client

        removed = await bus.trim(uuid4())
        assert removed == 0

    @pytest.mark.asyncio
    async def test_trim_uses_default_maxlen(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xlen = AsyncMock(return_value=0)
        mock_client.xtrim = AsyncMock()
        bus._client = mock_client

        await bus.trim(uuid4())
        xtrim_kwargs = mock_client.xtrim.call_args.kwargs
        assert xtrim_kwargs["maxlen"] == bus._maxlen


# ---------------------------------------------------------------------------
# MessageBus.ensure_consumer_group
# ---------------------------------------------------------------------------


class TestMessageBusEnsureConsumerGroup:
    """Tests for the ensure_consumer_group method."""

    @pytest.mark.asyncio
    async def test_creates_group_returns_true(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xgroup_create = AsyncMock(return_value=True)
        bus._client = mock_client

        result = await bus.ensure_consumer_group(uuid4(), "test-group")
        assert result is True

    @pytest.mark.asyncio
    async def test_existing_group_returns_false(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        import redis.asyncio as aioredis

        mock_client.xgroup_create = AsyncMock(
            side_effect=aioredis.ResponseError(
                "BUSYGROUP Consumer Group name already exists"
            )
        )
        bus._client = mock_client

        result = await bus.ensure_consumer_group(uuid4(), "test-group")
        assert result is False

    @pytest.mark.asyncio
    async def test_other_redis_error_raises(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        import redis.asyncio as aioredis

        mock_client.xgroup_create = AsyncMock(
            side_effect=aioredis.ResponseError("some other error")
        )
        bus._client = mock_client

        with pytest.raises(aioredis.ResponseError):
            await bus.ensure_consumer_group(uuid4(), "test-group")

    @pytest.mark.asyncio
    async def test_creates_stream_with_mkstream(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.xgroup_create = AsyncMock(return_value=True)
        bus._client = mock_client

        await bus.ensure_consumer_group(uuid4(), "my-group")
        call_kwargs = mock_client.xgroup_create.call_args.kwargs
        assert call_kwargs.get("mkstream") is True


# ---------------------------------------------------------------------------
# MessageBus.close
# ---------------------------------------------------------------------------


class TestMessageBusClose:
    """Tests for the close method."""

    @pytest.mark.asyncio
    async def test_close_when_client_is_none(self) -> None:
        bus = MessageBus()
        # Should not raise even when no client exists
        await bus.close()
        assert bus._client is None

    @pytest.mark.asyncio
    async def test_close_resets_client_to_none(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        bus._client = mock_client

        await bus.close()
        assert bus._client is None

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        bus._client = mock_client

        await bus.close()
        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestMessageBusSingleton:
    """Verify the module-level message_bus singleton."""

    def test_singleton_exists(self) -> None:
        assert message_bus is not None

    def test_singleton_is_correct_type(self) -> None:
        assert isinstance(message_bus, MessageBus)

    def test_singleton_has_default_config(self) -> None:
        from app.core.config import settings

        assert message_bus._redis_url == settings.rq_redis_url
        assert message_bus._maxlen == settings.memory_stream_maxlen


# ---------------------------------------------------------------------------
# _async_sleep helper
# ---------------------------------------------------------------------------


class TestAsyncSleepHelper:
    """Tests for the _async_sleep helper function."""

    @pytest.mark.asyncio
    async def test_completes_without_error(self) -> None:
        # Just verify it doesn't raise
        await _async_sleep(0.001)

    @pytest.mark.asyncio
    async def test_zero_sleep(self) -> None:
        await _async_sleep(0)

    @pytest.mark.asyncio
    async def test_is_coroutine_function(self) -> None:
        assert asyncio.iscoroutinefunction(_async_sleep)


# ---------------------------------------------------------------------------
# BusEvent with various event_type values
# ---------------------------------------------------------------------------


class TestBusEventEventTypes:
    """Verify BusEvent works with all documented event types."""

    EVENT_TYPES = [
        "deliberation.started",
        "deliberation.entry_added",
        "deliberation.phase_advanced",
        "deliberation.synthesis_submitted",
        "deliberation.concluded",
        "deliberation.abandoned",
        "agent.position_changed",
        "memory.promoted",
    ]

    @pytest.mark.parametrize("event_type", EVENT_TYPES)
    def test_event_type_preserved(self, event_type: str) -> None:
        event = BusEvent(
            event_type=event_type,
            board_id=uuid4(),
            payload={},
        )
        assert event.event_type == event_type

    @pytest.mark.parametrize("event_type", EVENT_TYPES)
    def test_event_type_round_trip(self, event_type: str) -> None:
        event = BusEvent(
            event_type=event_type,
            board_id=uuid4(),
            payload={"event": event_type},
        )
        fields = event.to_stream_fields()
        restored = BusEvent.from_stream_fields(fields)
        assert restored.event_type == event_type

    def test_custom_event_type(self) -> None:
        event = BusEvent(
            event_type="custom.user_defined",
            board_id=uuid4(),
            payload={},
        )
        assert event.event_type == "custom.user_defined"

    def test_empty_event_type(self) -> None:
        """Empty string is technically allowed by the dataclass."""
        event = BusEvent(
            event_type="",
            board_id=uuid4(),
            payload={},
        )
        assert event.event_type == ""


# ---------------------------------------------------------------------------
# BusEvent payload edge cases
# ---------------------------------------------------------------------------


class TestBusEventPayloadEdgeCases:
    """Test edge cases in payload serialisation."""

    def test_payload_with_none_value(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={"key": None},
        )
        fields = event.to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["key"] is None

    def test_payload_with_boolean_values(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={"yes": True, "no": False},
        )
        fields = event.to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["yes"] is True
        assert decoded["no"] is False

    def test_payload_with_numeric_values(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={"int": 42, "float": 3.14, "negative": -7},
        )
        fields = event.to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["int"] == 42
        assert decoded["float"] == pytest.approx(3.14)
        assert decoded["negative"] == -7

    def test_payload_with_list_of_lists(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={"matrix": [[1, 2], [3, 4]]},
        )
        fields = event.to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["matrix"] == [[1, 2], [3, 4]]

    def test_payload_with_empty_string_value(self) -> None:
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={"empty": ""},
        )
        fields = event.to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert decoded["empty"] == ""

    def test_large_payload(self) -> None:
        """A payload with many keys should serialise correctly."""
        payload = {f"key_{i}": f"value_{i}" for i in range(500)}
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload=payload,
        )
        fields = event.to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert len(decoded) == 500
        assert decoded["key_0"] == "value_0"
        assert decoded["key_499"] == "value_499"

    def test_payload_with_datetime_via_default_str(self) -> None:
        """datetime objects in payload should be serialised via default=str."""
        now = datetime.now(UTC)
        event = BusEvent(
            event_type="test",
            board_id=uuid4(),
            payload={"timestamp": now},
        )
        fields = event.to_stream_fields()
        decoded = json.loads(fields["payload"])
        assert isinstance(decoded["timestamp"], str)


# ---------------------------------------------------------------------------
# MessageBus._get_client (lazy initialisation)
# ---------------------------------------------------------------------------


class TestMessageBusGetClient:
    """Tests for the _get_client lazy Redis connection method."""

    @pytest.mark.asyncio
    async def test_creates_client_on_first_call(self) -> None:
        bus = MessageBus(redis_url="redis://localhost:6379/0")
        assert bus._client is None

        with patch("app.services.memory.message_bus.aioredis") as mock_aioredis:
            mock_conn = AsyncMock()
            mock_aioredis.from_url.return_value = mock_conn

            client = await bus._get_client()

            assert client is mock_conn
            assert bus._client is mock_conn
            mock_aioredis.from_url.assert_called_once_with(
                "redis://localhost:6379/0",
                decode_responses=False,
            )

    @pytest.mark.asyncio
    async def test_returns_existing_client_on_subsequent_calls(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        bus._client = mock_client

        with patch("app.services.memory.message_bus.aioredis") as mock_aioredis:
            client = await bus._get_client()

            assert client is mock_client
            mock_aioredis.from_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_reused_across_multiple_calls(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()
        bus._client = mock_client

        client1 = await bus._get_client()
        client2 = await bus._get_client()
        assert client1 is client2


# ---------------------------------------------------------------------------
# MessageBus.subscribe (async generator via xread)
#
# NOTE: We use ``asyncio.CancelledError`` (a BaseException, not Exception)
# to break the ``while True`` loops inside subscribe/consume_group.
# ``StopAsyncIteration`` is a subclass of ``Exception`` and would be caught
# by the broad ``except Exception`` error handlers in those methods, causing
# infinite loops in tests.
# ---------------------------------------------------------------------------


class TestMessageBusSubscribe:
    """Tests for the subscribe async generator with mocked Redis."""

    @pytest.mark.asyncio
    async def test_yields_events_from_xread(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(
            event_type="deliberation.started",
            board_id=board_id,
            payload={"topic": "test"},
        )
        stream_fields = {
            k.encode(): v.encode() for k, v in event.to_stream_fields().items()
        }
        stream_name = _stream_key(board_id).encode()

        # First call returns one message; we break after consuming it
        mock_client.xread = AsyncMock(
            side_effect=[
                [(stream_name, [(b"1-0", stream_fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        try:
            async for ev in bus.subscribe(board_id, last_id="0"):
                collected.append(ev)
                break  # Only consume one event
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        assert collected[0].event_type == "deliberation.started"
        assert collected[0].event_id == event.event_id

    @pytest.mark.asyncio
    async def test_subscribe_filters_by_event_type(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        wanted = BusEvent(
            event_type="deliberation.concluded",
            board_id=board_id,
            payload={},
        )
        unwanted = BusEvent(
            event_type="deliberation.started",
            board_id=board_id,
            payload={},
        )
        stream = _stream_key(board_id).encode()

        wanted_fields = {
            k.encode(): v.encode() for k, v in wanted.to_stream_fields().items()
        }
        unwanted_fields = {
            k.encode(): v.encode() for k, v in unwanted.to_stream_fields().items()
        }

        mock_client.xread = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", unwanted_fields), (b"2-0", wanted_fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        try:
            async for ev in bus.subscribe(
                board_id,
                event_types={"deliberation.concluded"},
                last_id="0",
            ):
                collected.append(ev)
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        assert collected[0].event_type == "deliberation.concluded"

    @pytest.mark.asyncio
    async def test_subscribe_skips_malformed_entries(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        good = BusEvent(
            event_type="test",
            board_id=board_id,
            payload={},
        )
        stream = _stream_key(board_id).encode()
        good_fields = {
            k.encode(): v.encode() for k, v in good.to_stream_fields().items()
        }
        bad_fields = {b"garbage": b"data"}

        mock_client.xread = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", bad_fields), (b"2-0", good_fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        try:
            async for ev in bus.subscribe(board_id, last_id="0"):
                collected.append(ev)
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        assert collected[0].event_id == good.event_id

    @pytest.mark.asyncio
    async def test_subscribe_retries_on_exception(self) -> None:
        """On xread exception the generator backs off and retries."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(
            event_type="test",
            board_id=board_id,
            payload={},
        )
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xread = AsyncMock(
            side_effect=[
                ConnectionError("redis down"),
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        with patch(
            "app.services.memory.message_bus._async_sleep", new_callable=AsyncMock
        ):
            try:
                async for ev in bus.subscribe(board_id, last_id="0"):
                    collected.append(ev)
                    break
            except asyncio.CancelledError:
                pass

        assert len(collected) == 1
        assert collected[0].event_type == "test"

    @pytest.mark.asyncio
    async def test_subscribe_continues_on_empty_results(self) -> None:
        """When xread returns empty (timeout), the loop continues."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(
            event_type="test",
            board_id=board_id,
            payload={},
        )
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        # First two calls return nothing (block timeout), third has data
        mock_client.xread = AsyncMock(
            side_effect=[
                [],
                [],
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        try:
            async for ev in bus.subscribe(board_id, last_id="0"):
                collected.append(ev)
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_subscribe_advances_cursor(self) -> None:
        """The cursor should advance to the latest message ID."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        ev1 = BusEvent(event_type="test.1", board_id=board_id, payload={})
        ev2 = BusEvent(event_type="test.2", board_id=board_id, payload={})
        stream = _stream_key(board_id).encode()
        f1 = {k.encode(): v.encode() for k, v in ev1.to_stream_fields().items()}
        f2 = {k.encode(): v.encode() for k, v in ev2.to_stream_fields().items()}

        mock_client.xread = AsyncMock(
            side_effect=[
                [(stream, [(b"100-0", f1)])],
                [(stream, [(b"200-0", f2)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        count = 0
        try:
            async for ev in bus.subscribe(board_id, last_id="0"):
                collected.append(ev)
                count += 1
                if count >= 2:
                    break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 2
        assert collected[0].event_type == "test.1"
        assert collected[1].event_type == "test.2"

        # Verify second xread used the advanced cursor
        second_call = mock_client.xread.call_args_list[1]
        stream_key_str = _stream_key(board_id)
        assert second_call[0][0][stream_key_str] == "100-0"

    @pytest.mark.asyncio
    async def test_subscribe_uses_custom_channel(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(event_type="test", board_id=board_id, payload={})
        stream = _stream_key(board_id, "notifications").encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xread = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        try:
            async for ev in bus.subscribe(
                board_id, channel="notifications", last_id="0"
            ):
                collected.append(ev)
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        # Verify the correct stream key was used
        first_call = mock_client.xread.call_args_list[0]
        expected_stream = _stream_key(board_id, "notifications")
        assert expected_stream in first_call[0][0]

    @pytest.mark.asyncio
    async def test_subscribe_no_event_types_yields_all(self) -> None:
        """Without an event_types filter, all events are yielded."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        ev_a = BusEvent(
            event_type="deliberation.started", board_id=board_id, payload={}
        )
        ev_b = BusEvent(
            event_type="agent.position_changed", board_id=board_id, payload={}
        )
        stream = _stream_key(board_id).encode()
        fa = {k.encode(): v.encode() for k, v in ev_a.to_stream_fields().items()}
        fb = {k.encode(): v.encode() for k, v in ev_b.to_stream_fields().items()}

        mock_client.xread = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", fa), (b"2-0", fb)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        count = 0
        try:
            async for ev in bus.subscribe(board_id, last_id="0"):
                collected.append(ev)
                count += 1
                if count >= 2:
                    break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 2
        assert collected[0].event_type == "deliberation.started"
        assert collected[1].event_type == "agent.position_changed"


# ---------------------------------------------------------------------------
# MessageBus.consume_group (async generator via xreadgroup)
# ---------------------------------------------------------------------------


class TestMessageBusConsumeGroup:
    """Tests for the consume_group async generator with mocked Redis."""

    @pytest.mark.asyncio
    async def test_yields_message_id_and_event(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(
            event_type="deliberation.entry_added",
            board_id=board_id,
            payload={"entry_id": "e1"},
        )
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        try:
            async for msg_id, ev in bus.consume_group(
                board_id, "test-group", "worker-1"
            ):
                collected.append((msg_id, ev))
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        msg_id, received = collected[0]
        assert msg_id == "1-0"
        assert received.event_type == "deliberation.entry_added"
        assert received.event_id == event.event_id

    @pytest.mark.asyncio
    async def test_consume_group_creates_group_on_start(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        mock_client.xgroup_create = AsyncMock(return_value=True)
        # CancelledError is a BaseException — it escapes the except Exception
        # handler in consume_group, cleanly terminating the while-True loop.
        mock_client.xreadgroup = AsyncMock(side_effect=asyncio.CancelledError())
        bus._client = mock_client

        try:
            async for _ in bus.consume_group(board_id, "my-group"):
                break
        except asyncio.CancelledError:
            pass

        mock_client.xgroup_create.assert_awaited_once()
        call_args = mock_client.xgroup_create.call_args
        assert call_args[0][1] == "my-group"

    @pytest.mark.asyncio
    async def test_consume_group_filters_by_event_type_and_acks(self) -> None:
        """Filtered events should still be acked to avoid re-delivery."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        wanted = BusEvent(
            event_type="deliberation.concluded",
            board_id=board_id,
            payload={},
        )
        unwanted = BusEvent(
            event_type="deliberation.started",
            board_id=board_id,
            payload={},
        )
        stream = _stream_key(board_id).encode()
        wanted_fields = {
            k.encode(): v.encode() for k, v in wanted.to_stream_fields().items()
        }
        unwanted_fields = {
            k.encode(): v.encode() for k, v in unwanted.to_stream_fields().items()
        }

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", unwanted_fields), (b"2-0", wanted_fields)])],
                asyncio.CancelledError(),
            ]
        )
        mock_client.xack = AsyncMock(return_value=1)
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        try:
            async for msg_id, ev in bus.consume_group(
                board_id,
                "test-group",
                "worker-1",
                event_types={"deliberation.concluded"},
            ):
                collected.append((msg_id, ev))
                break
        except asyncio.CancelledError:
            pass

        # Only the wanted event should be yielded
        assert len(collected) == 1
        assert collected[0][1].event_type == "deliberation.concluded"

        # The unwanted event should have been acked
        mock_client.xack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_consume_group_skips_malformed_entries(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        good = BusEvent(event_type="test", board_id=board_id, payload={})
        stream = _stream_key(board_id).encode()
        good_fields = {
            k.encode(): v.encode() for k, v in good.to_stream_fields().items()
        }
        bad_fields = {b"not_valid": b"garbage"}

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", bad_fields), (b"2-0", good_fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        try:
            async for msg_id, ev in bus.consume_group(board_id, "g", "c"):
                collected.append((msg_id, ev))
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        assert collected[0][1].event_id == good.event_id

    @pytest.mark.asyncio
    async def test_consume_group_retries_on_xreadgroup_exception(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(event_type="test", board_id=board_id, payload={})
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                ConnectionError("redis down"),
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        with patch(
            "app.services.memory.message_bus._async_sleep", new_callable=AsyncMock
        ):
            try:
                async for msg_id, ev in bus.consume_group(board_id, "g", "c"):
                    collected.append((msg_id, ev))
                    break
            except asyncio.CancelledError:
                pass

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_consume_group_continues_on_empty_results(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(event_type="test", board_id=board_id, payload={})
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                [],
                [],
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        try:
            async for msg_id, ev in bus.consume_group(board_id, "g", "c"):
                collected.append((msg_id, ev))
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_consume_group_auto_generates_consumer_name(self) -> None:
        """When consumer=None, a name is auto-generated."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(side_effect=asyncio.CancelledError())
        bus._client = mock_client

        try:
            async for _ in bus.consume_group(board_id, "g", consumer=None):
                break
        except asyncio.CancelledError:
            pass

        # Verify xreadgroup was called with an auto-generated consumer name
        call_args = mock_client.xreadgroup.call_args
        consumer_name = call_args[0][1]
        assert consumer_name.startswith("consumer-")
        assert len(consumer_name) == len("consumer-") + 8  # 8 hex chars

    @pytest.mark.asyncio
    async def test_consume_group_decodes_bytes_message_id(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        event = BusEvent(event_type="test", board_id=board_id, payload={})
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                [(stream, [(b"9876543210-99", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        try:
            async for msg_id, ev in bus.consume_group(board_id, "g", "c"):
                collected.append((msg_id, ev))
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        assert collected[0][0] == "9876543210-99"
        assert isinstance(collected[0][0], str)

    @pytest.mark.asyncio
    async def test_consume_group_uses_custom_channel(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(side_effect=asyncio.CancelledError())
        bus._client = mock_client

        try:
            async for _ in bus.consume_group(
                board_id, "g", "c", channel="notifications"
            ):
                break
        except asyncio.CancelledError:
            pass

        # Verify ensure_consumer_group was called with the custom channel
        create_call = mock_client.xgroup_create.call_args
        expected_stream = _stream_key(board_id, "notifications")
        assert create_call[0][0] == expected_stream

    @pytest.mark.asyncio
    async def test_consume_group_no_filter_yields_all(self) -> None:
        """Without event_types filter all events are yielded without ack."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        ev_a = BusEvent(
            event_type="deliberation.started", board_id=board_id, payload={}
        )
        ev_b = BusEvent(event_type="memory.promoted", board_id=board_id, payload={})
        stream = _stream_key(board_id).encode()
        fa = {k.encode(): v.encode() for k, v in ev_a.to_stream_fields().items()}
        fb = {k.encode(): v.encode() for k, v in ev_b.to_stream_fields().items()}

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", fa), (b"2-0", fb)])],
                asyncio.CancelledError(),
            ]
        )
        mock_client.xack = AsyncMock(return_value=1)
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        count = 0
        try:
            async for msg_id, ev in bus.consume_group(board_id, "g", "c"):
                collected.append((msg_id, ev))
                count += 1
                if count >= 2:
                    break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 2
        assert collected[0][1].event_type == "deliberation.started"
        assert collected[1][1].event_type == "memory.promoted"
        # No events should have been acked (no filter → caller is responsible)
        mock_client.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_consume_group_uses_explicit_consumer_name(self) -> None:
        """When an explicit consumer name is provided it is forwarded."""
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(side_effect=asyncio.CancelledError())
        bus._client = mock_client

        try:
            async for _ in bus.consume_group(board_id, "g", consumer="my-worker-7"):
                break
        except asyncio.CancelledError:
            pass

        call_args = mock_client.xreadgroup.call_args
        assert call_args[0][1] == "my-worker-7"


# ---------------------------------------------------------------------------
# MessageBus.subscribe correlation_id propagation
# ---------------------------------------------------------------------------


class TestMessageBusCorrelation:
    """Verify correlation_id flows through publish → subscribe round trips."""

    @pytest.mark.asyncio
    async def test_correlation_id_preserved_through_subscribe(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        correlation_id = uuid4()
        event = BusEvent(
            event_type="deliberation.started",
            board_id=board_id,
            payload={"topic": "correlated test"},
            correlation_id=correlation_id,
        )
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xread = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[BusEvent] = []
        try:
            async for ev in bus.subscribe(board_id, last_id="0"):
                collected.append(ev)
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        assert collected[0].correlation_id == correlation_id

    @pytest.mark.asyncio
    async def test_correlation_id_preserved_through_consume_group(self) -> None:
        bus = MessageBus()
        mock_client = AsyncMock()

        board_id = uuid4()
        correlation_id = uuid4()
        agent_id = uuid4()
        event = BusEvent(
            event_type="deliberation.entry_added",
            board_id=board_id,
            payload={"entry": "thesis"},
            correlation_id=correlation_id,
            agent_id=agent_id,
        )
        stream = _stream_key(board_id).encode()
        fields = {k.encode(): v.encode() for k, v in event.to_stream_fields().items()}

        mock_client.xgroup_create = AsyncMock(return_value=True)
        mock_client.xreadgroup = AsyncMock(
            side_effect=[
                [(stream, [(b"1-0", fields)])],
                asyncio.CancelledError(),
            ]
        )
        bus._client = mock_client

        collected: list[tuple[str, BusEvent]] = []
        try:
            async for msg_id, ev in bus.consume_group(board_id, "g", "c"):
                collected.append((msg_id, ev))
                break
        except asyncio.CancelledError:
            pass

        assert len(collected) == 1
        assert collected[0][1].correlation_id == correlation_id
        assert collected[0][1].agent_id == agent_id

    @pytest.mark.asyncio
    async def test_multiple_events_share_correlation_id(self) -> None:
        """Events in the same deliberation flow should share correlation_id."""
        board_id = uuid4()
        correlation_id = uuid4()

        events = [
            BusEvent(
                event_type=f"deliberation.event_{i}",
                board_id=board_id,
                payload={"seq": i},
                correlation_id=correlation_id,
            )
            for i in range(3)
        ]

        for ev in events:
            fields = ev.to_stream_fields()
            restored = BusEvent.from_stream_fields(fields)
            assert restored.correlation_id == correlation_id
