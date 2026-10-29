"""Firebase ID-token check for endpoints that spend money (the ask box). Needs only FIREBASE_PROJECT_ID: tokens are
verified against Google's public keys, no service-account secret. Unset -> 503, so the endpoint is never open."""

import os
from functools import lru_cache
from typing import Annotated

from fastapi import Header, HTTPException


@lru_cache
def firebase_app():
    project = os.environ.get("FIREBASE_PROJECT_ID")
    if not project:
        return None
    import firebase_admin

    return firebase_admin.initialize_app(options={"projectId": project}, name="edgeos")


def verify(token: str, app) -> str:
    from firebase_admin import auth

    return auth.verify_id_token(token, app=app)["uid"]


def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    app = firebase_app()
    if app is None:
        raise HTTPException(503, "sign-in is not configured (FIREBASE_PROJECT_ID)")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "sign in to use the ask box")
    try:
        return verify(authorization.removeprefix("Bearer "), app)
    except Exception as e:  # expired, revoked, wrong project, malformed: all the same to the caller
        raise HTTPException(401, "sign-in token is invalid or expired") from e
