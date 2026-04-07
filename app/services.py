from sqlalchemy.orm import Session

from .models import AuditLog, Notification, Stock, StockMovement


def notify(db: Session, event: str, payload: str) -> None:
    db.add(Notification(event=event, payload=payload))


def audit(
    db: Session,
    *,
    user_name: str,
    user_role: str,
    action: str,
    entity: str,
    entity_id: int | None,
    details: str = "",
) -> None:
    db.add(
        AuditLog(
            user_name=user_name,
            user_role=user_role,
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details,
        )
    )


def get_or_create_stock(db: Session, product_id: int) -> Stock:
    stock = db.query(Stock).filter(Stock.product_id == product_id).first()
    if stock:
        return stock
    stock = Stock(product_id=product_id, qty_total=0, qty_defect=0, qty_reserved=0)
    db.add(stock)
    db.flush()
    return stock


def register_movement(db: Session, product_id: int, movement_type: str, qty: int, comment: str = "") -> None:
    db.add(
        StockMovement(
            product_id=product_id,
            movement_type=movement_type,
            qty=qty,
            comment=comment,
        )
    )
