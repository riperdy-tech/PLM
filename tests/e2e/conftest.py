# tests/e2e/conftest.py
"""Shared fixtures and harnesses for end-to-end acceptance tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from tests.support.analytics_db import analytics_session, create_analytics_engine
from tests.support.ar_db import ar_session, create_ar_engine
from tests.support.distribution_db import create_distribution_engine, distribution_session
from tests.support.experiments_db import create_experiments_engine, experiments_session
from tests.support.market_db import create_market_engine
from tests.support.media_db import create_media_engine, media_session
from tests.support.music_db import create_music_engine, music_session
from tests.support.qa_db import create_qa_engine, qa_session
from tests.support.youtube_db import create_youtube_engine, youtube_session


@pytest.fixture
def ar_db() -> Generator[Session, None, None]:
    engine = create_ar_engine(static_pool=True)
    session = ar_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def analytics_db() -> Generator[Session, None, None]:
    engine = create_analytics_engine(static_pool=True)
    session = analytics_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def distribution_db() -> Generator[Session, None, None]:
    engine = create_distribution_engine(static_pool=True)
    session = distribution_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def youtube_db() -> Generator[Session, None, None]:
    engine = create_youtube_engine(static_pool=True)
    session = youtube_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def qa_db() -> Generator[Session, None, None]:
    engine = create_qa_engine(static_pool=True)
    session = qa_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def media_db() -> Generator[Session, None, None]:
    engine = create_media_engine(static_pool=True)
    session = media_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def music_db() -> Generator[Session, None, None]:
    engine = create_music_engine(static_pool=True)
    session = music_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def experiments_db() -> Generator[Session, None, None]:
    engine = create_experiments_engine(static_pool=True)
    session = experiments_session(engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def market_db() -> Generator[Session, None, None]:
    engine = create_market_engine()
    session = sa.orm.sessionmaker(bind=engine)()
    try:
        yield session
        session.commit()
    finally:
        session.close()
