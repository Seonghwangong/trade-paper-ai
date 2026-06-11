from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Trade Paper Backend is running"}

@app.get("/status")
def status():
    return {
        "service": "trade-paper-backend",
        "version": "0.1.0",
        "status": "ok",
    }
from routers.web import router as web_router
app.include_router(web_router)
