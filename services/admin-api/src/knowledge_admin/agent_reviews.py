"""Tuning reviews of a conversation: the reviewer's output (proposed prompt and instruction texts) and applying it."""
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServerInstructions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server_id: UUID
    instructions: str = Field(max_length=40000)


class InstructionChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    system_prompt: str | None = Field(max_length=100000)
    instructions: str | None = Field(max_length=40000)
    server_instructions: list[ServerInstructions] = Field(max_length=200)


class TuningSuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    suggestion: str = Field(min_length=1)
    changes: InstructionChanges

    @field_validator('changes', mode='before')
    @classmethod
    def decode_changes(cls, value: Any) -> Any:
        # Bedrock structured output can JSON-encode this nested object.
        # Decode it once; InstructionChanges still validates every field.
        return json.loads(value) if isinstance(value, str) else value
