from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import quote


@dataclass(frozen=True)
class DocumentDefinition:
    key: str
    label: str
    storage_filename: str
    identifier_field: str
    identifier_prefix: str
    title_field: str
    list_route: str
    form_route: str
    detail_route: str
    edit_route: str
    pdf_route: str
    delete_route: str
    searchable_fields: tuple[str, ...]
    dashboard_category: str
    supported_source_parameters: tuple[str, ...] = ()


DOCUMENT_DEFINITIONS: tuple[DocumentDefinition, ...] = (
    DocumentDefinition("company", "Company", "company.json", "name", "", "name", "/company", "/company", "/company", "", "", "", ("name", "address", "email", "phone"), "Master Data"),
    DocumentDefinition("customers", "Customers", "customers.json", "company", "", "company", "/customer", "/customer", "/customer", "", "", "/delete-customer/{value}", ("company", "country", "address", "email", "phone", "pic"), "Master Data"),
    DocumentDefinition("buyers", "Buyers", "buyers.json", "name", "", "name", "/buyers", "/buyer-form", "/buyers", "/edit-buyer/{value}", "", "/delete-buyer/{value}", ("name", "address", "email", "country"), "Master Data"),
    DocumentDefinition("products", "Products", "products.json", "name", "", "name", "/products", "/product-form", "/products", "/edit-product/{value}", "", "/delete-product/{value}", ("name", "hs_code", "origin"), "Master Data"),
    DocumentDefinition("quotation", "Quotation", "quotations.json", "quotation_no", "QT", "buyer_name", "/quotation-list", "/quotation-form", "", "/edit-quotation/{value}", "/quotation-pdf/{value}", "/delete-quotation/{value}", ("quotation_no", "buyer_name", "buyer_address", "buyer_email", "seller"), "Commercial Documents"),
    DocumentDefinition("proforma", "Proforma Invoice", "proformas.json", "pi_no", "PI", "buyer", "/proforma-list", "/proforma-form", "", "/edit-proforma/{value}", "/proforma-pdf/{value}", "/delete-proforma/{value}", ("pi_no", "quotation_no", "seller", "buyer", "buyer_address", "buyer_email"), "Commercial Documents", ("quotation_no",)),
    DocumentDefinition("invoice", "Commercial Invoice", "invoices.json", "invoice_no", "INV", "buyer", "/invoice-list", "/invoice", "", "/edit-invoice/{value}", "/invoice-pdf/{value}", "/delete-invoice/{value}", ("invoice_no", "pi_no", "seller", "buyer", "buyer_address", "buyer_email"), "Commercial Documents", ("pi_no", "shipment_no")),
    DocumentDefinition("packing", "Packing List", "packing_lists.json", "packing_no", "PK", "buyer", "/packing-list", "/packing-page", "", "/edit-packing/{value}", "/packing-list-pdf/{value}", "/packing-delete/{value}", ("packing_no", "invoice_no", "seller", "buyer"), "Commercial Documents", ("invoice_no", "shipment_no")),
    DocumentDefinition("shipment", "Shipment", "shipments.json", "shipment_no", "SHP", "shipment_name", "/shipment-list", "/shipment-form", "/shipment/{value}", "/edit-shipment/{value}", "/shipment-pdf/{value}", "/delete-shipment/{value}", ("shipment_no", "shipment_name", "customer", "buyer", "status", "quotation_no", "pi_no", "invoice_no", "packing_no", "si_no", "bl_no", "co_no", "inspection_no", "insurance_no", "weight_no"), "Shipping Operations"),
    DocumentDefinition("shipping_instruction", "Shipping Instruction", "shipping_instructions.json", "si_no", "SI", "consignee", "/si-list", "/si-form", "", "/edit-si/{value}", "/si-pdf/{value}", "/delete-si/{value}", ("si_no", "packing_no", "invoice_no", "shipper", "consignee", "notify_party", "carrier", "vessel", "voyage_no"), "Shipping Operations", ("packing_no", "shipment_no")),
    DocumentDefinition("booking", "Booking Confirmation", "booking_confirmations.json", "booking_record_no", "BK", "booking_no", "/booking-list", "/booking-form", "/booking/{value}", "/edit-booking/{value}", "/booking-pdf/{value}", "/delete-booking/{value}", ("booking_record_no", "booking_no", "booking_reference", "shipment_no", "si_no", "packing_no", "bl_no", "invoice_no", "carrier", "vessel", "voyage_no"), "Shipping Operations", ("shipment_no", "si_no", "packing_no", "bl_no")),
    DocumentDefinition("container", "Container Management", "containers.json", "container_record_no", "CON", "container_no", "/container-list", "/container-form", "/container/{value}", "/edit-container/{value}", "/container-pdf/{value}", "/delete-container/{value}", ("container_record_no", "container_no", "seal_no", "shipment_no", "packing_no", "bl_no", "invoice_no", "carrier", "vessel", "voyage_no"), "Shipping Operations", ("shipment_no", "packing_no", "bl_no")),
    DocumentDefinition("bill_of_lading", "Bill of Lading", "bills_of_lading.json", "bl_no", "BL", "consignee", "/bl-list", "/bl-form", "", "/edit-bl/{value}", "/bl-pdf/{value}", "/delete-bl/{value}", ("bl_no", "packing_no", "invoice_no", "shipper", "consignee", "notify_party", "vessel", "voyage_no"), "Shipping Operations", ("packing_no",)),
    DocumentDefinition("certificate_of_origin", "Certificate of Origin", "certificates_of_origin.json", "co_no", "CO", "consignee", "/co-list", "/co-form", "/co/{value}", "/edit-co/{value}", "/co-pdf/{value}", "/delete-co/{value}", ("co_no", "bl_no", "invoice_no", "packing_no", "exporter", "consignee", "country_of_origin", "destination_country", "transport_details"), "Certificates and Compliance", ("bl_no",)),
    DocumentDefinition("inspection", "Inspection Certificate", "inspection_certificates.json", "inspection_no", "IC", "consignee", "/inspection-list", "/inspection-form", "/inspection/{value}", "/edit-inspection/{value}", "/inspection-pdf/{value}", "/delete-inspection/{value}", ("inspection_no", "bl_no", "packing_no", "invoice_no", "exporter", "consignee", "inspection_company", "inspection_location", "inspection_result", "transport_details"), "Certificates and Compliance", ("bl_no",)),
    DocumentDefinition("insurance", "Insurance Certificate", "insurance_certificates.json", "insurance_no", "INS", "consignee", "/insurance-list", "/insurance-form", "/insurance/{value}", "/edit-insurance/{value}", "/insurance-pdf/{value}", "/delete-insurance/{value}", ("insurance_no", "bl_no", "packing_no", "invoice_no", "exporter", "consignee", "insurance_company", "policy_no", "coverage_type", "transport_details"), "Certificates and Compliance", ("bl_no",)),
    DocumentDefinition("weight", "Weight Certificate", "weight_certificates.json", "weight_no", "WT", "consignee", "/weight-list", "/weight-form", "/weight/{value}", "/edit-weight/{value}", "/weight-pdf/{value}", "/delete-weight/{value}", ("weight_no", "bl_no", "packing_no", "invoice_no", "exporter", "consignee", "weighing_place", "weighing_method", "transport_details"), "Certificates and Compliance", ("bl_no",)),
    DocumentDefinition("customs", "Customs Declaration", "customs_declarations.json", "customs_record_no", "CD", "declaration_no", "/customs-list", "/customs-form", "/customs/{value}", "/edit-customs/{value}", "/customs-pdf/{value}", "/delete-customs/{value}", ("customs_record_no", "declaration_no", "shipment_no", "booking_record_no", "invoice_no", "packing_no", "container_record_no", "bl_no", "exporter", "consignee", "destination_country", "vessel", "voyage_no", "container_no"), "Certificates and Compliance", ("shipment_no", "invoice_no", "packing_no", "booking_record_no", "container_record_no", "bl_no")),
)

_DEFINITIONS_BY_KEY = MappingProxyType({definition.key: definition for definition in DOCUMENT_DEFINITIONS})
_DEFINITIONS_BY_LABEL = MappingProxyType({definition.label: definition for definition in DOCUMENT_DEFINITIONS})


def get_document_definition(key_or_label: str) -> DocumentDefinition:
    try:
        return _DEFINITIONS_BY_KEY[key_or_label]
    except KeyError:
        try:
            return _DEFINITIONS_BY_LABEL[key_or_label]
        except KeyError as exc:
            raise KeyError(f"Unknown document definition: {key_or_label}") from exc


def document_url(key_or_definition: str | DocumentDefinition, action: str, identifier: object = "") -> str:
    definition = get_document_definition(key_or_definition) if isinstance(key_or_definition, str) else key_or_definition
    route = getattr(definition, f"{action}_route")
    if not route:
        return ""
    return route.format(value=quote(str(identifier or ""), safe=""))


def definitions_for_category(category: str) -> tuple[DocumentDefinition, ...]:
    return tuple(definition for definition in DOCUMENT_DEFINITIONS if definition.dashboard_category == category)
