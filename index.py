from fastapi import FastAPI, Depends, HTTPException
import os
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from .routers import db_router
load_dotenv()

app = FastAPI(
    docs_url="/api/py/docs",
    openapi_url="/api/py/openapi.json",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Modify this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(db_router, prefix="/api/v1/db")


@app.get("/api/py/helloFastApi")
def hello_fast_api():
    return {"message": "Hello from FastAPI"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
