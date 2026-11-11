"""Tests laufen standardmäßig gegen SQLite. Mit ERP_TEST_DATABASE_URL (z. B.
postgresql+psycopg2://erp:erp@localhost/erp_test) gegen einen echten Server."""
import os

import pytest

from erp import db, models as m


@pytest.fixture()
def db_url(tmp_path):
    url = os.environ.get("ERP_TEST_DATABASE_URL")
    if url:
        eng = db.make_engine(url)
        m.Base.metadata.drop_all(eng)
        eng.dispose()
        return url
    return f"sqlite:///{tmp_path / 'test.sqlite'}"
