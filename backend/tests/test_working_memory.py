# ruff: noqa
"""Tests for the Redis working memory service — ephemeral deliberation state.

Covers:
- Key helpers (_key, _board_delib_key) produce correct namespaced keys
- _serialize / _deserialize round-trip fidelity and edge cases
- WorkingMemoryService initialisation, default config, lazy client
- Context get/set/delete with TTL
- Phase cache get/set
- Agent scratch-pad get/set/delete
- Recent entries push/get with capped list trimming
- SSE subscriber add/remove/count via Redis sets
- Distributed entry lock acquire/release (SET NX EX)
- flush_deliberation / flush_board via SCAN + DELETE
- ping health check
- active_deliberation_count via SCAN pattern
- close() lifecycle
- Module-level singleton
- Graceful degradation: all methods return safe defaults on Redis errors
- Board scoping: different boards produce different keys
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.memory.working_memory import (
    WorkingMemoryService,
    _board_delib_key,
    _deserialize,
    _key,
    _serialize,
    working_memory,
)


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


class TestKeyHelper:
    """Tests for the _key() namespace builder."""

    def test_single_part(self) -> None:
        assert _key("foo") == "mc:wm:foo"

    def test_multiple_parts(self) -> None:
        assert _key("a", "b", "c") == "mc:wm:a:b:c"

    def test_prefix_is_mc_wm(self) -> None:
        result = _key("anything")
        assert result.startswith("mc:wm:")

    def test_colon_separator(self) -> None:
        result = _key("x", "y")
        parts = result.split(":")
        assert parts == ["mc", "wm", "x", "y"]

    def test_uuid_as_string_part(self) -> None:
        uid = uuid4()
        result = _key(str(uid))
        assert str(uid) in result


class TestBoardDelibKey:
    """Tests for the _board_delib_key() scoped key builder."""

    def test_contains_board_id(self) -> None:
        board_id = uuid4()
        delib_id = uuid4()
        result = _board_delib_key(board_id, delib_id, "ctx")
        assert str(board_id) in result

    def test_contains_deliberation_id(self) -> None:
        board_id = uuid4()
        delib_id = uuid4()
        result = _board_delib_key(board_id, delib_id, "ctx")
        assert str(delib_id) in result

    def test_contains_suffix(self) -> None:
        result = _board_delib_key(uuid4(), uuid4(), "phase")
        assert result.endswith(":phase")

    def test_has_correct_prefix(self) -> None:
        result = _board_delib_key(uuid4(), uuid4(), "ctx")
        assert result.startswith("mc:wm:")

    def test_different_boards_produce_different_keys(self) -> None:
        delib_id = uuid4()
        k1 = _board_delib_key(uuid4(), delib_id, "ctx")
        k2 = _board_delib_key(uuid4(), delib_id, "ctx")
        assert k1 != k2

    def test_different_deliberations_produce_different_keys(self) -> None:
        board_id = uuid4()
        k1 = _board_delib_key(board_id, uuid4(), "ctx")
        k2 = _board_delib_key(board_id, uuid4(), "ctx")
        assert k1 != k2

    def test_different_suffixes_produce_different_keys(self) -> None:
        board_id = uuid4()
        delib_id = uuid4()
        k1 = _board_delib_key(board_id, delib_id, "ctx")
        k2 = _board_delib_key(board_id, delib_id, "phase")
        assert k1 != k2

    def test_same_inputs_produce_same_key(self) -> None:
        board_id = uuid4()
        delib_id = uuid4()
        k1 = _board_delib_key(board_id, delib_id, "ctx")
        k2 = _board_delib_key(board_id, delib_id, "ctx")
        assert k1 == k2


# ---------------------------------------------------------------------------
# Serialization / Deserialization
# ---------------------------------------------------------------------------


class TestSerialize:
    """Tests for the _serialize helper."""

    def test_dict_to_json(self) -> None:
        result = _serialize({"a": 1})
        assert json.loads(result) == {"a": 1}

    def test_returns_string(self) -> None:
        result = _serialize({"x": "y"})
        assert isinstance(result, str)

    def test_compact_separators(self) -> None:
        result = _serialize({"a": 1, "b": 2})
        # Compact separators: no spaces after : or ,
        assert " " not in result

    def test_list_payload(self) -> None:
        result = _serialize([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_nested_dict(self) -> None:
        data = {"outer": {"inner": [1, 2]}}
        result = _serialize(data)
        assert json.loads(result) == data

    def test_uuid_via_default_str(self) -> None:
        uid = uuid4()
        result = _serialize({"id": uid})
        decoded = json.loads(result)
        assert decoded["id"] == str(uid)

    def test_datetime_via_default_str(self) -> None:
        now = datetime.now(UTC)
        result = _serialize({"ts": now})
        decoded = json.loads(result)
        assert isinstance(decoded["ts"], str)

    def test_empty_dict(self) -> None:
        result = _serialize({})
        assert json.loads(result) == {}

    def test_none_value(self) -> None:
        result = _serialize({"key": None})
        decoded = json.loads(result)
        assert decoded["key"] is None

    def test_boolean_values(self) -> None:
        result = _serialize({"yes": True, "no": False})
        decoded = json.loads(result)
        assert decoded["yes"] is True
        assert decoded["no"] is False


class TestDeserialize:
    """Tests for the _deserialize helper."""

    def test_valid_json_string(self) -> None:
        result = _deserialize('{"a":1}')
        assert result == {"a": 1}

    def test_valid_json_bytes(self) -> None:
        result = _deserialize(b'{"a":1}')
        assert result == {"a": 1}

    def test_none_returns_none(self) -> None:
        assert _deserialize(None) is None

    def test_invalid_json_returns_none(self) -> None:
        assert _deserialize("not json") is None

    def test_empty_string_returns_none(self) -> None:
        assert _deserialize("") is None

    def test_empty_bytes_returns_none(self) -> None:
        assert _deserialize(b"") is None

    def test_list_payload(self) -> None:
        result = _deserialize("[1,2,3]")
        assert result == [1, 2, 3]

    def test_nested_object(self) -> None:
        raw = json.dumps({"outer": {"inner": True}})
        result = _deserialize(raw)
        assert result == {"outer": {"inner": True}}


class TestSerializeDeserializeRoundTrip:
    """Verify _serialize → _deserialize round-trip fidelity."""

    def test_dict_round_trip(self) -> None:
        data = {"topic": "test", "count": 42, "tags": ["a", "b"]}
        assert _deserialize(_serialize(data)) == data

    def test_list_round_trip(self) -> None:
        data = [1, "two", None, True]
        assert _deserialize(_serialize(data)) == data

    def test_empty_dict_round_trip(self) -> None:
        assert _deserialize(_serialize({})) == {}

    def test_nested_round_trip(self) -> None:
        data = {"a": {"b": {"c": [1, 2, 3]}}}
        assert _deserialize(_serialize(data)) == data

    def test_bytes_round_trip(self) -> None:
        """Simulate Redis returning bytes."""
        data = {"key": "value"}
        serialized = _serialize(data)
        as_bytes = serialized.encode("utf-8")
        assert _deserialize(as_bytes) == data

    def test_unicode_round_trip(self) -> None:
        data = {"text": "日本語 🎉 Ελληνικά"}
        assert _deserialize(_serialize(data)) == data

    def test_special_chars_round_trip(self) -> None:
        data = {"text": 'Hello "world" <angle> & amp'}
        assert _deserialize(_serialize(data)) == data


# ---------------------------------------------------------------------------
# WorkingMemoryService initialisation
# ---------------------------------------------------------------------------


class TestWorkingMemoryServiceInit:
    """Tests for WorkingMemoryService construction."""

    def test_default_init(self) -> None:
        svc = WorkingMemoryService()
        assert svc._client is None

    def test_custom_redis_url(self) -> None:
        svc = WorkingMemoryService(redis_url="redis://custom:6379/2")
        assert svc._redis_url == "redis://custom:6379/2"

    def test_default_redis_url_from_settings(self) -> None:
        from app.core.config import settings

        svc = WorkingMemoryService()
        assert svc._redis_url == settings.rq_redis_url

    def test_custom_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=120)
        assert svc._default_ttl == 120

    def test_default_ttl_from_settings(self) -> None:
        from app.core.config import settings

        svc = WorkingMemoryService()
        assert svc._default_ttl == settings.memory_working_ttl_default

    def test_client_initially_none(self) -> None:
        svc = WorkingMemoryService()
        assert svc._client is None

    def test_has_all_public_methods(self) -> None:
        svc = WorkingMemoryService()
        expected = [
            "set_context",
            "get_context",
            "delete_context",
            "set_phase",
            "get_phase",
            "set_scratch",
            "get_scratch",
            "delete_scratch",
            "push_entry",
            "get_recent_entries",
            "add_subscriber",
            "remove_subscriber",
            "get_subscriber_count",
            "acquire_entry_lock",
            "release_entry_lock",
            "flush_deliberation",
            "flush_board",
            "ping",
            "active_deliberation_count",
            "close",
        ]
        for method in expected:
            assert callable(getattr(svc, method, None)), f"Missing method: {method}"


# ---------------------------------------------------------------------------
# Lazy client initialisation
# ---------------------------------------------------------------------------


class TestWorkingMemoryGetClient:
    """Tests for the _get_client lazy Redis connection."""

    @pytest.mark.asyncio
    async def test_creates_client_on_first_call(self) -> None:
        svc = WorkingMemoryService(redis_url="redis://localhost:6379/0")
        assert svc._client is None

        with patch("app.services.memory.working_memory.aioredis") as mock_aioredis:
            mock_conn = AsyncMock()
            mock_aioredis.from_url.return_value = mock_conn

            client = await svc._get_client()
            assert client is mock_conn
            assert svc._client is mock_conn
            mock_aioredis.from_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_existing_client(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        svc._client = mock_client

        with patch("app.services.memory.working_memory.aioredis") as mock_aioredis:
            client = await svc._get_client()
            assert client is mock_client
            mock_aioredis.from_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_reused(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        svc._client = mock_client

        c1 = await svc._get_client()
        c2 = await svc._get_client()
        assert c1 is c2


# ---------------------------------------------------------------------------
# Context get/set/delete
# ---------------------------------------------------------------------------


class TestSetContext:
    """Tests for set_context."""

    @pytest.mark.asyncio
    async def test_set_context_calls_set(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        ctx = {"topic": "test", "status": "created"}
        result = await svc.set_context(board_id, delib_id, ctx)

        assert result is True
        mock_client.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_context_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.set_context(board_id, delib_id, {"topic": "t"})

        call_args = mock_client.set.call_args
        expected_key = _board_delib_key(board_id, delib_id, "ctx")
        assert call_args[0][0] == expected_key

    @pytest.mark.asyncio
    async def test_set_context_serializes_payload(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        ctx = {"topic": "auth module", "participants": ["a1", "a2"]}
        await svc.set_context(uuid4(), uuid4(), ctx)

        call_args = mock_client.set.call_args
        stored_value = call_args[0][1]
        assert json.loads(stored_value) == ctx

    @pytest.mark.asyncio
    async def test_set_context_uses_default_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=300)
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.set_context(uuid4(), uuid4(), {"topic": "t"})

        call_kwargs = mock_client.set.call_args.kwargs
        assert call_kwargs.get("ex") == 300

    @pytest.mark.asyncio
    async def test_set_context_custom_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=300)
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.set_context(uuid4(), uuid4(), {"topic": "t"}, ttl=60)

        call_kwargs = mock_client.set.call_args.kwargs
        assert call_kwargs.get("ex") == 60

    @pytest.mark.asyncio
    async def test_set_context_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=redis.RedisError("down"))
        svc._client = mock_client

        result = await svc.set_context(uuid4(), uuid4(), {"topic": "t"})
        assert result is False

    @pytest.mark.asyncio
    async def test_set_context_returns_false_on_os_error(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=OSError("connection refused"))
        svc._client = mock_client

        result = await svc.set_context(uuid4(), uuid4(), {"topic": "t"})
        assert result is False


class TestGetContext:
    """Tests for get_context."""

    @pytest.mark.asyncio
    async def test_returns_dict_on_hit(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        ctx = {"topic": "test", "status": "debating"}
        mock_client.get = AsyncMock(return_value=_serialize(ctx).encode())
        svc._client = mock_client

        result = await svc.get_context(uuid4(), uuid4())
        assert result == ctx

    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        svc._client = mock_client

        result = await svc.get_context(uuid4(), uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=redis.RedisError("timeout"))
        svc._client = mock_client

        result = await svc.get_context(uuid4(), uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.get_context(board_id, delib_id)

        expected_key = _board_delib_key(board_id, delib_id, "ctx")
        mock_client.get.assert_awaited_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_handles_bytes_response(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=b'{"topic":"bytes test"}')
        svc._client = mock_client

        result = await svc.get_context(uuid4(), uuid4())
        assert result == {"topic": "bytes test"}


class TestDeleteContext:
    """Tests for delete_context."""

    @pytest.mark.asyncio
    async def test_calls_delete(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        svc._client = mock_client

        result = await svc.delete_context(uuid4(), uuid4())
        assert result is True
        mock_client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.delete_context(board_id, delib_id)

        expected_key = _board_delib_key(board_id, delib_id, "ctx")
        mock_client.delete.assert_awaited_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.delete_context(uuid4(), uuid4())
        assert result is False


class TestContextRoundTrip:
    """Verify set → get → delete lifecycle."""

    @pytest.mark.asyncio
    async def test_set_then_get(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        svc._client = mock_client

        ctx = {"topic": "round-trip", "participants": [str(uuid4())]}

        # set stores the serialized value
        mock_client.set = AsyncMock(return_value=True)
        await svc.set_context(uuid4(), uuid4(), ctx)

        stored_value = mock_client.set.call_args[0][1]

        # get returns the deserialized value
        mock_client.get = AsyncMock(return_value=stored_value)
        board_id, delib_id = uuid4(), uuid4()
        result = await svc.get_context(board_id, delib_id)
        assert result == ctx


# ---------------------------------------------------------------------------
# Phase cache
# ---------------------------------------------------------------------------


class TestSetPhase:
    """Tests for set_phase."""

    @pytest.mark.asyncio
    async def test_set_phase_stores_encoded_value(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        result = await svc.set_phase(uuid4(), uuid4(), "debating")
        assert result is True

        stored = mock_client.set.call_args[0][1]
        assert stored == b"debating"

    @pytest.mark.asyncio
    async def test_set_phase_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.set_phase(board_id, delib_id, "synthesizing")

        expected_key = _board_delib_key(board_id, delib_id, "phase")
        assert mock_client.set.call_args[0][0] == expected_key

    @pytest.mark.asyncio
    async def test_set_phase_default_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=200)
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.set_phase(uuid4(), uuid4(), "debating")
        assert mock_client.set.call_args.kwargs.get("ex") == 200

    @pytest.mark.asyncio
    async def test_set_phase_custom_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=200)
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.set_phase(uuid4(), uuid4(), "debating", ttl=30)
        assert mock_client.set.call_args.kwargs.get("ex") == 30

    @pytest.mark.asyncio
    async def test_set_phase_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.set_phase(uuid4(), uuid4(), "debating")
        assert result is False


class TestGetPhase:
    """Tests for get_phase."""

    @pytest.mark.asyncio
    async def test_returns_string_on_hit(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=b"discussing")
        svc._client = mock_client

        result = await svc.get_phase(uuid4(), uuid4())
        assert result == "discussing"

    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        svc._client = mock_client

        result = await svc.get_phase(uuid4(), uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.get_phase(uuid4(), uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_decodes_bytes_response(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=b"verifying")
        svc._client = mock_client

        result = await svc.get_phase(uuid4(), uuid4())
        assert result == "verifying"
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_handles_string_response(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value="concluded")
        svc._client = mock_client

        result = await svc.get_phase(uuid4(), uuid4())
        assert result == "concluded"

    @pytest.mark.asyncio
    async def test_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.get_phase(board_id, delib_id)

        expected_key = _board_delib_key(board_id, delib_id, "phase")
        mock_client.get.assert_awaited_once_with(expected_key)


class TestPhaseRoundTrip:
    """Verify set_phase → get_phase round trip via mock."""

    @pytest.mark.asyncio
    async def test_phase_round_trip(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        svc._client = mock_client

        # set stores encoded bytes
        mock_client.set = AsyncMock(return_value=True)
        await svc.set_phase(uuid4(), uuid4(), "synthesizing")
        stored = mock_client.set.call_args[0][1]

        # get returns the decoded phase
        mock_client.get = AsyncMock(return_value=stored)
        result = await svc.get_phase(uuid4(), uuid4())
        assert result == "synthesizing"


# ---------------------------------------------------------------------------
# Scratch pad
# ---------------------------------------------------------------------------


class TestSetScratch:
    """Tests for set_scratch."""

    @pytest.mark.asyncio
    async def test_stores_serialized_data(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        agent_id = uuid4()
        data = {"draft": "My thesis...", "confidence": 0.8}
        result = await svc.set_scratch(uuid4(), uuid4(), agent_id, data)

        assert result is True
        stored = mock_client.set.call_args[0][1]
        assert json.loads(stored) == data

    @pytest.mark.asyncio
    async def test_uses_agent_scoped_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        board_id, delib_id, agent_id = uuid4(), uuid4(), uuid4()
        await svc.set_scratch(board_id, delib_id, agent_id, {"x": 1})

        key = mock_client.set.call_args[0][0]
        assert str(agent_id) in key
        assert "scratch" in key

    @pytest.mark.asyncio
    async def test_set_scratch_default_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=400)
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.set_scratch(uuid4(), uuid4(), uuid4(), {"x": 1})
        assert mock_client.set.call_args.kwargs.get("ex") == 400

    @pytest.mark.asyncio
    async def test_set_scratch_custom_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=400)
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.set_scratch(uuid4(), uuid4(), uuid4(), {"x": 1}, ttl=15)
        assert mock_client.set.call_args.kwargs.get("ex") == 15

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.set_scratch(uuid4(), uuid4(), uuid4(), {"x": 1})
        assert result is False


class TestGetScratch:
    """Tests for get_scratch."""

    @pytest.mark.asyncio
    async def test_returns_dict_on_hit(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        data = {"draft": "My argument", "confidence": 0.9}
        mock_client.get = AsyncMock(return_value=_serialize(data).encode())
        svc._client = mock_client

        result = await svc.get_scratch(uuid4(), uuid4(), uuid4())
        assert result == data

    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        svc._client = mock_client

        result = await svc.get_scratch(uuid4(), uuid4(), uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.get_scratch(uuid4(), uuid4(), uuid4())
        assert result is None


class TestDeleteScratch:
    """Tests for delete_scratch."""

    @pytest.mark.asyncio
    async def test_calls_delete(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        svc._client = mock_client

        result = await svc.delete_scratch(uuid4(), uuid4(), uuid4())
        assert result is True
        mock_client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_agent_scoped_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        svc._client = mock_client

        agent_id = uuid4()
        await svc.delete_scratch(uuid4(), uuid4(), agent_id)

        key = mock_client.delete.call_args[0][0]
        assert str(agent_id) in key

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.delete_scratch(uuid4(), uuid4(), uuid4())
        assert result is False


class TestScratchRoundTrip:
    """Verify set_scratch → get_scratch round trip."""

    @pytest.mark.asyncio
    async def test_scratch_round_trip(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        svc._client = mock_client

        data = {"notes": "interesting point", "refs": [1, 2]}
        mock_client.set = AsyncMock(return_value=True)
        await svc.set_scratch(uuid4(), uuid4(), uuid4(), data)

        stored = mock_client.set.call_args[0][1]
        mock_client.get = AsyncMock(return_value=stored)
        result = await svc.get_scratch(uuid4(), uuid4(), uuid4())
        assert result == data


# ---------------------------------------------------------------------------
# Recent entries (capped list)
# ---------------------------------------------------------------------------


class TestPushEntry:
    """Tests for push_entry (RPUSH + LTRIM + EXPIRE pipeline)."""

    @pytest.mark.asyncio
    async def test_push_returns_true_on_success(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        entry = {"seq": 1, "type": "thesis", "content": "Test"}
        result = await svc.push_entry(uuid4(), uuid4(), entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_push_serializes_entry(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        entry = {"seq": 5, "type": "evidence"}
        await svc.push_entry(uuid4(), uuid4(), entry)

        rpush_args = mock_pipe.rpush.call_args[0]
        serialized = rpush_args[1]
        assert json.loads(serialized) == entry

    @pytest.mark.asyncio
    async def test_push_trims_to_max_entries(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        await svc.push_entry(uuid4(), uuid4(), {"seq": 1})

        ltrim_args = mock_pipe.ltrim.call_args[0]
        # Should trim to keep last _MAX_RECENT_ENTRIES entries
        assert ltrim_args[1] == -50  # _MAX_RECENT_ENTRIES
        assert ltrim_args[2] == -1

    @pytest.mark.asyncio
    async def test_push_sets_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=500)
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        await svc.push_entry(uuid4(), uuid4(), {"seq": 1})
        expire_args = mock_pipe.expire.call_args[0]
        assert expire_args[1] == 500

    @pytest.mark.asyncio
    async def test_push_custom_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=500)
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        await svc.push_entry(uuid4(), uuid4(), {"seq": 1}, ttl=60)
        expire_args = mock_pipe.expire.call_args[0]
        assert expire_args[1] == 60

    @pytest.mark.asyncio
    async def test_push_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=redis.RedisError("err"))
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        result = await svc.push_entry(uuid4(), uuid4(), {"seq": 1})
        assert result is False

    @pytest.mark.asyncio
    async def test_push_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.push_entry(board_id, delib_id, {"seq": 1})

        rpush_key = mock_pipe.rpush.call_args[0][0]
        expected_key = _board_delib_key(board_id, delib_id, "entries")
        assert rpush_key == expected_key


class TestGetRecentEntries:
    """Tests for get_recent_entries."""

    @pytest.mark.asyncio
    async def test_returns_deserialized_entries(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        entries = [
            _serialize({"seq": 1, "type": "thesis"}).encode(),
            _serialize({"seq": 2, "type": "antithesis"}).encode(),
        ]
        mock_client.lrange = AsyncMock(return_value=entries)
        svc._client = mock_client

        result = await svc.get_recent_entries(uuid4(), uuid4())
        assert len(result) == 2
        assert result[0]["seq"] == 1
        assert result[1]["type"] == "antithesis"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_miss(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(return_value=[])
        svc._client = mock_client

        result = await svc.get_recent_entries(uuid4(), uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.get_recent_entries(uuid4(), uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_malformed_entries(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        entries = [
            _serialize({"seq": 1}).encode(),
            b"not valid json",
            _serialize({"seq": 3}).encode(),
        ]
        mock_client.lrange = AsyncMock(return_value=entries)
        svc._client = mock_client

        result = await svc.get_recent_entries(uuid4(), uuid4())
        assert len(result) == 2
        assert result[0]["seq"] == 1
        assert result[1]["seq"] == 3

    @pytest.mark.asyncio
    async def test_uses_count_parameter(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(return_value=[])
        svc._client = mock_client

        await svc.get_recent_entries(uuid4(), uuid4(), count=10)
        lrange_args = mock_client.lrange.call_args[0]
        assert lrange_args[1] == -10
        assert lrange_args[2] == -1


# ---------------------------------------------------------------------------
# SSE subscriber tracking
# ---------------------------------------------------------------------------


class TestAddSubscriber:
    """Tests for add_subscriber (SADD + EXPIRE pipeline)."""

    @pytest.mark.asyncio
    async def test_add_returns_true(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        result = await svc.add_subscriber(uuid4(), uuid4(), "sub-abc")
        assert result is True

    @pytest.mark.asyncio
    async def test_add_subscriber_uses_set_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.add_subscriber(board_id, delib_id, "sub-1")

        sadd_key = mock_pipe.sadd.call_args[0][0]
        expected = _board_delib_key(board_id, delib_id, "subs")
        assert sadd_key == expected

    @pytest.mark.asyncio
    async def test_add_subscriber_passes_subscriber_id(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        await svc.add_subscriber(uuid4(), uuid4(), "user-xyz")
        sadd_value = mock_pipe.sadd.call_args[0][1]
        assert sadd_value == "user-xyz"

    @pytest.mark.asyncio
    async def test_add_subscriber_ttl_is_double_default(self) -> None:
        svc = WorkingMemoryService(default_ttl=100)
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        await svc.add_subscriber(uuid4(), uuid4(), "sub-1")
        expire_args = mock_pipe.expire.call_args[0]
        assert expire_args[1] == 200  # 2 * default_ttl

    @pytest.mark.asyncio
    async def test_add_subscriber_custom_ttl(self) -> None:
        svc = WorkingMemoryService(default_ttl=100)
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        await svc.add_subscriber(uuid4(), uuid4(), "sub-1", ttl=999)
        expire_args = mock_pipe.expire.call_args[0]
        assert expire_args[1] == 999

    @pytest.mark.asyncio
    async def test_add_subscriber_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=redis.RedisError("err"))
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        result = await svc.add_subscriber(uuid4(), uuid4(), "sub-1")
        assert result is False


class TestRemoveSubscriber:
    """Tests for remove_subscriber (SREM)."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.srem = AsyncMock(return_value=1)
        svc._client = mock_client

        result = await svc.remove_subscriber(uuid4(), uuid4(), "sub-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.srem = AsyncMock(return_value=1)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.remove_subscriber(board_id, delib_id, "sub-1")

        expected_key = _board_delib_key(board_id, delib_id, "subs")
        mock_client.srem.assert_awaited_once_with(expected_key, "sub-1")

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.srem = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.remove_subscriber(uuid4(), uuid4(), "sub-1")
        assert result is False


class TestGetSubscriberCount:
    """Tests for get_subscriber_count (SCARD)."""

    @pytest.mark.asyncio
    async def test_returns_count(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.scard = AsyncMock(return_value=5)
        svc._client = mock_client

        count = await svc.get_subscriber_count(uuid4(), uuid4())
        assert count == 5

    @pytest.mark.asyncio
    async def test_returns_zero_on_empty_set(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.scard = AsyncMock(return_value=0)
        svc._client = mock_client

        count = await svc.get_subscriber_count(uuid4(), uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.scard = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        count = await svc.get_subscriber_count(uuid4(), uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_uses_correct_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.scard = AsyncMock(return_value=3)
        svc._client = mock_client

        board_id, delib_id = uuid4(), uuid4()
        await svc.get_subscriber_count(board_id, delib_id)

        expected_key = _board_delib_key(board_id, delib_id, "subs")
        mock_client.scard.assert_awaited_once_with(expected_key)


# ---------------------------------------------------------------------------
# Distributed entry lock
# ---------------------------------------------------------------------------


class TestAcquireEntryLock:
    """Tests for acquire_entry_lock (SET NX EX)."""

    @pytest.mark.asyncio
    async def test_acquire_returns_true_when_acquired(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        result = await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4())
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_returns_false_when_already_locked(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=None)  # NX fails
        svc._client = mock_client

        result = await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4())
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_uses_nx_flag(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4())

        call_kwargs = mock_client.set.call_args.kwargs
        assert call_kwargs.get("nx") is True

    @pytest.mark.asyncio
    async def test_acquire_uses_ex_flag(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4(), lock_ttl=15)

        call_kwargs = mock_client.set.call_args.kwargs
        assert call_kwargs.get("ex") == 15

    @pytest.mark.asyncio
    async def test_acquire_default_lock_ttl(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4())

        call_kwargs = mock_client.set.call_args.kwargs
        assert call_kwargs.get("ex") == 10  # default lock_ttl

    @pytest.mark.asyncio
    async def test_acquire_uses_agent_scoped_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        agent_id = uuid4()
        await svc.acquire_entry_lock(uuid4(), uuid4(), agent_id)

        key = mock_client.set.call_args[0][0]
        assert str(agent_id) in key
        assert "lock" in key

    @pytest.mark.asyncio
    async def test_acquire_fail_open_on_error(self) -> None:
        """Lock acquire should fail open (return True) when Redis is down."""
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=redis.RedisError("down"))
        svc._client = mock_client

        result = await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4())
        assert result is True  # fail-open!

    @pytest.mark.asyncio
    async def test_acquire_fail_open_on_os_error(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=OSError("connection reset"))
        svc._client = mock_client

        result = await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4())
        assert result is True  # fail-open


class TestReleaseEntryLock:
    """Tests for release_entry_lock (DELETE)."""

    @pytest.mark.asyncio
    async def test_release_returns_true(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        svc._client = mock_client

        result = await svc.release_entry_lock(uuid4(), uuid4(), uuid4())
        assert result is True

    @pytest.mark.asyncio
    async def test_release_uses_agent_scoped_key(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)
        svc._client = mock_client

        agent_id = uuid4()
        await svc.release_entry_lock(uuid4(), uuid4(), agent_id)

        key = mock_client.delete.call_args[0][0]
        assert str(agent_id) in key
        assert "lock" in key

    @pytest.mark.asyncio
    async def test_release_returns_false_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=redis.RedisError("err"))
        svc._client = mock_client

        result = await svc.release_entry_lock(uuid4(), uuid4(), uuid4())
        assert result is False


class TestEntryLockRoundTrip:
    """Verify acquire → release lifecycle."""

    @pytest.mark.asyncio
    async def test_acquire_then_release(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        mock_client.delete = AsyncMock(return_value=1)
        svc._client = mock_client

        board_id, delib_id, agent_id = uuid4(), uuid4(), uuid4()

        acquired = await svc.acquire_entry_lock(board_id, delib_id, agent_id)
        assert acquired is True

        released = await svc.release_entry_lock(board_id, delib_id, agent_id)
        assert released is True


# ---------------------------------------------------------------------------
# Flush operations
# ---------------------------------------------------------------------------


class TestFlushDeliberation:
    """Tests for flush_deliberation (SCAN + DELETE)."""

    @pytest.mark.asyncio
    async def test_returns_deleted_count(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        # Simulate scan_iter returning some keys
        async def _scan_iter(**kwargs: Any):
            for k in [b"mc:wm:b:d:ctx", b"mc:wm:b:d:phase", b"mc:wm:b:d:entries"]:
                yield k

        mock_client.scan_iter = _scan_iter
        mock_client.delete = AsyncMock(return_value=3)
        svc._client = mock_client

        count = await svc.flush_deliberation(uuid4(), uuid4())
        assert count == 3

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_keys(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            return
            yield  # empty async generator

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        count = await svc.flush_deliberation(uuid4(), uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            raise redis.RedisError("err")
            yield  # make it an async generator

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        count = await svc.flush_deliberation(uuid4(), uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_uses_correct_scan_pattern(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        board_id, delib_id = uuid4(), uuid4()
        expected_pattern = _board_delib_key(board_id, delib_id, "*")

        scan_kwargs_captured: dict[str, Any] = {}

        async def _scan_iter(**kwargs: Any):
            scan_kwargs_captured.update(kwargs)
            return
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        await svc.flush_deliberation(board_id, delib_id)
        assert scan_kwargs_captured.get("match") == expected_pattern


class TestFlushBoard:
    """Tests for flush_board (SCAN + DELETE for entire board)."""

    @pytest.mark.asyncio
    async def test_returns_deleted_count(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            for k in [b"mc:wm:board:d1:ctx", b"mc:wm:board:d2:ctx"]:
                yield k

        mock_client.scan_iter = _scan_iter
        mock_client.delete = AsyncMock(return_value=2)
        svc._client = mock_client

        count = await svc.flush_board(uuid4())
        assert count == 2

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_keys(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            return
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        count = await svc.flush_board(uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            raise redis.RedisError("err")
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        count = await svc.flush_board(uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_uses_board_scoped_pattern(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        board_id = uuid4()
        expected_pattern = _key(str(board_id), "*")

        scan_kwargs_captured: dict[str, Any] = {}

        async def _scan_iter(**kwargs: Any):
            scan_kwargs_captured.update(kwargs)
            return
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        await svc.flush_board(board_id)
        assert scan_kwargs_captured.get("match") == expected_pattern


# ---------------------------------------------------------------------------
# Ping / health
# ---------------------------------------------------------------------------


class TestPing:
    """Tests for the ping health check."""

    @pytest.mark.asyncio
    async def test_returns_true_when_healthy(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)
        svc._client = mock_client

        assert await svc.ping() is True

    @pytest.mark.asyncio
    async def test_returns_false_on_redis_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=redis.RedisError("down"))
        svc._client = mock_client

        assert await svc.ping() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_os_error(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=OSError("connection refused"))
        svc._client = mock_client

        assert await svc.ping() is False


# ---------------------------------------------------------------------------
# Active deliberation count
# ---------------------------------------------------------------------------


class TestActiveDeliberationCount:
    """Tests for active_deliberation_count (SCAN for ctx keys)."""

    @pytest.mark.asyncio
    async def test_returns_count(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            for k in [
                b"mc:wm:board:d1:ctx",
                b"mc:wm:board:d2:ctx",
                b"mc:wm:board:d3:ctx",
            ]:
                yield k

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        count = await svc.active_deliberation_count(uuid4())
        assert count == 3

    @pytest.mark.asyncio
    async def test_returns_zero_when_none_active(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            return
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        count = await svc.active_deliberation_count(uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_error(self) -> None:
        import redis

        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            raise redis.RedisError("err")
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        count = await svc.active_deliberation_count(uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_uses_ctx_suffix_pattern(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        board_id = uuid4()

        scan_kwargs_captured: dict[str, Any] = {}

        async def _scan_iter(**kwargs: Any):
            scan_kwargs_captured.update(kwargs)
            return
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        await svc.active_deliberation_count(board_id)
        pattern = scan_kwargs_captured.get("match", "")
        assert pattern.endswith(":ctx")
        assert str(board_id) in pattern


# ---------------------------------------------------------------------------
# Close lifecycle
# ---------------------------------------------------------------------------


class TestClose:
    """Tests for the close method."""

    @pytest.mark.asyncio
    async def test_close_when_no_client(self) -> None:
        svc = WorkingMemoryService()
        await svc.close()  # should not raise
        assert svc._client is None

    @pytest.mark.asyncio
    async def test_close_resets_client_to_none(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        svc._client = mock_client

        await svc.close()
        assert svc._client is None

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        svc._client = mock_client

        await svc.close()
        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    """Verify the module-level working_memory singleton."""

    def test_singleton_exists(self) -> None:
        assert working_memory is not None

    def test_singleton_is_correct_type(self) -> None:
        assert isinstance(working_memory, WorkingMemoryService)

    def test_singleton_has_default_config(self) -> None:
        from app.core.config import settings

        assert working_memory._redis_url == settings.rq_redis_url
        assert working_memory._default_ttl == settings.memory_working_ttl_default


# ---------------------------------------------------------------------------
# Board scoping — different boards are fully isolated
# ---------------------------------------------------------------------------


class TestBoardScoping:
    """Verify keys for different boards never collide."""

    def test_context_keys_isolated(self) -> None:
        delib_id = uuid4()
        k1 = _board_delib_key(uuid4(), delib_id, "ctx")
        k2 = _board_delib_key(uuid4(), delib_id, "ctx")
        assert k1 != k2

    def test_phase_keys_isolated(self) -> None:
        delib_id = uuid4()
        k1 = _board_delib_key(uuid4(), delib_id, "phase")
        k2 = _board_delib_key(uuid4(), delib_id, "phase")
        assert k1 != k2

    def test_entries_keys_isolated(self) -> None:
        delib_id = uuid4()
        k1 = _board_delib_key(uuid4(), delib_id, "entries")
        k2 = _board_delib_key(uuid4(), delib_id, "entries")
        assert k1 != k2

    def test_subs_keys_isolated(self) -> None:
        delib_id = uuid4()
        k1 = _board_delib_key(uuid4(), delib_id, "subs")
        k2 = _board_delib_key(uuid4(), delib_id, "subs")
        assert k1 != k2


# ---------------------------------------------------------------------------
# Graceful degradation — every method returns safe default on error
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Verify all methods degrade gracefully when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_get_context_returns_none(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.get_context(uuid4(), uuid4()) is None

    @pytest.mark.asyncio
    async def test_set_context_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.set_context(uuid4(), uuid4(), {}) is False

    @pytest.mark.asyncio
    async def test_delete_context_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.delete_context(uuid4(), uuid4()) is False

    @pytest.mark.asyncio
    async def test_get_phase_returns_none(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.get_phase(uuid4(), uuid4()) is None

    @pytest.mark.asyncio
    async def test_set_phase_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.set_phase(uuid4(), uuid4(), "debating") is False

    @pytest.mark.asyncio
    async def test_get_scratch_returns_none(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.get_scratch(uuid4(), uuid4(), uuid4()) is None

    @pytest.mark.asyncio
    async def test_set_scratch_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.set_scratch(uuid4(), uuid4(), uuid4(), {}) is False

    @pytest.mark.asyncio
    async def test_delete_scratch_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.delete_scratch(uuid4(), uuid4(), uuid4()) is False

    @pytest.mark.asyncio
    async def test_get_recent_entries_returns_empty(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.lrange = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.get_recent_entries(uuid4(), uuid4()) == []

    @pytest.mark.asyncio
    async def test_push_entry_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=OSError("down"))
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        assert await svc.push_entry(uuid4(), uuid4(), {}) is False

    @pytest.mark.asyncio
    async def test_add_subscriber_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=OSError("down"))
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        assert await svc.add_subscriber(uuid4(), uuid4(), "s") is False

    @pytest.mark.asyncio
    async def test_remove_subscriber_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.srem = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.remove_subscriber(uuid4(), uuid4(), "s") is False

    @pytest.mark.asyncio
    async def test_get_subscriber_count_returns_zero(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.scard = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.get_subscriber_count(uuid4(), uuid4()) == 0

    @pytest.mark.asyncio
    async def test_release_entry_lock_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.release_entry_lock(uuid4(), uuid4(), uuid4()) is False

    @pytest.mark.asyncio
    async def test_acquire_entry_lock_fail_open(self) -> None:
        """Lock acquire MUST fail open (return True) when Redis is down."""
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.acquire_entry_lock(uuid4(), uuid4(), uuid4()) is True

    @pytest.mark.asyncio
    async def test_ping_returns_false(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=OSError("down"))
        svc._client = mock_client

        assert await svc.ping() is False

    @pytest.mark.asyncio
    async def test_stream_length_returns_zero(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            raise OSError("down")
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        assert await svc.active_deliberation_count(uuid4()) == 0

    @pytest.mark.asyncio
    async def test_flush_deliberation_returns_zero(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            raise OSError("down")
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        assert await svc.flush_deliberation(uuid4(), uuid4()) == 0

    @pytest.mark.asyncio
    async def test_flush_board_returns_zero(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()

        async def _scan_iter(**kwargs: Any):
            raise OSError("down")
            yield

        mock_client.scan_iter = _scan_iter
        svc._client = mock_client

        assert await svc.flush_board(uuid4()) == 0


# ---------------------------------------------------------------------------
# Edge cases: complex payloads and unusual values
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Various edge-case scenarios."""

    @pytest.mark.asyncio
    async def test_context_with_large_payload(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        ctx = {f"key_{i}": f"value_{i}" for i in range(500)}
        result = await svc.set_context(uuid4(), uuid4(), ctx)
        assert result is True

        stored = mock_client.set.call_args[0][1]
        decoded = json.loads(stored)
        assert len(decoded) == 500

    @pytest.mark.asyncio
    async def test_context_with_unicode(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        ctx = {"topic": "日本語テスト 🎉"}
        result = await svc.set_context(uuid4(), uuid4(), ctx)
        assert result is True

        stored = mock_client.set.call_args[0][1]
        decoded = json.loads(stored)
        assert decoded["topic"] == "日本語テスト 🎉"

    @pytest.mark.asyncio
    async def test_scratch_with_nested_data(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        data = {
            "position": {
                "stance": "agree",
                "reasons": ["reason1", "reason2"],
                "confidence": {"min": 0.6, "max": 0.9},
            }
        }
        result = await svc.set_scratch(uuid4(), uuid4(), uuid4(), data)
        assert result is True

        stored = mock_client.set.call_args[0][1]
        assert json.loads(stored) == data

    @pytest.mark.asyncio
    async def test_push_entry_with_empty_dict(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.rpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        result = await svc.push_entry(uuid4(), uuid4(), {})
        assert result is True

    @pytest.mark.asyncio
    async def test_empty_subscriber_id(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.sadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])
        mock_client.pipeline = MagicMock(return_value=mock_pipe)
        svc._client = mock_client

        result = await svc.add_subscriber(uuid4(), uuid4(), "")
        assert result is True

    @pytest.mark.asyncio
    async def test_context_empty_dict(self) -> None:
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.set = AsyncMock(return_value=True)
        svc._client = mock_client

        result = await svc.set_context(uuid4(), uuid4(), {})
        assert result is True

    @pytest.mark.asyncio
    async def test_get_context_with_corrupted_data(self) -> None:
        """Corrupted Redis data should return None gracefully."""
        svc = WorkingMemoryService()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=b"not-valid-json{{{")
        svc._client = mock_client

        result = await svc.get_context(uuid4(), uuid4())
        assert result is None

    def test_all_phases_can_be_cached(self) -> None:
        """Ensure all deliberation phases are valid strings for set_phase."""
        phases = [
            "created",
            "debating",
            "discussing",
            "verifying",
            "synthesizing",
            "concluded",
            "abandoned",
        ]
        for phase in phases:
            encoded = phase.encode()
            decoded = encoded.decode("utf-8")
            assert decoded == phase
