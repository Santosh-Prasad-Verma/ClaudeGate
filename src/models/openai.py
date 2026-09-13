from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional, Union


class OpenAIFunctionCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    arguments: str


class OpenAIToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str = "function"
    function: OpenAIFunctionCall


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[OpenAIToolCall]] = None


class OpenAIUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_details: Optional[Dict[str, Any]] = None


class OpenAIChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    index: int = 0
    message: Optional[OpenAIMessage] = None
    delta: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None


class OpenAIChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    object: str = "chat.completion"
    created: Optional[int] = None
    model: str
    choices: List[OpenAIChoice] = Field(default_factory=list)
    usage: Optional[OpenAIUsage] = None
