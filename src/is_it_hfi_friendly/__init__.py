def main() -> None:
    import uvicorn

    uvicorn.run("is_it_hfi_friendly.app:app", host="0.0.0.0", port=8000)
