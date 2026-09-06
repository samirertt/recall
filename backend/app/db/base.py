from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every canonical/derived table.

    Import every model module in app/models/__init__.py so Alembic
    autogenerate and Base.metadata.create_all() see the full schema.
    """
