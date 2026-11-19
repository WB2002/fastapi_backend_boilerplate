from __future__ import annotations

import json
import logging
from typing import Any

from app.common.redis.redis_service import RedisService
from app.core.feature_flag.constants import (
    feature_flag_events_channel,
    feature_flag_index_key,
    feature_flag_key,
)
from app.core.feature_flag.evaluator import (
    dict_to_feature_flag,
    is_valid_feature_flag_shape,
)
from app.core.feature_flag.types import IFeatureFlag

logger = logging.getLogger("FeatureFlagRepository")

CAS_WRITE_LUA = """
local flagKey = KEYS[1]
local indexKey = KEYS[2]
local channel = KEYS[3]
local op = ARGV[1]
local expectedVersion = tonumber(ARGV[2])
local eventTemplate = ARGV[3]
local flagJson = ARGV[4]

local current = redis.call('GET', flagKey)

if op == 'create' then
  if current then
    local cur = cjson.decode(current)
    return { 'conflict', tostring(cur.version), current }
  end
  if expectedVersion ~= 0 then
    return { 'conflict', '0', '' }
  end
  local flag = cjson.decode(flagJson)
  flag.version = 1
  local encoded = cjson.encode(flag)
  redis.call('SET', flagKey, encoded)
  redis.call('SADD', indexKey, flag.key)
  local event = cjson.decode(eventTemplate)
  event.version = 1
  event.event = 'FLAG_UPDATED'
  redis.call('PUBLISH', channel, cjson.encode(event))
  return { 'ok', '1', encoded }
end

if not current then
  return { 'not_found', '0', '' }
end

local cur = cjson.decode(current)
if cur.version ~= expectedVersion then
  return { 'conflict', tostring(cur.version), current }
end

if op == 'delete' then
  local newVersion = cur.version + 1
  redis.call('DEL', flagKey)
  redis.call('SREM', indexKey, cur.key)
  local event = cjson.decode(eventTemplate)
  event.version = newVersion
  event.event = 'FLAG_DELETED'
  redis.call('PUBLISH', channel, cjson.encode(event))
  return { 'ok', tostring(newVersion), '' }
end

if op == 'update' then
  local flag = cjson.decode(flagJson)
  flag.version = cur.version + 1
  flag.createdAt = cur.createdAt
  local encoded = cjson.encode(flag)
  redis.call('SET', flagKey, encoded)
  redis.call('SADD', indexKey, flag.key)
  local event = cjson.decode(eventTemplate)
  event.version = flag.version
  event.event = 'FLAG_UPDATED'
  redis.call('PUBLISH', channel, cjson.encode(event))
  return { 'ok', tostring(flag.version), encoded }
end

return { 'not_found', '0', '' }
"""


class FeatureFlagRepository:
    def __init__(self, redis_service: RedisService | None = None) -> None:
        self._redis_service = redis_service
        self._memory_store: dict[str, dict[str, IFeatureFlag]] = {}

    async def get_flag(self, env: str, key: str) -> IFeatureFlag | None:
        if self._redis_service is not None and self._redis_service.is_connected:
            try:
                client = self._redis_service.get_client()
                raw = await client.get(feature_flag_key(env, key))
                if raw is None:
                    return None
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                if is_valid_feature_flag_shape(data):
                    return dict_to_feature_flag(data)
            except Exception as exc:
                logger.warning("Redis get_flag failed, checking memory: %s", exc)

        return self._memory_store.get(env, {}).get(key)

    async def load_all_flags(self, env: str) -> list[IFeatureFlag]:
        if self._redis_service is not None and self._redis_service.is_connected:
            try:
                client = self._redis_service.get_client()
                members = await client.smembers(feature_flag_index_key(env))
                if not members:
                    return []
                keys = [
                    feature_flag_key(env, m if isinstance(m, str) else m.decode())
                    for m in members
                ]
                raws = await client.mget(keys)
                flags: list[IFeatureFlag] = []
                for item in raws:
                    if item:
                        data = json.loads(
                            item if isinstance(item, str) else item.decode()
                        )
                        if is_valid_feature_flag_shape(data):
                            flags.append(dict_to_feature_flag(data))
                return flags
            except Exception as exc:
                logger.warning("Redis load_all_flags failed, returning memory: %s", exc)

        return list(self._memory_store.get(env, {}).values())

    async def cas_write(
        self,
        env: str,
        op: str,
        key: str,
        expected_version: int,
        flag_payload: dict[str, Any] | None,
    ) -> tuple[str, int, IFeatureFlag | None]:
        flag_k = feature_flag_key(env, key)
        idx_k = feature_flag_index_key(env)
        channel = feature_flag_events_channel(env)
        event_tmpl = json.dumps(
            {"environment": env, "key": key, "event": "FLAG_UPDATED"}
        )
        flag_json = json.dumps(flag_payload) if flag_payload else ""

        if self._redis_service is not None and self._redis_service.is_connected:
            try:
                client = self._redis_service.get_client()
                res = await client.eval(
                    CAS_WRITE_LUA,
                    3,
                    flag_k,
                    idx_k,
                    channel,
                    op,
                    str(expected_version),
                    event_tmpl,
                    flag_json,
                )
                status = res[0] if isinstance(res[0], str) else res[0].decode()
                version = int(res[1])
                flag_data = res[2] if isinstance(res[2], str) else res[2].decode()
                flag: IFeatureFlag | None = None
                if flag_data:
                    parsed = json.loads(flag_data)
                    flag = dict_to_feature_flag(parsed)
                return status, version, flag
            except Exception as exc:
                logger.warning("Redis cas_write failed, falling back: %s", exc)

        # In-memory CAS fallback
        env_store = self._memory_store.setdefault(env, {})
        cur = env_store.get(key)

        if op == "create":
            if cur is not None:
                return "conflict", cur.version, cur
            if expected_version != 0:
                return "conflict", 0, None
            if not flag_payload:
                return "conflict", 0, None
            flag_payload["version"] = 1
            new_flag = dict_to_feature_flag(flag_payload)
            env_store[key] = new_flag
            return "ok", 1, new_flag

        if cur is None:
            return "not_found", 0, None

        if cur.version != expected_version:
            return "conflict", cur.version, cur

        if op == "delete":
            del env_store[key]
            return "ok", cur.version + 1, None

        if op == "update":
            if not flag_payload:
                return "conflict", cur.version, cur
            flag_payload["version"] = cur.version + 1
            flag_payload["createdAt"] = cur.created_at
            updated_flag = dict_to_feature_flag(flag_payload)
            env_store[key] = updated_flag
            return "ok", updated_flag.version, updated_flag

        return "not_found", 0, None
