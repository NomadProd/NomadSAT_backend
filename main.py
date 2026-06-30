from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
    allow_origin_regex=(
        r"http://(localhost|127\.0\.0\.1|0\.0\.0\.0|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):\d+"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(classes_router.router)
app.include_router(sessions_router.router)
app.include_router(attendance_router.router)
app.include_router(assignments_router.router)
app.include_router(results_router.router)
app.include_router(lesson_notes_router.router)
