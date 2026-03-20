"""
utils/auth.py — Vérification des JWT Supabase
Utilise RS256 via JWKS endpoint (nouveaux projets Supabase).
Dépendance FastAPI : Depends(get_current_user_id) → str (user UUID)
"""
import logging
import jwt
from config import settings
from fastapi import Header, HTTPException

from models.errors import ErrorCode, get_error_message

logger = logging.getLogger("bombo.auth")

# Cache du client JWKS (évite de re-fetcher les clés à chaque requête)
_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def get_current_user_id(authorization: str = Header(None)) -> str:
    """
    Extrait et vérifie le JWT Supabase depuis le header Authorization.
    Vérification via JWKS endpoint (RS256).
    Retourne le user_id (sub) si valide, sinon lève une 401.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={
            "error_code": ErrorCode.NOT_AUTHENTICATED,
            "message": get_error_message(ErrorCode.NOT_AUTHENTICATED),
        })

    token = authorization[7:]

    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")

        if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512"):
            raise HTTPException(status_code=401, detail={
                "error_code": ErrorCode.NOT_AUTHENTICATED,
                "message": get_error_message(ErrorCode.NOT_AUTHENTICATED),
            })

        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            audience="authenticated",
        )

        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail={
                "error_code": ErrorCode.INVALID_TOKEN,
                "message": get_error_message(ErrorCode.INVALID_TOKEN),
            })

        return user_id

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Erreur de vérification JWT : %s", exc)
        raise HTTPException(status_code=401, detail={
            "error_code": ErrorCode.INVALID_TOKEN,
            "message": get_error_message(ErrorCode.INVALID_TOKEN),
        })
