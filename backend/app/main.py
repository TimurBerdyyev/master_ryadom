from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, SessionLocal, engine
from app.routers import admin, auth, categories, complaints, masters, notifications, orders, reviews
from app.seed import seed_categories

app = FastAPI(title="Мастер рядом API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(masters.router)
app.include_router(orders.router)
app.include_router(reviews.router)
app.include_router(notifications.router)
app.include_router(complaints.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_categories(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
