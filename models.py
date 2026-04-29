from sqlalchemy import Column, DateTime, Integer, String, ForeignKey, Boolean, Date, Time, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=False)
    surname = Column(String, nullable=False)
    role = Column(String, default="student", nullable=False)

    verbal_classes = relationship(
        "Class",
        foreign_keys="Class.verbal_teacher_id",
        back_populates="verbal_teacher"
    )
    math_classes = relationship(
        "Class",
        foreign_keys="Class.math_teacher_id",
        back_populates="math_teacher"
    )
    sessions_taught = relationship(
        "Session",
        foreign_keys="Session.teacher_id",
        back_populates="teacher"
    )

    enrollments = relationship("ClassEnrollment", back_populates="student")
    attendances = relationship("Attendance", back_populates="student")
    assignments = relationship("Assignment", back_populates="student")
    mock_results = relationship("MockResult", back_populates="student")


class Class(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    verbal_teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    math_teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    verbal_teacher = relationship(
        "User",
        foreign_keys=[verbal_teacher_id],
        back_populates="verbal_classes"
    )
    math_teacher = relationship(
        "User",
        foreign_keys=[math_teacher_id],
        back_populates="math_classes"
    )

    enrollments = relationship(
        "ClassEnrollment",
        back_populates="class_obj",
        cascade="all, delete-orphan"
    )
    sessions = relationship(
        "Session",
        back_populates="class_obj",
        cascade="all, delete-orphan"
    )


class ClassEnrollment(Base):
    __tablename__ = "class_enrollment"

    class_id = Column(Integer, ForeignKey("classes.id"), primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), primary_key=True)

    class_obj = relationship("Class", back_populates="enrollments")
    student = relationship("User", back_populates="enrollments")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)

    session_type = Column(String, nullable=False)
    topic = Column(String, nullable=True)
    academic_plan_item_id = Column(ARRAY(Integer), nullable=True)
    lesson_notes = Column(Text, nullable=True)

    class_obj = relationship("Class", back_populates="sessions")
    teacher = relationship(
        "User",
        foreign_keys=[teacher_id],
        back_populates="sessions_taught"
    )

    attendances = relationship(
        "Attendance",
        back_populates="session",
        cascade="all, delete-orphan"
    )
    assignments = relationship(
        "Assignment",
        back_populates="session",
        cascade="all, delete-orphan"
    )


class AcademicPlanItem(Base):
    __tablename__ = "academic_plan_items"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, nullable=True)
    general_topic = Column(String, nullable=True)
    plan_text = Column(Text, nullable=True)


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Boolean, default=False, nullable=False)

    session = relationship("Session", back_populates="attendances")
    student = relationship("User", back_populates="attendances")

    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_attendance_session_student"),
    )


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    slot_index = Column(Integer, nullable=True)

    title = Column(String, nullable=True)
    instruction = Column(String, nullable=True)
    task_link = Column(String, nullable=True)
    due_date = Column(Date, nullable=True)
    due_time = Column(Time, nullable=True)
    photo_required = Column(Boolean, default=False, nullable=False)

    session = relationship("Session", back_populates="assignments")
    student = relationship("User", back_populates="assignments")

    homework_result = relationship(
        "HomeworkResult",
        back_populates="assignment",
        cascade="all, delete-orphan",
        uselist=False
    )
    mock_result = relationship(
        "MockResult",
        back_populates="assignment",
        cascade="all, delete-orphan",
        uselist=False
    )

    __table_args__ = (
        UniqueConstraint("session_id", "student_id", "slot_index", name="uq_assignment_session_student_slot"),
    )


class HomeworkResult(Base):
    __tablename__ = "homework_results"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False, unique=True)

    submitted = Column(Boolean, default=False, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    photo_link = Column(String, nullable=True)

    correct_total = Column(Integer, nullable=True)
    incorrect_total = Column(Integer, nullable=True)
    analysis = Column(Text, nullable=True)

    assignment = relationship("Assignment", back_populates="homework_result")


class MockResult(Base):
    __tablename__ = "mock_results"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    submitted = Column(Boolean, default=False, nullable=False)

    total_points = Column(Integer, nullable=True)
    verbal_points = Column(Integer, nullable=True)
    math_points = Column(Integer, nullable=True)

    verbal_incorrect = Column(Integer, nullable=True)
    math_incorrect = Column(Integer, nullable=True)

    weak_areas = Column(Text, nullable=True)
    photo_link = Column(String, nullable=True)

    assignment = relationship("Assignment", back_populates="mock_result")
    student = relationship("User", back_populates="mock_results")

    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", name="uq_mock_assignment_student"),
    )
