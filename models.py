from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, ForeignKey, Boolean, Date, Time, Text, UniqueConstraint, Index, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
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

    enrollments = relationship(
        "ClassEnrollment",
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    attendances = relationship("Attendance", back_populates="student")
    assignments = relationship("Assignment", back_populates="student")
    mock_results = relationship("MockResult", back_populates="student")
    diagnostic_attempts = relationship(
        "DiagnosticAttempt",
        back_populates="student",
        foreign_keys="DiagnosticAttempt.student_id",
    )


class Class(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    archived = Column(Boolean, default=False, nullable=False, server_default="false")

    verbal_teacher_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    math_teacher_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

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
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    sessions = relationship(
        "Session",
        back_populates="class_obj",
        cascade="all, delete-orphan"
    )


class ClassEnrollment(Base):
    __tablename__ = "class_enrollment"

    class_id = Column(
        Integer,
        ForeignKey("classes.id", ondelete="CASCADE"),
        primary_key=True
    )

    student_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True
    )

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
    subject = Column(String, nullable=True)
    topic = Column(String, nullable=True)
    academic_plan_item_id = Column(ARRAY(Integer), nullable=True)
    lesson_notes = Column(Text, nullable=True)
    mock_document = Column(JSONB, nullable=True)

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
    status = Column(String(16), default="absent", nullable=False)

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
    homework_document = Column(JSONB, nullable=True)

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

    returned_at = Column(DateTime, nullable=True)
    returned_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    return_reason = Column(Text, nullable=True)
    attachments = Column(JSONB, nullable=False, server_default="[]")
    original_attachments = Column(JSONB, nullable=False, server_default="[]")
    submission_history = Column(JSONB, nullable=False, server_default="[]")

    assignment = relationship("Assignment", back_populates="homework_result")


class MockResult(Base):
    __tablename__ = "mock_results"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    submitted = Column(Boolean, default=False, nullable=False)
    submitted_at = Column(DateTime, nullable=True)

    total_points = Column(Integer, nullable=True)
    verbal_points = Column(Integer, nullable=True)
    math_points = Column(Integer, nullable=True)

    verbal_incorrect = Column(Integer, nullable=True)
    math_incorrect = Column(Integer, nullable=True)

    weak_areas = Column(Text, nullable=True)
    photo_link = Column(String, nullable=True)
    attachments = Column(JSONB, nullable=False, server_default="[]")

    assignment = relationship("Assignment", back_populates="mock_result")
    student = relationship("User", back_populates="mock_results")

    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", name="uq_mock_assignment_student"),
    )


class DiagnosticQuestion(Base):
    __tablename__ = "diagnostic_questions"

    id = Column(BigInteger, primary_key=True, index=True)
    section = Column(String, nullable=False)
    domain = Column(String, nullable=False)
    difficulty = Column(String, nullable=False)
    points = Column(Integer, nullable=False)
    order_index = Column(Integer, nullable=False)
    passage_text = Column(Text, nullable=True)
    question_text = Column(Text, nullable=False)
    question_url = Column(Text, nullable=True)
    question_image = Column(Text, nullable=True)
    question_image_public_id = Column(Text, nullable=True)
    image_scale = Column(Float, nullable=False, default=0.85, server_default="0.85")
    choices = Column(JSONB, nullable=False)
    correct_choice = Column(String, nullable=False)
    explanation = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    answers = relationship("DiagnosticAnswer", back_populates="question")

    __table_args__ = (
        Index(
            "uq_diagnostic_questions_order_index",
            "order_index",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_diagnostic_questions_order", "order_index"),
        Index(
            "uq_diagnostic_questions_question_url",
            "question_url",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND question_url IS NOT NULL"),
        ),
    )


class DiagnosticAttempt(Base):
    __tablename__ = "diagnostic_attempts"

    id = Column(BigInteger, primary_key=True, index=True)
    student_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    rw_points = Column(Integer, nullable=True)
    math_points = Column(Integer, nullable=True)
    rw_scaled_estimate = Column(Integer, nullable=True)
    math_scaled_estimate = Column(Integer, nullable=True)
    total_point_estimate = Column(Integer, nullable=True)
    total_range_low = Column(Integer, nullable=True)
    total_range_high = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="in_progress", server_default="in_progress")
    math_started_at = Column(DateTime(timezone=True), nullable=True)
    current_question_id = Column(
        BigInteger,
        ForeignKey("diagnostic_questions.id"),
        nullable=True,
    )
    timer_paused_at = Column(DateTime(timezone=True), nullable=True)
    timer_pause_seconds = Column(Integer, nullable=False, default=0, server_default="0")
    question_ids = Column(JSONB, nullable=True)

    student = relationship(
        "User",
        back_populates="diagnostic_attempts",
        foreign_keys=[student_id],
    )
    answers = relationship(
        "DiagnosticAnswer",
        back_populates="attempt",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_diagnostic_attempts_student_id", "student_id"),
    )


class DiagnosticAnswer(Base):
    __tablename__ = "diagnostic_answers"

    id = Column(BigInteger, primary_key=True, index=True)
    attempt_id = Column(
        BigInteger,
        ForeignKey("diagnostic_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id = Column(BigInteger, ForeignKey("diagnostic_questions.id"), nullable=False)
    selected_choice = Column(String, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)

    attempt = relationship("DiagnosticAttempt", back_populates="answers")
    question = relationship("DiagnosticQuestion", back_populates="answers")

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_diagnostic_answers_attempt_question",
        ),
        Index("idx_diagnostic_answers_attempt_id", "attempt_id"),
    )
