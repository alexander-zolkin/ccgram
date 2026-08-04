"""Provider adapters for command hook stdin payloads.

The adapters validate common fields, map native event names to ccgram's canonical
lifecycle events, and retain only metadata safe enough for ``events.jsonl``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from ccgram.providers.base import UUID_RE

from .model import HookAdapter, JsonValue, NormalizedHookEvent, ProviderName

_SAFE_PROVIDERS: tuple[ProviderName, ...] = ("claude", "pi", "codex", "gemini", "grok")

# Grok emits snake_case ``hookEventName`` values; map them to ccgram's canonical
# PascalCase lifecycle names (Grok also accepts Claude/Cursor PascalCase, which
# we pass through unchanged below).
_GROK_EVENT_MAP: dict[str, str] = {
    "session_start": "SessionStart",
    "user_prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "post_tool_use_failure": "PostToolUseFailure",
    "permission_denied": "PermissionDenied",
    "stop": "Stop",
    "stop_failure": "StopFailure",
    "notification": "Notification",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "subagent_end": "SubagentStop",
    "pre_compact": "PreCompact",
    "post_compact": "PostCompact",
    "session_end": "SessionEnd",
}

# Event names emitted only by Gemini — used by detect_provider_from_payload
# to distinguish Gemini payloads when transcript path is absent. SessionStart,
# SessionEnd, and Notification are shared across providers so they don't help.
_GEMINI_ONLY_EVENT_TYPES: tuple[str, ...] = (
    "AfterAgent",
    "BeforeAgent",
    "BeforeTool",
    "AfterTool",
    "PreCompress",
)


def _str_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _is_claude_transcript_path(transcript_path: str) -> bool:
    """Return whether a transcript path belongs to the Claude configuration."""
    if "/.claude/" in transcript_path:
        return True
    config_dir = os.getenv("CLAUDE_CONFIG_DIR")
    if not config_dir:
        return False
    return (
        Path(transcript_path)
        .expanduser()
        .is_relative_to(Path(config_dir).expanduser() / "projects")
    )


def _int_field(payload: dict[str, object], key: str, default: int = 0) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) else default


def _bool_field(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    return value if isinstance(value, bool) else False


def _path_or_none(value: str) -> Path | None:
    if not value:
        return None
    return Path(value) if os.path.isabs(value) else None


def _safe_details_tool_name(payload: dict[str, object]) -> str:
    details = payload.get("details")
    if not isinstance(details, dict):
        return ""
    tool_name = details.get("tool_name") or details.get("toolName")
    return tool_name if isinstance(tool_name, str) else ""


def _event(
    *,
    provider_name: ProviderName,
    native_event_name: str,
    canonical_event_name: str,
    session_id: str,
    cwd: str,
    transcript_path: str,
    data: dict[str, JsonValue] | None = None,
) -> NormalizedHookEvent | None:
    if not session_id or not native_event_name:
        return None
    cwd_path = _path_or_none(cwd)
    if cwd and cwd_path is None:
        return None
    if canonical_event_name == "SessionStart" and cwd_path is None:
        return None
    safe_data: dict[str, JsonValue] = {
        "provider_name": provider_name,
        "native_event_name": native_event_name,
    }
    if data:
        safe_data.update(data)
    return NormalizedHookEvent(
        provider_name=provider_name,
        native_event_name=native_event_name,
        canonical_event_name=canonical_event_name,
        session_id=session_id,
        cwd=cwd_path,
        transcript_path=_path_or_none(transcript_path),
        data=safe_data,
    )


class ClaudeHookAdapter:
    """Normalize Claude Code hook payloads."""

    provider_name: ProviderName = "claude"
    event_types: tuple[str, ...] = (
        "SessionStart",
        "Notification",
        "Stop",
        "StopFailure",
        "SessionEnd",
        "SubagentStart",
        "SubagentStop",
        "TeammateIdle",
        "TaskCompleted",
    )
    # Claude installs all of its event types — the install path uses
    # ~/.claude/settings.json schema rather than the JSON-hook installer,
    # but installable_events lets call sites read every adapter uniformly.
    installable_events: tuple[str, ...] = event_types

    def normalize(self, payload: dict[str, object]) -> NormalizedHookEvent | None:
        event_name = _str_field(payload, "hook_event_name")
        session_id = _str_field(payload, "session_id")
        if event_name not in self.event_types or not UUID_RE.match(session_id):
            return None
        data = _extract_claude_data(event_name, payload)
        return _event(
            provider_name=self.provider_name,
            native_event_name=event_name,
            canonical_event_name=event_name,
            session_id=session_id,
            cwd=_str_field(payload, "cwd"),
            transcript_path=_str_field(payload, "transcript_path"),
            data=data,
        )


class PiHookAdapter:
    """Normalize Pi hook-runner payloads."""

    provider_name: ProviderName = "pi"
    event_types: tuple[str, ...] = (
        "SessionStart",
        "Stop",
        "SessionEnd",
        "SubagentStart",
        "SubagentStop",
        "Notification",
        "PreCompact",
        "PostCompact",
    )
    # Pi hooks are installed by cc-thingz hook-runner, not ccgram.
    installable_events: tuple[str, ...] = ()

    def normalize(self, payload: dict[str, object]) -> NormalizedHookEvent | None:
        event_name = _str_field(payload, "hook_event_name")
        if event_name not in self.event_types:
            return None
        session_id = _str_field(payload, "session_id")
        if not UUID_RE.match(session_id):
            return None
        canonical = event_name
        data: dict[str, JsonValue] = {}
        if event_name == "SessionEnd":
            data["reason"] = _str_field(payload, "reason") or _str_field(
                payload, "end_reason"
            )
        elif event_name == "Notification":
            data["message"] = _str_field(payload, "message")
            data["notification_type"] = _str_field(payload, "notification_type")
            data["tool_name"] = _str_field(payload, "tool_name")
        elif event_name in {"SubagentStart", "SubagentStop"}:
            data["subagent_id"] = _str_field(payload, "subagent_id")
            data["name"] = _str_field(payload, "name") or "Pi agent"
            data["description"] = _str_field(payload, "description")
        return _event(
            provider_name=self.provider_name,
            native_event_name=event_name,
            canonical_event_name=canonical,
            session_id=session_id,
            cwd=_str_field(payload, "cwd"),
            transcript_path=_str_field(payload, "transcript_path"),
            data=data,
        )


class CodexHookAdapter:
    """Normalize Codex hook payloads."""

    provider_name: ProviderName = "codex"
    event_types: tuple[str, ...] = (
        "SessionStart",
        "Stop",
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "UserPromptSubmit",
        "PreCompact",
        "PostCompact",
    )
    # Only the lifecycle signals ccgram acts on — the rest are accepted by
    # normalize() in case Codex starts emitting them with useful data.
    installable_events: tuple[str, ...] = ("SessionStart", "Stop")

    def normalize(self, payload: dict[str, object]) -> NormalizedHookEvent | None:
        event_name = _str_field(payload, "hook_event_name")
        if event_name not in self.event_types:
            return None
        session_id = _str_field(payload, "session_id")
        if not UUID_RE.match(session_id):
            return None
        canonical = event_name
        data: dict[str, JsonValue] = {}
        if event_name == "Stop":
            data["stop_hook_active"] = _bool_field(payload, "stop_hook_active")
            data["stop_reason"] = _str_field(payload, "stopReason")
        elif event_name in {"PreToolUse", "PostToolUse", "PermissionRequest"}:
            data["tool_name"] = _str_field(payload, "tool_name")
        elif event_name == "SessionStart":
            data["source"] = _str_field(payload, "source")
        elif event_name in {"PreCompact", "PostCompact"}:
            data["trigger"] = _str_field(payload, "trigger")
        return _event(
            provider_name=self.provider_name,
            native_event_name=event_name,
            canonical_event_name=canonical,
            session_id=session_id,
            cwd=_str_field(payload, "cwd"),
            transcript_path=_str_field(payload, "transcript_path"),
            data=data,
        )


class GeminiHookAdapter:
    """Normalize Gemini CLI hook payloads."""

    provider_name: ProviderName = "gemini"
    event_types: tuple[str, ...] = (
        "SessionStart",
        "SessionEnd",
        "Notification",
        "AfterAgent",
        "BeforeAgent",
        "BeforeTool",
        "AfterTool",
        "PreCompress",
    )
    # AfterAgent maps to canonical Stop; the rest line up directly.
    installable_events: tuple[str, ...] = (
        "SessionStart",
        "AfterAgent",
        "SessionEnd",
        "Notification",
    )

    def normalize(self, payload: dict[str, object]) -> NormalizedHookEvent | None:
        # Gemini session IDs are not UUIDs in current builds (free-form CLI
        # tokens). Skipping UUID validation here is intentional; the empty-
        # session_id and absolute-cwd checks in _event still apply.
        event_name = _str_field(payload, "hook_event_name")
        if event_name not in self.event_types:
            return None
        canonical = "Stop" if event_name == "AfterAgent" else event_name
        data: dict[str, JsonValue] = {}
        if event_name == "SessionEnd":
            data["reason"] = _str_field(payload, "reason")
        elif event_name == "Notification":
            data["message"] = _str_field(payload, "message")
            data["notification_type"] = _str_field(payload, "notification_type")
            data["tool_name"] = _safe_details_tool_name(payload)
        elif event_name == "AfterAgent":
            data["stop_hook_active"] = _bool_field(payload, "stop_hook_active")
        elif event_name in {"BeforeTool", "AfterTool"}:
            data["tool_name"] = _str_field(payload, "tool_name")
        elif event_name == "SessionStart":
            data["source"] = _str_field(payload, "source")
        elif event_name == "PreCompress":
            data["trigger"] = _str_field(payload, "trigger")
        return _event(
            provider_name=self.provider_name,
            native_event_name=event_name,
            canonical_event_name=canonical,
            session_id=_str_field(payload, "session_id"),
            cwd=_str_field(payload, "cwd"),
            transcript_path=_str_field(payload, "transcript_path"),
            data=data,
        )


def _first_str_field(payload: dict[str, object], *keys: str) -> str:
    """Return the first non-empty string among *keys* (camelCase/snake_case)."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


class GrokHookAdapter:
    """Normalize Grok Build hook payloads.

    Grok sends camelCase stdin keys (``hookEventName``, ``sessionId``,
    ``workspaceRoot``) with snake_case event values (``session_start``,
    ``stop`` …).  It also accepts Claude/Cursor PascalCase, so both are handled.
    Session IDs are UUIDv7 strings, which satisfy ``UUID_RE``.  The payload omits
    a transcript path — it is reconstructed downstream from the session id + cwd.
    """

    provider_name: ProviderName = "grok"
    event_types: tuple[str, ...] = (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PermissionDenied",
        "Stop",
        "StopFailure",
        "Notification",
        "SubagentStart",
        "SubagentStop",
        "PreCompact",
        "PostCompact",
        "SessionEnd",
    )
    # The lifecycle signals ccgram acts on and writes into ~/.grok/hooks/.
    installable_events: tuple[str, ...] = (
        "SessionStart",
        "Stop",
        "StopFailure",
        "SessionEnd",
        "Notification",
        "SubagentStart",
        "SubagentStop",
    )

    def normalize(self, payload: dict[str, object]) -> NormalizedHookEvent | None:
        raw_event = _first_str_field(payload, "hookEventName", "hook_event_name")
        canonical = _GROK_EVENT_MAP.get(raw_event)
        if canonical is None:
            canonical = raw_event if raw_event in self.event_types else ""
        if not canonical:
            return None
        session_id = _first_str_field(payload, "sessionId", "session_id")
        if not UUID_RE.match(session_id):
            return None
        cwd = _first_str_field(payload, "cwd", "workspaceRoot", "workspace_root")
        data: dict[str, JsonValue] = {}
        if canonical == "Notification":
            data["message"] = _first_str_field(payload, "message")
            data["notification_type"] = _first_str_field(
                payload, "notificationType", "notification_type"
            )
            data["tool_name"] = _first_str_field(payload, "toolName", "tool_name")
        elif canonical in {"Stop", "StopFailure"}:
            data["stop_reason"] = _first_str_field(payload, "stopReason", "stop_reason")
        elif canonical == "SessionEnd":
            data["reason"] = _first_str_field(
                payload, "reason", "endReason", "end_reason"
            )
        elif canonical in {"SubagentStart", "SubagentStop"}:
            data["subagent_id"] = _first_str_field(payload, "subagentId", "subagent_id")
            data["name"] = _first_str_field(payload, "name") or "Grok subagent"
            data["description"] = _first_str_field(payload, "description")
        elif canonical == "SessionStart":
            data["source"] = _first_str_field(payload, "source")
        return _event(
            provider_name=self.provider_name,
            native_event_name=raw_event,
            canonical_event_name=canonical,
            session_id=session_id,
            cwd=cwd,
            transcript_path=_first_str_field(
                payload, "transcriptPath", "transcript_path"
            ),
            data=data,
        )


def _extract_claude_data(
    event_name: str, payload: dict[str, object]
) -> dict[str, JsonValue]:
    if event_name == "Notification":
        return {
            "tool_name": _str_field(payload, "tool_name"),
            "message": _str_field(payload, "message"),
        }
    if event_name == "Stop":
        return {
            "stop_reason": _str_field(payload, "stop_reason"),
            "num_turns": _int_field(payload, "num_turns"),
        }
    if event_name == "StopFailure":
        return {
            "error": _str_field(payload, "error"),
            "error_details": _str_field(payload, "error_details"),
        }
    if event_name == "SessionEnd":
        return {"reason": _str_field(payload, "reason")}
    if event_name in {"SubagentStart", "SubagentStop"}:
        return {
            "subagent_id": _str_field(payload, "subagent_id"),
            "description": _str_field(payload, "description"),
            "name": _str_field(payload, "name"),
        }
    if event_name == "TeammateIdle":
        return {
            "teammate_name": _str_field(payload, "teammate_name"),
            "team_name": _str_field(payload, "team_name"),
        }
    if event_name == "TaskCompleted":
        return {
            "task_id": _str_field(payload, "task_id"),
            "task_subject": _str_field(payload, "task_subject"),
            "task_description": _str_field(payload, "task_description"),
            "teammate_name": _str_field(payload, "teammate_name"),
            "team_name": _str_field(payload, "team_name"),
        }
    return {}


_ADAPTERS: dict[ProviderName, HookAdapter] = {
    "claude": ClaudeHookAdapter(),
    "pi": PiHookAdapter(),
    "codex": CodexHookAdapter(),
    "gemini": GeminiHookAdapter(),
    "grok": GrokHookAdapter(),
}


def get_hook_adapter(provider_name: str) -> HookAdapter | None:
    """Return the hook adapter for a provider name."""
    if provider_name not in _SAFE_PROVIDERS:
        return None
    return _ADAPTERS[cast(ProviderName, provider_name)]


def detect_provider_from_payload(  # noqa: C901 — linear provider-detection chain
    payload: dict[str, object],
) -> ProviderName | None:
    """Best-effort provider detection when installed hook lacks --provider."""
    explicit = _str_field(payload, "provider_name")
    transcript_path = _str_field(payload, "transcript_path")
    event_name = _str_field(payload, "hook_event_name")
    session_id = _str_field(payload, "session_id")

    provider: ProviderName | None = None
    if explicit in _SAFE_PROVIDERS:
        provider = cast(ProviderName, explicit)
    elif "/.grok/" in transcript_path:
        provider = "grok"
    elif "/.codex/" in transcript_path:
        provider = "codex"
    elif "/.gemini/" in transcript_path:
        provider = "gemini"
    elif "/.pi/" in transcript_path:
        provider = "pi"
    elif _str_field(payload, "hookEventName") or _str_field(payload, "workspaceRoot"):
        # Grok is the only provider that sends camelCase hook keys; this covers a
        # manually-started grok pane whose hook lacks an explicit --provider flag.
        provider = "grok"
    elif "/.claude/" in transcript_path:
        # CCGRAM-HOTFIX:claude-stop-permmode — Claude Code >=2.1 puts
        # permission_mode/model into the Stop payload; the codex heuristic below
        # then misdetects claude Stop events as 'codex' and the reply is never
        # posted (Notification lacks those fields, so it stays claude → "typing"
        # shows but no text). A /.claude/ transcript is authoritative claude;
        # return None so the caller's pane-tty/claude default applies.
        provider = None
    elif event_name in _GEMINI_ONLY_EVENT_TYPES:
        provider = "gemini"
    elif (
        _str_field(payload, "permission_mode") or _str_field(payload, "model")
    ) and not _is_claude_transcript_path(transcript_path):
        # ``model``/``permission_mode`` are weak codex signals: Claude's Stop and
        # Notification payloads now also carry ``model``, so without this guard a
        # Claude session installed with no --provider flag is misdetected as codex.
        provider = "codex"
    elif _str_field(payload, "end_reason") or (
        session_id and not UUID_RE.match(session_id)
    ):
        provider = "pi"
    return provider
