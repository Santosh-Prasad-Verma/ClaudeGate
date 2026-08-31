"""Security and Secret Sanitization for outgoing LLM prompts."""

import re
import os
import copy
from typing import Any, Dict, List

# Patterns matching sensitive keys, tokens, and credentials
SECRET_PATTERNS = [
    (re.compile(r'(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}'), '[REDACTED_AWS_KEY]'),
    (re.compile(r'github_pat_[a-zA-Z0-9_]{22,255}'), '[REDACTED_GITHUB_PAT]'),
    (re.compile(r'gh[pousr]_[a-zA-Z0-9]{36,255}'), '[REDACTED_GITHUB_TOKEN]'),
    (re.compile(r'sk-(?:ant-|or-v1-|proj-)?[a-zA-Z0-9_-]{20,128}'), '[REDACTED_API_KEY]'),
    (re.compile(r'xox[baprs]-[0-9a-zA-Z]{10,48}'), '[REDACTED_SLACK_TOKEN]'),
    (re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]+'), '[REDACTED_JWT]'),
    (re.compile(r'-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----'), '[REDACTED_PRIVATE_KEY]'),
    (re.compile(r'((?:postgres|postgresql|mysql|mongodb|redis):\/\/[^:\s]+:)([^@\s]+)(@)'), r'\1[REDACTED_PASSWORD]\3'),
]

class SecretSanitizer:
    """Sanitizes outgoing messages to prevent accidental credential exfiltration."""
    
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def sanitize_text(self, text: str) -> str:
        """Sanitize a text string against sensitive credential patterns."""
        if not self.enabled or not text or not isinstance(text, str):
            return text

        sanitized = text
        for pattern, replacement in SECRET_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    def sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Recursively sanitize prompt messages, tool calls, and text blocks."""
        if not self.enabled or not messages:
            return messages

        messages = copy.deepcopy(messages)
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = self.sanitize_text(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text" and "text" in block:
                            block["text"] = self.sanitize_text(str(block.get("text", "")))
                        elif "text" in block and isinstance(block["text"], str):
                            block["text"] = self.sanitize_text(block["text"])
                        elif "source" in block and isinstance(block["source"], dict):
                            block["source"] = self._sanitize_value(block["source"])

            # Sanitize tool calls arguments
            if "tool_calls" in msg and isinstance(msg["tool_calls"], list):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and "function" in tc:
                        fn = tc["function"]
                        if isinstance(fn, dict) and "arguments" in fn and isinstance(fn["arguments"], str):
                            fn["arguments"] = self.sanitize_text(fn["arguments"])

        return messages

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.sanitize_text(value)
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._sanitize_value(item) for key, item in value.items()}
        return value

# Global sanitizer instance controlled by SANITIZE_SECRETS in .env
sanitizer = SecretSanitizer(enabled=os.environ.get("SANITIZE_SECRETS", "false").lower() in ("true", "1", "yes"))
