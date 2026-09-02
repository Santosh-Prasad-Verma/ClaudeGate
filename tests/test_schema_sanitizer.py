import pytest
from src.conversion.schema_sanitizer import (
    sanitize_tool_parameters,
    sanitize_schema_node,
    inline_and_resolve_refs,
)
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage, ClaudeTool
from src.conversion.request_converter import convert_claude_to_openai
from src.core.model_manager import model_manager


def test_property_names_and_disallowed_keywords_stripped():
    raw_schema = {
        "type": "object",
        "propertyNames": {"pattern": "^[a-zA-Z0-9_]+$"},
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://example.com/schema.json",
        "patternProperties": {"^S_": {"type": "string"}},
        "unevaluatedProperties": False,
        "properties": {
            "name": {
                "type": "string",
                "propertyNames": {"pattern": "^[a-z]+$"},
            }
        },
    }
    sanitized = sanitize_tool_parameters(raw_schema)
    assert "propertyNames" not in sanitized
    assert "$schema" not in sanitized
    assert "$id" not in sanitized
    assert "patternProperties" not in sanitized
    assert "unevaluatedProperties" not in sanitized
    assert "propertyNames" not in sanitized["properties"]["name"]
    assert sanitized["properties"]["name"]["type"] == "string"


def test_const_converted_to_enum():
    raw_schema = {
        "type": "object",
        "properties": {
            "action": {
                "const": "list"
            },
            "priority": {
                "const": 1
            },
        },
    }
    sanitized = sanitize_tool_parameters(raw_schema)
    assert "const" not in sanitized["properties"]["action"]
    assert sanitized["properties"]["action"]["enum"] == ["list"]
    assert sanitized["properties"]["action"]["type"] == "string"

    assert "const" not in sanitized["properties"]["priority"]
    assert sanitized["properties"]["priority"]["enum"] == [1]
    assert sanitized["properties"]["priority"]["type"] == "integer"


def test_exclusive_minimum_and_maximum_converted():
    raw_schema = {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "exclusiveMinimum": 0,
                "exclusiveMaximum": 100,
            }
        },
    }
    sanitized = sanitize_tool_parameters(raw_schema)
    count_prop = sanitized["properties"]["count"]
    assert "exclusiveMinimum" not in count_prop
    assert "exclusiveMaximum" not in count_prop
    assert count_prop["minimum"] == 0
    assert count_prop["maximum"] == 100


def test_nested_array_items_sanitized():
    # Exactly matching the structure from the user's reported error
    raw_schema = {
        "type": "object",
        "properties": {
            "items_list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "inner_field": {
                            "type": "object",
                            "properties": {
                                "deep_number": {
                                    "type": "number",
                                    "exclusiveMinimum": 5.5,
                                    "const": 10.0,
                                }
                            },
                        }
                    },
                },
            }
        },
    }
    sanitized = sanitize_tool_parameters(raw_schema)
    deep_node = sanitized["properties"]["items_list"]["items"]["properties"]["inner_field"]["properties"]["deep_number"]
    assert "exclusiveMinimum" not in deep_node
    assert "const" not in deep_node
    assert deep_node["minimum"] == 5.5
    assert deep_node["enum"] == [10.0]


def test_any_of_const_collapsed():
    raw_schema = {
        "type": "object",
        "properties": {
            "status": {
                "anyOf": [
                    {"const": "active"},
                    {"const": "inactive"},
                    {"const": "pending"},
                ]
            }
        },
    }
    sanitized = sanitize_tool_parameters(raw_schema)
    status_prop = sanitized["properties"]["status"]
    assert "anyOf" not in status_prop
    assert status_prop["enum"] == ["active", "inactive", "pending"]
    assert status_prop["type"] == "string"


def test_nullable_type_array_conversion():
    raw_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": ["string", "null"]
            }
        },
    }
    sanitized = sanitize_tool_parameters(raw_schema)
    desc_prop = sanitized["properties"]["description"]
    assert desc_prop["type"] == "string"
    assert desc_prop["nullable"] is True


def test_ref_defs_inlining():
    raw_schema = {
        "$defs": {
            "ReplacementChunk": {
                "type": "object",
                "properties": {
                    "TargetContent": {"type": "string"},
                    "ReplacementContent": {"type": "string"},
                },
                "required": ["TargetContent", "ReplacementContent"],
            }
        },
        "type": "object",
        "properties": {
            "chunks": {
                "type": "array",
                "items": {"$ref": "#/$defs/ReplacementChunk"},
            }
        },
    }
    sanitized = sanitize_tool_parameters(raw_schema)
    assert "$defs" not in sanitized
    chunks_items = sanitized["properties"]["chunks"]["items"]
    assert "$ref" not in chunks_items
    assert chunks_items["type"] == "object"
    assert "TargetContent" in chunks_items["properties"]
    assert "ReplacementContent" in chunks_items["properties"]
    assert chunks_items["required"] == ["TargetContent", "ReplacementContent"]


def test_claude_request_tools_integration():
    claude_req = ClaudeMessagesRequest(
        model="claude-opus-5",
        messages=[ClaudeMessage(role="user", content="Run tool")],
        tools=[
            ClaudeTool(
                name="edit_file",
                description="Edit a file",
                input_schema={
                    "type": "object",
                    "propertyNames": {"pattern": "^[a-zA-Z_]+$"},
                    "properties": {
                        "action": {"const": "replace"},
                        "line_no": {"type": "integer", "exclusiveMinimum": 1},
                    },
                    "required": ["action", "line_no"],
                },
            )
        ],
    )
    openai_req = convert_claude_to_openai(claude_req, model_manager)
    assert "tools" in openai_req
    tool_func = openai_req["tools"][0]["function"]
    params = tool_func["parameters"]
    assert "propertyNames" not in params
    assert params["properties"]["action"]["enum"] == ["replace"]
    assert "const" not in params["properties"]["action"]
    assert params["properties"]["line_no"]["minimum"] == 1
    assert "exclusiveMinimum" not in params["properties"]["line_no"]
