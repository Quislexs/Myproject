from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import (
    AuditLog,
    Box,
    BoxItem,
    Defect,
    DiscrepancyLog,
    News,
    Notification,
    Product,
    Reception,
    Reservation,
    Shipment,
    Stock,
    StockMovement,
    Supply,
)
from .schemas import (
    BoxCreate,
    BoxItemCreate,
    DefectCreate,
    NewsCreate,
    ProductCreate,
    ReceptionCreate,
    ReservationCreate,
    ShipmentCreate,
    StatusUpdate,
    SupplyCreate,
)
from .services import audit, get_or_create_stock, notify, register_movement

app = FastAPI(title="WMS API", version="1.1.0")

SUPPLY_STATUSES = {"ожидается", "прибыл", "принят", "в архиве"}
BOX_STATUSES = {"создана", "в комплектации", "закрыта", "отгружена"}
SHIPMENT_STATUSES = {"собирается", "готово к отгрузке", "отгружено"}
NEWS_STATUSES = {"черновик", "опубликована", "архив"}


class CurrentUser(BaseModel):
    name: str
    role: str


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


def get_current_user(
    x_user_name: str = Header(default="system"),
    x_user_role: str = Header(default="admin"),
) -> CurrentUser:
    role = x_user_role.lower()
    if role not in {"admin", "operator", "customer"}:
        raise HTTPException(status_code=400, detail="Unknown role")
    return CurrentUser(name=x_user_name, role=role)


def require_roles(*roles: str):
    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Access denied")
        return user

    return checker


def validate_status(status: str, allowed: set[str], entity: str):
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid {entity} status: {status}")


@app.post("/products")
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    product = Product(**payload.model_dump())
    db.add(product)
    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="product", entity_id=product.id)
    db.commit()
    db.refresh(product)
    return product


@app.get("/products")
def list_products(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    audit(db, user_name=user.name, user_role=user.role, action="list", entity="product", entity_id=None)
    db.commit()
    return db.query(Product).all()


@app.post("/supplies")
def create_supply(
    payload: SupplyCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    supply = Supply(**payload.model_dump())
    db.add(supply)
    db.flush()
    notify(db, "прибытие груза", f"Поставка {supply.number} создана")
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="supply", entity_id=supply.id)
    db.commit()
    db.refresh(supply)
    return supply


@app.patch("/supplies/{supply_id}/status")
def update_supply_status(
    supply_id: int,
    payload: StatusUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    validate_status(payload.status, SUPPLY_STATUSES, "supply")
    supply = db.get(Supply, supply_id)
    if not supply:
        raise HTTPException(status_code=404, detail="Supply not found")
    supply.status = payload.status
    notify(db, "статус поставки", f"Поставка {supply.number}: {payload.status}")
    audit(db, user_name=user.name, user_role=user.role, action="status", entity="supply", entity_id=supply.id)
    db.commit()
    db.refresh(supply)
    return supply


@app.post("/receptions")
def create_reception(
    payload: ReceptionCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    reception = Reception(
        supply_id=payload.supply_id,
        product_id=payload.product_id,
        expected_qty=payload.expected_qty,
        actual_qty=payload.actual_qty,
    )
    db.add(reception)

    stock = get_or_create_stock(db, payload.product_id)
    stock.qty_total += payload.actual_qty
    register_movement(db, payload.product_id, "приемка", payload.actual_qty)

    if payload.expected_qty != payload.actual_qty:
        discrepancy = DiscrepancyLog(
            supply_id=payload.supply_id,
            product_id=payload.product_id,
            expected_qty=payload.expected_qty,
            actual_qty=payload.actual_qty,
            diff_qty=payload.actual_qty - payload.expected_qty,
            reason=payload.discrepancy_reason,
            comment=payload.discrepancy_comment,
        )
        db.add(discrepancy)
        notify(
            db,
            "расхождение",
            f"product_id={payload.product_id} expected={payload.expected_qty} actual={payload.actual_qty}",
        )

    notify(db, "завершение приемки", f"Приемка supply_id={payload.supply_id}")
    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="reception", entity_id=reception.id)
    db.commit()
    db.refresh(reception)
    return reception


@app.get("/discrepancies")
def list_discrepancies(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    audit(db, user_name=user.name, user_role=user.role, action="list", entity="discrepancy", entity_id=None)
    db.commit()
    return db.query(DiscrepancyLog).order_by(DiscrepancyLog.id.desc()).all()


@app.post("/defects")
def create_defect(
    payload: DefectCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    defect = Defect(**payload.model_dump())
    db.add(defect)

    stock = get_or_create_stock(db, payload.product_id)
    stock.qty_defect += payload.qty
    stock.qty_total = max(stock.qty_total - payload.qty, 0)
    register_movement(db, payload.product_id, "брак", payload.qty, payload.reason)
    notify(db, "фиксация брака", f"product_id={payload.product_id} qty={payload.qty}")

    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="defect", entity_id=defect.id)
    db.commit()
    db.refresh(defect)
    return defect


@app.post("/reservations")
def reserve_stock(
    payload: ReservationCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    stock = get_or_create_stock(db, payload.product_id)
    available = stock.qty_total - stock.qty_reserved
    if payload.qty > available:
        raise HTTPException(status_code=400, detail="Not enough available stock")

    reservation = Reservation(**payload.model_dump())
    db.add(reservation)
    stock.qty_reserved += payload.qty
    register_movement(db, payload.product_id, "резервирование", payload.qty, payload.order_ref)
    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="reservation", entity_id=reservation.id)
    db.commit()
    db.refresh(reservation)
    return reservation


@app.get("/stocks")
def list_stocks(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.query(Stock).all()
    audit(db, user_name=user.name, user_role=user.role, action="list", entity="stock", entity_id=None)
    db.commit()
    return [
        {
            "product_id": s.product_id,
            "qty_total": s.qty_total,
            "qty_defect": s.qty_defect,
            "qty_reserved": s.qty_reserved,
            "qty_available": s.qty_total - s.qty_reserved,
        }
        for s in rows
    ]


@app.post("/boxes")
def create_box(
    payload: BoxCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    box = Box(**payload.model_dump())
    db.add(box)
    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="box", entity_id=box.id)
    db.commit()
    db.refresh(box)
    return box


@app.post("/boxes/{box_id}/items")
def add_item_to_box(
    box_id: int,
    payload: BoxItemCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    box = db.get(Box, box_id)
    if not box:
        raise HTTPException(status_code=404, detail="Box not found")

    stock = get_or_create_stock(db, payload.product_id)
    available = stock.qty_total - stock.qty_reserved
    if payload.qty > available:
        raise HTTPException(status_code=400, detail="Not enough stock for packing")

    item = BoxItem(box_id=box_id, product_id=payload.product_id, qty=payload.qty)
    db.add(item)
    stock.qty_total -= payload.qty
    register_movement(db, payload.product_id, "упаковка", payload.qty, box.box_number)

    box.status = "в комплектации"
    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="pack", entity="box", entity_id=box.id)
    db.commit()
    db.refresh(item)
    return item


@app.post("/boxes/{box_id}/close")
def close_box(
    box_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    box = db.get(Box, box_id)
    if not box:
        raise HTTPException(status_code=404, detail="Box not found")

    box.status = "закрыта"
    qr_payload = f"BOX:{box.box_number}:{box.destination}"
    notify(db, "закрытие короба", qr_payload)
    audit(db, user_name=user.name, user_role=user.role, action="close", entity="box", entity_id=box.id)
    db.commit()
    return {"box_id": box.id, "status": box.status, "qr": qr_payload}


@app.post("/shipments")
def create_shipment(
    payload: ShipmentCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    shipment = Shipment(**payload.model_dump())
    db.add(shipment)
    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="shipment", entity_id=shipment.id)
    db.commit()
    db.refresh(shipment)
    return shipment


@app.patch("/shipments/{shipment_id}/status")
def update_shipment_status(
    shipment_id: int,
    payload: StatusUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    validate_status(payload.status, SHIPMENT_STATUSES, "shipment")
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    shipment.status = payload.status
    if payload.status == "отгружено":
        notify(db, "отгрузка", f"shipment_id={shipment_id}")
    audit(db, user_name=user.name, user_role=user.role, action="status", entity="shipment", entity_id=shipment.id)
    db.commit()
    db.refresh(shipment)
    return shipment


@app.post("/news")
def create_news(
    payload: NewsCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    news = News(**payload.model_dump())
    db.add(news)
    db.flush()
    audit(db, user_name=user.name, user_role=user.role, action="create", entity="news", entity_id=news.id)
    db.commit()
    db.refresh(news)
    return news


@app.patch("/news/{news_id}/status")
def update_news_status(
    news_id: int,
    payload: StatusUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "operator")),
):
    validate_status(payload.status, NEWS_STATUSES, "news")
    news = db.get(News, news_id)
    if not news:
        raise HTTPException(status_code=404, detail="News not found")
    news.status = payload.status
    if payload.status == "опубликована":
        notify(db, "публикация новостей", f"{news.title}")
    audit(db, user_name=user.name, user_role=user.role, action="status", entity="news", entity_id=news.id)
    db.commit()
    db.refresh(news)
    return news


@app.get("/notifications")
def list_notifications(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    audit(db, user_name=user.name, user_role=user.role, action="list", entity="notification", entity_id=None)
    db.commit()
    return db.query(Notification).order_by(Notification.id.desc()).all()


@app.get("/movements")
def list_movements(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    audit(db, user_name=user.name, user_role=user.role, action="list", entity="movement", entity_id=None)
    db.commit()
    return db.query(StockMovement).order_by(StockMovement.id.desc()).all()


@app.get("/audit-logs")
def list_audit_logs(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin")),
):
    return db.query(AuditLog).order_by(AuditLog.id.desc()).all()
