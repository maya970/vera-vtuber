"""启动入口：python run.py"""
from __future__ import annotations

import uvicorn

from server.config import config

if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host=str(config.get("server.host", "127.0.0.1")),
        port=int(config.get("server.port", 8765)),
        reload=False,
        log_level="info",
    )
