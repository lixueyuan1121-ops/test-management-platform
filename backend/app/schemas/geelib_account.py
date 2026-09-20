from pydantic import BaseModel, Field, SecretStr


class GeelibAuthorizeIn(BaseModel):
    state: str = Field(min_length=1, max_length=128)
    code: SecretStr = Field(min_length=1, max_length=4096)
