from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import env_list

from routers import (
    auth_router,
    users_router,
    classes_router,
    sessions_router,
    attendance_router,
    assignments_router,
    results_router,
    lesson_notes_router
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=env_list(
        "CORS_ORIGINS",
        "https://turansat.com,https://www.turansat.com,http://localhost:55555",
    ),
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(classes_router.router)
app.include_router(sessions_router.router)
app.include_router(attendance_router.router)
app.include_router(assignments_router.router)
app.include_router(results_router.router)
app.include_router(lesson_notes_router.router)
