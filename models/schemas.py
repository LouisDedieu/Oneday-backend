from typing import Optional
from pydantic import BaseModel


class AnalyzeUrlRequest(BaseModel):
    url: str
    user_id: Optional[str] = None
    entity_type_override: Optional[str] = None  # 'trip' | 'city' | None for auto
    cookies_file: Optional[str] = None
    proxy: Optional[str] = None


class JobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    entity_type: Optional[str] = None
    trip_id: Optional[str] = None
    city_id: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
