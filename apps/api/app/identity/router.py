"""HTTP layer of the ``identity`` domain. No routes yet; they arrive with S-2.1.1 and S-2.2.2."""

from fastapi import APIRouter

router = APIRouter(tags=["identity"])
