import os
import pytest
from fastapi.testclient import TestClient

# Ensure test environment variables before imports
os.environ["OPENAI_API_KEY"] = "sk-test-openai-key-for-unit-tests-12345"
os.environ["ALLOW_ANONYMOUS_ACCESS"] = "true"

from src.main import app
from src.security.sanitizer import SecretSanitizer
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage
from src.conversion.request_converter import convert_claude_to_openai
from src.core.model_manager import model_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"
    assert data.get("service") == "claudegate"


def test_hello_endpoint(client):
    response = client.get("/api/hello")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert "ClaudeGate is online" in data.get("message", "")


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data.get("service") == "ClaudeGate"
    assert data.get("version") == "1.0.0"
    assert "endpoints" in data


def test_secret_sanitizer():
    sanitizer = SecretSanitizer(enabled=True)
    sample_text = "Here is my secret token: ghp_123456789012345678901234567890123456"
    sanitized = sanitizer.sanitize_text(sample_text)
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized
    assert "ghp_123456789012345678901234567890123456" not in sanitized


def test_claude_to_openai_conversion():
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        system="You are a helpful coding assistant.",
        messages=[
            ClaudeMessage(role="user", content="Hello, ClaudeGate!")
        ],
        max_tokens=1000,
        temperature=0.7,
        stream=False,
    )
    openai_req = convert_claude_to_openai(claude_req, model_manager)
    assert "messages" in openai_req
    assert len(openai_req["messages"]) >= 2  # system + user
    assert openai_req["messages"][0]["role"] == "system"
    assert openai_req["messages"][0]["content"] == "You are a helpful coding assistant."
    assert openai_req["messages"][1]["role"] == "user"
    assert openai_req["messages"][1]["content"] == "Hello, ClaudeGate!"
    assert openai_req["max_tokens"] == 1000
