"""
Phase 5 fix verification — Plan.name SQL Server column type.

Original error on SQL Server table creation:
    Column 'name' in table 'plans' is invalid for use as an index/key
    because it is VARCHAR(MAX) but has UNIQUE constraint.

Root cause: app/models.py's Plan.name was `Column(String, unique=True, ...)`
— a bare, unbounded String, which SQLAlchemy's mssql dialect compiles to
VARCHAR(MAX). SQL Server refuses UNIQUE/index constraints on VARCHAR(MAX).

Fix: bounded to Column(String(120), unique=True, ...) — 120 chosen to match
the existing Pydantic validation (PlanIn/PlanUpdateIn already cap `name` at
max_length=120 in app/schemas.py), so nothing the API already accepts could
ever be truncated or rejected.

This file has two independent tests:

1. test_plans_table_ddl_is_sql_server_safe — compiles the actual `plans`
   CREATE TABLE statement against SQLAlchemy's mssql dialect (no live SQL
   Server connection needed) and asserts the `name` column is a bounded
   VARCHAR(120), not VARCHAR(max). This directly proves the reported error
   is gone, without needing a real SQL Server instance.

2. test_plan_crud_and_uniqueness — functional test against in-memory
   SQLite, exercising the actual /admin/plans router (create, read, update,
   duplicate-name rejection) to confirm Plan CRUD and unique-name
   enforcement still work correctly after the column-type change.

Run with:
    pip install -r requirements.txt
    pytest tests/test_phase5_plan_column.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from app import models
from app.database import Base, get_db
from app.routers import admin as admin_router
from app.routers import admin_plans as admin_plans_router
from app.security import create_super_admin_token, hash_password


def test_plans_table_ddl_is_sql_server_safe():
    """
    Compiles CREATE TABLE plans (...) against the mssql dialect and checks
    the `name` column is a bounded VARCHAR — proves the exact reported
    SQL Server error ("VARCHAR(MAX) but has UNIQUE constraint") can no
    longer occur, without requiring a live SQL Server connection.
    """
    ddl = str(CreateTable(models.Plan.__table__).compile(dialect=mssql.dialect()))
    assert "plans" in ddl.lower()

    # Explicit, unambiguous check on the `name` column's compiled type.
    name_col = models.Plan.__table__.c.name
    compiled_type = name_col.type.compile(dialect=mssql.dialect())
    assert compiled_type.upper() == "VARCHAR(120)", (
        f"Plan.name compiled to {compiled_type!r} on SQL Server — expected a "
        f"bounded VARCHAR so it can carry a UNIQUE constraint."
    )
    assert name_col.unique is True


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        # See note in test_phase8_tenant_isolation.py: ":memory:" gives each
        # pooled connection its own empty database, which breaks any test
        # that crosses a thread boundary via TestClient.
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(admin_router.router)
    app.include_router(admin_plans_router.router)
    app.dependency_overrides[get_db] = override_get_db

    # Seed a SuperAdmin row directly (no self-registration endpoint exists
    # for super admins) and mint a token the same way create_super_admin.py /
    # admin.login would.
    db = TestingSessionLocal()
    admin = models.SuperAdmin(
        email="root@platform.example",
        hashed_password=hash_password("Sup3rSecret!"),
        full_name="Root Admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    token = create_super_admin_token(admin.id)
    db.close()

    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def test_plan_crud_and_uniqueness(client):
    # Create
    r = client.post(
        "/admin/plans",
        json={"name": "Growth Plan", "monthly_price_cents": 9900, "voice_minutes_limit": 3000},
    )
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    assert r.json()["name"] == "Growth Plan"

    # Read
    r = client.get(f"/admin/plans/{plan_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "Growth Plan"

    # List
    r = client.get("/admin/plans")
    assert r.status_code == 200
    assert any(p["id"] == plan_id for p in r.json())

    # Update (rename)
    r = client.patch(f"/admin/plans/{plan_id}", json={"name": "Growth Plan v2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Growth Plan v2"

    # Duplicate name is rejected at create time
    client.post("/admin/plans", json={"name": "Starter Plan"})
    r = client.post("/admin/plans", json={"name": "Starter Plan"})
    assert r.status_code == 409

    # Duplicate name is rejected at update time too
    r = client.post("/admin/plans", json={"name": "Another Plan"})
    other_id = r.json()["id"]
    r = client.patch(f"/admin/plans/{other_id}", json={"name": "Starter Plan"})
    assert r.status_code == 409
