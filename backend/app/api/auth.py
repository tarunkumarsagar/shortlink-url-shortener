"""
Auth API routes.

Status code notes:
  - POST /api/v1/auth/register -> 201 Created (a new user resource).
  - POST /api/v1/auth/login    -> 200 OK with a token pair. NOT a
    resource creation, so not 201, even though it "creates" a session
    conceptually -- REST status codes describe the RESOURCE outcome,
    and no persistent resource is created here (our tokens are
    stateless JWTs, not server-side session records).
  - POST /api/v1/auth/refresh  -> 200 OK with a new access token.
  - Duplicate email            -> 409 Conflict.
  - Bad credentials on login   -> 401 Unauthorized (not 404 -- see
    InvalidCredentialsError's docstring on why we don't leak which
    part was wrong).
  - Invalid/expired refresh token -> 401 Unauthorized.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_auth_service
from app.models.auth_schemas import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.repositories.exceptions import EmailAlreadyRegisteredError
from app.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    InvalidEmailError,
    WeakPasswordError,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=201)
def register(payload: RegisterRequest, service: AuthService = Depends(get_auth_service)):
    try:
        user = service.register(payload.email, payload.password)
    except InvalidEmailError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except WeakPasswordError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except EmailAlreadyRegisteredError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return UserResponse(id=user.id, email=user.email, created_at=user.created_at)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, service: AuthService = Depends(get_auth_service)):
    try:
        access_token, refresh_token = service.login(payload.email, payload.password)
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(payload: RefreshRequest, service: AuthService = Depends(get_auth_service)):
    try:
        access_token = service.refresh(payload.refresh_token)
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))

    return AccessTokenResponse(access_token=access_token)
