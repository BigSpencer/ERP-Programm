"""Datenmodell (SQLAlchemy ORM).

Alle Clients arbeiten auf demselben Schema. Konkurrierende Änderungen werden
über optimistisches Sperren (Spalte ``version``) erkannt: speichert ein Client
einen Datensatz, der zwischenzeitlich von einem anderen Client geändert wurde,
wird ``StaleDataError`` ausgelöst statt Daten still zu überschreiben.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, declared_attr, Mapped, mapped_column, relationship

Money = Numeric(14, 2)
Qty = Numeric(14, 3)


class Base(DeclarativeBase):
    pass


class Versioned:
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())

    @declared_attr.directive
    def __mapper_args__(cls):
        return {"version_id_col": cls.__table__.c.version}


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------

ROLES = {
    "admin": "Administrator",
    "sales": "Vertrieb",
    "purchasing": "Einkauf",
    "warehouse": "Lager",
    "accounting": "Buchhaltung",
}

# Welche Rollen welches Modul nutzen dürfen (admin darf alles)
MODULE_ROLES = {
    "dashboard": set(ROLES),
    "cockpit": set(ROLES),
    "vehicles": {"sales", "purchasing", "warehouse"},
    "showroom": {"sales", "warehouse"},
    "vehicle_models": {"sales", "purchasing"},
    "offers": {"sales"},
    "vehicle_orders": {"sales", "purchasing"},
    "vehicle_sales": {"sales", "accounting"},
    "used_purchases": {"sales", "purchasing", "accounting"},
    "customers": {"sales", "accounting"},
    "suppliers": {"purchasing", "accounting"},
    "products": {"sales", "purchasing", "warehouse"},
    "sales": {"sales"},
    "invoices": {"sales", "accounting"},
    "purchasing": {"purchasing"},
    "inventory": {"warehouse", "purchasing"},
    "finance": {"accounting"},
    "admin": set(),
}


class User(Versioned, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="sales")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def may(self, module: str) -> bool:
        return self.role == "admin" or self.role in MODULE_ROLES.get(module, set())


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class NumberRange(Base):
    """Nummernkreise (z. B. Kunden 10000…, Aufträge AU-…)."""
    __tablename__ = "number_ranges"
    key: Mapped[str] = mapped_column(String(30), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(10), default="")
    next_value: Mapped[int] = mapped_column(Integer, default=1)
    width: Mapped[int] = mapped_column(Integer, default=6)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    username: Mapped[str] = mapped_column(String(50), default="")
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    details: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------
# Stammdaten
# --------------------------------------------------------------------------

class _Partner:
    number: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    street: Mapped[str] = mapped_column(String(120), default="")
    zip: Mapped[str] = mapped_column(String(10), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    country: Mapped[str] = mapped_column(String(2), default="DE")
    email: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    vat_id: Mapped[str] = mapped_column(String(20), default="")
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=14)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Customer(_Partner, Versioned, Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    credit_limit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))


class Supplier(_Partner, Versioned, Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)


UNITS = ["Stk", "kg", "m", "l", "Std", "Pkt"]


class Product(Versioned, Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(10), default="Stk")
    sales_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    purchase_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("19"))
    min_stock: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    stock_managed: Mapped[bool] = mapped_column(Boolean, default=True)
    default_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    default_supplier: Mapped[Supplier | None] = relationship()


class Warehouse(Versioned, Base):
    __tablename__ = "warehouses"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(80))


# --------------------------------------------------------------------------
# Lager (MM)
# --------------------------------------------------------------------------

class StockLevel(Versioned, Base):
    __tablename__ = "stock_levels"
    __table_args__ = (UniqueConstraint("product_id", "warehouse_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))

    product: Mapped[Product] = relationship()
    warehouse: Mapped[Warehouse] = relationship()


MOVEMENT_TYPES = {
    "receipt": "Wareneingang",
    "issue": "Warenausgang",
    "adjust": "Inventurkorrektur",
    "transfer_in": "Umlagerung Zugang",
    "transfer_out": "Umlagerung Abgang",
}


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    movement_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Qty)   # vorzeichenbehaftet
    reference: Mapped[str] = mapped_column(String(40), default="")
    username: Mapped[str] = mapped_column(String(50), default="")

    product: Mapped[Product] = relationship()
    warehouse: Mapped[Warehouse] = relationship()


# --------------------------------------------------------------------------
# Vertrieb (SD)
# --------------------------------------------------------------------------

SALES_STATUS = {
    "draft": "Entwurf",
    "confirmed": "Bestätigt",
    "delivered": "Geliefert",
    "invoiced": "Fakturiert",
    "cancelled": "Storniert",
}


class SalesOrder(Versioned, Base):
    __tablename__ = "sales_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    order_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    delivery_date: Mapped[dt.date | None] = mapped_column(Date)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    customer_reference: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")

    customer: Mapped[Customer] = relationship()
    warehouse: Mapped[Warehouse | None] = relationship()
    items: Mapped[list["SalesOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="SalesOrderItem.position")

    @property
    def net_total(self) -> Decimal:
        return sum((i.net_amount for i in self.items), Decimal("0"))

    @property
    def tax_total(self) -> Decimal:
        return sum((i.tax_amount for i in self.items), Decimal("0"))

    @property
    def gross_total(self) -> Decimal:
        return self.net_total + self.tax_total


class _Item:
    id: Mapped[int] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=10)
    description: Mapped[str] = mapped_column(String(200), default="")
    quantity: Mapped[Decimal] = mapped_column(Qty, default=Decimal("1"))
    unit_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("19"))

    @property
    def net_amount(self) -> Decimal:
        gross = Decimal(self.quantity) * Decimal(self.unit_price)
        disc = gross * Decimal(self.discount_pct or 0) / Decimal(100)
        return (gross - disc).quantize(Decimal("0.01"))

    @property
    def tax_amount(self) -> Decimal:
        return (self.net_amount * Decimal(self.tax_rate) / Decimal(100)).quantize(Decimal("0.01"))


class SalesOrderItem(_Item, Base):
    __tablename__ = "sales_order_items"
    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    order: Mapped[SalesOrder] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()


INVOICE_STATUS = {"open": "Offen", "partial": "Teilbezahlt", "paid": "Bezahlt", "cancelled": "Storniert"}


class Invoice(Versioned, Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    sales_order_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id"))
    invoice_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    due_date: Mapped[dt.date] = mapped_column(Date)
    net_amount: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money)
    gross_amount: Mapped[Decimal] = mapped_column(Money)
    paid_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(20), default="open")

    customer: Mapped[Customer] = relationship()
    sales_order: Mapped[SalesOrder | None] = relationship()

    @property
    def open_amount(self) -> Decimal:
        return Decimal(self.gross_amount) - Decimal(self.paid_amount)


# --------------------------------------------------------------------------
# Einkauf (MM)
# --------------------------------------------------------------------------

PURCHASE_STATUS = {
    "draft": "Entwurf",
    "ordered": "Bestellt",
    "partial": "Teilgeliefert",
    "received": "Geliefert",
    "cancelled": "Storniert",
}


class PurchaseOrder(Versioned, Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    order_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    expected_date: Mapped[dt.date | None] = mapped_column(Date)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")

    supplier: Mapped[Supplier] = relationship()
    warehouse: Mapped[Warehouse | None] = relationship()
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="PurchaseOrderItem.position")

    @property
    def net_total(self) -> Decimal:
        return sum((i.net_amount for i in self.items), Decimal("0"))

    @property
    def tax_total(self) -> Decimal:
        return sum((i.tax_amount for i in self.items), Decimal("0"))

    @property
    def gross_total(self) -> Decimal:
        return self.net_total + self.tax_total


class PurchaseOrderItem(_Item, Base):
    __tablename__ = "purchase_order_items"
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    received_qty: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()

    @property
    def open_qty(self) -> Decimal:
        return Decimal(self.quantity) - Decimal(self.received_qty or 0)


# --------------------------------------------------------------------------
# Finanzbuchhaltung (FI)
# --------------------------------------------------------------------------

ACCOUNT_TYPES = {
    "asset": "Aktiva",
    "liability": "Passiva",
    "equity": "Eigenkapital",
    "revenue": "Erlöse",
    "expense": "Aufwand",
}


class Account(Versioned, Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    account_type: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    entry_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    description: Mapped[str] = mapped_column(String(200), default="")
    reference: Mapped[str] = mapped_column(String(40), default="")
    username: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    debit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()


# --------------------------------------------------------------------------
# Autohaus: Fahrzeugkatalog, Bestand, Ausstellung, Bestellungen, Verkauf, Ankauf
# --------------------------------------------------------------------------
#
# Fahrzeugpreise werden – wie im Fahrzeughandel üblich – als Bruttobeträge
# (inkl. USt) geführt. Die Steueraufteilung erfolgt erst bei der Buchung.

VEHICLE_CATEGORIES = [
    "Kompakt", "Limousine", "Coupé", "Cabrio", "Sportwagen", "Supersportwagen",
    "SUV", "Geländewagen", "Muscle Car", "Klassiker", "Van", "Transporter",
    "Motorrad", "Sonstige",
]

VEHICLE_CONDITION = {"new": "Neu", "used": "Gebraucht"}

VEHICLE_STATUS = {
    "stock": "Lager",
    "showroom": "Ausstellung",
    "reserved": "Reserviert",
    "sold": "Verkauft",
    "removed": "Ausgebucht",
}
# Status, in denen ein Fahrzeug verkauft werden kann
VEHICLE_AVAILABLE = ("stock", "showroom", "reserved")

VEHICLE_SOURCE = {
    "order": "Bestellung",
    "used_purchase": "Ankauf",
    "trade_in": "Inzahlungnahme",
    "manual": "Manuell",
}

PAYMENT_ACCOUNTS = {"1200": "Überweisung (Bank)", "1000": "Bar (Kasse)"}

VEHICLE_ORDER_STATUS = {
    "ordered": "Bestellt",
    "arrived": "Eingetroffen",
    "delivered": "Ausgeliefert",
    "cancelled": "Storniert",
}

DEAL_STATUS = {"done": "Abgeschlossen", "cancelled": "Storniert"}

OFFER_STATUS = {"open": "Offen", "declined": "Abgelehnt", "expired": "Abgelaufen", "ordered": "Bestellt"}


class VehicleModel(Versioned, Base):
    """Fahrzeugkatalog: Modelle mit Standard-Einkaufs- und -Verkaufspreis."""
    __tablename__ = "vehicle_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    make: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40), default="")
    purchase_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    sales_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    default_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    default_supplier: Mapped[Supplier | None] = relationship()

    @property
    def name(self) -> str:
        return f"{self.make} {self.model}".strip()

    @property
    def margin(self) -> Decimal:
        return Decimal(self.sales_price or 0) - Decimal(self.purchase_price or 0)


class Vehicle(Versioned, Base):
    """Einzelnes Fahrzeug im Bestand (neu oder gebraucht)."""
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_models.id"))
    make: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40), default="")
    condition: Mapped[str] = mapped_column(String(10), default="new")
    status: Mapped[str] = mapped_column(String(20), default="stock")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    vin: Mapped[str] = mapped_column(String(40), default="")
    plate: Mapped[str] = mapped_column(String(20), default="")
    color: Mapped[str] = mapped_column(String(40), default="")
    year: Mapped[int] = mapped_column(Integer, default=0)
    mileage: Mapped[int] = mapped_column(Integer, default=0)
    purchase_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    list_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    purchase_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    # Differenzbesteuerung (§25a UStG), z. B. bei Ankauf von Privatpersonen
    margin_taxation: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[str] = mapped_column(String(40), default="")
    showroom_since: Mapped[dt.date | None] = mapped_column(Date)
    reserved_for_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    reserved_until: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")

    model_ref: Mapped[VehicleModel | None] = relationship()
    reserved_for: Mapped[Customer | None] = relationship()

    @property
    def name(self) -> str:
        return f"{self.make} {self.model}".strip()

    @property
    def label(self) -> str:
        extra = self.plate or self.vin
        return f"{self.number} {self.name}" + (f" ({extra})" if extra else "")

    @property
    def margin(self) -> Decimal:
        return Decimal(self.list_price or 0) - Decimal(self.purchase_price or 0)

    @property
    def days_in_stock(self) -> int:
        return (dt.date.today() - self.purchase_date).days if self.purchase_date else 0


class VehicleOrder(Versioned, Base):
    """Fahrzeugbestellung beim Hersteller/Lieferanten – auf Lager oder für einen Kunden."""
    __tablename__ = "vehicle_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    color: Mapped[str] = mapped_column(String(40), default="")
    purchase_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    sales_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    deposit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    order_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    expected_date: Mapped[dt.date | None] = mapped_column(Date)
    arrival_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="ordered")
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")

    model: Mapped[VehicleModel] = relationship()
    supplier: Mapped[Supplier | None] = relationship()
    customer: Mapped[Customer | None] = relationship()
    vehicle: Mapped[Vehicle | None] = relationship()


class VehicleSale(Versioned, Base):
    """Fahrzeugverkauf (Kaufvertrag), optional mit Inzahlungnahme."""
    __tablename__ = "vehicle_sales"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    sale_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    price: Mapped[Decimal] = mapped_column(Money)
    purchase_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))  # EK zum Verkaufszeitpunkt
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    trade_in_vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    trade_in_value: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    payment_account: Mapped[str] = mapped_column(String(10), default="1200")
    status: Mapped[str] = mapped_column(String(20), default="done")
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    salesperson: Mapped[str] = mapped_column(String(50), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Ab Version 1.3: Verkauf aus einer Bestellung mit bereits geleisteter Anzahlung
    order_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_orders.id"), nullable=True)
    deposit: Mapped[Decimal | None] = mapped_column(Money, nullable=True, default=Decimal("0"))

    vehicle: Mapped[Vehicle] = relationship(foreign_keys=[vehicle_id])
    trade_in_vehicle: Mapped[Vehicle | None] = relationship(foreign_keys=[trade_in_vehicle_id])
    customer: Mapped[Customer] = relationship()

    @property
    def margin(self) -> Decimal:
        return Decimal(self.price) - Decimal(self.purchase_price or 0)

    @property
    def payment_amount(self) -> Decimal:
        """Restzahlung bei Übergabe: Preis abzüglich Inzahlungnahme und Anzahlung."""
        return Decimal(self.price) - Decimal(self.trade_in_value or 0) - Decimal(self.deposit or 0)


class UsedPurchase(Versioned, Base):
    """Gebrauchtwagen-Ankauf (Ankaufvertrag)."""
    __tablename__ = "used_purchases"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))  # Verkäufer
    purchase_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    price: Mapped[Decimal] = mapped_column(Money)
    payment_account: Mapped[str] = mapped_column(String(10), default="1200")
    status: Mapped[str] = mapped_column(String(20), default="done")
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    buyer: Mapped[str] = mapped_column(String(50), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    vehicle: Mapped[Vehicle] = relationship()
    customer: Mapped[Customer] = relationship()


class Offer(Versioned, Base):
    """Angebot an einen Kunden – für ein Katalogmodell oder ein Fahrzeug aus dem Bestand.

    Ablauf: Angebot -> (Zahlung/Anzahlung) Bestellung -> (Übergabe, Restzahlung) Verkauf.
    """
    __tablename__ = "offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_models.id"))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    color: Mapped[str] = mapped_column(String(40), default="")
    price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    offer_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    valid_until: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")
    order_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_orders.id"))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")

    customer: Mapped[Customer] = relationship()
    model: Mapped[VehicleModel | None] = relationship()
    vehicle: Mapped[Vehicle | None] = relationship()
    order: Mapped[VehicleOrder | None] = relationship()

    @property
    def subject(self) -> str:
        if self.vehicle is not None:
            return self.vehicle.label
        return self.model.name if self.model is not None else ""
