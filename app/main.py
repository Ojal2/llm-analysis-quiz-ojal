# app/main.py

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import os

from .solver import solve_quiz_chain

# Load environment variables from .env in project root
load_dotenv()

app = FastAPI()

# Read config from environment
EXPECTED_SECRET = os.getenv("LLM_QUIZ_SECRET")
STUDENT_EMAIL = os.getenv("STUDENT_EMAIL")


class QuizRequest(BaseModel):
    email: str
    secret: str
    url: str


@app.post("/quiz")
async def handle_quiz(request: Request):
    # 1) Parse JSON body
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 2) Validate required fields using Pydantic
    try:
        qr = QuizRequest(**data)
    except Exception:
        raise HTTPException(status_code=400, detail="Missing/invalid fields")

    # 3) Check secret
    if qr.secret != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # 4) Call the quiz solver (async)
    await solve_quiz_chain(qr.url, qr.email, qr.secret)

    # 5) Return a simple JSON confirming we started/ran it
    return {
        "status": "ok",
        "message": "Quiz processing attempted (see server logs for details)",
    }
