"""
Stantech - Backend Working Task
A small multi-tenant inventory and order service. See README.md for your task.

Run:
    pip install -r requirements.txt
    python app.py

Then open http://localhost:8000/docs

Each request identifies its tenant via the X-Tenant-Id header.
"""

from datetime import datetime
from typing import List

from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    create_engine,
    ForeignKey,
    String,
    Integer,
    DateTime,
    func,
    update,
    Index,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    Session,
    sessionmaker,
    selectinload,
)


engine = create_engine(
    "sqlite:///./inventory.db",
    connect_args={
        "check_same_thread": False,
        "timeout": 30,
    },
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String)

    __table_args__ = (
        Index("ix_warehouses_tenant_id", "tenant_id"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    sku: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)

    __table_args__ = (
        Index("ix_products_tenant_id", "tenant_id"),
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String)


class StockLevel(Base):
    __tablename__ = "stock_levels"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity_available: Mapped[int] = mapped_column(Integer, default=0)

    warehouse: Mapped["Warehouse"] = relationship()
    product: Mapped["Product"] = relationship()

    __table_args__ = (
        Index(
            "ix_stock_tenant_warehouse_product",
            "tenant_id",
            "warehouse_id",
            "product_id",
            unique=True,
        ),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    notes: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )

    customer: Mapped["Customer"] = relationship()
    lines: Mapped[List["OrderLine"]] = relationship()

    __table_args__ = (
        Index("ix_orders_tenant_id", "tenant_id"),
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)

    product: Mapped["Product"] = relationship()


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def current_tenant_id(
    x_tenant_id: int = Header(...),
) -> int:
    return x_tenant_id


app = FastAPI(title="Stantech Inventory")


def serialize_order(o: Order) -> dict:
    return {
        "id": o.id,
        "customer_name": o.customer.name,
        "notes": o.notes,
        "status": o.status,
        "lines": [
            {
                "product_sku": line.product.sku,
                "product_name": line.product.name,
                "quantity": line.quantity,
            }
            for line in o.lines
        ],
    }


def serialize_stock(s: StockLevel) -> dict:
    return {
        "id": s.id,
        "warehouse": s.warehouse.name,
        "product_sku": s.product.sku,
        "quantity_available": s.quantity_available,
    }


@app.get("/orders")
def list_orders(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    orders = (
        db.query(Order)
        .options(
            selectinload(Order.customer),
            selectinload(Order.lines).selectinload(OrderLine.product),
        )
        .filter(Order.tenant_id == tenant_id)
        .all()
    )

    return [serialize_order(order) for order in orders]


@app.get("/orders/{order_id}")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    order = (
        db.query(Order)
        .options(
            selectinload(Order.customer),
            selectinload(Order.lines).selectinload(OrderLine.product),
        )
        .filter(
            Order.id == order_id,
            Order.tenant_id == tenant_id,
        )
        .first()
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    return serialize_order(order)


@app.get("/stock")
def list_stock(
    warehouse_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    warehouse = (
        db.query(Warehouse)
        .filter(
            Warehouse.id == warehouse_id,
            Warehouse.tenant_id == tenant_id,
        )
        .first()
    )

    if not warehouse:
        raise HTTPException(
            status_code=404,
            detail="Warehouse not found",
        )

    levels = (
        db.query(StockLevel)
        .options(
            selectinload(StockLevel.warehouse),
            selectinload(StockLevel.product),
        )
        .filter(
            StockLevel.warehouse_id == warehouse_id,
            StockLevel.tenant_id == tenant_id,
        )
        .all()
    )

    return [serialize_stock(level) for level in levels]


class StockAdjustIn(BaseModel):
    stock_level_id: int
    delta: int


@app.post("/stock/adjust")
def adjust_stock(
    payload: StockAdjustIn,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    """
    Adjust stock atomically.

    The update itself checks that the resulting quantity cannot
    become negative. This avoids the read/check/write race where
    two concurrent requests can both observe the same quantity.
    """

    result = db.execute(
        update(StockLevel)
        .where(
            StockLevel.id == payload.stock_level_id,
            StockLevel.tenant_id == tenant_id,
            StockLevel.quantity_available + payload.delta >= 0,
        )
        .values(
            quantity_available=StockLevel.quantity_available
            + payload.delta
        )
    )

    if result.rowcount == 0:
        stock = (
            db.query(StockLevel)
            .filter(
                StockLevel.id == payload.stock_level_id,
                StockLevel.tenant_id == tenant_id,
            )
            .first()
        )

        if not stock:
            raise HTTPException(
                status_code=404,
                detail="Stock level not found",
            )

        raise HTTPException(
            status_code=400,
            detail="Insufficient stock",
        )

    db.commit()

    stock = (
        db.query(StockLevel)
        .options(
            selectinload(StockLevel.warehouse),
            selectinload(StockLevel.product),
        )
        .filter(
            StockLevel.id == payload.stock_level_id,
            StockLevel.tenant_id == tenant_id,
        )
        .first()
    )

    return serialize_stock(stock)


class ReserveStockIn(BaseModel):
    warehouse_id: int


@app.post("/orders/{order_id}/reserve")
def reserve_order(
    order_id: int,
    payload: ReserveStockIn,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(current_tenant_id),
):
    """
    Reserve all stock required by an order from one warehouse.

    Reservation is atomic:
    - Either every order line is reserved.
    - Or no stock is changed.

    An already-reserved order is returned unchanged. This makes
    retries safe when the fulfilment agent times out after the
    original request has committed.
    """

    try:
        # SQLite does not support SELECT ... FOR UPDATE in the same
        # way as databases such as PostgreSQL. BEGIN IMMEDIATE
        # obtains the SQLite write lock before we inspect stock,
        # preventing concurrent reservations from racing.
        db.execute(text("BEGIN IMMEDIATE"))

        order = (
            db.query(Order)
            .options(
                selectinload(Order.customer),
                selectinload(Order.lines).selectinload(OrderLine.product),
            )
            .filter(
                Order.id == order_id,
                Order.tenant_id == tenant_id,
            )
            .first()
        )

        if not order:
            raise HTTPException(
                status_code=404,
                detail="Order not found",
            )

        # Idempotency:
        # A retry after a successful reservation must not decrement
        # stock again.
        if order.status == "reserved":
            db.commit()
            return serialize_order(order)

        if order.status != "pending":
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Order cannot be reserved from status '{order.status}'",
            )

        warehouse = (
            db.query(Warehouse)
            .filter(
                Warehouse.id == payload.warehouse_id,
                Warehouse.tenant_id == tenant_id,
            )
            .first()
        )

        if not warehouse:
            db.rollback()
            raise HTTPException(
                status_code=404,
                detail="Warehouse not found",
            )

        # Get all stock rows needed by this order.
        product_ids = [line.product_id for line in order.lines]

        stock_levels = (
            db.query(StockLevel)
            .filter(
                StockLevel.tenant_id == tenant_id,
                StockLevel.warehouse_id == payload.warehouse_id,
                StockLevel.product_id.in_(product_ids),
            )
            .all()
        )

        stock_by_product = {
            stock.product_id: stock
            for stock in stock_levels
        }

        # First verify the complete order can be satisfied.
        for line in order.lines:
            stock = stock_by_product.get(line.product_id)

            if stock is None:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Product {line.product_id} has no stock "
                        "record in this warehouse"
                    ),
                )

            if stock.quantity_available < line.quantity:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Insufficient stock for product "
                        f"{line.product_id}"
                    ),
                )

        # Decrement every stock row atomically.
        #
        # The quantity_available >= quantity condition is an
        # additional safety net against overselling.
        for line in order.lines:
            result = db.execute(
                update(StockLevel)
                .where(
                    StockLevel.id == stock_by_product[line.product_id].id,
                    StockLevel.tenant_id == tenant_id,
                    StockLevel.quantity_available >= line.quantity,
                )
                .values(
                    quantity_available=(
                        StockLevel.quantity_available - line.quantity
                    )
                )
            )

            if result.rowcount != 1:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Insufficient stock for product "
                        f"{line.product_id}"
                    ),
                )

        order.status = "reserved"

        db.commit()

        # Reload after commit so the returned object is fresh.
        order = (
            db.query(Order)
            .options(
                selectinload(Order.customer),
                selectinload(Order.lines).selectinload(OrderLine.product),
            )
            .filter(
                Order.id == order_id,
                Order.tenant_id == tenant_id,
            )
            .first()
        )

        return serialize_order(order)

    except HTTPException:
        raise

    except Exception:
        db.rollback()
        raise


def seed():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    db = SessionLocal()

    northwind = Tenant(name="Northwind Traders")
    globex = Tenant(name="Globex")

    db.add_all([northwind, globex])
    db.flush()

    warehouses = {}

    for tenant in (northwind, globex):
        for warehouse_name in ("Central", "Overflow"):
            warehouse = Warehouse(
                tenant_id=tenant.id,
                name=f"{warehouse_name} ({tenant.name})",
            )

            db.add(warehouse)
            db.flush()

            warehouses.setdefault(tenant.id, []).append(warehouse)

    products = {}

    for tenant in (northwind, globex):
        for i in range(1, 41):
            product = Product(
                tenant_id=tenant.id,
                sku=(
                    f"{'NW' if tenant.id == northwind.id else 'GX'}"
                    f"-{1000 + i}"
                ),
                name=f"Component {i}",
            )

            db.add(product)
            db.flush()

            products.setdefault(tenant.id, []).append(product)

    for tenant in (northwind, globex):
        for warehouse in warehouses[tenant.id]:
            for product in products[tenant.id]:
                db.add(
                    StockLevel(
                        tenant_id=tenant.id,
                        warehouse_id=warehouse.id,
                        product_id=product.id,
                        quantity_available=25,
                    )
                )

    db.flush()

    def make_order(tenant, customer, notes, items):
        customer_row = Customer(
            tenant_id=tenant.id,
            name=customer,
        )

        db.add(customer_row)
        db.flush()

        order = Order(
            tenant_id=tenant.id,
            customer_id=customer_row.id,
            notes=notes,
        )

        db.add(order)
        db.flush()

        for product, quantity in items:
            db.add(
                OrderLine(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=quantity,
                )
            )

        return order

    make_order(
        northwind,
        "Acme Retail",
        "Standard terms",
        [
            (products[northwind.id][0], 3),
            (products[northwind.id][1], 2),
        ],
    )

    make_order(
        northwind,
        "Bluebird Stores",
        "Ship in one consignment",
        [
            (products[northwind.id][2], 5),
        ],
    )

    make_order(
        globex,
        "Initech",
        "Standard terms",
        [
            (products[globex.id][0], 4),
            (products[globex.id][3], 1),
        ],
    )

    make_order(
        globex,
        "Umbrella Group",
        "Confidential: 40% negotiated discount, "
        "Q4 renewal - do not share externally",
        [
            (products[globex.id][1], 8),
            (products[globex.id][4], 6),
        ],
    )

    for i in range(60):
        make_order(
            northwind,
            f"Northwind customer {i + 1}",
            "",
            [
                (
                    products[northwind.id][i % 40],
                    (i % 4) + 1,
                ),
                (
                    products[northwind.id][(i + 17) % 40],
                    (i % 3) + 1,
                ),
            ],
        )

    for i in range(40):
        make_order(
            globex,
            f"Globex customer {i + 1}",
            "",
            [
                (
                    products[globex.id][i % 40],
                    (i % 4) + 1,
                ),
                (
                    products[globex.id][(i + 13) % 40],
                    (i % 3) + 1,
                ),
            ],
        )

    db.commit()
    db.close()


if __name__ == "__main__":
    seed()

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )