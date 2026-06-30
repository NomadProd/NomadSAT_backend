from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class MockFileSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class MockResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    assignment_id: int
    student_id: int
    submitted: bool
    total_points: Optional[int] = None
    verbal_points: Optional[int] = None
    math_points: Optional[int] = None
    verbal_incorrect: Optional[int] = None
    math_incorrect: Optional[int] = None
    weak_areas: Optional[str] = None
    photo_link: Optional[str] = None
    attachments: list[MockFileSchema] = []
    legacy_photo: bool = False
