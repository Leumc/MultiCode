"""Pydantic request contracts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import (
    MAX_CASES, MAX_INPUT_BYTES, MAX_SOURCE_BYTES, MAX_TIME_MS,
    MAX_USER_RUNNING_MEMORY_MIB, MIN_MEMORY_MIB, MIN_TIME_MS,
)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,31}$")
    password: str = Field(min_length=12, max_length=256)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,31}$")
    status: str | None = Field(default=None, pattern=r"^(active|disabled)$")
    password: str | None = Field(default=None, min_length=12, max_length=256)


class HeaderUpdate(BaseModel):
    enabled: bool


class ExtensionCatalogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9][A-Za-z0-9_.-]*$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExtensionRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_id: int = Field(gt=0)


class ExtensionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(pattern=r"^(user|global)$")


class GrantCreate(BaseModel):
    memory_limit_mib: int | None = Field(default=None, ge=MIN_MEMORY_MIB, le=4096)
    time_limit_ms: int | None = Field(default=None, ge=MIN_TIME_MS, le=600_000)
    max_inflight: int | None = Field(default=None, ge=1, le=20)
    remaining_uses: int | None = Field(default=None, ge=1, le=10_000)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=31_536_000)


class JobCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    filename: str
    source: str
    compiler: str
    time_limit_ms: int = Field(ge=MIN_TIME_MS, le=600_000)
    memory_limit_mib: int = Field(ge=MIN_MEMORY_MIB, le=4096)
    inputs: list[str] = Field(min_length=1, max_length=MAX_CASES)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or not value.endswith(".cpp"):
            raise ValueError("filename must be one basename ending in .cpp")
        if value in {".cpp", "..cpp"}:
            raise ValueError("invalid filename")
        return value

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds system ceiling")
        return value

    @field_validator("compiler")
    @classmethod
    def validate_compiler(cls, value: str) -> str:
        if value != "gcc-14-gnu++17":
            raise ValueError("unsupported compiler")
        return value

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, values: list[str]) -> list[str]:
        if any(len(value.encode("utf-8")) > MAX_INPUT_BYTES for value in values):
            raise ValueError("input exceeds system ceiling")
        return values
