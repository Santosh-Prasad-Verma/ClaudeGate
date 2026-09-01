from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional, Union, Literal

class ClaudeContentBlockText(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Optional[str] = "text"
    text: Optional[str] = ""

class ClaudeContentBlockImage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Optional[str] = "image"
    source: Dict[str, Any]

class ClaudeContentBlockToolUse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Optional[str] = "tool_use"
    id: str
    name: str
    input: Dict[str, Any]

class ClaudeContentBlockToolResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Optional[str] = "tool_result"
    tool_use_id: str
    content: Optional[Union[str, List[Any], Dict[str, Any], Any]] = ""

class ClaudeSystemContent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Optional[str] = "text"
    text: Optional[str] = None
    cache_control: Optional[Dict[str, Any]] = None

class ClaudeMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: Optional[Union[str, List[Any], Dict[str, Any], Any]] = ""

class ClaudeTool(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    cache_control: Optional[Dict[str, Any]] = None

class ClaudeThinkingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: Optional[bool] = True
    budget_tokens: Optional[int] = None
    type: Optional[str] = None

class ClaudeMessagesRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = Field(min_length=1, max_length=256)
    max_tokens: Optional[int] = Field(default=4096, ge=1, le=200000)
    messages: List[ClaudeMessage] = Field(min_length=1, max_length=1000)
    system: Optional[Union[str, List[Any], Dict[str, Any]]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = Field(default=1.0, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, gt=0.0, le=1.0)
    top_k: Optional[int] = Field(default=None, ge=1, le=1000)
    metadata: Optional[Dict[str, Any]] = None
    tools: Optional[List[ClaudeTool]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    thinking: Optional[Union[ClaudeThinkingConfig, Dict[str, Any]]] = None
    context_management: Optional[Dict[str, Any]] = None
    output_config: Optional[Dict[str, Any]] = None

class ClaudeTokenCountRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = Field(min_length=1, max_length=256)
    messages: List[ClaudeMessage] = Field(min_length=1, max_length=1000)
    system: Optional[Union[str, List[Any], Dict[str, Any]]] = None
    tools: Optional[List[ClaudeTool]] = None
    thinking: Optional[Union[ClaudeThinkingConfig, Dict[str, Any]]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    context_management: Optional[Dict[str, Any]] = None
    output_config: Optional[Dict[str, Any]] = None
