from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
OPERATOR_HEADERS = {"x-user-name": "operator_1", "x-user-role": "operator"}
CUSTOMER_HEADERS = {"x-user-name": "customer_1", "x-user-role": "customer"}
ADMIN_HEADERS = {"x-user-name": "admin_1", "x-user-role": "admin"}


def test_reception_reservation_and_box_flow_with_rbac_and_audit():
    uid = uuid4().hex[:8]

    p = client.post(
        "/products",
        headers=OPERATOR_HEADERS,
        json={"name": "Футболка", "sku": f"SKU-{uid}", "barcode": f"111{uid}", "color": "black", "size": "L"},
    )
    assert p.status_code == 200
    product_id = p.json()["id"]

    s = client.post("/supplies", headers=OPERATOR_HEADERS, json={"number": f"SUP-{uid}"})
    assert s.status_code == 200
    supply_id = s.json()["id"]

    r = client.post(
        "/receptions",
        headers=OPERATOR_HEADERS,
        json={
            "supply_id": supply_id,
            "product_id": product_id,
            "expected_qty": 10,
            "actual_qty": 8,
            "discrepancy_reason": "Недостача",
            "discrepancy_comment": "2 единицы отсутствуют",
        },
    )
    assert r.status_code == 200

    discrepancies = client.get("/discrepancies", headers=CUSTOMER_HEADERS)
    assert discrepancies.status_code == 200
    assert any(d["supply_id"] == supply_id for d in discrepancies.json())

    reserve = client.post(
        "/reservations",
        headers=OPERATOR_HEADERS,
        json={"product_id": product_id, "qty": 3, "order_ref": f"ORDER-{uid}"},
    )
    assert reserve.status_code == 200

    box = client.post(
        "/boxes",
        headers=OPERATOR_HEADERS,
        json={"box_number": f"BOX-{uid}", "destination": "Москва"},
    )
    assert box.status_code == 200
    box_id = box.json()["id"]

    add_item = client.post(f"/boxes/{box_id}/items", headers=OPERATOR_HEADERS, json={"product_id": product_id, "qty": 4})
    assert add_item.status_code == 200

    close = client.post(f"/boxes/{box_id}/close", headers=OPERATOR_HEADERS)
    assert close.status_code == 200
    assert close.json()["status"] == "закрыта"

    stocks = client.get("/stocks", headers=CUSTOMER_HEADERS)
    assert stocks.status_code == 200
    row = [x for x in stocks.json() if x["product_id"] == product_id][0]
    assert row["qty_total"] == 4
    assert row["qty_reserved"] == 3
    assert row["qty_available"] == 1

    denied = client.post(
        "/products",
        headers=CUSTOMER_HEADERS,
        json={"name": "Запрещено", "sku": f"NO-{uid}", "barcode": f"NO{uid}"},
    )
    assert denied.status_code == 403

    audits = client.get("/audit-logs", headers=ADMIN_HEADERS)
    assert audits.status_code == 200
    assert len(audits.json()) > 0
