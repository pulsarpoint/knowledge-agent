"""Service inventory inputs. These fields describe systems; they never execute commands."""

import ipaddress
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DeploymentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    id: UUID | None = None
    server_id: UUID
    environment_id: UUID | None = None
    environment: str = Field(default='', max_length=100)
    runtime: str = Field(default='', max_length=80)
    runtime_name: str = Field(default='', max_length=200)
    port: int | None = Field(default=None, ge=1, le=65535, strict=True)
    log_location: str = Field(default='', max_length=1000)
    deployed_version: str = Field(default='', max_length=200)

    @model_validator(mode='after')
    def environment_required(self):
        if self.environment_id is None and not self.environment:
            raise ValueError('Choose an environment for each deployment')
        return self


class ServiceDependencyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    service_id: UUID
    description: str = Field(default='', max_length=4000)

    @field_validator('description')
    @classmethod
    def valid_description(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('Relationship descriptions cannot contain null characters')
        return value


class ServiceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    service_type: Literal['unclassified', 'custom', 'builtin', 'external'] = 'unclassified'
    service_role: Literal[
        'unclassified', 'application', 'proxy', 'web_server', 'database', 'cache', 'message_broker', 'worker', 'other'
    ] = 'unclassified'
    description: str = Field(default='', max_length=4000)
    owner: str = Field(default='', max_length=200)
    repository_ids: list[UUID] = Field(default_factory=list, max_length=100)
    environment_ids: list[UUID] = Field(default_factory=list, max_length=100)
    deployments: list[DeploymentInput] = Field(default_factory=list, max_length=100)
    dependencies: list[ServiceDependencyInput] = Field(default_factory=list, max_length=100)
    dependents: list[ServiceDependencyInput] = Field(default_factory=list, max_length=100)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode='after')
    def unique_links(self) -> 'ServiceInput':
        if len(set(self.repository_ids)) != len(self.repository_ids):
            raise ValueError('Select each repository only once')
        if len(set(self.environment_ids)) != len(self.environment_ids):
            raise ValueError('Select each environment only once')
        for links in (self.dependencies, self.dependents):
            if len({link.service_id for link in links}) != len(links):
                raise ValueError('Select each service only once in each dependency direction')
        identities = [(d.server_id, d.environment_id or d.environment, d.runtime_name) for d in self.deployments]
        ids = [d.id for d in self.deployments if d.id is not None]
        if len(set(identities)) != len(identities) or len(set(ids)) != len(ids):
            raise ValueError('Each deployment must be unique')
        return self


class ServerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    hostname: str = Field(min_length=1, max_length=253)
    description: str = Field(default='', max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    ssh_allowed: bool = Field(default=False, strict=True)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('tags')
    @classmethod
    def normalized_tags(cls, values: list[str]) -> list[str]:
        tags = []
        for value in values:
            tag = value.strip().lower()
            if not tag or len(tag) > 64 or ',' in tag or any(ord(char) < 32 or ord(char) == 127 for char in tag):
                raise ValueError('Tags must contain 1–64 characters without commas or control characters')
            if tag not in tags:
                tags.append(tag)
        return tags

    @field_validator('hostname')
    @classmethod
    def hostname_only(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            labels = value.removesuffix('.').split('.')
            if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
                raise ValueError('Enter an IP address or hostname without a URL, port, or credentials') from None
            return value.removesuffix('.').lower()
