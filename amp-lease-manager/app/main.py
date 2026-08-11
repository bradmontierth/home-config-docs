from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router
from .config import Config
from .ha_client import HomeAssistantClient
from .lease_manager import LeaseManager
from .ma_client import MusicAssistantClient
from .persistence import StateStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.from_env()
    instance = LeaseManager(
        config,
        HomeAssistantClient(config.ha_base_url, config.ha_token, config.request_timeout_seconds),
        MusicAssistantClient(config.ma_base_url, config.request_timeout_seconds),
        StateStore(config.data_path),
    )
    app.state.config = config
    app.state.manager = instance
    await instance.start()
    try:
        yield
    finally:
        await instance.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Whole-Home Amp Lease Manager", version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
