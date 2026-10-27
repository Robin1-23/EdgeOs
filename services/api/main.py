from fastapi import FastAPI

app = FastAPI(title="EdgeOS API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
