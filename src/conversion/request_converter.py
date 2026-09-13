import json
import logging
from typing import Dict, Any, List, Optional
from src.core.constants import Constants
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage
from src.core.config import config
from src.security.sanitizer import sanitizer
from src.conversion.schema_sanitizer import sanitize_tool_parameters

logger = logging.getLogger(__name__)


def extract_text_from_content(content: Any) -> str:
    """Extract clean string text from various Claude content formats."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block.strip())
            elif isinstance(block, dict):
                if block.get("text"):
                    parts.append(str(block["text"]).strip())
                elif block.get("content"):
                    parts.append(str(block["content"]).strip())
            elif hasattr(block, "text") and block.text:
                parts.append(str(block.text).strip())
        return "\n\n".join(parts)
    if isinstance(content, dict):
        if content.get("text"):
            return str(content["text"]).strip()
        if content.get("content"):
            return str(content["content"]).strip()
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)
    return str(content)


def reconcile_tool_calls_and_responses(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure every tool_call in an assistant message has a matching tool role message following it."""
    reconciled = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        reconciled.append(msg)
        if msg.get("role") == Constants.ROLE_ASSISTANT and msg.get("tool_calls"):
            expected_ids = [tc["id"] for tc in msg["tool_calls"] if isinstance(tc, dict) and "id" in tc]
            answered_ids = set()
            j = i + 1
            while j < len(messages) and messages[j].get("role") == Constants.ROLE_TOOL:
                answered_ids.add(messages[j].get("tool_call_id"))
                j += 1
            for missing_id in expected_ids:
                if missing_id not in answered_ids:
                    reconciled.append(
                        {
                            "role": Constants.ROLE_TOOL,
                            "tool_call_id": missing_id,
                            "content": "Tool execution was skipped or interrupted.",
                        }
                    )
        i += 1
    return reconciled


def is_text_only_model(model_name: Optional[str]) -> bool:
    """Detect if target OpenAI model is known to be text-only (lacks multimodal vision)."""
    if not model_name:
        return False
    m = model_name.lower()
    # Explicit multimodal / vision indicators
    if any(tag in m for tag in ("vl", "vision", "4o", "gemini", "claude", "omni", "pixtral", "llava", "minicpm")):
        return False
    # Known text-only patterns
    text_only_patterns = (
        "coder",
        "codestral",
        "deepseek-coder",
        "deepseek-math",
        "starcoder",
        "llama-3-",
        "llama-3.1-",
        "llama-3.2-1b",
        "llama-3.2-3b",
        "mistral-7b",
        "qwen2.5-coder",
    )
    return any(tag in m for tag in text_only_patterns)


def request_has_images(claude_request: ClaudeMessagesRequest) -> bool:
    """Detect if the incoming Claude request contains image blocks."""
    for msg in claude_request.messages:
        if isinstance(msg.content, list):
            for block in msg.content:
                btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                if btype == Constants.CONTENT_IMAGE:
                    return True
    return False


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """Rough estimation of token count from OpenAI messages (~3.5 chars per token)."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text", "")))
        if "tool_calls" in msg:
            total_chars += len(str(msg["tool_calls"]))
    return int(total_chars / 3.5)


def compact_message_history(
    messages: List[Dict[str, Any]], max_context_tokens: int = 100000
) -> List[Dict[str, Any]]:
    """
    Compact message history if total tokens exceed max_context_tokens.
    Preserves system message (index 0), initial user message, and recent 10 messages.
    Compacts intermediate tool outputs by truncating excessive outputs to keep essential context.
    """
    if len(messages) <= 12:
        return messages

    estimated = estimate_tokens(messages)
    if estimated <= max_context_tokens:
        return messages

    logger.info(
        "Context Guard triggered: estimated %d tokens exceeds threshold %d tokens. Compacting ancient tool outputs...",
        estimated,
        max_context_tokens,
    )

    compacted = []
    cutoff = len(messages) - 10

    for idx, msg in enumerate(messages):
        # Always preserve system message and the 10 most recent messages
        if idx == 0 or idx >= cutoff:
            compacted.append(msg)
            continue

        if msg.get("role") == Constants.ROLE_TOOL and isinstance(msg.get("content"), str):
            content = msg["content"]
            if len(content) > 1000:
                head = content[:400]
                tail = content[-400:]
                omitted = len(content) - 800
                new_content = (
                    f"{head}\n\n"
                    f"[... ClaudeGate Context Guard: {omitted} characters of intermediate tool output compacted to prevent context limit overflow ...]\n\n"
                    f"{tail}"
                )
                msg_copy = dict(msg)
                msg_copy["content"] = new_content
                compacted.append(msg_copy)
                continue

        compacted.append(msg)

    return compacted


def convert_claude_to_openai(
    claude_request: ClaudeMessagesRequest, model_manager: Any
) -> Dict[str, Any]:
    """Convert Claude API request format to OpenAI format with robust tool reconciliation."""

    # Map model
    openai_model = model_manager.map_claude_model_to_openai(claude_request.model)

    # 0. Check vision capability and auto-fallback
    if request_has_images(claude_request) and is_text_only_model(openai_model):
        vision_fallback = getattr(config, "vision_fallback_model", None)
        if vision_fallback and not is_text_only_model(vision_fallback):
            logger.info(
                "Active model '%s' is text-only but request contains images. Auto-routing to vision fallback model '%s'.",
                openai_model,
                vision_fallback,
            )
            openai_model = vision_fallback

    openai_messages = []
    system_parts = []

    # 1. Collect system prompt from claude_request.system
    if claude_request.system:
        system_text = extract_text_from_content(claude_request.system)
        if system_text.strip():
            system_parts.append(system_text.strip())

    # 2. Process Claude messages and ensure no system messages are placed after user/assistant
    i = 0
    non_system_started = False

    while i < len(claude_request.messages):
        msg = claude_request.messages[i]
        role = msg.role.lower() if isinstance(msg.role, str) else str(msg.role)

        if role in (Constants.ROLE_SYSTEM, "system"):
            content_str = extract_text_from_content(msg.content)
            if not non_system_started:
                if content_str.strip():
                    system_parts.append(content_str.strip())
            else:
                openai_messages.append({"role": Constants.ROLE_USER, "content": f"[System Note]: {content_str}"})
            i += 1
            continue

        non_system_started = True

        if role == Constants.ROLE_USER:
            # Check if user message contains tool_result blocks
            if isinstance(msg.content, list) and any(
                (isinstance(block, dict) and block.get("type") == Constants.CONTENT_TOOL_RESULT)
                or getattr(block, "type", None) == Constants.CONTENT_TOOL_RESULT
                for block in msg.content
            ):
                tool_results = convert_claude_tool_results(msg)
                openai_messages.extend(tool_results)

                # Check if there are ALSO non-tool-result blocks (e.g. user text alongside tool results)
                non_tool_blocks = [
                    block
                    for block in msg.content
                    if not (
                        (isinstance(block, dict) and block.get("type") == Constants.CONTENT_TOOL_RESULT)
                        or getattr(block, "type", None) == Constants.CONTENT_TOOL_RESULT
                    )
                ]
                if non_tool_blocks:
                    sub_msg = ClaudeMessage(role=Constants.ROLE_USER, content=non_tool_blocks)
                    openai_messages.append(convert_claude_user_message(sub_msg, openai_model))
            else:
                openai_message = convert_claude_user_message(msg, openai_model)
                openai_messages.append(openai_message)
        elif role == Constants.ROLE_ASSISTANT:
            openai_message = convert_claude_assistant_message(msg)
            openai_messages.append(openai_message)
        else:
            content_str = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            openai_messages.append({"role": Constants.ROLE_USER, "content": content_str})

        i += 1

    # Insert exactly ONE consolidated system message at the very beginning (index 0) if present
    if system_parts:
        consolidated_system = "\n\n".join(system_parts)
        openai_messages.insert(0, {"role": Constants.ROLE_SYSTEM, "content": consolidated_system})

    if not openai_messages:
        openai_messages = [{"role": Constants.ROLE_USER, "content": "Hello"}]

    # Reconcile any orphan tool calls so upstream never throws 400 Bad Request
    openai_messages = reconcile_tool_calls_and_responses(openai_messages)

    # Apply secret sanitization if enabled
    openai_messages = sanitizer.sanitize_messages(openai_messages)

    # Apply Context Overflow Guard if enabled
    if getattr(config, "context_guard_enabled", True):
        max_ctx = getattr(config, "max_context_tokens", 100000)
        openai_messages = compact_message_history(openai_messages, max_ctx)

    # Model category detection: o1 / o3 / o4 reasoning models
    model_lower = str(openai_model).lower()
    is_reasoning_model = (
        model_lower.startswith(("o1", "o3", "o4"))
        or "o1-" in model_lower
        or "o3-" in model_lower
        or "o4-" in model_lower
    )

    target_max_tokens = min(
        max(claude_request.max_tokens or 4096, config.min_tokens_limit),
        config.max_tokens_limit,
    )

    # Build OpenAI request
    openai_request = {
        "model": openai_model,
        "messages": openai_messages,
        "stream": claude_request.stream,
    }

    if is_reasoning_model:
        # o1 / o3 / o4 require max_completion_tokens instead of max_tokens
        openai_request["max_completion_tokens"] = target_max_tokens
    else:
        openai_request["max_tokens"] = target_max_tokens
        openai_request["temperature"] = claude_request.temperature

    # Reasoning effort mapping for o1/o3/o4
    budget_tokens = None
    if claude_request.thinking:
        if isinstance(claude_request.thinking, dict):
            budget_tokens = claude_request.thinking.get("budget_tokens")
        elif hasattr(claude_request.thinking, "budget_tokens"):
            budget_tokens = claude_request.thinking.budget_tokens

    if is_reasoning_model and budget_tokens is not None:
        if budget_tokens < 2048:
            openai_request["reasoning_effort"] = "low"
        elif budget_tokens <= 8192:
            openai_request["reasoning_effort"] = "medium"
        else:
            openai_request["reasoning_effort"] = "high"

    sanitized_model_for_log = str(openai_model).replace("\r", "").replace("\n", "")
    sanitized_stream_for_log = str(claude_request.stream).replace("\r", "").replace("\n", "")
    logger.debug(
        "Converted Claude request to OpenAI format: model=%s, messages_count=%d, stream=%s",
        sanitized_model_for_log,
        len(openai_messages),
        sanitized_stream_for_log,
    )

    # Add optional parameters
    if claude_request.stop_sequences:
        openai_request["stop"] = claude_request.stop_sequences
    if claude_request.top_p is not None:
        openai_request["top_p"] = claude_request.top_p

    # Convert tools
    if claude_request.tools:
        openai_tools = []
        for tool in claude_request.tools:
            if tool.name and tool.name.strip():
                sanitized_schema = sanitize_tool_parameters(tool.input_schema)
                openai_tools.append(
                    {
                        "type": Constants.TOOL_FUNCTION,
                        Constants.TOOL_FUNCTION: {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": sanitized_schema,
                        },
                    }
                )
        if openai_tools:
            openai_request["tools"] = openai_tools

    # Convert tool choice accurately
    if claude_request.tool_choice:
        choice_type = claude_request.tool_choice.get("type")
        if choice_type == "auto":
            openai_request["tool_choice"] = "auto"
        elif choice_type == "any":
            openai_request["tool_choice"] = "required"
        elif choice_type == "tool" and "name" in claude_request.tool_choice:
            openai_request["tool_choice"] = {
                "type": Constants.TOOL_FUNCTION,
                Constants.TOOL_FUNCTION: {"name": claude_request.tool_choice["name"]},
            }
        else:
            openai_request["tool_choice"] = "auto"

        if claude_request.tool_choice.get("disable_parallel_tool_use"):
            openai_request["parallel_tool_calls"] = False

    return openai_request


def convert_claude_user_message(
    msg: ClaudeMessage, model_name: Optional[str] = None
) -> Dict[str, Any]:
    """Convert Claude user message to OpenAI format, gracefully degrading images if model is text-only."""
    if msg.content is None:
        return {"role": Constants.ROLE_USER, "content": ""}

    if isinstance(msg.content, str):
        return {"role": Constants.ROLE_USER, "content": msg.content}

    if not isinstance(msg.content, list):
        return {"role": Constants.ROLE_USER, "content": str(msg.content)}

    degrade_images = is_text_only_model(model_name) if model_name else False

    # Handle multimodal content
    openai_content = []
    for block in msg.content:
        block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        block_text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
        block_source = getattr(block, "source", None) or (block.get("source") if isinstance(block, dict) else None)

        if block_type == Constants.CONTENT_TEXT:
            openai_content.append({"type": "text", "text": block_text or ""})
        elif block_type == Constants.CONTENT_IMAGE:
            media_type = block_source.get("media_type", "image") if isinstance(block_source, dict) else "image"
            if degrade_images:
                openai_content.append(
                    {
                        "type": "text",
                        "text": f"[Attached image ({media_type}) - omitted as active model '{model_name}' does not support vision]",
                    }
                )
            elif (
                isinstance(block_source, dict)
                and block_source.get("type") == "base64"
                and "media_type" in block_source
                and "data" in block_source
            ):
                openai_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{block_source['media_type']};base64,{block_source['data']}"
                        },
                    }
                )

    if len(openai_content) == 1 and openai_content[0]["type"] == "text":
        return {"role": Constants.ROLE_USER, "content": openai_content[0]["text"]}
    else:
        return {"role": Constants.ROLE_USER, "content": openai_content if openai_content else str(msg.content)}


def convert_claude_assistant_message(msg: ClaudeMessage) -> Dict[str, Any]:
    """Convert Claude assistant message to OpenAI format."""
    text_parts = []
    tool_calls = []

    if msg.content is None:
        return {"role": Constants.ROLE_ASSISTANT, "content": ""}

    if isinstance(msg.content, str):
        return {"role": Constants.ROLE_ASSISTANT, "content": msg.content}

    if not isinstance(msg.content, list):
        return {"role": Constants.ROLE_ASSISTANT, "content": str(msg.content)}

    for block in msg.content:
        block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        block_text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
        block_id = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else None)
        block_name = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else None)
        block_input = getattr(block, "input", None) or (block.get("input") if isinstance(block, dict) else None)

        if block_type == Constants.CONTENT_TEXT:
            if block_text:
                text_parts.append(block_text)
        elif block_type in ("thinking", "redacted_thinking"):
            # Omit thinking blocks from assistant history sent to OpenAI API
            pass
        elif block_type == Constants.CONTENT_TOOL_USE:
            tool_calls.append(
                {
                    "id": block_id or "tool_call_1",
                    "type": Constants.TOOL_FUNCTION,
                    Constants.TOOL_FUNCTION: {
                        "name": block_name or "",
                        "arguments": json.dumps(block_input or {}, ensure_ascii=False),
                    },
                }
            )

    openai_message: Dict[str, Any] = {"role": Constants.ROLE_ASSISTANT}

    # Set content (always string or empty string to avoid API null rejections)
    openai_message["content"] = "".join(text_parts) if text_parts else ""

    # Set tool calls
    if tool_calls:
        openai_message["tool_calls"] = tool_calls

    return openai_message


def convert_claude_tool_results(msg: ClaudeMessage) -> List[Dict[str, Any]]:
    """Convert Claude tool results to OpenAI format."""
    tool_messages = []

    if isinstance(msg.content, list):
        for block in msg.content:
            block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            block_content = getattr(block, "content", None) or (block.get("content") if isinstance(block, dict) else None)
            block_tool_use_id = getattr(block, "tool_use_id", None) or (block.get("tool_use_id") if isinstance(block, dict) else None)

            if block_type == Constants.CONTENT_TOOL_RESULT:
                content = parse_tool_result_content(block_content)
                tool_messages.append(
                    {
                        "role": Constants.ROLE_TOOL,
                        "tool_call_id": block_tool_use_id or "",
                        "content": content,
                    }
                )

    return tool_messages


def parse_tool_result_content(content: Any) -> str:
    """Parse and normalize tool result content into a string format."""
    if content is None:
        return "No content provided"

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        result_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == Constants.CONTENT_TEXT:
                result_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                result_parts.append(item)
            elif isinstance(item, dict):
                if "text" in item:
                    result_parts.append(item.get("text", ""))
                else:
                    try:
                        result_parts.append(json.dumps(item, ensure_ascii=False))
                    except Exception:
                        result_parts.append(str(item))
        return "\n".join(result_parts).strip()

    if isinstance(content, dict):
        if content.get("type") == Constants.CONTENT_TEXT:
            return content.get("text", "")
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)

    try:
        return str(content)
    except Exception:
        return "Unparseable content"
