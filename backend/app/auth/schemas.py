import uuid

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=1024)


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str


class CsrfResponse(BaseModel):
    csrf_token: str


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class OtherSessions(BaseModel):
    # Browsers signed in to this account besides this one.
    others: int


class SignedOut(BaseModel):
    signed_out: int
