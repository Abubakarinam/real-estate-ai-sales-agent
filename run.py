"""Start the app:  python run.py   then open http://localhost:8000"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  Customer chat:   http://localhost:{port}\n  Staff dashboard: http://localhost:{port}/dashboard\n")
    uvicorn.run("app.server:app", host=os.getenv("HOST", "127.0.0.1"), port=port)
