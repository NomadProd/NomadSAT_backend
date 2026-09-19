import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from config import env, env_list

logger = logging.getLogger(__name__)

# Render's latency metric is empty for this service and uvicorn's access log
# carries no duration, so slow endpoints are invisible in production. Log any
# request past the threshold; SLOW_REQUEST_SECONDS tunes it without a deploy.
SLOW_REQUEST_SECONDS = float(env("SLOW_REQUEST_SECONDS", "0.5"))

from routers import (
    auth_router,
    users_router,
    classes_router,
    sessions_router,
    attendance_router,
    assignments_router,
    results_router,
    lesson_notes_router,
)
from routes import assignments, attendance, classes, diagnostic, homework_files, homework_results, mock_files, mock_results, practice_tests, sessions, users

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


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - started
    if elapsed >= SLOW_REQUEST_SECONDS:
        logger.warning(
            "SLOW %.3fs %s %s -> %s",
            elapsed, request.method, request.url.path, response.status_code,
        )
    response.headers["X-Response-Time"] = f"{elapsed:.3f}"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Without this the 500 is returned with the traceback discarded, which is
    # why production 500s leave no trace in the logs at all.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    origin = request.headers.get("origin", "")
    headers = {}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
    )


app.include_router(auth_router.router)
app.include_router(users.router)
app.include_router(classes.router)
app.include_router(sessions.router)
app.include_router(assignments.router)
app.include_router(attendance.router)
app.include_router(homework_results.router)
app.include_router(homework_files.router)
app.include_router(mock_results.router)
app.include_router(mock_files.router)
app.include_router(diagnostic.router)
app.include_router(practice_tests.router)
app.include_router(users_router.router)
app.include_router(classes_router.router)
app.include_router(sessions_router.router)
app.include_router(attendance_router.router)
app.include_router(assignments_router.router)
app.include_router(results_router.router)
app.include_router(lesson_notes_router.router)
