from pydantic import BaseModel, EmailStr
from typing import Optional, List
import datetime as dt

class LoginRequest(BaseModel):
    email: str
    password: str

class NewUserData(BaseModel):
    email: EmailStr
    password: str
    name: str
    surname: str
    role: str

class UpdateUserData(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    name: Optional[str] = None
    surname: Optional[str] = None
    role: Optional[str] = None

class CreateClassData(BaseModel):
    name: str
    verbal_teacher_id: int
    math_teacher_id: int
    schedule_template: Optional[str] = None
    start_date: Optional[dt.date] = None

class UpdateClassData(BaseModel):
    name: Optional[str] = None
    verbal_teacher_id: Optional[int] = None
    math_teacher_id: Optional[int] = None
    archived: Optional[bool] = None

class EnrollmentData(BaseModel):
    student_id: int

class CreateSessionData(BaseModel):
    date: dt.date
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    session_type: str
    teacher_id: Optional[int] = None
    topic: Optional[str] = None
    academic_plan_item_id: Optional[int] = None
    academic_plan_item_ids: Optional[List[int]] = None
    lesson_notes: Optional[str] = None

class UpdateSessionData(BaseModel):
    teacher_id: Optional[int] = None
    date: Optional[dt.date] = None
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    session_type: Optional[str] = None
    topic: Optional[str] = None
    academic_plan_item_id: Optional[int] = None
    academic_plan_item_ids: Optional[List[int]] = None
    lesson_notes: Optional[str] = None

class CreateSessionLessonNotesData(BaseModel):
    lesson_notes: str
    academic_plan_item_id: Optional[int] = None
    academic_plan_item_ids: Optional[List[int]] = None

class UpdateSessionLessonNotesData(BaseModel):
    lesson_notes: Optional[str] = None
    academic_plan_item_id: Optional[int] = None
    academic_plan_item_ids: Optional[List[int]] = None

class UpdateSessionAcademicPlanData(BaseModel):
    subject: Optional[str] = None
    general_topic: Optional[str] = None
    plan_text: Optional[str] = None
    lesson_notes: Optional[str] = None
    date: Optional[dt.date] = None

class AttendanceRecordData(BaseModel):
    student_id: int
    status: bool

class AttendanceBulkData(BaseModel):
    records: List[AttendanceRecordData]

class CreateAssignmentData(BaseModel):
    student_id: int
    slot_index: Optional[int] = None
    title: Optional[str] = None
    instruction: Optional[str] = None
    task_link: Optional[str] = None
    due_date: Optional[dt.date] = None
    due_time: Optional[dt.time] = None
    photo_required: bool = False

class UpdateAssignmentData(BaseModel):
    student_id: Optional[int] = None
    slot_index: Optional[int] = None
    title: Optional[str] = None
    instruction: Optional[str] = None
    task_link: Optional[str] = None
    due_date: Optional[dt.date] = None
    due_time: Optional[dt.time] = None
    photo_required: Optional[bool] = None

class CopyAssignmentData(BaseModel):
    target_student_ids: Optional[List[int]] = None
    all_students: bool = False
    session_id: Optional[int] = None
    target_slot_index: Optional[int] = None

class CreateHomeworkResultData(BaseModel):
    submitted: bool = False
    photo_link: Optional[str] = None
    correct_total: Optional[int] = None
    incorrect_total: Optional[int] = None
    analysis: Optional[str] = None

class UpdateHomeworkResultData(BaseModel):
    submitted: Optional[bool] = None
    photo_link: Optional[str] = None
    correct_total: Optional[int] = None
    incorrect_total: Optional[int] = None
    analysis: Optional[str] = None

class CreateMockResultData(BaseModel):
    student_id: int
    submitted: bool = False
    verbal_points: Optional[int] = None
    math_points: Optional[int] = None
    verbal_incorrect: Optional[int] = None
    math_incorrect: Optional[int] = None
    weak_areas: Optional[str] = None
    photo_link: Optional[str] = None

class UpdateMockResultData(BaseModel):
    submitted: Optional[bool] = None
    verbal_points: Optional[int] = None
    math_points: Optional[int] = None
    verbal_incorrect: Optional[int] = None
    math_incorrect: Optional[int] = None
    weak_areas: Optional[str] = None
    photo_link: Optional[str] = None
