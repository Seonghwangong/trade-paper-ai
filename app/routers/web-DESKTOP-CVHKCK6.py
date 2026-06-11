from fastapi import APIRouter, Body

from services.invoice_service import generate_invoice
from services.packing_service import generate_packing_list

router = APIRouter()

@router.post("/api/invoice")
def api_invoice(payload: dict = Body(...)):
    return generate_invoice(payload)

@router.post("/api/packing-list")
def api_packing_list(payload: dict = Body(...)):
    return generate_packing_list(payload)
