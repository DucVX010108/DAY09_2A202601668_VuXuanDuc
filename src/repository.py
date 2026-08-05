"""Read-only CSV repository and indexes. Owned by Member 2."""

import csv
import os
from typing import Any

MODEL_NAME = "gpt-4o-mini"


class OlistRepository:
    """Load and index Olist CSV data once per batch run."""

    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = data_dir
        self.orders: dict[str, dict[str, str]] = {}
        self.customers: dict[str, dict[str, str]] = {}
        self.customer_unique_to_orders: dict[str, list[str]] = {}
        self.order_items: dict[str, list[dict[str, Any]]] = {}
        self.products: dict[str, dict[str, str]] = {}
        self.sellers: dict[str, dict[str, str]] = {}
        self.category_translation: dict[str, str] = {}
        self.order_payments: dict[str, list[dict[str, Any]]] = {}

        self._load_all()

    def _get_path(self, filename: str) -> str:
        return os.path.join(self.data_dir, filename)

    def _load_all(self) -> None:
        # Load customers
        cust_path = self._get_path("olist_customers_dataset.csv")
        if os.path.exists(cust_path):
            with open(cust_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cid = row["customer_id"]
                    self.customers[cid] = row

        # Load orders and index by order_id and customer_unique_id
        orders_path = self._get_path("olist_orders_dataset.csv")
        if os.path.exists(orders_path):
            with open(orders_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    oid = row["order_id"]
                    self.orders[oid] = row

                    cid = row.get("customer_id")
                    if cid in self.customers:
                        unique_id = self.customers[cid].get("customer_unique_id")
                        if unique_id:
                            if unique_id not in self.customer_unique_to_orders:
                                self.customer_unique_to_orders[unique_id] = []
                            self.customer_unique_to_orders[unique_id].append(oid)

        # Load order_items
        items_path = self._get_path("olist_order_items_dataset.csv")
        if os.path.exists(items_path):
            with open(items_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    oid = row["order_id"]
                    item_data = {
                        "order_id": oid,
                        "order_item_id": int(row["order_item_id"]),
                        "product_id": row["product_id"],
                        "seller_id": row["seller_id"],
                        "shipping_limit_date": row["shipping_limit_date"],
                        "price": float(row["price"]) if row["price"] else 0.0,
                        "freight_value": float(row["freight_value"]) if row["freight_value"] else 0.0,
                    }
                    if oid not in self.order_items:
                        self.order_items[oid] = []
                    self.order_items[oid].append(item_data)

        # Load products
        products_path = self._get_path("olist_products_dataset.csv")
        if os.path.exists(products_path):
            with open(products_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pid = row["product_id"]
                    self.products[pid] = row

        # Load sellers
        sellers_path = self._get_path("olist_sellers_dataset.csv")
        if os.path.exists(sellers_path):
            with open(sellers_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sid = row["seller_id"]
                    self.sellers[sid] = row

        # Load category translation
        trans_path = self._get_path("product_category_name_translation.csv")
        if os.path.exists(trans_path):
            with open(trans_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pt = row.get("product_category_name")
                    en = row.get("product_category_name_english")
                    if pt and en:
                        self.category_translation[pt.strip()] = en.strip()

        # Load order payments
        pay_path = self._get_path("olist_order_payments_dataset.csv")
        if os.path.exists(pay_path):
            with open(pay_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    oid = row["order_id"]
                    pay_data = {
                        "order_id": oid,
                        "payment_sequential": int(row["payment_sequential"]),
                        "payment_type": row["payment_type"],
                        "payment_installments": int(row["payment_installments"]),
                        "payment_value": float(row["payment_value"]) if row["payment_value"] else 0.0,
                    }
                    if oid not in self.order_payments:
                        self.order_payments[oid] = []
                    self.order_payments[oid].append(pay_data)

    def get_order(self, order_id: str) -> dict[str, str] | None:
        return self.orders.get(order_id)

    def get_customer(self, customer_id: str) -> dict[str, str] | None:
        return self.customers.get(customer_id)

    def get_customer_unique_id(self, customer_id: str) -> str | None:
        cust = self.get_customer(customer_id)
        return cust.get("customer_unique_id") if cust else None

    def get_related_orders(self, customer_unique_id: str, current_order_id: str, limit: int = 5) -> list[str]:
        all_orders = self.customer_unique_to_orders.get(customer_unique_id, [])
        related = [oid for oid in all_orders if oid != current_order_id]
        return related[:limit]

    def get_order_items(self, order_id: str) -> list[dict[str, Any]]:
        return self.order_items.get(order_id, [])

    def get_product(self, product_id: str) -> dict[str, str] | None:
        return self.products.get(product_id)

    def get_seller(self, seller_id: str) -> dict[str, str] | None:
        return self.sellers.get(seller_id)

    def translate_category(self, category_name_pt: str | None) -> str | None:
        if not category_name_pt:
            return None
        return self.category_translation.get(category_name_pt, category_name_pt)
