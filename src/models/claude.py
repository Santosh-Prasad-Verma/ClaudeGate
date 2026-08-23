from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union, Literal

class ClaudeContentBlockText(BaseModel):
    type: Optional[str] = "text"
    text: Optional[str] = ""

class ClaudeContentBlockImage(BaseModel):
    type: Optional[str] = "image"
    source: Dict[str, Any]

class ClaudeContentBlockToolUse(BaseModel):
    type: Optional[str] = "tool_use"
    id: str
    name: str
    input: Dict[str, Any]

class ClaudeContentBlockToolResult(BaseModel):
    type: Optional[str] = "tool_result"
    tool_use_id: str
    content: Optional[Union[str, List[Any], Dict[str, Any], Any]] = ""

class ClaudeSystemContent(BaseModel):
    type: Optional[str] = "text"
    text: Optional[str] = None
    cache_control: Optional[Dict[str, Any]] = None

class ClaudeMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Any], Dict[str, Any], Any]] = ""

class ClaudeTool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    cache_control: Optional[Dict[str, Any]] = None

class ClaudeThinkingConfig(BaseModel):
    enabled: Optional[bool] = True
    budget_tokens: Optional[int] = None
    type: Optional[str] = None

class ClaudeMessagesRequest(BaseModel):
    model: str
    max_tokens: Optional[int] = 4096
    messages: List[ClaudeMessage]
    system: Optional[Union[str, List[Any], Dict[str, Any]]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    tools: Optional[List[ClaudeTool]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    thinking: Optional[ClaudeThinkingConfig] = None

class ClaudeTokenCountRequest(BaseModel):
    model: str
    messages: List[ClaudeMessage]
    system: Optional[Union[str, List[Any], Dict[str, Any]]] = None
    tools: Optional[List[ClaudeTool]] = None
    thinking: Optional[ClaudeThinkingConfig] = None
    tool_choice: Optional[Dict[str, Any]] = None
