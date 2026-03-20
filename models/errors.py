from enum import Enum
from typing import Optional
from pydantic import BaseModel


class ErrorCode(str, Enum):
    # Analyse / Job errors
    UNSUPPORTED_URL = "UNSUPPORTED_URL"
    PRIVATE_VIDEO = "PRIVATE_VIDEO"
    IP_BLOCKED = "IP_BLOCKED"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    INFERENCE_ERROR = "INFERENCE_ERROR"
    MODEL_NOT_LOADED = "MODEL_NOT_LOADED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

    # Resource errors
    TRIP_NOT_FOUND = "TRIP_NOT_FOUND"
    CITY_NOT_FOUND = "CITY_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    HIGHLIGHT_NOT_FOUND = "HIGHLIGHT_NOT_FOUND"
    DESTINATION_NOT_FOUND = "DESTINATION_NOT_FOUND"

    # Permission / Auth errors
    ACCESS_DENIED = "ACCESS_DENIED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    INVALID_TOKEN = "INVALID_TOKEN"

    # Validation errors
    INVALID_REQUEST = "INVALID_REQUEST"
    MISSING_FIELD = "MISSING_FIELD"

    # Generic
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    field: Optional[str] = None


class ErrorResponse(BaseModel):
    error_code: ErrorCode
    message: str
    details: Optional[list[ErrorDetail]] = None


ERROR_MESSAGES = {
    ErrorCode.UNSUPPORTED_URL: "Cette URL n'est pas supportée (TikTok, Instagram Reels, YouTube uniquement)",
    ErrorCode.PRIVATE_VIDEO: "La vidéo est privée ou n'est plus disponible",
    ErrorCode.IP_BLOCKED: "Accès temporairement bloqué, réessayez plus tard",
    ErrorCode.DOWNLOAD_ERROR: "Impossible de télécharger la vidéo",
    ErrorCode.INFERENCE_ERROR: "Erreur lors de l'analyse de la vidéo",
    ErrorCode.MODEL_NOT_LOADED: "Le modèle n'est pas encore chargé",
    ErrorCode.SERVICE_UNAVAILABLE: "Le service n'est pas disponible",
    ErrorCode.TRIP_NOT_FOUND: "Voyage introuvable",
    ErrorCode.CITY_NOT_FOUND: "Ville introuvable",
    ErrorCode.JOB_NOT_FOUND: "Job introuvable",
    ErrorCode.HIGHLIGHT_NOT_FOUND: "Highlight introuvable",
    ErrorCode.DESTINATION_NOT_FOUND: "Destination introuvable",
    ErrorCode.ACCESS_DENIED: "Accès refusé",
    ErrorCode.NOT_AUTHENTICATED: "Non authentifié",
    ErrorCode.INVALID_TOKEN: "Token invalide",
    ErrorCode.INVALID_REQUEST: "Requête invalide",
    ErrorCode.MISSING_FIELD: "Champ manquant",
    ErrorCode.UNKNOWN_ERROR: "Une erreur inattendue s'est produite",
}


def get_error_message(code: ErrorCode) -> str:
    return ERROR_MESSAGES.get(code, ERROR_MESSAGES[ErrorCode.UNKNOWN_ERROR])