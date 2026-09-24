"""Shared argument aliases so models may send orderNo as well as order_no."""
from pydantic import AliasChoices, ConfigDict, Field

from domain_packs.mold.ports.schemas import StrictModel


class CamelModel(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def identity_order_no(**kwargs):
    return Field(validation_alias=AliasChoices("order_no", "orderNo"), **kwargs)


def identity_mold(**kwargs):
    return Field(validation_alias=AliasChoices("mold", "moldNo", "mold_no"), **kwargs)


def identity_batch(**kwargs):
    return Field(validation_alias=AliasChoices("batch", "moldBatch", "mold_batch"), **kwargs)


def identity_part(**kwargs):
    return Field(
        validation_alias=AliasChoices("part", "part_no", "partNo", "parts", "partDetails"),
        **kwargs,
    )
