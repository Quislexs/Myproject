from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    name: str
    sku: str
    barcode: str
    color: str | None = None
    size: str | None = None


class SupplyCreate(BaseModel):
    number: str
    vehicle_number: str | None = None
    driver_name: str | None = None


class ReceptionCreate(BaseModel):
    supply_id: int
    product_id: int
    expected_qty: int = Field(ge=0)
    actual_qty: int = Field(ge=0)
    discrepancy_reason: str | None = None
    discrepancy_comment: str | None = None


class DefectCreate(BaseModel):
    supply_id: int
    product_id: int
    reason: str
    qty: int = Field(gt=0)
    comment: str | None = None


class ReservationCreate(BaseModel):
    product_id: int
    qty: int = Field(gt=0)
    order_ref: str


class BoxCreate(BaseModel):
    box_number: str
    destination: str


class BoxItemCreate(BaseModel):
    product_id: int
    qty: int = Field(gt=0)


class ShipmentCreate(BaseModel):
    pallets: int = Field(ge=0)
    boxes: int = Field(ge=0)


class NewsCreate(BaseModel):
    title: str
    text: str
    author: str


class StatusUpdate(BaseModel):
    status: str
