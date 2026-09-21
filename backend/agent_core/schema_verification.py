"""Read-only ORM structure checks for clients of a shared PostgreSQL database.

This is not a migration substitute: triggers, functions and newer business
semantics still belong to the deployment that owns the complete migration chain.
"""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect


def verify_schema(connection, metadata) -> None:
    """Reject model drift while permitting additions that old inserts can omit."""
    inspector = inspect(connection)
    installed = set(inspector.get_table_names())
    problems = []
    nullable_additions = {}
    for table in metadata.sorted_tables:
        if table.name not in installed:
            problems.append(f"missing table {table.name}")
            continue
        columns = inspector.get_columns(table.name)
        nullable_additions[table.name] = {
            col["name"] for col in columns
            if col["name"] not in table.c and col["nullable"]
            and col.get("default") is None and not col.get("identity")
            and not col.get("computed")
        }
        for col in columns:
            if (col["name"] not in table.c and not col["nullable"]
                    and col.get("default") is None and not col.get("identity")
                    and not col.get("computed")):
                problems.append(f"unmapped required column {table.name}.{col['name']}")
        primary_key = inspector.get_pk_constraint(table.name)["constrained_columns"]
        if list(primary_key) != [col.name for col in table.primary_key]:
            problems.append(f"primary key differs on {table.name}")

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table":
            return name in metadata.tables
        if not reflected or compare_to is not None:
            return True
        if type_ == "column":
            # Required extra columns have been checked above.
            return False
        if type_ == "index" and not obj.unique:
            return False
        if type_ in {"unique_constraint", "index", "foreign_key_constraint"}:
            columns = {col.name for col in obj.columns}
            safe_columns = nullable_additions.get(obj.table.name, set())
            # A constraint on new NULL columns cannot restrict old inserts.
            # NULLS NOT DISTINCT unique constraints are the exception.
            nulls_not_distinct = obj.dialect_options["postgresql"].get("nulls_not_distinct")
            if columns and columns <= safe_columns and not nulls_not_distinct:
                return False
        return True

    def compare_default(context, inspected, model, inspected_default, model_default, rendered):
        # ORM-supplied defaults need no matching server default. Explicit model
        # server defaults do, since those values are omitted by the application.
        return False if model.server_default is None else None

    context = MigrationContext.configure(connection, opts={
        "include_object": include_object,
        "compare_type": True,
        "compare_server_default": compare_default,
    })
    for difference in compare_metadata(context, metadata):
        for item in difference if isinstance(difference, list) else [difference]:
            kind = item[0]
            if kind.startswith("modify_"):
                label = f"{item[2]}.{item[3]}"
            elif kind in {"add_column", "remove_column"}:
                label = f"{item[2]}.{item[3].name}"
            else:
                obj = item[-1]
                table = getattr(obj, "table", None)
                label = f"{table.name}." if table is not None else ""
                label += str(getattr(obj, "name", None) or kind)
            problems.append(f"{kind}: {label}")
    if problems:
        raise RuntimeError(
            "Shared database is incompatible with this checkout: " + "; ".join(problems)
            + ". Sync the matching code/migrations before starting. No database changes were made."
        )
