"""
utils/auth.py — Vérification des JWT Supabase
Dépendance FastAPI : Depends(get_current_user_id) → str (user UUID)
"""
import logging
from fastapi import Header, HTTPException

logger = logging.getLogger("bombo.auth")


def get_current_user_id(authorization: str = Header(None)) -> str:
    """
    Extrait et vérifie le JWT Supabase depuis le header Authorization.
    Retourne le user_id (sub) si valide, sinon lève une 401.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Non authentifié")

    token = authorization[7:]

    try:
        import jwt
        from config import settings

        if not settings.SUPABASE_JWT_SECRET:
            # Mode dégradé : on décode sans vérifier (dev uniquement)
            logger.warning(
                "SUPABASE_JWT_SECRET non configuré — JWT décodé sans vérification de signature !"
            )
            payload = jwt.decode(token, options={"verify_signature": False})
        else:
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )

        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token invalide : sub manquant")

        return user_id

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Erreur de vérification JWT : %s", exc)
        raise HTTPException(status_code=401, detail=f"Token invalide : {exc}")
