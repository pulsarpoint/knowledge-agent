"""Conversation context and source boundaries for the search agent."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

CollectionName = Annotated[str, Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$', max_length=60)]


class AgentContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # None includes every collection the agent may use; [] explicitly includes none.
    collections: list[CollectionName] | None = Field(default=None, max_length=200)

    def normalized(self):
        return self.model_copy(update={'collections': sorted(set(self.collections)) if self.collections is not None else None})
