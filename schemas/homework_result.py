from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ReturnHomeworkRequest(BaseModel):
    reason: Optional[str] = None


class HomeworkFileSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class HomeworkResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    assignment_id: int
    submitted: bool
    submitted_at: Optional[datetime] = None
    photo_link: Optional[str] = None
    correct_total: Optional[int] = None
    incorrect_total: Optional[int] = None
    analysis: Optional[str] = None
    returned_at: Optional[datetime] = None
    returned_by_id: Optional[int] = None
    return_reason: Optional[str] = None
    attachments: list[HomeworkFileSchema] = []
    legacy_photo: bool = False
