import json
import logging
from typing import Dict, Any, List
from src.core.constants import Constants
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage
from src.core.config import config
from src.security.sanitizer import sanitizer

logger = logging.getLogger(__name__)


def convert_claude_to_openai(
    claude_request: ClaudeMessagesRequest, model_manager: Any
) -> Dict[str, Any]:
    """Convert Claude API request format to OpenAI format."""

    # Map model
    openai_model = model_manager.map_claude_model_to_openai(claude_request.model)

    openai_messages = []
    system_parts = []

    # 1. Collect system prompt from claude_request.system
    if claude_request.system:
        if isinstance(claude_request.system, str):
            if claude_request.system.strip():
                system_parts.append(claude_request.system.strip())
        elif isinstance(claude_request.system, list):
            for block in claude_request.system:
                if isinstance(block, str) and block.strip():
                    system_parts.append(block.strip())
                elif hasattr(block, "text") and block.text:
                    system_parts.append(block.text.strip())
                elif isinstance(block, dict):
                    if block.get("text"):
                        system_parts.append(str(block["text"]).strip())
                    elif block.get("content"):
                        system_parts.append(str(block["content"]).strip())
        elif isinstance(claude_request.system, dict):
            if claude_request.system.get("text"):
                system_parts.append(str(claude_request.system["text"]).strip())

    # 2. Process Claude messages and ensure no system messages are placed after user/assistant
    i = 0
    non_system_started = False

    while i < len(claude_request.messages):
        msg = claude_request.messages[i]
        role = msg.role.lower() if isinstance(msg.role, str) else str(msg.role)

        if role in (Constants.ROLE_SYSTEM, "system"):
            content_str = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            if not non_system_started:
                if content_str.strip():
                    system_parts.append(content_str.strip())
            else:
                openai_messages.append({"role": Constants.ROLE_USER, "content": f"[System Note]: {content_str}"})
            i += 1
            continue

        non_system_started = True

        if role == Constants.ROLE_USER:
            openai_message = convert_claude_user_message(msg)
            openai_messages.append(openai_message)
        elif role == Constants.ROLE_ASSISTANT:
            openai_message = convert_claude_assistant_message(msg)
            openai_messages.append(openai_message)

            # Check if next message contains tool results
            if i + 1 < len(claude_request.messages):
                next_msg = claude_request.messages[i + 1]
                if (
                    next_msg.role == Constants.ROLE_USER
                    and isinstance(next_msg.content, list)
                    and any(
                        isinstance(block, dict) and block.get("type") == Constants.CONTENT_TOOL_RESULT
                        or hasattr(block, "type") and block.type == Constants.CONTENT_TOOL_RESULT
                        for block in next_msg.content
                    )
                ):
                    # Process tool results
                    i += 1  # Skip to tool result message
                    tool_results = convert_claude_tool_results(next_msg)
                    openai_messages.extend(tool_results)
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

    # Apply secret sanitization if enabled
    openai_messages = sanitizer.sanitize_messages(openai_messages)

    # Build OpenAI request
    openai_request = {
        "model": openai_model,
        "messages": openai_messages,
        "max_tokens": min(
            max(claude_request.max_tokens or 4096, config.min_tokens_limit),
            config.max_tokens_limit,
        ),
        "temperature": claude_request.temperature,
        "stream": claude_request.stream,
    }
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
                openai_tools.append(
                    {
                        "type": Constants.TOOL_FUNCTION,
                        Constants.TOOL_FUNCTION: {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.input_schema,
                        },
                    }
                )
        if openai_tools:
            openai_request["tools"] = openai_tools

    # Convert tool choice
    if claude_request.tool_choice:
        choice_type = claude_request.tool_choice.get("type")
        if choice_type == "auto":
            openai_request["tool_choice"] = "auto"
        elif choice_type == "any":
            openai_request["tool_choice"] = "auto"
        elif choice_type == "tool" and "name" in claude_request.tool_choice:
            openai_request["tool_choice"] = {
                "type": Constants.TOOL_FUNCTION,
                Constants.TOOL_FUNCTION: {"name": claude_request.tool_choice["name"]},
            }
        else:
            openai_request["tool_choice"] = "auto"

    return openai_request


def convert_claude_user_message(msg: ClaudeMessage) -> Dict[str, Any]:
    """Convert Claude user message to OpenAI format."""
    if msg.content is None:
        return {"role": Constants.ROLE_USER, "content": ""}
    
    if isinstance(msg.content, str):
        return {"role": Constants.ROLE_USER, "content": msg.content}

    if not isinstance(msg.content, list):
        return {"role": Constants.ROLE_USER, "content": str(msg.content)}

    # Handle multimodal content
    openai_content = []
    for block in msg.content:
        block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        block_text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
        block_source = getattr(block, "source", None) or (block.get("source") if isinstance(block, dict) else None)

        if block_type == Constants.CONTENT_TEXT:
            openai_content.append({"type": "text", "text": block_text or ""})
        elif block_type == Constants.CONTENT_IMAGE:
            # Convert Claude image format to OpenAI format
            if (
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

    openai_message = {"role": Constants.ROLE_ASSISTANT}

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
