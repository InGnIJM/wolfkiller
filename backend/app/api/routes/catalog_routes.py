from fastapi import APIRouter

from app.catalog import FIELD_CONSTRAINTS, STANDARD_PRESETS, role_catalog
from app.api.catalog_schemas import (
    ConstraintsResponse, PresetItem, PresetsResponse, RoleCatalogResponse,
)
from app.roles.registry import builtin_registry

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/roles", response_model=RoleCatalogResponse)
async def list_roles():
    return RoleCatalogResponse(roles=role_catalog(builtin_registry.freeze()))


@router.get("/presets", response_model=PresetsResponse)
async def list_presets():
    return PresetsResponse(presets=[PresetItem(**p) for p in STANDARD_PRESETS])


@router.get("/constraints", response_model=ConstraintsResponse)
async def get_constraints():
    return ConstraintsResponse(**FIELD_CONSTRAINTS)
