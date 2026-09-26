from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
import sqlite3, json, hashlib, hmac, os, secrets, re, io, urllib.request, urllib.error, mimetypes, shutil, base64
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote_plus, urlparse
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
import qrcode

BASE_DIR = Path(__file__).resolve().parent
SEED_PATH = BASE_DIR / "seed_products.json"

# Deployment-safe persistent storage.
# Locally this defaults to the project folder. On Railway, attaching a Volume
# automatically provides RAILWAY_VOLUME_MOUNT_PATH; V16.3 uses it without any
# code changes. DATABASE_PATH/DATA_DIR can still be overridden explicitly.
_volume_dir = os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
DATA_DIR = Path(_volume_dir).expanduser().resolve() if _volume_dir else BASE_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(DATA_DIR / "store.db"))).expanduser().resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
REAL_IMAGE_DIR = DATA_DIR / "real-product-images"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
REAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# If a deployment contains an existing local store.db, copy it into the
# persistent volume only on the very first start. Existing persistent data is
# never overwritten by a redeploy.
_bundled_db = BASE_DIR / "store.db"
if DB_PATH != _bundled_db and not DB_PATH.exists() and _bundled_db.exists():
    shutil.copy2(_bundled_db, DB_PATH)

ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").strip().lower()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
if not SESSION_SECRET:
    SESSION_SECRET = "maneendra-local-development-secret" if ENVIRONMENT != "production" else secrets.token_urlsafe(48)

app = FastAPI(title="Maneendra General Stores", version="16.11")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=ENVIRONMENT == "production",
)
# Writable files live outside the read-only application bundle in production.
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/real-product-images", StaticFiles(directory=REAL_IMAGE_DIR), name="real-product-images")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def prevent_old_frontend_cache(request: Request, call_next):
    response = await call_next(request)
    # The project is frequently upgraded in-place during local development.
    # Never let the browser keep an older app.js/styles.css after an upgrade.
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/api/version")
def app_version():
    return {"version": "16.11", "features": ["database-cart", "persistent-customer-data", "order-history-audit", "inventory-audit", "customer-cancel", "returns", "request-photo-attachments", "delivery-otp", "cod-amount-with-otp", "admin-order-edit", "revised-total", "upi-payment-confirmation", "payment-history", "expanded-kirana-catalog", "database-explorer", "real-product-image-sync", "cod-to-upi-at-delivery", "upi-on-delivery", "admin-payment-center", "payment-method-history", "saved-addresses", "buy-again", "printable-invoices", "final-pdf-invoice", "delivery-invoice-email", "invoice-database-copy", "invoice-resend", "low-stock-reorder-level", "mrp-profit-fields", "duplicate-utr-protection", "database-backup", "audit-log", "login-rate-limit", "free-delivery", "modern-ui-v16", "final-polish-v16-1", "deployment-ready-v16-3", "brevo-https-email-api", "persistent-volume-storage", "healthcheck", "revised-return-invoice-email"]}


@app.get("/health", include_in_schema=False)
def healthcheck():
    try:
        con = sqlite3.connect(DB_PATH, timeout=3)
        con.execute("SELECT 1").fetchone()
        con.close()
        return {"status": "ok", "version": "16.11", "database": "ready"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=10000")
    # WAL gives SQLite much better read/write behaviour for a small web shop.
    # It is safe on the persistent filesystem used by this single-service app.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000).hex()
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def ensure_column(con, table: str, column: str, definition: str):
    cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    con = db()
    con.executescript(
        '''
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          full_name TEXT NOT NULL,
          email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'customer',
          phone TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS products(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          category TEXT NOT NULL,
          unit TEXT NOT NULL,
          price REAL NOT NULL,
          stock INTEGER NOT NULL DEFAULT 0,
          image TEXT NOT NULL,
          description TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          customer_name TEXT NOT NULL,
          phone TEXT NOT NULL,
          address TEXT NOT NULL,
          payment_method TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Placed',
          total REAL NOT NULL,
          upi_reference TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS order_items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          product_id INTEGER NOT NULL,
          product_name TEXT NOT NULL,
          unit TEXT NOT NULL,
          price REAL NOT NULL,
          quantity INTEGER NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS store_settings(
          id INTEGER PRIMARY KEY CHECK (id=1),
          phone TEXT NOT NULL,
          whatsapp TEXT NOT NULL,
          email TEXT NOT NULL,
          address TEXT NOT NULL,
          map_url TEXT NOT NULL,
          hours TEXT NOT NULL,
          upi_id TEXT NOT NULL DEFAULT '',
          upi_payee_name TEXT NOT NULL DEFAULT 'Maneendra General Stores',
          smtp_host TEXT NOT NULL DEFAULT 'smtp.gmail.com',
          smtp_port INTEGER NOT NULL DEFAULT 587,
          smtp_username TEXT NOT NULL DEFAULT '',
          smtp_app_password TEXT NOT NULL DEFAULT '',
          smtp_from_name TEXT NOT NULL DEFAULT 'Maneendra General Stores',
          email_notifications_enabled INTEGER NOT NULL DEFAULT 1,
          brevo_sender_email TEXT NOT NULL DEFAULT '',
          brevo_sender_name TEXT NOT NULL DEFAULT 'Maneendra General Stores'
        );
        CREATE TABLE IF NOT EXISTS email_logs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER,
          user_id INTEGER,
          to_email TEXT NOT NULL,
          subject TEXT NOT NULL,
          status TEXT NOT NULL,
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS return_requests(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          order_item_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'Pending',
          refund_amount REAL NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          resolved_at TEXT NOT NULL DEFAULT '',
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(order_item_id) REFERENCES order_items(id),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS carts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER,
          cart_token TEXT UNIQUE NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS cart_items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          cart_id INTEGER NOT NULL,
          product_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(cart_id,product_id),
          FOREIGN KEY(cart_id) REFERENCES carts(id) ON DELETE CASCADE,
          FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS order_status_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          changed_by_user_id INTEGER,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(changed_by_user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS order_adjustments(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          order_item_id INTEGER NOT NULL,
          product_id INTEGER NOT NULL,
          old_quantity INTEGER NOT NULL,
          new_quantity INTEGER NOT NULL,
          amount_difference REAL NOT NULL DEFAULT 0,
          reason TEXT NOT NULL DEFAULT '',
          changed_by_user_id INTEGER,
          created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(order_item_id) REFERENCES order_items(id),
          FOREIGN KEY(product_id) REFERENCES products(id),
          FOREIGN KEY(changed_by_user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS inventory_movements(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          product_id INTEGER NOT NULL,
          order_id INTEGER,
          movement_type TEXT NOT NULL,
          quantity INTEGER NOT NULL,
          stock_after INTEGER NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(product_id) REFERENCES products(id),
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS payment_records(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER UNIQUE NOT NULL,
          payment_method TEXT NOT NULL,
          original_amount REAL NOT NULL,
          current_amount REAL NOT NULL,
          upi_reference TEXT NOT NULL DEFAULT '',
          payment_status TEXT NOT NULL DEFAULT 'Pending',
          refund_due REAL NOT NULL DEFAULT 0,
          refund_status TEXT NOT NULL DEFAULT 'Not Required',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS cancellation_requests(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER UNIQUE NOT NULL,
          user_id INTEGER NOT NULL,
          reason TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Cancelled',
          created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS request_attachments(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          request_type TEXT NOT NULL,
          request_id INTEGER NOT NULL,
          filename TEXT NOT NULL,
          content_type TEXT NOT NULL,
          file_size INTEGER NOT NULL,
          file_data BLOB NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_status_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          payment_status TEXT NOT NULL,
          changed_by_user_id INTEGER,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(changed_by_user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS payment_method_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          old_method TEXT NOT NULL,
          new_method TEXT NOT NULL,
          changed_by_user_id INTEGER,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(changed_by_user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS customer_addresses(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          label TEXT NOT NULL DEFAULT 'Home',
          recipient_name TEXT NOT NULL,
          phone TEXT NOT NULL,
          address TEXT NOT NULL,
          is_default INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_logs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER,
          action TEXT NOT NULL,
          entity_type TEXT NOT NULL DEFAULT '',
          entity_id TEXT NOT NULL DEFAULT '',
          details TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS invoice_records(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER UNIQUE NOT NULL,
          user_id INTEGER NOT NULL,
          invoice_number TEXT NOT NULL,
          file_name TEXT NOT NULL,
          pdf_data BLOB NOT NULL,
          generated_at TEXT NOT NULL,
          emailed_at TEXT NOT NULL DEFAULT '',
          email_status TEXT NOT NULL DEFAULT 'Not sent',
          FOREIGN KEY(order_id) REFERENCES orders(id),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS login_security(
          email TEXT PRIMARY KEY,
          fail_count INTEGER NOT NULL DEFAULT 0,
          first_failed_at TEXT NOT NULL DEFAULT '',
          locked_until TEXT NOT NULL DEFAULT ''
        );
        '''
    )

    # Safe migrations for older databases copied from V6 or earlier.
    ensure_column(con, "users", "phone", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "users", "address", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "users", "updated_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "orders", "upi_reference", "TEXT DEFAULT ''")
    ensure_column(con, "orders", "original_total", "REAL NOT NULL DEFAULT 0")
    ensure_column(con, "orders", "adjustment_amount", "REAL NOT NULL DEFAULT 0")
    ensure_column(con, "orders", "delivery_otp_hash", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "orders", "delivery_otp_expires_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "orders", "delivery_otp_sent_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "orders", "delivered_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "orders", "cancelled_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "order_items", "original_quantity", "INTEGER NOT NULL DEFAULT 0")
    con.execute("UPDATE orders SET original_total=total WHERE COALESCE(original_total,0)=0")
    con.execute("UPDATE order_items SET original_quantity=quantity WHERE COALESCE(original_quantity,0)=0")
    ensure_column(con, "store_settings", "upi_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "store_settings", "upi_payee_name", "TEXT NOT NULL DEFAULT 'Maneendra General Stores'")
    ensure_column(con, "store_settings", "smtp_host", "TEXT NOT NULL DEFAULT 'smtp.gmail.com'")
    ensure_column(con, "store_settings", "smtp_port", "INTEGER NOT NULL DEFAULT 587")
    ensure_column(con, "store_settings", "smtp_username", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "store_settings", "smtp_app_password", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "store_settings", "smtp_from_name", "TEXT NOT NULL DEFAULT 'Maneendra General Stores'")
    ensure_column(con, "store_settings", "email_notifications_enabled", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(con, "store_settings", "brevo_sender_email", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "store_settings", "brevo_sender_name", "TEXT NOT NULL DEFAULT 'Maneendra General Stores'")
    ensure_column(con, "products", "image_source_url", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "products", "image_source_provider", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "products", "image_cached_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "payment_records", "confirmed_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "payment_records", "confirmed_by_user_id", "INTEGER")
    ensure_column(con, "payment_records", "payment_note", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "products", "mrp", "REAL NOT NULL DEFAULT 0")
    ensure_column(con, "products", "purchase_price", "REAL NOT NULL DEFAULT 0")
    ensure_column(con, "products", "reorder_level", "INTEGER NOT NULL DEFAULT 5")
    ensure_column(con, "products", "sku", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "products", "barcode", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "return_requests", "admin_note", "TEXT NOT NULL DEFAULT ''")
    ensure_column(con, "return_requests", "refund_status", "TEXT NOT NULL DEFAULT 'Not Required'")
    ensure_column(con, "invoice_records", "revision_no", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(con, "invoice_records", "revision_reason", "TEXT NOT NULL DEFAULT ''")
    con.execute("UPDATE products SET mrp=price WHERE COALESCE(mrp,0)<=0")

    admin = con.execute("SELECT id FROM users WHERE email=?", ("admin@maneendrastores.local",)).fetchone()
    if not admin:
        now = datetime.now().isoformat()
        con.execute(
            "INSERT INTO users(full_name,email,password_hash,role,phone,address,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("Maneendra Admin", "admin@maneendrastores.local", hash_password("Admin@123"), "admin", "", "", now, now),
        )

    settings = con.execute("SELECT id FROM store_settings WHERE id=1").fetchone()
    if not settings:
        con.execute(
            """INSERT INTO store_settings(
               id,phone,whatsapp,email,address,map_url,hours,upi_id,upi_payee_name,
               smtp_host,smtp_port,smtp_username,smtp_app_password,smtp_from_name,email_notifications_enabled,
               brevo_sender_email,brevo_sender_name
               ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "9966454521",
                "9966454521",
                "www.maneendrageneralstores@gmail.com",
                "Maneendra General Stores, 1, Gampalagudem - Tiruvuru Rd, Kanumuru, Gampalagudem, Andhra Pradesh 521403",
                "https://maps.app.goo.gl/smLHxwqRtPz4u76A7?g_st=ac",
                "7:00 AM – 10:00 PM",
                "",
                "Maneendra General Stores",
                "smtp.gmail.com",
                587,
                "www.maneendrageneralstores@gmail.com",
                "",
                "Maneendra General Stores",
                1,
                "www.maneendrageneralstores@gmail.com",
                "Maneendra General Stores",
            ),
        )
    else:
        # Apply the email change requested by the store owner to an older V6 database.
        con.execute(
            "UPDATE store_settings SET email=? WHERE email=?",
            ("www.maneendrageneralstores@gmail.com", "maneendrageneralstores@gmail.com"),
        )
        row = con.execute("SELECT smtp_username FROM store_settings WHERE id=1").fetchone()
        if row and not (row["smtp_username"] or "").strip():
            con.execute(
                "UPDATE store_settings SET smtp_username=? WHERE id=1",
                ("www.maneendrageneralstores@gmail.com",),
            )
        con.execute("UPDATE store_settings SET brevo_sender_email=email WHERE TRIM(COALESCE(brevo_sender_email,''))=''")
        con.execute("UPDATE store_settings SET brevo_sender_name='Maneendra General Stores' WHERE TRIM(COALESCE(brevo_sender_name,''))=''")

    # Create one default saved address for existing customers who already have profile delivery details.
    for u in con.execute("SELECT id,full_name,phone,address FROM users WHERE role='customer' AND TRIM(COALESCE(address,''))<>''").fetchall():
        if not con.execute("SELECT 1 FROM customer_addresses WHERE user_id=? LIMIT 1", (u["id"],)).fetchone():
            now=datetime.now().isoformat()
            con.execute("INSERT INTO customer_addresses(user_id,label,recipient_name,phone,address,is_default,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (u["id"],"Home",u["full_name"],u["phone"] or '',u["address"],now,now))

    # Seed synchronization: upgrades add newly supplied kirana products without deleting
    # or overwriting products already edited by the store owner.
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for p in items:
        existing = con.execute(
            "SELECT id FROM products WHERE lower(name)=lower(?) AND lower(category)=lower(?)",
            (p["name"], p["category"]),
        ).fetchone()
        if not existing:
            cur = con.execute(
                "INSERT INTO products(name,category,unit,price,stock,image,description,active,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (p["name"], p["category"], p["unit"], p["price"], p["stock"], p["image"], p["description"], 1, datetime.now().isoformat()),
            )
            if str(p.get("image", "")).startswith("http"):
                con.execute("UPDATE products SET image_source_url=?,image_source_provider='Seed web image' WHERE id=?", (p["image"], cur.lastrowid))

    # Ensure newly seeded V14 products have a sensible MRP default.
    con.execute("UPDATE products SET mrp=price WHERE COALESCE(mrp,0)<=0")

    # V10 removes one obsolete duplicated seed entry from older V9 databases, but only
    # when it still has the original duplicated web image (so a store-owner-edited product is untouched).
    con.execute("""UPDATE products SET active=0
                   WHERE name='Fortune Double Roasted Suji / Rava'
                     AND image='https://instamart-media-assets.swiggy.com/swiggy/image/upload/fl_lossy%2Cf_auto%2Cq_auto%2Cw_1200%2Ch_630/NI_CATALOG/IMAGES/ciw/2025/12/16/cddcff78-b5a0-46ee-bca6-ddeabcff269d_0PLW60VA9S_MN_15122025.png'""")

    # Preserve the original remote source for already seeded real branded images.
    con.execute("""UPDATE products SET image_source_url=image, image_source_provider=CASE WHEN image_source_provider='' THEN 'Seed web image' ELSE image_source_provider END
                   WHERE image LIKE 'http%' AND COALESCE(image_source_url,'')=''""")

    # Backfill audit/payment rows when upgrading an older V8/V8.1 database.
    for o in con.execute("SELECT * FROM orders").fetchall():
        if not con.execute("SELECT 1 FROM order_status_history WHERE order_id=? LIMIT 1",(o["id"],)).fetchone():
            con.execute("INSERT INTO order_status_history(order_id,status,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?)",(o["id"],o["status"],None,"Migrated current order status",o["created_at"]))
        update_payment_record(con,o["id"])
        if not con.execute("SELECT 1 FROM payment_status_history WHERE order_id=? LIMIT 1",(o["id"],)).fetchone():
            pr=con.execute("SELECT payment_status,updated_at FROM payment_records WHERE order_id=?",(o["id"],)).fetchone()
            if pr:
                con.execute("INSERT INTO payment_status_history(order_id,payment_status,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?)",(o["id"],pr["payment_status"],None,"Migrated current payment status",pr["updated_at"] or o["created_at"]))
    if con.execute("SELECT COUNT(*) c FROM inventory_movements").fetchone()["c"]==0:
        now=datetime.now().isoformat()
        for pr in con.execute("SELECT id,stock FROM products").fetchall():
            con.execute("INSERT INTO inventory_movements(product_id,order_id,movement_type,quantity,stock_after,note,created_at) VALUES(?,NULL,'MIGRATION_SNAPSHOT',0,?,'Initial V10 stock snapshot',?)",(pr["id"],pr["stock"],now))
    con.commit()
    con.close()



class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=6, max_length=100)


class LoginIn(BaseModel):
    email: str = Field(min_length=5, max_length=120)
    password: str


class ProfileIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=120)
    phone: str = Field(default="", max_length=20)
    address: str = Field(default="", max_length=400)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=100)
    new_password: str = Field(min_length=6, max_length=100)


class CartItemIn(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=50)


class CartQuantityIn(BaseModel):
    quantity: int = Field(ge=0, le=50)


class OrderIn(BaseModel):
    customer_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=8, max_length=20)
    address: str = Field(min_length=8, max_length=400)
    payment_method: str = "Cash on Delivery"
    upi_reference: str = ""
    items: list[CartItemIn] = []  # V9 checkout reads the persistent database cart; kept for backward compatibility.


class ReturnRequestIn(BaseModel):
    order_item_id: int
    quantity: int = Field(ge=1, le=50)
    reason: str = Field(min_length=3, max_length=300)


class AdminOrderItemEdit(BaseModel):
    order_item_id: int
    quantity: int = Field(ge=0, le=50)


class AdminOrderEditIn(BaseModel):
    items: list[AdminOrderItemEdit]


class DeliveryOtpIn(BaseModel):
    otp: str = Field(min_length=4, max_length=10)


class ReturnResolutionIn(BaseModel):
    status: str


class RefundStatusIn(BaseModel):
    status: str
    note: str = Field(default="", max_length=300)


class PaymentStatusIn(BaseModel):
    status: str
    note: str = Field(default="", max_length=300)


class SwitchToUpiIn(BaseModel):
    upi_reference: str = Field(min_length=4, max_length=120)


class AddressIn(BaseModel):
    label: str = Field(default="Home", min_length=1, max_length=40)
    recipient_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=8, max_length=20)
    address: str = Field(min_length=8, max_length=400)
    is_default: bool = False


class ProductIn(BaseModel):
    name: str
    category: str
    unit: str
    price: float = Field(gt=0)
    stock: int = Field(ge=0)
    mrp: float = Field(default=0, ge=0)
    purchase_price: float = Field(default=0, ge=0)
    reorder_level: int = Field(default=5, ge=0, le=10000)
    sku: str = Field(default="", max_length=80)
    barcode: str = Field(default="", max_length=80)
    image: str = "/static/images/products/default-product.png"
    description: str = ""
    active: bool = True


class StoreSettingsIn(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    whatsapp: str = Field(min_length=8, max_length=20)
    email: str = Field(min_length=5, max_length=120)
    address: str = Field(min_length=5, max_length=300)
    map_url: str = Field(min_length=5, max_length=500)
    hours: str = Field(min_length=3, max_length=80)
    upi_id: str = Field(default="", max_length=120)
    upi_payee_name: str = Field(default="Maneendra General Stores", min_length=2, max_length=100)


class MailSettingsIn(BaseModel):
    sender_email: str = Field(default="", min_length=5, max_length=120)
    sender_name: str = Field(default="Maneendra General Stores", min_length=2, max_length=100)
    email_notifications_enabled: bool = True


class TestEmailIn(BaseModel):
    to_email: str = Field(min_length=5, max_length=120)


# REAL_IMAGE_DIR is defined in the persistent DATA_DIR near the top of the file.
IMAGE_UA = "ManeendraGeneralStores/16.11 (contact: www.maneendrageneralstores@gmail.com)"


def _safe_table_name(con, table_name: str) -> str:
    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=? AND name NOT LIKE 'sqlite_%'", (table_name,)).fetchone()
    if not row:
        raise HTTPException(404, "Database table not found")
    return row["name"]


def _masked_db_value(column: str, value):
    sensitive = {"password_hash", "smtp_app_password", "delivery_otp_hash"}
    if column in sensitive and value:
        return "••••••••"
    if column in {"file_data", "pdf_data"} and value is not None:
        try:
            return f"<BLOB {len(value)} bytes>"
        except Exception:
            return "<BLOB>"
    return value


def _image_search_hosts(category: str):
    if category in {"Personal Care", "Baby Care"}:
        return [
            ("https://world.openbeautyfacts.org", "Open Beauty Facts"),
            ("https://world.openproductsfacts.org", "Open Products Facts"),
            ("https://world.openfoodfacts.org", "Open Food Facts"),
        ]
    if category in {"Home Cleaning", "Household Essentials", "Pooja & Devotional", "Stationery"}:
        return [
            ("https://world.openproductsfacts.org", "Open Products Facts"),
            ("https://world.openbeautyfacts.org", "Open Beauty Facts"),
            ("https://world.openfoodfacts.org", "Open Food Facts"),
        ]
    return [
        ("https://world.openfoodfacts.org", "Open Food Facts"),
        ("https://world.openproductsfacts.org", "Open Products Facts"),
    ]


def _remote_json(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers={"User-Agent": IMAGE_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if int(getattr(resp, "status", 200)) >= 400:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.loads(resp.read().decode("utf-8", "replace"))


def _find_real_image_url(product_name: str, category: str, used_urls: set[str]):
    query = quote_plus(product_name)
    for host, provider in _image_search_hosts(category):
        # Product Opener's legacy search endpoint supports full-text search. We keep the result small
        # and only use public image URLs returned by the open product databases.
        url = f"{host}/cgi/search.pl?search_terms={query}&search_simple=1&action=process&json=1&page_size=12"
        try:
            data = _remote_json(url)
        except Exception:
            continue
        for item in data.get("products", []) or []:
            image_url = item.get("image_front_url") or item.get("image_url") or item.get("image_front_small_url")
            if image_url and str(image_url).startswith("http") and image_url not in used_urls:
                label = item.get("product_name") or item.get("generic_name") or product_name
                return image_url, provider, label
    return None, None, None


def _download_product_image(url: str, product_id: int):
    req = urllib.request.Request(url, headers={"User-Agent": IMAGE_UA, "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            raise RuntimeError("The selected web URL did not return an image")
        raw = resp.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("Product image is larger than 8 MB")
    ext = mimetypes.guess_extension(content_type) or Path(urlparse(url).path).suffix or ".jpg"
    if ext.lower() in {".jpe", ".jpeg"}:
        ext = ".jpg"
    if ext.lower() not in {".jpg", ".png", ".webp", ".gif", ".avif"}:
        ext = ".jpg"
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    filename = f"product-{product_id}-{digest}{ext.lower()}"
    path = REAL_IMAGE_DIR / filename
    path.write_bytes(raw)
    return f"/real-product-images/{filename}"


def sync_real_image_for_product(product_id: int, force_search: bool = False):
    con = db()
    product = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        con.close()
        raise HTTPException(404, "Product not found")
    used = {r["image_source_url"] for r in con.execute("SELECT image_source_url FROM products WHERE id<>? AND COALESCE(image_source_url,'')<>''", (product_id,)).fetchall()}
    source_url = (product["image_source_url"] or "").strip()
    provider = (product["image_source_provider"] or "").strip()
    current = (product["image"] or "").strip()
    if not force_search and source_url.startswith("http"):
        candidate = source_url
        provider = provider or "Existing web source"
    elif not force_search and current.startswith("http"):
        candidate = current
        provider = provider or "Existing web source"
    else:
        candidate, provider, _ = _find_real_image_url(product["name"], product["category"], used)
    if not candidate:
        con.close()
        return {"status": "not_found", "product_id": product_id, "name": product["name"], "message": "No matching real product photo was found automatically"}
    try:
        local_url = _download_product_image(candidate, product_id)
    except Exception as exc:
        con.close()
        return {"status": "failed", "product_id": product_id, "name": product["name"], "message": str(exc), "source_url": candidate}
    now = datetime.now().isoformat()
    con.execute("UPDATE products SET image=?,image_source_url=?,image_source_provider=?,image_cached_at=? WHERE id=?", (local_url, candidate, provider or "Web product database", now, product_id))
    con.commit()
    con.close()
    return {"status": "updated", "product_id": product_id, "name": product["name"], "image": local_url, "source_url": candidate, "provider": provider}


def current_user(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(401, "Please login")
    con = db()
    row = con.execute(
        "SELECT id,full_name,email,role,phone,address,created_at,updated_at FROM users WHERE id=?",
        (uid,),
    ).fetchone()
    con.close()
    if not row:
        # A browser can keep an old signed session cookie after store.db is
        # replaced/copied from another version. Clear the stale user id so
        # guest endpoints (especially the cart) do not try to reference a
        # user row that no longer exists.
        request.session.pop("user_id", None)
        raise HTTPException(401, "Session expired")
    return dict(row)


def admin_user(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def _cart_token(request: Request):
    token = request.session.get("cart_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["cart_token"] = token
    return token


def get_or_create_cart(con, request: Request, user_id=None):
    """Return a DB-backed cart. Guest carts are keyed by a signed-session token.
    When a customer logs in, the guest cart is merged into the customer's persistent cart.
    """
    token = _cart_token(request)

    # Defensive session repair:
    # If the browser still carries a user_id from an older/replaced database,
    # treating it as a valid customer causes a FOREIGN KEY failure when a cart
    # is created. Validate the user first; if it no longer exists, downgrade
    # the request to a guest cart and remove the stale login from the session.
    if user_id:
        user_exists = con.execute("SELECT 1 FROM users WHERE id=? LIMIT 1", (user_id,)).fetchone()
        if not user_exists:
            request.session.pop("user_id", None)
            user_id = None

    if user_id:
        user_cart = con.execute("SELECT * FROM carts WHERE user_id=? ORDER BY id LIMIT 1", (user_id,)).fetchone()
        guest_cart = con.execute("SELECT * FROM carts WHERE cart_token=? AND user_id IS NULL", (token,)).fetchone()
        now = datetime.now().isoformat()
        if user_cart:
            if guest_cart and guest_cart["id"] != user_cart["id"]:
                rows = con.execute("SELECT product_id,quantity FROM cart_items WHERE cart_id=?", (guest_cart["id"],)).fetchall()
                for r in rows:
                    current = con.execute("SELECT quantity FROM cart_items WHERE cart_id=? AND product_id=?", (user_cart["id"], r["product_id"])).fetchone()
                    if current:
                        con.execute("UPDATE cart_items SET quantity=?,updated_at=? WHERE cart_id=? AND product_id=?", (min(50, int(current["quantity"])+int(r["quantity"])), now, user_cart["id"], r["product_id"]))
                    else:
                        con.execute("INSERT INTO cart_items(cart_id,product_id,quantity,created_at,updated_at) VALUES(?,?,?,?,?)", (user_cart["id"],r["product_id"],r["quantity"],now,now))
                con.execute("DELETE FROM carts WHERE id=?", (guest_cart["id"],))
            request.session["cart_token"] = user_cart["cart_token"]
            con.execute("UPDATE carts SET updated_at=? WHERE id=?", (now,user_cart["id"]))
            return con.execute("SELECT * FROM carts WHERE id=?", (user_cart["id"],)).fetchone()
        if guest_cart:
            con.execute("UPDATE carts SET user_id=?,updated_at=? WHERE id=?", (user_id,now,guest_cart["id"]))
            return con.execute("SELECT * FROM carts WHERE id=?", (guest_cart["id"],)).fetchone()
        cur=con.execute("INSERT INTO carts(user_id,cart_token,created_at,updated_at) VALUES(?,?,?,?)",(user_id,token,now,now))
        return con.execute("SELECT * FROM carts WHERE id=?",(cur.lastrowid,)).fetchone()
    row=con.execute("SELECT * FROM carts WHERE cart_token=?",(token,)).fetchone()
    if row:
        return row
    now=datetime.now().isoformat()
    cur=con.execute("INSERT INTO carts(user_id,cart_token,created_at,updated_at) VALUES(NULL,?,?,?)",(token,now,now))
    return con.execute("SELECT * FROM carts WHERE id=?",(cur.lastrowid,)).fetchone()


def cart_payload(con, cart_id: int):
    rows=con.execute("""SELECT ci.product_id,ci.quantity,p.name,p.category,p.unit,p.price,p.stock,p.image,p.active
                        FROM cart_items ci JOIN products p ON p.id=ci.product_id
                        WHERE ci.cart_id=? ORDER BY ci.id""",(cart_id,)).fetchall()
    items=[]; total=0.0; count=0
    for r in rows:
        d=dict(r); d["subtotal"]=round(float(r["price"])*int(r["quantity"]),2)
        items.append(d); total+=d["subtotal"]; count+=int(r["quantity"])
    return {"items":items,"total":round(total,2),"count":count}


def record_status(con, order_id: int, status: str, changed_by_user_id=None, note=""):
    con.execute("INSERT INTO order_status_history(order_id,status,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?)",(order_id,status,changed_by_user_id,note,datetime.now().isoformat()))


def record_inventory(con, product_id: int, order_id, movement_type: str, quantity: int, note=""):
    stock=con.execute("SELECT stock FROM products WHERE id=?",(product_id,)).fetchone()
    if stock:
        con.execute("INSERT INTO inventory_movements(product_id,order_id,movement_type,quantity,stock_after,note,created_at) VALUES(?,?,?,?,?,?,?)",(product_id,order_id,movement_type,quantity,int(stock["stock"]),note,datetime.now().isoformat()))


def update_payment_record(con, order_id: int):
    o=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: return
    fin=order_financials(con,o)
    now=datetime.now().isoformat()
    existing=con.execute("SELECT * FROM payment_records WHERE order_id=?",(order_id,)).fetchone()
    method=o["payment_method"]
    if method in ("UPI Payment", "UPI on Delivery"):
        if existing and existing["payment_status"]=="Payment Confirmed":
            status="Payment Confirmed"
        elif (o["upi_reference"] or "").strip():
            status="Pending Verification"
        elif method=="UPI on Delivery":
            status="UPI Due on Delivery"
        else:
            status="Pending Verification"
    else:
        status="Cash Collected" if o["status"]=="Delivered" else "Pay on Delivery"
    refund=max(0.0,float(fin["refund_due"]))
    refund_status="Pending" if refund>0 else "Not Required"
    if existing:
        con.execute("""UPDATE payment_records SET payment_method=?,current_amount=?,refund_due=?,refund_status=?,payment_status=?,upi_reference=?,updated_at=? WHERE order_id=?""",(method,fin["revised_total"],refund,refund_status,status,o["upi_reference"] or '',now,order_id))
    else:
        con.execute("""INSERT INTO payment_records(order_id,payment_method,original_amount,current_amount,upi_reference,payment_status,refund_due,refund_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",(order_id,method,fin["original_total"],fin["revised_total"],o["upi_reference"] or '',status,refund,refund_status,now,now))


def audit(con, user_id, action: str, entity_type: str = "", entity_id = "", details: str = ""):
    try:
        con.execute("INSERT INTO audit_logs(user_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)", (user_id, action, entity_type, str(entity_id or ''), details[:2000], datetime.now().isoformat()))
    except Exception:
        pass


def log_email(order_id, user_id, to_email, subject, status, error=""):
    try:
        con = db()
        con.execute(
            "INSERT INTO email_logs(order_id,user_id,to_email,subject,status,error,created_at) VALUES(?,?,?,?,?,?,?)",
            (order_id, user_id, to_email, subject, status, error[:1000], datetime.now().isoformat()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def email_settings_snapshot():
    con = db()
    row = con.execute(
        """SELECT email,phone,email_notifications_enabled,brevo_sender_email,brevo_sender_name
           FROM store_settings WHERE id=1"""
    ).fetchone()
    con.close()
    return dict(row) if row else None


def deliver_email(to_email: str, subject: str, plain_text: str, html: str = "", order_id=None, user_id=None, attachments=None):
    """Send transactional email through Brevo's HTTPS API.

    Railway Free/Trial/Hobby environments can use this because it is normal HTTPS,
    not SMTP. The Brevo API key is intentionally read only from the environment
    so it is not stored in SQLite or exposed through the admin UI.
    """
    settings = email_settings_snapshot()
    if not settings or not settings.get("email_notifications_enabled"):
        log_email(order_id, user_id, to_email, subject, "Skipped", "Email notifications are disabled")
        return False

    api_key = (os.environ.get("BREVO_API_KEY") or "").strip()
    sender_email = (os.environ.get("BREVO_SENDER_EMAIL") or settings.get("brevo_sender_email") or settings.get("email") or "").strip()
    sender_name = (os.environ.get("BREVO_SENDER_NAME") or settings.get("brevo_sender_name") or "Maneendra General Stores").strip()
    if not api_key:
        log_email(order_id, user_id, to_email, subject, "Skipped", "BREVO_API_KEY is not configured")
        return False
    if not sender_email:
        log_email(order_id, user_id, to_email, subject, "Skipped", "Brevo sender email is not configured")
        return False

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
    }
    if html:
        payload["htmlContent"] = html
    else:
        payload["textContent"] = plain_text
    if settings.get("email"):
        payload["replyTo"] = {"email": settings["email"], "name": "Maneendra General Stores"}

    encoded_attachments = []
    for attachment in attachments or []:
        filename, _content_type, attachment_bytes = attachment
        encoded_attachments.append({
            "name": filename,
            "content": base64.b64encode(attachment_bytes).decode("ascii"),
        })
    if encoded_attachments:
        payload["attachment"] = encoded_attachments

    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read().decode("utf-8", errors="replace")
            if response.status not in (200, 201, 202):
                raise RuntimeError(f"Brevo returned HTTP {response.status}: {body[:500]}")
        log_email(order_id, user_id, to_email, subject, "Sent", "")
        return True
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        log_email(order_id, user_id, to_email, subject, "Failed", f"Brevo HTTP {exc.code}: {detail}")
        return False
    except Exception as exc:
        log_email(order_id, user_id, to_email, subject, "Failed", str(exc))
        return False


async def save_request_attachments(con, request_type: str, request_id: int, files):
    files = files or []
    real_files = [f for f in files if getattr(f, "filename", "")]
    if len(real_files) > 3:
        raise HTTPException(400, "You can attach up to 3 photos")
    allowed = {"image/jpeg", "image/png", "image/webp"}
    ids=[]
    for f in real_files:
        if f.content_type not in allowed:
            raise HTTPException(400, "Attachments must be JPG, PNG or WEBP images")
        content = await f.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(400, "Each attachment must be 5 MB or smaller")
        cur=con.execute("INSERT INTO request_attachments(request_type,request_id,filename,content_type,file_size,file_data,created_at) VALUES(?,?,?,?,?,?,?)",(request_type,request_id,Path(f.filename or 'photo').name,f.content_type,len(content),sqlite3.Binary(content),datetime.now().isoformat()))
        ids.append(cur.lastrowid)
    return ids


def attachment_list(con, request_type: str, request_id: int):
    rows=con.execute("SELECT id,filename,content_type,file_size,created_at FROM request_attachments WHERE request_type=? AND request_id=? ORDER BY id",(request_type,request_id)).fetchall()
    return [{**dict(r),"url":f"/api/request-attachments/{r['id']}"} for r in rows]


def payment_record_dict(con, order_id: int):
    row=con.execute("SELECT * FROM payment_records WHERE order_id=?",(order_id,)).fetchone()
    if not row:
        return None
    d=dict(row)
    d["history"]=[dict(x) for x in con.execute("SELECT payment_status,note,created_at FROM payment_status_history WHERE order_id=? ORDER BY id",(order_id,)).fetchall()]
    return d


def order_financials(con, order_row):
    original_total = float(order_row["original_total"] or order_row["total"] or 0)
    current_total = float(order_row["total"] or 0)
    approved_returns = con.execute(
        "SELECT COALESCE(SUM(refund_amount),0) s FROM return_requests WHERE order_id=? AND status='Approved'",
        (order_row["id"],),
    ).fetchone()["s"]
    approved_returns = float(approved_returns or 0)
    payment=con.execute("SELECT payment_status,upi_reference FROM payment_records WHERE order_id=?",(order_row["id"],)).fetchone()
    upi_paid_or_submitted = order_row["payment_method"] in ("UPI Payment","UPI on Delivery") and bool((order_row["upi_reference"] or "").strip()) and (not payment or payment["payment_status"] in ("Pending Verification","Payment Confirmed"))
    if order_row["status"] == "Cancelled":
        refund_due = original_total if upi_paid_or_submitted else 0.0
        amount_payable = 0.0
    else:
        adjustment_refund = max(0.0, original_total - current_total)
        refund_due = (adjustment_refund + approved_returns) if upi_paid_or_submitted else approved_returns
        amount_payable = current_total if order_row["payment_method"] in ("Cash on Delivery","UPI on Delivery") and not upi_paid_or_submitted else 0.0
    return {
        "original_total": round(original_total, 2),
        "revised_total": round(current_total, 2),
        "adjustment_amount": round(max(0.0, original_total-current_total), 2),
        "refund_due": round(refund_due, 2),
        "amount_payable": round(amount_payable, 2),
        "approved_return_refund": round(approved_returns, 2),
    }


def send_order_adjusted_email(order_id: int):
    con = db()
    order = con.execute("SELECT o.*,u.email customer_email FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=?", (order_id,)).fetchone()
    if not order:
        con.close(); return
    items = con.execute("SELECT product_name,unit,price,quantity FROM order_items WHERE order_id=? AND quantity>0 ORDER BY id", (order_id,)).fetchall()
    money = order_financials(con, order)
    con.close()
    lines = "\n".join(f"- {i['product_name']} ({i['unit']}) × {i['quantity']} = ₹{i['price']*i['quantity']:.2f}" for i in items) or "- No items remaining"
    extra = f"Refund due: ₹{money['refund_due']:.2f}" if money['refund_due'] else f"Amount payable: ₹{money['amount_payable']:.2f}"
    subject=f"Order #{order_id} updated | Maneendra General Stores"
    plain=f"""Hello {order['customer_name']},\n\nYour order was updated because one or more quantities are unavailable.\n\nCurrent items:\n{lines}\n\nOriginal total: ₹{money['original_total']:.2f}\nRevised total: ₹{money['revised_total']:.2f}\nDifference: ₹{money['adjustment_amount']:.2f}\n{extra}\n\nManeendra General Stores"""
    deliver_email(order['customer_email'],subject,plain,order_id=order_id,user_id=order['user_id'])



def _invoice_snapshot(order_id: int):
    con = db()
    order = con.execute("""SELECT o.*,u.email customer_email FROM orders o
                         JOIN users u ON u.id=o.user_id WHERE o.id=?""", (order_id,)).fetchone()
    if not order:
        con.close()
        raise HTTPException(404, "Order not found")
    items = con.execute("SELECT product_name,unit,price,quantity FROM order_items WHERE order_id=? AND quantity>0 ORDER BY id", (order_id,)).fetchall()
    settings = con.execute("SELECT * FROM store_settings WHERE id=1").fetchone()
    payment = con.execute("SELECT * FROM payment_records WHERE order_id=?", (order_id,)).fetchone()
    financials = order_financials(con, order)
    con.close()
    return dict(order), [dict(x) for x in items], dict(settings) if settings else {}, dict(payment) if payment else {}, financials


def _approved_return_details(order_id: int):
    con = db()
    rows = con.execute("""SELECT rr.id, rr.quantity, rr.refund_amount, rr.reason,
                                oi.product_name, oi.unit, oi.price
                         FROM return_requests rr
                         JOIN order_items oi ON oi.id=rr.order_item_id
                         WHERE rr.order_id=? AND rr.status='Approved'
                         ORDER BY rr.id""", (order_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def build_invoice_pdf(order_id: int, revised: bool = False, revision_no: int = 0) -> tuple[bytes, str]:
    order, items, settings, payment, financials = _invoice_snapshot(order_id)
    approved_returns = _approved_return_details(order_id) if revised else []
    returned_by_product = {}
    for r in approved_returns:
        returned_by_product[r['product_name'], r['unit']] = returned_by_product.get((r['product_name'], r['unit']), 0) + int(r['quantity'])
    return_total = round(sum(float(r['refund_amount'] or 0) for r in approved_returns), 2)
    bill_total = round(max(0.0, float(financials['revised_total']) - return_total), 2) if revised else float(financials['revised_total'])
    invoice_number = f"MGS-{order_id:06d}-R{revision_no}" if revised and revision_no > 0 else f"MGS-{order_id:06d}"
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=14*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    green = colors.HexColor("#08783f")
    ink = colors.HexColor("#17221c")
    muted = colors.HexColor("#66736c")
    pale = colors.HexColor("#eef8f2")
    styles.add(ParagraphStyle(name="StoreTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=green, spaceAfter=4))
    styles.add(ParagraphStyle(name="InvoiceMeta", parent=styles["BodyText"], fontSize=9.5, leading=13, textColor=muted))
    styles.add(ParagraphStyle(name="InvoiceHead", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=ink, spaceBefore=8, spaceAfter=6))
    title = "REVISED FINAL TAX INVOICE / BILL" if revised else "FINAL TAX INVOICE / BILL"
    story = [
        Paragraph("Maneendra General Stores", styles["StoreTitle"]),
        Paragraph(f"{settings.get('address','')}<br/>{settings.get('phone','')} &nbsp; | &nbsp; {settings.get('email','')}", styles["InvoiceMeta"]),
        Spacer(1, 5*mm),
    ]
    header = Table([
        [Paragraph(f"<b>{title}</b><br/><font size=9>Invoice No: {invoice_number}</font>", styles["BodyText"]),
         Paragraph(f"<b>Order #{order_id}</b><br/><font size=9>Delivered: {order.get('delivered_at') or order.get('created_at')}</font>", styles["BodyText"])],
    ], colWidths=[110*mm, 65*mm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), pale), ("BOX", (0,0), (-1,-1), .6, colors.HexColor("#cfe6d7")),
        ("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 9), ("RIGHTPADDING", (0,0), (-1,-1), 9),
        ("TOPPADDING", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story += [header, Spacer(1, 5*mm), Paragraph("Customer & delivery", styles["InvoiceHead"]),
              Paragraph(f"<b>{order['customer_name']}</b><br/>{order['phone']}<br/>{order['address']}", styles["BodyText"])]
    data = [["Item", "Qty", "Rate", "Amount"]]
    for item in items:
        key=(item['product_name'], item['unit'])
        returned_qty = returned_by_product.get(key, 0)
        shown_qty = max(0, int(item['quantity']) - returned_qty) if revised else int(item['quantity'])
        if shown_qty <= 0:
            continue
        data.append([f"{item['product_name']}\n{item['unit']}", str(shown_qty), f"INR {float(item['price']):.2f}", f"INR {float(item['price'])*shown_qty:.2f}"])
    if len(data) == 1:
        data.append(["No items remaining after approved returns", "", "", "INR 0.00"])
    item_table = Table(data, colWidths=[94*mm, 18*mm, 30*mm, 33*mm], repeatRows=1)
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), green), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("FONTSIZE", (0,0), (-1,-1), 9),
        ("ALIGN", (1,1), (-1,-1), "RIGHT"), ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#dde8e0")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#fafcfb")]),
        ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ]))
    story += [Spacer(1, 4*mm), item_table, Spacer(1, 5*mm)]
    if revised and approved_returns:
        ret_data = [["Returned item", "Qty", "Refund"]]
        for r in approved_returns:
            ret_data.append([f"{r['product_name']}\n{r['unit']}", str(r['quantity']), f"- INR {float(r['refund_amount'] or 0):.2f}"])
        ret_table = Table(ret_data, colWidths=[112*mm, 18*mm, 45*mm], repeatRows=1)
        ret_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#8a5a00")), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("FONTSIZE", (0,0), (-1,-1), 9),
            ("ALIGN", (1,1), (-1,-1), "RIGHT"), ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#ead9b7")),
            ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ]))
        story += [Paragraph("Approved returns & refund adjustment", styles["InvoiceHead"]), ret_table, Spacer(1, 5*mm)]
    pay_status = payment.get("payment_status", "")
    if revised:
        totals_data = [
            ["Delivered bill total", f"INR {float(financials['revised_total']):.2f}"],
            ["Approved return refund", f"- INR {return_total:.2f}"],
            ["Revised bill total", f"INR {bill_total:.2f}"],
            ["Payment method", order["payment_method"]],
            ["Payment status", pay_status or ("Cash Collected" if order["payment_method"]=="Cash on Delivery" else "")],
        ]
    else:
        totals_data = [
            ["Products total", f"INR {financials['revised_total']:.2f}"],
            ["Delivery", "FREE"],
            ["Total paid / payable", f"INR {financials['revised_total']:.2f}"],
            ["Payment method", order["payment_method"]],
            ["Payment status", pay_status or ("Cash Collected" if order["payment_method"]=="Cash on Delivery" else "")],
        ]
    totals = Table(totals_data, colWidths=[115*mm, 60*mm])
    totals.setStyle(TableStyle([
        ("ALIGN", (1,0), (1,-1), "RIGHT"), ("FONTNAME", (0,2), (-1,2), "Helvetica-Bold"),
        ("TEXTCOLOR", (0,1), (1,1), green), ("LINEABOVE", (0,2), (-1,2), .7, colors.HexColor("#b9d9c4")),
        ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    note = "This revised bill was generated after an approved product return. The return refund is shown as a separate adjustment." if revised else "This bill was generated automatically after successful delivery. Delivery is FREE."
    story += [totals, Spacer(1, 8*mm), Paragraph("Thank you for shopping with Maneendra General Stores.", ParagraphStyle(name="Thanks", parent=styles["BodyText"], alignment=TA_CENTER, textColor=green, fontName="Helvetica-Bold", fontSize=11)), Spacer(1, 2*mm), Paragraph(note, ParagraphStyle(name="FooterNote", parent=styles["BodyText"], alignment=TA_CENTER, textColor=muted, fontSize=8.5))]
    doc.build(story)
    return buf.getvalue(), invoice_number


def ensure_invoice_record(order_id: int, force: bool = False, revised: bool = False, revision_reason: str = ""):
    con = db()
    existing = con.execute("SELECT * FROM invoice_records WHERE order_id=?", (order_id,)).fetchone()
    order = con.execute("SELECT id,user_id,status FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404, "Order not found")
    if order["status"] != "Delivered":
        con.close(); raise HTTPException(400, "Final invoice is available after successful delivery")
    if existing and not force and not revised:
        out = dict(existing); con.close(); return out
    next_revision = (int(existing["revision_no"] or 0) + 1) if (existing and revised) else (1 if revised else (int(existing["revision_no"] or 0) if existing else 0))
    con.close()
    pdf, invoice_number = build_invoice_pdf(order_id, revised=revised, revision_no=next_revision)
    if revised:
        file_name = f"Maneendra-General-Stores-Invoice-{order_id}-Revised-{next_revision}.pdf"
    else:
        file_name = f"Maneendra-General-Stores-Invoice-{order_id}.pdf"
    now = datetime.now().isoformat()
    con = db()
    if existing:
        con.execute("""UPDATE invoice_records SET invoice_number=?,file_name=?,pdf_data=?,generated_at=?,email_status='Not sent',revision_no=?,revision_reason=? WHERE order_id=?""",
                    (invoice_number, file_name, sqlite3.Binary(pdf), now, next_revision, revision_reason[:500], order_id))
    else:
        con.execute("""INSERT INTO invoice_records(order_id,user_id,invoice_number,file_name,pdf_data,generated_at,email_status,revision_no,revision_reason)
                       VALUES(?,?,?,?,?,?, 'Not sent',?,?)""",
                    (order_id, order["user_id"], invoice_number, file_name, sqlite3.Binary(pdf), now, next_revision, revision_reason[:500]))
    con.commit()
    row = con.execute("SELECT * FROM invoice_records WHERE order_id=?", (order_id,)).fetchone()
    out = dict(row); con.close(); return out


def send_delivery_invoice_email(order_id: int):
    try:
        invoice = ensure_invoice_record(order_id)
        order, items, settings, payment, financials = _invoice_snapshot(order_id)
        subject = f"Order #{order_id} delivered - final bill attached | Maneendra General Stores"
        plain = f"""Hello {order['customer_name']},\n\nYour order #{order_id} has been delivered successfully.\n\nFinal total: INR {financials['revised_total']:.2f}\nPayment method: {order['payment_method']}\nPayment status: {payment.get('payment_status','')}\n\nYour final bill is attached as a PDF. You can also download it anytime from My Account > My Orders.\n\nThank you for shopping with Maneendra General Stores.\nPhone: {settings.get('phone','9966454521')}\nEmail: {settings.get('email','www.maneendrageneralstores@gmail.com')}"""
        item_html = ''.join(f"<tr><td style='padding:7px 0;border-bottom:1px solid #edf2ee'>{i['product_name']} <span style='color:#7a857f'>({i['unit']})</span> × {i['quantity']}</td><td style='padding:7px 0;border-bottom:1px solid #edf2ee;text-align:right'>₹{float(i['price'])*int(i['quantity']):.2f}</td></tr>" for i in items)
        html = f"""<div style='font-family:Arial,sans-serif;max-width:650px;margin:auto;color:#17241c'><div style='background:linear-gradient(135deg,#08783f,#12a35b);color:white;padding:24px;border-radius:16px 16px 0 0'><div style='font-size:13px;letter-spacing:1px;opacity:.9'>MANEENDRA GENERAL STORES</div><h2 style='margin:7px 0 0'>Delivered successfully ✓</h2></div><div style='border:1px solid #dce8df;border-top:0;padding:24px;border-radius:0 0 16px 16px'><p>Hello <b>{order['customer_name']}</b>,</p><p>Your order <b>#{order_id}</b> has been delivered. Your final bill is attached to this email.</p><table style='width:100%;border-collapse:collapse;margin:16px 0'>{item_html}</table><div style='background:#eef8f2;padding:14px 16px;border-radius:10px'><b>Final total: ₹{financials['revised_total']:.2f}</b><br><span>Payment: {order['payment_method']} · {payment.get('payment_status','')}</span><br><span>Delivery: FREE</span></div><p style='color:#657169'>You can download the bill again anytime from <b>My Account → My Orders</b>.</p><p style='margin-bottom:0'><b>Thank you for shopping with us.</b></p></div></div>"""
        ok = deliver_email(order["customer_email"], subject, plain, html, order_id=order_id, user_id=order["user_id"], attachments=[(invoice["file_name"], "application/pdf", invoice["pdf_data"])])
        con = db()
        con.execute("UPDATE invoice_records SET emailed_at=?,email_status=? WHERE order_id=?", (datetime.now().isoformat() if ok else invoice.get("emailed_at", ""), "Sent" if ok else "Failed / skipped", order_id))
        con.commit(); con.close()
        return ok
    except Exception:
        return False


def send_revised_return_invoice_email(order_id: int, return_id: int):
    """Generate the revised PDF bill after an approved return and email it to the customer."""
    try:
        con = db()
        r = con.execute("""SELECT rr.*, oi.product_name, oi.unit, o.customer_name, o.user_id,
                                 u.email customer_email
                          FROM return_requests rr
                          JOIN order_items oi ON oi.id=rr.order_item_id
                          JOIN orders o ON o.id=rr.order_id
                          JOIN users u ON u.id=rr.user_id
                          WHERE rr.id=? AND rr.status='Approved'""", (return_id,)).fetchone()
        con.close()
        if not r:
            return False
        invoice = ensure_invoice_record(order_id, force=True, revised=True,
                                        revision_reason=f"Approved return #{return_id}: {r['product_name']} × {r['quantity']}")
        order, items, settings, payment, financials = _invoice_snapshot(order_id)
        approved_returns = _approved_return_details(order_id)
        refund_total = round(sum(float(x['refund_amount'] or 0) for x in approved_returns), 2)
        revised_total = round(max(0.0, float(financials['revised_total']) - refund_total), 2)
        subject = f"Revised bill after return - Order #{order_id} | Maneendra General Stores"
        plain = f"""Hello {order['customer_name']},\n\nYour return for order #{order_id} has been approved.\n\nReturned product: {r['product_name']} ({r['unit']}) × {r['quantity']}\nReturn refund for this request: INR {float(r['refund_amount'] or 0):.2f}\nTotal approved return refunds on this order: INR {refund_total:.2f}\nRevised bill total after approved returns: INR {revised_total:.2f}\n\nThe revised bill is attached as a PDF. The latest bill is also available in My Account → My Orders.\n\nManeendra General Stores\nPhone: {settings.get('phone','9966454521')}\nEmail: {settings.get('email','www.maneendrageneralstores@gmail.com')}"""
        return_rows=''.join(f"<tr><td style='padding:8px;border-bottom:1px solid #edf2ee'>{x['product_name']} ({x['unit']}) × {x['quantity']}</td><td style='padding:8px;border-bottom:1px solid #edf2ee;text-align:right'>- ₹{float(x['refund_amount'] or 0):.2f}</td></tr>" for x in approved_returns)
        html = f"""<div style='font-family:Arial,sans-serif;max-width:650px;margin:auto;color:#17241c'><div style='background:linear-gradient(135deg,#08783f,#12a35b);color:white;padding:24px;border-radius:16px 16px 0 0'><div style='font-size:13px;letter-spacing:1px;opacity:.9'>MANEENDRA GENERAL STORES</div><h2 style='margin:7px 0 0'>Revised bill after return ✓</h2></div><div style='border:1px solid #dce8df;border-top:0;padding:24px;border-radius:0 0 16px 16px'><p>Hello <b>{order['customer_name']}</b>,</p><p>Your return request for order <b>#{order_id}</b> has been approved. Your revised bill is attached.</p><p><b>Returned now:</b> {r['product_name']} ({r['unit']}) × {r['quantity']} — refund ₹{float(r['refund_amount'] or 0):.2f}</p><table style='width:100%;border-collapse:collapse;margin:16px 0'>{return_rows}</table><div style='background:#eef8f2;padding:14px 16px;border-radius:10px'><b>Revised bill total: ₹{revised_total:.2f}</b><br><span>Total approved return refunds: ₹{refund_total:.2f}</span><br><span>Delivery: FREE</span></div><p style='color:#657169'>The attached PDF is the latest revised bill. You can also download it from <b>My Account → My Orders</b>.</p><p style='margin-bottom:0'><b>Thank you for shopping with Maneendra General Stores.</b></p></div></div>"""
        ok = deliver_email(order['customer_email'], subject, plain, html, order_id=order_id, user_id=order['user_id'], attachments=[(invoice['file_name'], 'application/pdf', invoice['pdf_data'])])
        con = db()
        con.execute("UPDATE invoice_records SET emailed_at=?,email_status=? WHERE order_id=?", (datetime.now().isoformat() if ok else invoice.get('emailed_at',''), 'Sent' if ok else 'Failed / skipped', order_id))
        con.commit(); con.close()
        return ok
    except Exception:
        return False

def otp_hash(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def send_delivery_otp_email(order_id: int, otp: str):
    con=db()
    order=con.execute("SELECT o.*,u.email customer_email FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=?",(order_id,)).fetchone()
    if not order:
        con.close(); return
    money=order_financials(con,order)
    con.close()
    cod_line = f"\nCash amount to pay on delivery: ₹{money['amount_payable']:.2f}\n" if order['payment_method']=='Cash on Delivery' else ""
    cod_html = f"<div style='margin:14px 0;padding:12px;background:#fff7df;border:1px solid #f0d386;border-radius:8px'><b>Cash to pay: ₹{money['amount_payable']:.2f}</b></div>" if order['payment_method']=='Cash on Delivery' else ""
    upi_delivery_line = f"\nUPI amount due on delivery: ₹{money['revised_total']:.2f}\nOpen My Account → My Orders → Pay by UPI on Delivery, scan the QR, and submit the UTR/reference number.\n" if order['payment_method']=='UPI on Delivery' and not (order['upi_reference'] or '').strip() else ""
    upi_delivery_html = f"<div style='margin:14px 0;padding:12px;background:#eef8f2;border:1px solid #b9dfc9;border-radius:8px'><b>UPI amount due: ₹{money['revised_total']:.2f}</b><br>Open My Account → My Orders → <b>Pay by UPI on Delivery</b>, then submit the UTR/reference number.</div>" if order['payment_method']=='UPI on Delivery' and not (order['upi_reference'] or '').strip() else ""
    subject=f"Delivery OTP for Order #{order_id} | Maneendra General Stores"
    switch_line = "\nIf you prefer UPI instead of cash, open My Account → My Orders → Pay by UPI instead. After payment, the store must confirm it before delivery is completed.\n" if order['payment_method']=='Cash on Delivery' else ""
    switch_html = "<p><b>Prefer UPI?</b> Open My Account → My Orders and choose <b>Pay by UPI instead</b>. The store will verify the payment before delivery is completed.</p>" if order['payment_method']=='Cash on Delivery' else ""
    plain=f"""Hello {order['customer_name']},\n\nYour order #{order_id} is Out for Delivery.\n{cod_line}{upi_delivery_line}{switch_line}\nDelivery OTP: {otp}\n\nPlease give this OTP only to the delivery person after receiving your order and after the payment is completed. The OTP expires in 24 hours.\n\nManeendra General Stores"""
    html=f"<div style='font-family:Arial,sans-serif'><h2>Maneendra General Stores</h2><p>Your order <b>#{order_id}</b> is Out for Delivery.</p>{cod_html}{upi_delivery_html}{switch_html}<p>Delivery OTP:</p><div style='font-size:30px;font-weight:800;letter-spacing:8px;padding:14px;background:#eef8f2;color:#08783d;display:inline-block'>{otp}</div><p>Please share it only after receiving the order and after payment is completed. It expires in 24 hours.</p></div>"
    deliver_email(order['customer_email'],subject,plain,html,order_id=order_id,user_id=order['user_id'])


def send_return_email(return_id: int, event: str):
    con=db()
    r=con.execute("""SELECT rr.*,oi.product_name,oi.unit,oi.price,o.customer_name,u.email customer_email
                     FROM return_requests rr JOIN order_items oi ON oi.id=rr.order_item_id
                     JOIN orders o ON o.id=rr.order_id JOIN users u ON u.id=rr.user_id WHERE rr.id=?""",(return_id,)).fetchone()
    con.close()
    if not r: return
    subject=f"Return {event} - Order #{r['order_id']} | Maneendra General Stores"
    plain=f"""Hello {r['customer_name']},\n\nReturn update for order #{r['order_id']}: {event}\nProduct: {r['product_name']} ({r['unit']})\nQuantity: {r['quantity']}\nRefund amount: ₹{float(r['refund_amount'] or 0):.2f}\nReason: {r['reason']}\n\nManeendra General Stores"""
    deliver_email(r['customer_email'],subject,plain,order_id=r['order_id'],user_id=r['user_id'])


def send_payment_confirmation_email(order_id: int):
    con=db()
    order=con.execute("SELECT o.*,u.email customer_email FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=?",(order_id,)).fetchone()
    payment=con.execute("SELECT * FROM payment_records WHERE order_id=?",(order_id,)).fetchone()
    con.close()
    if not order or not payment: return
    subject=f"Payment confirmed for Order #{order_id} | Maneendra General Stores"
    plain=f"""Hello {order['customer_name']},\n\nWe have checked your UPI payment for order #{order_id}.\n\nPayment status: CONFIRMED\nAmount currently on the order: ₹{float(payment['current_amount']):.2f}\nUPI reference: {payment['upi_reference'] or '-'}\n\nYour order will continue through packing and delivery.\n\nManeendra General Stores"""
    html=f"<div style='font-family:Arial,sans-serif;max-width:620px'><h2 style='color:#08783d'>Maneendra General Stores</h2><p>Hello <b>{order['customer_name']}</b>,</p><div style='padding:14px;background:#eef8f2;border-radius:10px'><b>✓ UPI payment confirmed</b><br>Order #{order_id}<br>Amount: ₹{float(payment['current_amount']):.2f}<br>UPI reference: {payment['upi_reference'] or '-'}</div><p>Your order will continue through packing and delivery.</p></div>"
    deliver_email(order['customer_email'],subject,plain,html,order_id=order_id,user_id=order['user_id'])


def send_upi_switch_submitted_email(order_id: int):
    con=db()
    order=con.execute("SELECT o.*,u.email customer_email FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=?",(order_id,)).fetchone()
    payment=con.execute("SELECT * FROM payment_records WHERE order_id=?",(order_id,)).fetchone()
    con.close()
    if not order or not payment: return
    subject=f"UPI payment submitted for Order #{order_id} | Maneendra General Stores"
    plain=f"""Hello {order['customer_name']},\n\nYour order #{order_id} is set for UPI on Delivery and a UPI payment was submitted.\n\nAmount: ₹{float(payment['current_amount']):.2f}\nUPI reference: {payment['upi_reference'] or '-'}\nPayment status: PENDING VERIFICATION\n\nThe store will check the payment. Do not share the delivery OTP until the payment is confirmed and you have received the order.\n\nManeendra General Stores"""
    html=f"<div style='font-family:Arial,sans-serif;max-width:620px'><h2 style='color:#08783d'>Maneendra General Stores</h2><p>Hello <b>{order['customer_name']}</b>,</p><p>Order <b>#{order_id}</b> is set for UPI on Delivery and a UPI payment was submitted.</p><div style='padding:14px;background:#fff7df;border-radius:10px'><b>UPI payment submitted</b><br>Amount: ₹{float(payment['current_amount']):.2f}<br>Reference: {payment['upi_reference'] or '-'}<br>Status: <b>Pending Verification</b></div><p>Please wait for store confirmation before sharing the delivery OTP.</p></div>"
    deliver_email(order['customer_email'],subject,plain,html,order_id=order_id,user_id=order['user_id'])


def cancel_order_in_tx(con, order_id: int, cancelled_by: str):
    row=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not row: raise HTTPException(404,"Order not found")
    if row['status']=='Cancelled': return row
    if row['status']=='Delivered': raise HTTPException(400,"Delivered orders cannot be cancelled; request a return instead")
    items=con.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?",(order_id,)).fetchall()
    for i in items:
        con.execute("UPDATE products SET stock=stock+? WHERE id=?",(i['quantity'],i['product_id']))
        record_inventory(con,i['product_id'],order_id,"ORDER_CANCEL_RESTORE",int(i['quantity']),f"Cancelled by {cancelled_by}")
    con.execute("UPDATE orders SET status='Cancelled',cancelled_at=? WHERE id=?",(datetime.now().isoformat(),order_id))
    changer = row['user_id'] if cancelled_by=='customer' else None
    record_status(con,order_id,'Cancelled',changer,f"Cancelled by {cancelled_by}")
    update_payment_record(con,order_id)
    return con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()


def send_order_status_email(order_id: int, status: str):
    con = db()
    order = con.execute(
        """SELECT o.*,u.email AS customer_email,u.full_name AS profile_name
           FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=?""",
        (order_id,),
    ).fetchone()
    if not order:
        con.close()
        return
    items = con.execute(
        "SELECT product_name,unit,price,quantity FROM order_items WHERE order_id=? ORDER BY id",
        (order_id,),
    ).fetchall()
    store = con.execute("SELECT phone,email FROM store_settings WHERE id=1").fetchone()
    money = order_financials(con, order)
    con.close()

    item_lines = [f"- {i['product_name']} ({i['unit']}) × {i['quantity']} = ₹{i['price'] * i['quantity']:.2f}" for i in items]
    subject = f"Order #{order_id} - {status} | Maneendra General Stores"
    plain = f"""Hello {order['customer_name']},

Your order #{order_id} status is now: {status}

Order summary:
{chr(10).join(item_lines)}

Original total: ₹{money['original_total']:.2f}
Current total: ₹{money['revised_total']:.2f}
Payment: {order['payment_method']}
Delivery address: {order['address']}

We will email you whenever the order status changes.

Maneendra General Stores
Phone: {store['phone'] if store else '9966454521'}
Email: {store['email'] if store else 'www.maneendrageneralstores@gmail.com'}
"""
    rows = "".join(
        f"<tr><td style='padding:6px 0'>{i['product_name']} ({i['unit']}) × {i['quantity']}</td><td style='padding:6px 0;text-align:right'>₹{i['price'] * i['quantity']:.2f}</td></tr>"
        for i in items
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:650px;margin:auto;color:#17241c">
      <div style="background:#08783d;color:white;padding:18px 22px;border-radius:12px 12px 0 0">
        <h2 style="margin:0">Maneendra General Stores</h2>
      </div>
      <div style="border:1px solid #dce8df;border-top:0;padding:22px;border-radius:0 0 12px 12px">
        <p>Hello <b>{order['customer_name']}</b>,</p>
        <p>Your order <b>#{order_id}</b> status is now:</p>
        <div style="font-size:20px;font-weight:700;color:#08783d;background:#eef8f2;padding:12px 16px;border-radius:8px">{status}</div>
        <h3>Order summary</h3>
        <table style="width:100%;border-collapse:collapse">{rows}</table>
        <hr style="border:0;border-top:1px solid #e5ece7">
        <p><b>Original total:</b> ₹{money['original_total']:.2f}<br><b>Current total:</b> ₹{money['revised_total']:.2f}<br><b>Payment:</b> {order['payment_method']}<br><b>Delivery address:</b> {order['address']}</p>
        <p style="color:#657169">You will receive another email whenever the order status changes.</p>
      </div>
    </div>
    """
    deliver_email(order["customer_email"], subject, plain, html, order_id=order_id, user_id=order["user_id"])


init_db()


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/categories")
def categories():
    con = db()
    rows = con.execute("SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category").fetchall()
    con.close()
    return [r["category"] for r in rows]


@app.get("/api/products")
def products(q: str = "", category: str = ""):
    sql = "SELECT * FROM products WHERE active=1"
    args = []
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if category:
        sql += " AND category=?"
        args.append(category)
    sql += " ORDER BY category,name"
    con = db()
    rows = con.execute(sql, args).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/store-settings")
def store_settings():
    con = db()
    row = con.execute(
        "SELECT phone,whatsapp,email,address,map_url,hours,upi_id,upi_payee_name FROM store_settings WHERE id=1"
    ).fetchone()
    con.close()
    return dict(row)


@app.put("/api/admin/store-settings")
def update_store_settings(data: StoreSettingsIn, admin=Depends(admin_user)):
    con = db()
    con.execute(
        "UPDATE store_settings SET phone=?,whatsapp=?,email=?,address=?,map_url=?,hours=?,upi_id=?,upi_payee_name=? WHERE id=1",
        (data.phone, data.whatsapp, data.email, data.address, data.map_url, data.hours, data.upi_id.strip(), data.upi_payee_name.strip()),
    )
    con.commit()
    con.close()
    return {"message": "Store details updated"}


@app.get("/api/admin/mail-settings")
def get_mail_settings(admin=Depends(admin_user)):
    con = db()
    row = con.execute(
        "SELECT email,brevo_sender_email,brevo_sender_name,email_notifications_enabled FROM store_settings WHERE id=1"
    ).fetchone()
    con.close()
    api_key_set = bool((os.environ.get("BREVO_API_KEY") or "").strip())
    return {
        "provider": "Brevo HTTPS API",
        "sender_email": row["brevo_sender_email"] or row["email"],
        "sender_name": row["brevo_sender_name"] or "Maneendra General Stores",
        "email_notifications_enabled": bool(row["email_notifications_enabled"]),
        "api_key_set": api_key_set,
    }


@app.put("/api/admin/mail-settings")
def update_mail_settings(data: MailSettingsIn, admin=Depends(admin_user)):
    con = db()
    con.execute(
        "UPDATE store_settings SET brevo_sender_email=?,brevo_sender_name=?,email_notifications_enabled=? WHERE id=1",
        (data.sender_email.strip(), data.sender_name.strip(), 1 if data.email_notifications_enabled else 0),
    )
    audit(con, admin["id"], "email_settings_updated", "store_settings", 1, "Brevo sender settings updated")
    con.commit()
    con.close()
    return {"message": "Brevo email settings updated"}


@app.post("/api/admin/test-email")
def test_email(data: TestEmailIn, admin=Depends(admin_user)):
    ok = deliver_email(
        data.to_email.strip(),
        "Maneendra General Stores - Test Email",
        "This is a test email from your Maneendra General Stores website. Brevo email notifications are configured correctly.",
        "<h2>Maneendra General Stores</h2><p>This is a test email from your website.</p><p><b>Brevo HTTPS email notifications are configured correctly.</b></p>",
        user_id=admin["id"],
    )
    if not ok:
        raise HTTPException(400, "Email could not be sent. Check BREVO_API_KEY and verify the sender email in Brevo.")
    return {"message": "Test email sent through Brevo"}


@app.get("/api/admin/email-logs")
def email_logs(admin=Depends(admin_user)):
    con = db()
    rows = con.execute("SELECT * FROM email_logs ORDER BY id DESC LIMIT 30").fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/upi-qr")
def upi_qr(amount: float = 0):
    if amount <= 0:
        raise HTTPException(400, "Invalid payment amount")
    con = db()
    row = con.execute("SELECT upi_id,upi_payee_name FROM store_settings WHERE id=1").fetchone()
    con.close()
    if not row or not (row["upi_id"] or "").strip():
        raise HTTPException(400, "UPI ID is not configured. Admin can add it in Store Details.")
    params = {
        "pa": row["upi_id"].strip(),
        "pn": (row["upi_payee_name"] or "Maneendra General Stores").strip(),
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tn": "Maneendra General Stores order",
    }
    uri = "upi://pay?" + urlencode(params)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/register")
def register(data: RegisterIn, request: Request):
    con = db()
    try:
        now = datetime.now().isoformat()
        cur = con.execute(
            "INSERT INTO users(full_name,email,password_hash,role,phone,address,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (data.full_name.strip(), data.email.lower().strip(), hash_password(data.password), "customer", "", "", now, now),
        )
        request.session["user_id"] = cur.lastrowid
        get_or_create_cart(con,request,cur.lastrowid)
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        raise HTTPException(409, "Email already registered")
    con.close()
    return {"message": "Registered successfully"}


@app.post("/api/login")
def login(data: LoginIn, request: Request):
    con = db()
    email=data.email.lower().strip()
    sec=con.execute("SELECT * FROM login_security WHERE email=?",(email,)).fetchone()
    if sec and sec["locked_until"]:
        try:
            if datetime.now() < datetime.fromisoformat(sec["locked_until"]):
                con.close(); raise HTTPException(429, "Too many failed login attempts. Try again in a few minutes.")
        except ValueError:
            pass
    row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(data.password, row["password_hash"]):
        now=datetime.now(); first=now; count=1
        if sec:
            try: first=datetime.fromisoformat(sec["first_failed_at"]) if sec["first_failed_at"] else now
            except Exception: first=now
            if now-first>timedelta(minutes=15): first=now; count=1
            else: count=int(sec["fail_count"] or 0)+1
        locked=(now+timedelta(minutes=10)).isoformat() if count>=5 else ''
        con.execute("INSERT INTO login_security(email,fail_count,first_failed_at,locked_until) VALUES(?,?,?,?) ON CONFLICT(email) DO UPDATE SET fail_count=excluded.fail_count,first_failed_at=excluded.first_failed_at,locked_until=excluded.locked_until",(email,count,first.isoformat(),locked))
        con.commit(); con.close(); raise HTTPException(401, "Invalid email or password")
    con.execute("DELETE FROM login_security WHERE email=?",(email,))
    request.session["user_id"] = row["id"]
    get_or_create_cart(con,request,row["id"])
    audit(con,row["id"],"LOGIN","user",row["id"],"Successful login")
    con.commit(); con.close()
    return {
        "message": "Login successful",
        "user": {
            "id": row["id"],
            "full_name": row["full_name"],
            "email": row["email"],
            "role": row["role"],
            "phone": row["phone"] or "",
            "address": row["address"] or "",
        },
    }


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"message": "Logged out"}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


@app.get("/api/profile")
def profile(user=Depends(current_user)):
    return user


@app.put("/api/profile")
def update_profile(data: ProfileIn, user=Depends(current_user)):
    if user["role"] != "customer":
        raise HTTPException(403, "Customer profile only")
    con = db()
    try:
        con.execute(
            "UPDATE users SET full_name=?,email=?,phone=?,address=?,updated_at=? WHERE id=?",
            (
                data.full_name.strip(),
                data.email.lower().strip(),
                data.phone.strip(),
                data.address.strip(),
                datetime.now().isoformat(),
                user["id"],
            ),
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        raise HTTPException(409, "That email is already used by another account")
    row = con.execute(
        "SELECT id,full_name,email,role,phone,address,created_at,updated_at FROM users WHERE id=?",
        (user["id"],),
    ).fetchone()
    con.close()
    return {"message": "Profile updated permanently", "user": dict(row)}


@app.put("/api/profile/password")
def change_password(data: PasswordChangeIn, user=Depends(current_user)):
    con = db()
    row = con.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
    if not row or not verify_password(data.current_password, row["password_hash"]):
        con.close()
        raise HTTPException(400, "Current password is incorrect")
    con.execute(
        "UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
        (hash_password(data.new_password), datetime.now().isoformat(), user["id"]),
    )
    con.commit()
    con.close()
    return {"message": "Password changed"}


@app.get("/api/addresses")
def addresses(user=Depends(current_user)):
    if user["role"]!="customer": raise HTTPException(403,"Customer addresses only")
    con=db(); rows=con.execute("SELECT * FROM customer_addresses WHERE user_id=? ORDER BY is_default DESC,id",(user["id"],)).fetchall(); con.close()
    return [dict(r) for r in rows]


@app.post("/api/addresses")
def add_address(data: AddressIn, user=Depends(current_user)):
    if user["role"]!="customer": raise HTTPException(403,"Customer addresses only")
    con=db(); now=datetime.now().isoformat()
    if data.is_default: con.execute("UPDATE customer_addresses SET is_default=0 WHERE user_id=?",(user["id"],))
    cur=con.execute("INSERT INTO customer_addresses(user_id,label,recipient_name,phone,address,is_default,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(user["id"],data.label.strip(),data.recipient_name.strip(),data.phone.strip(),data.address.strip(),1 if data.is_default else 0,now,now))
    if not con.execute("SELECT 1 FROM customer_addresses WHERE user_id=? AND is_default=1",(user["id"],)).fetchone(): con.execute("UPDATE customer_addresses SET is_default=1 WHERE id=?",(cur.lastrowid,))
    audit(con,user["id"],"ADDRESS_ADDED","customer_address",cur.lastrowid,data.label)
    con.commit(); row=con.execute("SELECT * FROM customer_addresses WHERE id=?",(cur.lastrowid,)).fetchone(); con.close(); return dict(row)


@app.put("/api/addresses/{address_id}")
def edit_address(address_id:int,data:AddressIn,user=Depends(current_user)):
    con=db(); row=con.execute("SELECT * FROM customer_addresses WHERE id=? AND user_id=?",(address_id,user["id"])).fetchone()
    if not row: con.close(); raise HTTPException(404,"Address not found")
    if data.is_default: con.execute("UPDATE customer_addresses SET is_default=0 WHERE user_id=?",(user["id"],))
    con.execute("UPDATE customer_addresses SET label=?,recipient_name=?,phone=?,address=?,is_default=?,updated_at=? WHERE id=?",(data.label.strip(),data.recipient_name.strip(),data.phone.strip(),data.address.strip(),1 if data.is_default else 0,datetime.now().isoformat(),address_id))
    audit(con,user["id"],"ADDRESS_UPDATED","customer_address",address_id,data.label)
    con.commit(); con.close(); return {"message":"Address updated"}


@app.delete("/api/addresses/{address_id}")
def delete_address(address_id:int,user=Depends(current_user)):
    con=db(); row=con.execute("SELECT * FROM customer_addresses WHERE id=? AND user_id=?",(address_id,user["id"])).fetchone()
    if not row: con.close(); raise HTTPException(404,"Address not found")
    con.execute("DELETE FROM customer_addresses WHERE id=?",(address_id,)); audit(con,user["id"],"ADDRESS_DELETED","customer_address",address_id,row["label"])
    remaining=con.execute("SELECT id FROM customer_addresses WHERE user_id=? ORDER BY id LIMIT 1",(user["id"],)).fetchone()
    if remaining and not con.execute("SELECT 1 FROM customer_addresses WHERE user_id=? AND is_default=1",(user["id"],)).fetchone(): con.execute("UPDATE customer_addresses SET is_default=1 WHERE id=?",(remaining["id"],))
    con.commit(); con.close(); return {"message":"Address deleted"}


@app.get("/api/cart")
def get_cart(request: Request):
    con=db()
    uid=request.session.get("user_id")
    cart=get_or_create_cart(con,request,uid)
    con.commit()
    data=cart_payload(con,cart["id"])
    con.close()
    return data


@app.post("/api/cart/items")
def add_cart_item(data: CartItemIn, request: Request):
    con=db(); uid=request.session.get("user_id"); cart=get_or_create_cart(con,request,uid)
    p=con.execute("SELECT id,name,stock,active FROM products WHERE id=?",(data.product_id,)).fetchone()
    if not p or not p["active"]:
        con.close(); raise HTTPException(404,"Product unavailable")
    row=con.execute("SELECT quantity FROM cart_items WHERE cart_id=? AND product_id=?",(cart["id"],data.product_id)).fetchone()
    new_qty=(int(row["quantity"]) if row else 0)+data.quantity
    if new_qty>int(p["stock"]):
        con.close(); raise HTTPException(400,f"Only {p['stock']} left for {p['name']}")
    now=datetime.now().isoformat()
    if row:
        con.execute("UPDATE cart_items SET quantity=?,updated_at=? WHERE cart_id=? AND product_id=?",(new_qty,now,cart["id"],data.product_id))
    else:
        con.execute("INSERT INTO cart_items(cart_id,product_id,quantity,created_at,updated_at) VALUES(?,?,?,?,?)",(cart["id"],data.product_id,new_qty,now,now))
    con.execute("UPDATE carts SET updated_at=? WHERE id=?",(now,cart["id"]))
    con.commit(); result=cart_payload(con,cart["id"]); con.close(); return result


@app.put("/api/cart/items/{product_id}")
def set_cart_item(product_id: int, data: CartQuantityIn, request: Request):
    con=db(); uid=request.session.get("user_id"); cart=get_or_create_cart(con,request,uid)
    p=con.execute("SELECT id,name,stock,active FROM products WHERE id=?",(product_id,)).fetchone()
    if not p or not p["active"]:
        con.close(); raise HTTPException(404,"Product unavailable")
    if data.quantity>int(p["stock"]):
        con.close(); raise HTTPException(400,f"Only {p['stock']} left for {p['name']}")
    if data.quantity<=0:
        con.execute("DELETE FROM cart_items WHERE cart_id=? AND product_id=?",(cart["id"],product_id))
    else:
        now=datetime.now().isoformat()
        existing=con.execute("SELECT id FROM cart_items WHERE cart_id=? AND product_id=?",(cart["id"],product_id)).fetchone()
        if existing: con.execute("UPDATE cart_items SET quantity=?,updated_at=? WHERE id=?",(data.quantity,now,existing["id"]))
        else: con.execute("INSERT INTO cart_items(cart_id,product_id,quantity,created_at,updated_at) VALUES(?,?,?,?,?)",(cart["id"],product_id,data.quantity,now,now))
    con.commit(); result=cart_payload(con,cart["id"]); con.close(); return result


@app.delete("/api/cart/items/{product_id}")
def delete_cart_item(product_id: int, request: Request):
    con=db(); uid=request.session.get("user_id"); cart=get_or_create_cart(con,request,uid)
    con.execute("DELETE FROM cart_items WHERE cart_id=? AND product_id=?",(cart["id"],product_id)); con.commit()
    result=cart_payload(con,cart["id"]); con.close(); return result


@app.delete("/api/cart")
def clear_cart(request: Request):
    con=db(); uid=request.session.get("user_id"); cart=get_or_create_cart(con,request,uid)
    con.execute("DELETE FROM cart_items WHERE cart_id=?",(cart["id"],)); con.commit(); con.close()
    return {"items":[],"total":0,"count":0}


@app.post("/api/orders")
def place_order(data: OrderIn, request: Request, background_tasks: BackgroundTasks, user=Depends(current_user)):
    con = db()
    cart=get_or_create_cart(con,request,user["id"])
    con.commit()  # get_or_create_cart may merge/update cart rows before checkout transaction
    cart_rows=con.execute("SELECT product_id,quantity FROM cart_items WHERE cart_id=?",(cart["id"],)).fetchall()
    if not cart_rows:
        con.close(); raise HTTPException(400, "Cart is empty")
    total = 0
    resolved = []
    allowed_payment_methods={"Cash on Delivery","UPI Payment","UPI on Delivery"}
    if data.payment_method not in allowed_payment_methods:
        con.close(); raise HTTPException(400,"Invalid payment method")
    if data.payment_method in ("UPI Payment","UPI on Delivery"):
        settings = con.execute("SELECT upi_id FROM store_settings WHERE id=1").fetchone()
        if not settings or not (settings["upi_id"] or "").strip():
            con.close()
            raise HTTPException(400, "UPI is not configured yet")
    if data.payment_method == "UPI Payment" and len(data.upi_reference.strip()) < 4:
        con.close()
        raise HTTPException(400, "Enter the UPI transaction/reference ID after payment")
    if data.payment_method == "UPI Payment" and data.upi_reference.strip():
        dup=con.execute("SELECT id FROM orders WHERE TRIM(upi_reference)=? LIMIT 1",(data.upi_reference.strip(),)).fetchone()
        if dup:
            con.close(); raise HTTPException(409,"This UPI reference/UTR has already been used on another order")
    if data.payment_method == "UPI on Delivery":
        data.upi_reference=""
    try:
        con.execute("BEGIN IMMEDIATE")
        for item in cart_rows:
            p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (item["product_id"],)).fetchone()
            if not p:
                raise HTTPException(404, f"Product {item['product_id']} unavailable")
            if p["stock"] < item["quantity"]:
                raise HTTPException(400, f"Only {p['stock']} left for {p['name']}")
            total += p["price"] * item["quantity"]
            resolved.append((p, item["quantity"]))
        cur = con.execute(
            "INSERT INTO orders(user_id,customer_name,phone,address,payment_method,status,total,original_total,adjustment_amount,upi_reference,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                user["id"],
                data.customer_name.strip(),
                data.phone.strip(),
                data.address.strip(),
                data.payment_method,
                "Placed",
                round(total, 2),
                round(total, 2),
                0.0,
                data.upi_reference.strip(),
                datetime.now().isoformat(),
            ),
        )
        oid = cur.lastrowid
        for p, qty in resolved:
            con.execute(
                "INSERT INTO order_items(order_id,product_id,product_name,unit,price,quantity,original_quantity) VALUES(?,?,?,?,?,?,?)",
                (oid, p["id"], p["name"], p["unit"], p["price"], qty, qty),
            )
            con.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, p["id"]))
            record_inventory(con,p["id"],oid,"ORDER_RESERVED",-qty,f"Order #{oid} placed")
        record_status(con,oid,"Placed",user["id"],"Order placed by customer")
        now=datetime.now().isoformat()
        initial_payment_status = "Pending Verification" if data.payment_method=="UPI Payment" else ("UPI Due on Delivery" if data.payment_method=="UPI on Delivery" else "Pay on Delivery")
        con.execute("INSERT INTO payment_records(order_id,payment_method,original_amount,current_amount,upi_reference,payment_status,refund_due,refund_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(oid,data.payment_method,round(total,2),round(total,2),data.upi_reference.strip(),initial_payment_status,0,"Not Required",now,now))
        con.execute("INSERT INTO payment_status_history(order_id,payment_status,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?)",(oid,initial_payment_status,user["id"],"Order payment method recorded",now))
        con.execute("DELETE FROM cart_items WHERE cart_id=?",(cart["id"],))
        con.commit()
    except HTTPException:
        con.rollback()
        con.close()
        raise
    con.close()
    background_tasks.add_task(send_order_status_email, oid, "Placed")
    return {"message": "Order placed", "order_id": oid, "total": round(total, 2), "email_notification": "queued"}


@app.get("/api/orders/me")
def my_orders(user=Depends(current_user)):
    con = db()
    orders = con.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    out = []
    for o in orders:
        d = dict(o)
        items = con.execute(
            "SELECT id,product_id,product_name,unit,price,quantity,original_quantity FROM order_items WHERE order_id=?", (o["id"],)
        ).fetchall()
        d["items"] = [dict(i) for i in items]
        returns=con.execute("SELECT * FROM return_requests WHERE order_id=? AND user_id=? ORDER BY id DESC",(o['id'],user['id'])).fetchall()
        d['returns']=[]
        for r in returns:
            rr=dict(r); rr['attachments']=attachment_list(con,'return',r['id']); d['returns'].append(rr)
        cancel=con.execute("SELECT * FROM cancellation_requests WHERE order_id=? AND user_id=?",(o['id'],user['id'])).fetchone()
        if cancel:
            cc=dict(cancel); cc['attachments']=attachment_list(con,'cancel',cancel['id']); d['cancellation']=cc
        else:
            d['cancellation']=None
        d['payment']=payment_record_dict(con,o['id'])
        d.update(order_financials(con,o))
        d['can_cancel']=o['status'] in ('Placed','Confirmed')
        d['can_return']=o['status']=='Delivered'
        d['status_history']=[dict(x) for x in con.execute("SELECT status,note,created_at FROM order_status_history WHERE order_id=? ORDER BY id",(o['id'],)).fetchall()]
        inv=con.execute("SELECT invoice_number,generated_at,emailed_at,email_status,revision_no,revision_reason FROM invoice_records WHERE order_id=?",(o['id'],)).fetchone()
        d['invoice']=dict(inv) if inv else None
        d['invoice_available']=bool(o['status']=='Delivered')
        out.append(d)
    con.close()
    return out


@app.post("/api/orders/{order_id}/cancel")
async def customer_cancel_order(order_id: int, background_tasks: BackgroundTasks, reason: str = Form(...), files: list[UploadFile] = File(default=[]), user=Depends(current_user)):
    reason=reason.strip()
    if len(reason)<3:
        raise HTTPException(400,"Please enter a cancellation reason")
    con=db()
    order=con.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,user['id'])).fetchone()
    if not order:
        con.close(); raise HTTPException(404,"Order not found")
    if order['status'] not in ('Placed','Confirmed'):
        con.close(); raise HTTPException(400,"This order can no longer be cancelled. Contact the store or request a return after delivery.")
    try:
        con.execute('BEGIN IMMEDIATE')
        cur=con.execute("INSERT INTO cancellation_requests(order_id,user_id,reason,status,created_at) VALUES(?,?,?,'Cancelled',?)",(order_id,user['id'],reason,datetime.now().isoformat()))
        cancel_id=cur.lastrowid
        await save_request_attachments(con,'cancel',cancel_id,files)
        cancel_order_in_tx(con,order_id,'customer')
        con.execute("UPDATE order_status_history SET note=? WHERE id=(SELECT MAX(id) FROM order_status_history WHERE order_id=?)",(f"Cancelled by customer: {reason}",order_id))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback(); con.close(); raise HTTPException(409,"This order already has a cancellation record")
    except Exception:
        con.rollback(); con.close(); raise
    con.close()
    background_tasks.add_task(send_order_status_email,order_id,'Cancelled')
    return {'message':'Order cancelled. Reserved stock has been restored.','email_notification':'queued'}


@app.post("/api/orders/{order_id}/return")
async def customer_return_request(order_id: int, background_tasks: BackgroundTasks, order_item_id: int = Form(...), quantity: int = Form(...), reason: str = Form(...), files: list[UploadFile] = File(default=[]), user=Depends(current_user)):
    if quantity < 1 or quantity > 50:
        raise HTTPException(400,'Invalid return quantity')
    reason=reason.strip()
    if len(reason)<3:
        raise HTTPException(400,'Please enter a return reason')
    con=db()
    order=con.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,user['id'])).fetchone()
    if not order:
        con.close(); raise HTTPException(404,'Order not found')
    if order['status']!='Delivered':
        con.close(); raise HTTPException(400,'Returns can be requested only after delivery')
    delivered_at=order['delivered_at'] or order['created_at']
    try: delivered_dt=datetime.fromisoformat(delivered_at)
    except Exception: delivered_dt=datetime.now()
    if datetime.now()-delivered_dt > timedelta(days=7):
        con.close(); raise HTTPException(400,'The 7-day return window has closed')
    item=con.execute("SELECT * FROM order_items WHERE id=? AND order_id=?",(order_item_id,order_id)).fetchone()
    if not item:
        con.close(); raise HTTPException(404,'Order item not found')
    existing=con.execute("SELECT COALESCE(SUM(quantity),0) q FROM return_requests WHERE order_item_id=? AND status IN ('Pending','Approved')",(item['id'],)).fetchone()['q']
    if existing + quantity > item['quantity']:
        con.close(); raise HTTPException(400,'Return quantity is higher than the delivered quantity')
    try:
        con.execute('BEGIN IMMEDIATE')
        cur=con.execute("INSERT INTO return_requests(order_id,order_item_id,user_id,quantity,reason,status,refund_amount,created_at,resolved_at) VALUES(?,?,?,?,?,'Pending',0,?, '')",(order_id,item['id'],user['id'],quantity,reason,datetime.now().isoformat()))
        rid=cur.lastrowid
        await save_request_attachments(con,'return',rid,files)
        con.commit()
    except Exception:
        con.rollback(); con.close(); raise
    con.close()
    background_tasks.add_task(send_return_email,rid,'Requested')
    return {'message':'Return request submitted','return_id':rid,'email_notification':'queued'}


@app.post("/api/orders/{order_id}/switch-to-upi")
def switch_to_upi_at_delivery(order_id: int, data: SwitchToUpiIn, background_tasks: BackgroundTasks, user=Depends(current_user)):
    con=db()
    dup=con.execute("SELECT id FROM orders WHERE TRIM(upi_reference)=? AND id<>? LIMIT 1",(data.upi_reference.strip(),order_id)).fetchone()
    if dup:
        con.close(); raise HTTPException(409,"This UPI reference/UTR has already been used on another order")
    order=con.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,user["id"])).fetchone()
    if not order:
        con.close(); raise HTTPException(404,"Order not found")
    if order["status"] != "Out for Delivery":
        con.close(); raise HTTPException(400,"UPI-at-delivery is available only when the order is Out for Delivery")
    if order["payment_method"] not in ("Cash on Delivery","UPI on Delivery"):
        con.close(); raise HTTPException(400,"This order is not eligible for UPI on Delivery")
    settings=con.execute("SELECT upi_id FROM store_settings WHERE id=1").fetchone()
    if not settings or not (settings["upi_id"] or "").strip():
        con.close(); raise HTTPException(400,"Store UPI ID is not configured")
    reference=data.upi_reference.strip()
    if len(reference)<4:
        con.close(); raise HTTPException(400,"Enter the UPI transaction/reference ID after payment")
    now=datetime.now().isoformat()
    old_method=order["payment_method"]
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE orders SET payment_method='UPI on Delivery',upi_reference=? WHERE id=?",(reference,order_id))
        refreshed=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        fin=order_financials(con,refreshed)
        existing=con.execute("SELECT * FROM payment_records WHERE order_id=?",(order_id,)).fetchone()
        if existing:
            con.execute("UPDATE payment_records SET payment_method='UPI on Delivery',current_amount=?,upi_reference=?,payment_status='Pending Verification',confirmed_at='',confirmed_by_user_id=NULL,payment_note=?,updated_at=? WHERE order_id=?",(fin["revised_total"],reference,"UPI on Delivery payment submitted",now,order_id))
        else:
            con.execute("INSERT INTO payment_records(order_id,payment_method,original_amount,current_amount,upi_reference,payment_status,refund_due,refund_status,created_at,updated_at) VALUES(?,?,?,?,?,'Pending Verification',0,'Not Required',?,?)",(order_id,'UPI on Delivery',fin['original_total'],fin['revised_total'],reference,now,now))
        if old_method!='UPI on Delivery':
            con.execute("INSERT INTO payment_method_history(order_id,old_method,new_method,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?,?)",(order_id,old_method,'UPI on Delivery',user['id'],'Customer chose UPI on Delivery',now))
        con.execute("INSERT INTO payment_status_history(order_id,payment_status,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?)",(order_id,'Pending Verification',user['id'],'UPI on Delivery payment submitted; waiting for store verification',now))
        con.commit()
    except Exception:
        con.rollback(); con.close(); raise
    con.close()
    background_tasks.add_task(send_upi_switch_submitted_email,order_id)
    return {"message":"UPI on Delivery payment submitted. Waiting for store verification.","payment_status":"Pending Verification","email_notification":"queued"}


@app.post("/api/orders/{order_id}/buy-again")
def buy_again(order_id:int, request:Request, user=Depends(current_user)):
    con=db(); order=con.execute("SELECT id FROM orders WHERE id=? AND user_id=?",(order_id,user["id"])).fetchone()
    if not order: con.close(); raise HTTPException(404,"Order not found")
    cart=get_or_create_cart(con,request,user["id"]); unavailable=[]; added=0; now=datetime.now().isoformat()
    for i in con.execute("SELECT product_id,product_name,quantity FROM order_items WHERE order_id=? AND quantity>0",(order_id,)).fetchall():
        pr=con.execute("SELECT id,name,stock,active FROM products WHERE id=?",(i["product_id"],)).fetchone()
        if not pr or not pr["active"] or pr["stock"]<=0:
            unavailable.append(i["product_name"]); continue
        qty=min(int(i["quantity"]),int(pr["stock"]),50)
        ex=con.execute("SELECT id,quantity FROM cart_items WHERE cart_id=? AND product_id=?",(cart["id"],pr["id"])).fetchone()
        if ex: con.execute("UPDATE cart_items SET quantity=?,updated_at=? WHERE id=?",(min(50,int(ex["quantity"])+qty),now,ex["id"]))
        else: con.execute("INSERT INTO cart_items(cart_id,product_id,quantity,created_at,updated_at) VALUES(?,?,?,?,?)",(cart["id"],pr["id"],qty,now,now))
        added+=1
    audit(con,user["id"],"BUY_AGAIN","order",order_id,f"Added {added} products to cart")
    con.commit(); payload=cart_payload(con,cart["id"]); con.close(); return {"message":"Previous order added to cart","cart":payload,"unavailable":unavailable}



@app.get("/api/orders/{order_id}/invoice.pdf")
def order_invoice_pdf(order_id:int, user=Depends(current_user)):
    con=db(); o=con.execute("SELECT user_id,status FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o: con.close(); raise HTTPException(404,"Order not found")
    if user["role"]!="admin" and o["user_id"]!=user["id"]: con.close(); raise HTTPException(403,"Not allowed")
    if o["status"]!="Delivered": con.close(); raise HTTPException(400,"Final PDF bill is available after successful delivery")
    con.close(); inv=ensure_invoice_record(order_id)
    return StreamingResponse(io.BytesIO(inv["pdf_data"]), media_type="application/pdf", headers={"Content-Disposition":f'inline; filename="{inv["file_name"]}"'})


@app.post("/api/admin/orders/{order_id}/resend-invoice")
def resend_invoice(order_id:int, background_tasks: BackgroundTasks, admin=Depends(admin_user)):
    con=db(); o=con.execute("SELECT status FROM orders WHERE id=?",(order_id,)).fetchone(); con.close()
    if not o: raise HTTPException(404,"Order not found")
    if o["status"]!="Delivered": raise HTTPException(400,"Final bill can be emailed only after successful delivery")
    ensure_invoice_record(order_id)
    background_tasks.add_task(send_delivery_invoice_email,order_id)
    return {"message":"Final bill email queued"}


@app.get("/api/orders/{order_id}/invoice", response_class=HTMLResponse)
def order_invoice(order_id:int, receipt:bool=False, user=Depends(current_user)):
    con=db(); o=con.execute("SELECT o.*,u.email customer_email FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=?",(order_id,)).fetchone()
    if not o: con.close(); raise HTTPException(404,"Order not found")
    if user["role"]!="admin" and o["user_id"]!=user["id"]: con.close(); raise HTTPException(403,"Not allowed")
    items=con.execute("SELECT product_name,unit,price,quantity FROM order_items WHERE order_id=? AND quantity>0 ORDER BY id",(order_id,)).fetchall(); fin=order_financials(con,o); settings=con.execute("SELECT * FROM store_settings WHERE id=1").fetchone(); con.close()
    rows=''.join(f"<tr><td>{i['product_name']}<small>{i['unit']}</small></td><td>{i['quantity']}</td><td>₹{float(i['price']):.2f}</td><td>₹{float(i['price'])*int(i['quantity']):.2f}</td></tr>" for i in items)
    width='320px' if receipt else '820px'
    html=f'''<!doctype html><html><head><meta charset="utf-8"><title>Order #{order_id} Invoice</title><style>body{{font-family:Arial,sans-serif;color:#17251d;background:#f4f7f5;margin:0;padding:24px}}.sheet{{max-width:{width};margin:auto;background:white;padding:26px;border-radius:12px}}h1{{color:#08783d;margin:0}}small{{display:block;color:#667}}table{{width:100%;border-collapse:collapse;margin-top:18px}}th,td{{padding:10px 6px;border-bottom:1px solid #ddd;text-align:left}}.totals{{margin-top:18px;text-align:right;line-height:1.9}}.free{{color:#08783d;font-weight:bold}}@media print{{body{{background:white;padding:0}}.sheet{{box-shadow:none;max-width:none}}.noprint{{display:none}}}}</style></head><body><div class="sheet"><button class="noprint" onclick="print()">Print</button><h1>Maneendra General Stores</h1><p>{settings['address']}<br>{settings['phone']} · {settings['email']}</p><hr><h2>{'Receipt' if receipt else 'Invoice'} · Order #{order_id}</h2><p><b>Customer:</b> {o['customer_name']}<br><b>Phone:</b> {o['phone']}<br><b>Delivery:</b> {o['address']}<br><b>Payment:</b> {o['payment_method']}<br><b>Status:</b> {o['status']}<br><b>Date:</b> {o['created_at']}</p><table><thead><tr><th>Item</th><th>Qty</th><th>Rate</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table><div class="totals">Products total: <b>₹{fin['revised_total']:.2f}</b><br>Delivery: <span class="free">FREE</span><br><strong>Total payable: ₹{fin['revised_total']:.2f}</strong></div><p><small>Thank you for shopping with Maneendra General Stores.</small></p></div></body></html>'''
    return HTMLResponse(html)


@app.get("/api/admin/customers")
def admin_customers(admin=Depends(admin_user)):
    con=db()
    rows=con.execute("""SELECT u.id,u.full_name,u.email,u.phone,u.address,u.created_at,u.updated_at,
                        COUNT(o.id) AS order_count,COALESCE(SUM(o.total),0) AS order_value
                        FROM users u LEFT JOIN orders o ON o.user_id=u.id
                        WHERE u.role='customer' GROUP BY u.id ORDER BY u.id DESC""").fetchall()
    con.close(); return [dict(r) for r in rows]


@app.get("/api/admin/database-summary")
def database_summary(admin=Depends(admin_user)):
    con=db()
    tables={
        "Customers": "SELECT COUNT(*) c FROM users WHERE role='customer'",
        "Products": "SELECT COUNT(*) c FROM products",
        "Carts": "SELECT COUNT(*) c FROM carts",
        "Cart items": "SELECT COUNT(*) c FROM cart_items",
        "Orders": "SELECT COUNT(*) c FROM orders",
        "Order items": "SELECT COUNT(*) c FROM order_items",
        "Order status history": "SELECT COUNT(*) c FROM order_status_history",
        "Order adjustments": "SELECT COUNT(*) c FROM order_adjustments",
        "Returns": "SELECT COUNT(*) c FROM return_requests",
        "Cancellations": "SELECT COUNT(*) c FROM cancellation_requests",
        "Request photos": "SELECT COUNT(*) c FROM request_attachments",
        "Payment status history": "SELECT COUNT(*) c FROM payment_status_history",
        "Payment method history": "SELECT COUNT(*) c FROM payment_method_history",
        "Inventory movements": "SELECT COUNT(*) c FROM inventory_movements",
        "Payment records": "SELECT COUNT(*) c FROM payment_records",
        "Email logs": "SELECT COUNT(*) c FROM email_logs",
        "Invoices": "SELECT COUNT(*) c FROM invoice_records",
        "Store settings": "SELECT COUNT(*) c FROM store_settings",
    }
    result={name:con.execute(sql).fetchone()["c"] for name,sql in tables.items()}
    con.close(); return result


@app.get("/api/admin/database/tables")
def admin_database_tables(admin=Depends(admin_user)):
    con = db()
    tables = []
    for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        name = row["name"]
        quoted = '"' + name.replace('"', '""') + '"'
        count = con.execute(f"SELECT COUNT(*) c FROM {quoted}").fetchone()["c"]
        columns = [dict(c) for c in con.execute(f"PRAGMA table_info({quoted})").fetchall()]
        tables.append({"name": name, "row_count": count, "columns": columns})
    con.close()
    return {"database_file": "store.db", "tables": tables}


@app.get("/api/admin/database/table/{table_name}")
def admin_database_table(table_name: str, limit: int = 50, offset: int = 0, admin=Depends(admin_user)):
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    con = db()
    name = _safe_table_name(con, table_name)
    quoted = '"' + name.replace('"', '""') + '"'
    columns = [dict(c) for c in con.execute(f"PRAGMA table_info({quoted})").fetchall()]
    total = con.execute(f"SELECT COUNT(*) c FROM {quoted}").fetchone()["c"]
    order_col = next((c["name"] for c in columns if c["pk"]), None)
    order_sql = f' ORDER BY "{order_col.replace(chr(34), chr(34)*2)}" DESC' if order_col else ''
    rows = con.execute(f"SELECT * FROM {quoted}{order_sql} LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    safe_rows = []
    for row in rows:
        d = dict(row)
        safe_rows.append({k: _masked_db_value(k, v) for k, v in d.items()})
    con.close()
    return {"table": name, "total": total, "limit": limit, "offset": offset, "columns": columns, "rows": safe_rows}


@app.post("/api/admin/products/{pid}/sync-real-image")
def admin_sync_real_product_image(pid: int, force_search: bool = False, admin=Depends(admin_user)):
    return sync_real_image_for_product(pid, force_search=force_search)


@app.post("/api/admin/products/{pid}/cache-current-image")
def admin_cache_current_product_image(pid: int, admin=Depends(admin_user)):
    con = db()
    product = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not product:
        con.close(); raise HTTPException(404, "Product not found")
    current = (product["image"] or "").strip()
    if not current.startswith("http"):
        con.close(); return {"status": "already_local", "product_id": pid, "image": current}
    con.close()
    return sync_real_image_for_product(pid, force_search=False)


@app.get("/api/admin/backup")
def backup_database(admin=Depends(admin_user)):
    con=db()
    audit(con,admin["id"],"DATABASE_BACKUP","database","store.db","Admin downloaded database backup")
    con.commit()
    # Flush WAL pages before serving the database file as a backup.
    con.execute("PRAGMA wal_checkpoint(FULL)")
    con.close()
    stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
    return FileResponse(DB_PATH,media_type="application/vnd.sqlite3",filename=f"maneendra-store-backup-{stamp}.db")


@app.get("/api/admin/audit-logs")
def admin_audit_logs(limit:int=100,admin=Depends(admin_user)):
    con=db(); rows=con.execute("SELECT a.*,u.full_name actor_name,u.email actor_email FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT ?",(min(max(limit,1),500),)).fetchall(); con.close(); return [dict(r) for r in rows]


@app.get("/api/admin/stats")
def stats(admin=Depends(admin_user)):
    con = db()
    today=datetime.now().date().isoformat()
    data = {
        "products": con.execute("SELECT COUNT(*) c FROM products WHERE active=1").fetchone()["c"],
        "customers": con.execute("SELECT COUNT(*) c FROM users WHERE role='customer'").fetchone()["c"],
        "orders": con.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"],
        "revenue": con.execute("SELECT COALESCE(SUM(total),0) s FROM orders WHERE status!='Cancelled'").fetchone()["s"],
        "low_stock": con.execute("SELECT COUNT(*) c FROM products WHERE active=1 AND stock<=reorder_level").fetchone()["c"],
        "out_of_stock": con.execute("SELECT COUNT(*) c FROM products WHERE active=1 AND stock<=0").fetchone()["c"],
        "today_orders": con.execute("SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?",(today,)).fetchone()["c"],
        "today_sales": con.execute("SELECT COALESCE(SUM(total),0) s FROM orders WHERE substr(created_at,1,10)=? AND status!='Cancelled'",(today,)).fetchone()["s"],
        "pending_upi": con.execute("SELECT COUNT(*) c FROM payment_records WHERE payment_status='Pending Verification'").fetchone()["c"],
        "out_for_delivery": con.execute("SELECT COUNT(*) c FROM orders WHERE status='Out for Delivery'").fetchone()["c"],
        "pending_returns": con.execute("SELECT COUNT(*) c FROM return_requests WHERE status='Pending'").fetchone()["c"],
    }
    con.close()
    return data


@app.get("/api/admin/orders")
def admin_orders(admin=Depends(admin_user)):
    con = db()
    rows = con.execute("SELECT o.*,u.email FROM orders o JOIN users u ON u.id=o.user_id ORDER BY o.id DESC").fetchall()
    out=[]
    for o in rows:
        d=dict(o)
        d['items']=[dict(i) for i in con.execute("SELECT id,product_id,product_name,unit,price,quantity,original_quantity FROM order_items WHERE order_id=? ORDER BY id",(o['id'],)).fetchall()]
        d['returns']=[]
        for r in con.execute("""SELECT rr.*,oi.product_name,oi.unit FROM return_requests rr JOIN order_items oi ON oi.id=rr.order_item_id WHERE rr.order_id=? ORDER BY rr.id DESC""",(o['id'],)).fetchall():
            rr=dict(r); rr['attachments']=attachment_list(con,'return',r['id']); d['returns'].append(rr)
        cancel=con.execute("SELECT * FROM cancellation_requests WHERE order_id=?",(o['id'],)).fetchone()
        if cancel:
            cc=dict(cancel); cc['attachments']=attachment_list(con,'cancel',cancel['id']); d['cancellation']=cc
        else:
            d['cancellation']=None
        d['payment']=payment_record_dict(con,o['id'])
        d.update(order_financials(con,o))
        d['otp_pending']=bool(o['status']=='Out for Delivery' and o['delivery_otp_hash'])
        inv=con.execute("SELECT invoice_number,generated_at,emailed_at,email_status,revision_no,revision_reason FROM invoice_records WHERE order_id=?",(o['id'],)).fetchone()
        d['invoice']=dict(inv) if inv else None
        d['invoice_available']=bool(o['status']=='Delivered')
        out.append(d)
    con.close()
    return out


@app.patch("/api/admin/orders/{order_id}/status")
def order_status(order_id: int, status: str, background_tasks: BackgroundTasks, admin=Depends(admin_user)):
    allowed = {"Placed", "Confirmed", "Packed", "Out for Delivery", "Cancelled"}
    if status == 'Delivered':
        raise HTTPException(400, 'Use delivery OTP verification to mark the order Delivered')
    if status not in allowed:
        raise HTTPException(400, "Invalid status")
    con = db()
    row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        con.close(); raise HTTPException(404, "Order not found")
    previous = row["status"]
    otp=None
    try:
        con.execute('BEGIN IMMEDIATE')
        if status=='Cancelled' and previous!='Cancelled':
            cancel_order_in_tx(con,order_id,'admin')
        elif previous != status:
            if previous in ('Cancelled','Delivered'):
                raise HTTPException(400,f'Cannot change a {previous} order')
            if status=='Out for Delivery':
                otp=f"{secrets.randbelow(1000000):06d}"
                expires=(datetime.now()+timedelta(hours=24)).isoformat()
                con.execute("UPDATE orders SET status=?,delivery_otp_hash=?,delivery_otp_expires_at=?,delivery_otp_sent_at=? WHERE id=?",(status,otp_hash(otp),expires,datetime.now().isoformat(),order_id))
            else:
                con.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
            record_status(con,order_id,status,admin["id"],"Updated by admin")
            audit(con,admin["id"],"ORDER_STATUS_CHANGED","order",order_id,f"{previous} → {status}")
            update_payment_record(con,order_id)
        con.commit()
    except HTTPException:
        con.rollback(); con.close(); raise
    con.close()
    if previous != status:
        background_tasks.add_task(send_order_status_email, order_id, status)
        if otp:
            background_tasks.add_task(send_delivery_otp_email,order_id,otp)
        return {"message": "Status updated", "email_notification": "queued", "otp_email": bool(otp)}
    return {"message": "Status unchanged"}


@app.patch("/api/admin/orders/{order_id}/items")
def admin_edit_order(order_id: int, data: AdminOrderEditIn, background_tasks: BackgroundTasks, admin=Depends(admin_user)):
    con=db()
    order=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404,'Order not found')
    if order['status'] in ('Out for Delivery','Delivered','Cancelled'):
        con.close(); raise HTTPException(400,'Order items can be edited only before Out for Delivery')
    current={i['id']:i for i in con.execute("SELECT * FROM order_items WHERE order_id=?",(order_id,)).fetchall()}
    if not data.items:
        con.close(); raise HTTPException(400,'No item quantities supplied')
    try:
        con.execute('BEGIN IMMEDIATE')
        for req in data.items:
            item=current.get(req.order_item_id)
            if not item: raise HTTPException(404,f'Order item {req.order_item_id} not found')
            old=int(item['quantity']); new=int(req.quantity)
            if new>old:
                raise HTTPException(400,'This screen is for unavailable stock: quantities can only be reduced')
            if new<old:
                diff=old-new
                con.execute("UPDATE products SET stock=stock+? WHERE id=?",(diff,item['product_id']))
                con.execute("UPDATE order_items SET quantity=? WHERE id=?",(new,item['id']))
                con.execute("INSERT INTO order_adjustments(order_id,order_item_id,product_id,old_quantity,new_quantity,amount_difference,reason,changed_by_user_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(order_id,item['id'],item['product_id'],old,new,round(float(item['price'])*diff,2),'Unavailable stock',admin['id'],datetime.now().isoformat()))
                record_inventory(con,item['product_id'],order_id,'ORDER_ADJUST_RESTORE',diff,'Unavailable quantity removed by admin')
        new_total=con.execute("SELECT COALESCE(SUM(price*quantity),0) t FROM order_items WHERE order_id=?",(order_id,)).fetchone()['t']
        original=float(order['original_total'] or order['total'])
        new_total=round(float(new_total or 0),2)
        if new_total<=0:
            con.execute("UPDATE orders SET total=0,adjustment_amount=?,status='Cancelled',cancelled_at=? WHERE id=?",(round(original,2),datetime.now().isoformat(),order_id))
        else:
            con.execute("UPDATE orders SET total=?,adjustment_amount=? WHERE id=?",(new_total,round(max(0,original-new_total),2),order_id))
        audit(con,admin["id"],"ORDER_ITEMS_EDITED","order",order_id,f"Revised total ₹{new_total:.2f}")
        if new_total<=0:
            record_status(con,order_id,'Cancelled',admin['id'],'All items removed as unavailable')
        update_payment_record(con,order_id)
        con.commit()
    except HTTPException:
        con.rollback(); con.close(); raise
    updated=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    financials=order_financials(con,updated)
    con.close()
    if new_total<=0:
        background_tasks.add_task(send_order_status_email,order_id,'Cancelled')
    else:
        background_tasks.add_task(send_order_adjusted_email,order_id)
    return {'message':'Order quantities updated','financials':financials,'email_notification':'queued'}


@app.post("/api/admin/orders/{order_id}/verify-delivery")
def verify_delivery(order_id: int, data: DeliveryOtpIn, background_tasks: BackgroundTasks, admin=Depends(admin_user)):
    con=db()
    order=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404,'Order not found')
    if order['status']!='Out for Delivery':
        con.close(); raise HTTPException(400,'Order is not Out for Delivery')
    if not order['delivery_otp_hash']:
        con.close(); raise HTTPException(400,'No delivery OTP is active')
    try: expires=datetime.fromisoformat(order['delivery_otp_expires_at'])
    except Exception: expires=datetime.min
    if datetime.now()>expires:
        con.close(); raise HTTPException(400,'Delivery OTP has expired. Set Out for Delivery again to send a new OTP.')
    if not hmac.compare_digest(order['delivery_otp_hash'],otp_hash(data.otp.strip())):
        con.close(); raise HTTPException(400,'Invalid delivery OTP')
    if order['payment_method'] in ('UPI Payment','UPI on Delivery'):
        payment=con.execute("SELECT payment_status FROM payment_records WHERE order_id=?",(order_id,)).fetchone()
        if not payment or payment['payment_status']!='Payment Confirmed':
            con.close(); raise HTTPException(400,'UPI payment is not confirmed yet. Verify the payment first, then verify the delivery OTP.')
    con.execute("UPDATE orders SET status='Delivered',delivered_at=?,delivery_otp_hash='' WHERE id=?",(datetime.now().isoformat(),order_id))
    record_status(con,order_id,'Delivered',admin['id'],'Delivery OTP verified')
    update_payment_record(con,order_id)
    if order['payment_method']=='Cash on Delivery':
        con.execute("INSERT INTO payment_status_history(order_id,payment_status,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?)",(order_id,'Cash Collected',admin['id'],'Delivery OTP verified; COD treated as collected',datetime.now().isoformat()))
    con.commit(); con.close()
    background_tasks.add_task(send_delivery_invoice_email,order_id)
    return {'message':'OTP verified. Order marked Delivered. Final PDF bill email queued.','email_notification':'invoice_queued'}


@app.get("/api/request-attachments/{attachment_id}")
def request_attachment(attachment_id: int, user=Depends(current_user)):
    con=db()
    a=con.execute("SELECT * FROM request_attachments WHERE id=?",(attachment_id,)).fetchone()
    if not a:
        con.close(); raise HTTPException(404,"Attachment not found")
    owner_id=None
    if a['request_type']=='return':
        r=con.execute("SELECT user_id FROM return_requests WHERE id=?",(a['request_id'],)).fetchone(); owner_id=r['user_id'] if r else None
    elif a['request_type']=='cancel':
        r=con.execute("SELECT user_id FROM cancellation_requests WHERE id=?",(a['request_id'],)).fetchone(); owner_id=r['user_id'] if r else None
    if user['role']!='admin' and owner_id!=user['id']:
        con.close(); raise HTTPException(403,"You cannot view this attachment")
    data=bytes(a['file_data']); media=a['content_type']; filename=a['filename']; con.close()
    return StreamingResponse(io.BytesIO(data),media_type=media,headers={"Content-Disposition":f'inline; filename="{filename}"',"Cache-Control":"private, no-store"})


@app.patch("/api/admin/orders/{order_id}/payment")
def admin_update_payment(order_id: int, data: PaymentStatusIn, background_tasks: BackgroundTasks, admin=Depends(admin_user)):
    status=data.status.strip()
    if status not in ('Payment Confirmed','Pending Verification'):
        raise HTTPException(400,'Invalid payment status')
    con=db()
    order=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    payment=con.execute("SELECT * FROM payment_records WHERE order_id=?",(order_id,)).fetchone()
    if not order or not payment:
        con.close(); raise HTTPException(404,'Order payment record not found')
    if order['payment_method'] not in ('UPI Payment','UPI on Delivery'):
        con.close(); raise HTTPException(400,'Manual payment confirmation is for UPI orders')
    now=datetime.now().isoformat()
    confirmed_at=now if status=='Payment Confirmed' else ''
    confirmed_by=admin['id'] if status=='Payment Confirmed' else None
    con.execute("UPDATE payment_records SET payment_status=?,confirmed_at=?,confirmed_by_user_id=?,payment_note=?,updated_at=? WHERE order_id=?",(status,confirmed_at,confirmed_by,data.note.strip(),now,order_id))
    con.execute("INSERT INTO payment_status_history(order_id,payment_status,changed_by_user_id,note,created_at) VALUES(?,?,?,?,?)",(order_id,status,admin['id'],data.note.strip() or ('UPI payment checked and received' if status=='Payment Confirmed' else 'Payment moved back to pending verification'),now))
    audit(con,admin["id"],"PAYMENT_STATUS_CHANGED","order",order_id,status)
    con.commit(); con.close()
    if status=='Payment Confirmed':
        background_tasks.add_task(send_payment_confirmation_email,order_id)
    return {'message':'Payment status updated','payment_status':status,'email_notification':'queued' if status=='Payment Confirmed' else 'not_sent'}


@app.patch("/api/admin/returns/{return_id}")
def resolve_return(return_id: int, data: ReturnResolutionIn, background_tasks: BackgroundTasks, admin=Depends(admin_user)):
    decision=data.status.strip().title()
    if decision not in ('Approved','Rejected'):
        raise HTTPException(400,'Return status must be Approved or Rejected')
    con=db()
    r=con.execute("""SELECT rr.*,oi.product_id,oi.price,oi.quantity delivered_qty FROM return_requests rr JOIN order_items oi ON oi.id=rr.order_item_id WHERE rr.id=?""",(return_id,)).fetchone()
    if not r:
        con.close(); raise HTTPException(404,'Return request not found')
    if r['status']!='Pending':
        con.close(); raise HTTPException(400,'Return request has already been resolved')
    refund=round(float(r['price'])*int(r['quantity']),2) if decision=='Approved' else 0.0
    try:
        con.execute('BEGIN IMMEDIATE')
        if decision=='Approved':
            con.execute("UPDATE products SET stock=stock+? WHERE id=?",(r['quantity'],r['product_id']))
            record_inventory(con,r['product_id'],r['order_id'],'RETURN_RESTOCK',int(r['quantity']),f'Return #{return_id} approved')
        con.execute("UPDATE return_requests SET status=?,refund_amount=?,refund_status=?,resolved_at=? WHERE id=?",(decision,refund,'Pending' if decision=='Approved' and refund>0 else 'Not Required',datetime.now().isoformat(),return_id))
        audit(con,admin["id"],"RETURN_RESOLVED","return",return_id,f"{decision} · refund ₹{refund:.2f}")
        update_payment_record(con,r['order_id'])
        con.commit()
    except Exception:
        con.rollback(); con.close(); raise
    con.close()
    background_tasks.add_task(send_return_email,return_id,decision)
    if decision == 'Approved':
        background_tasks.add_task(send_revised_return_invoice_email, r['order_id'], return_id)
    return {'message':f'Return {decision.lower()}','refund_amount':refund,'email_notification':'queued','revised_bill_email':'queued' if decision=='Approved' else 'not_sent'}


@app.patch("/api/admin/returns/{return_id}/refund-status")
def update_return_refund_status(return_id:int, data:RefundStatusIn, background_tasks:BackgroundTasks, admin=Depends(admin_user)):
    status=data.status.strip().title()
    if status not in ("Pending","Refunded","Not Required"):
        raise HTTPException(400,"Invalid refund status")
    con=db(); r=con.execute("SELECT * FROM return_requests WHERE id=?",(return_id,)).fetchone()
    if not r:
        con.close(); raise HTTPException(404,"Return request not found")
    if r["status"]!="Approved" and status!="Not Required":
        con.close(); raise HTTPException(400,"Only approved returns can be marked for refund")
    con.execute("UPDATE return_requests SET refund_status=?,admin_note=? WHERE id=?",(status,data.note.strip(),return_id))
    audit(con,admin["id"],"RETURN_REFUND_STATUS","return",return_id,f"{status} · {data.note.strip()}")
    con.commit(); con.close()
    if status=="Refunded": background_tasks.add_task(send_return_email,return_id,"Refunded")
    return {"message":"Refund status updated","refund_status":status}


@app.get("/api/admin/products")
def admin_products(admin=Depends(admin_user)):
    con = db()
    rows = con.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.post("/api/admin/products")
def add_product(data: ProductIn, admin=Depends(admin_user)):
    con = db()
    cur = con.execute(
        "INSERT INTO products(name,category,unit,price,stock,image,description,active,created_at,mrp,purchase_price,reorder_level,sku,barcode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (data.name, data.category, data.unit, data.price, data.stock, data.image, data.description, 1 if data.active else 0, datetime.now().isoformat(), data.mrp or data.price, data.purchase_price, data.reorder_level, data.sku.strip(), data.barcode.strip()),
    )
    pid = cur.lastrowid
    record_inventory(con,pid,None,"PRODUCT_CREATED",int(data.stock),"Product created in Admin dashboard")
    audit(con,admin["id"],"PRODUCT_CREATED","product",pid,f"{data.name} · selling ₹{data.price}")
    con.commit()
    con.close()
    return {"id": pid, "message": "Product added"}


@app.put("/api/admin/products/{pid}")
def update_product(pid: int, data: ProductIn, admin=Depends(admin_user)):
    con = db()
    old=con.execute("SELECT stock FROM products WHERE id=?",(pid,)).fetchone()
    cur = con.execute(
        "UPDATE products SET name=?,category=?,unit=?,price=?,stock=?,image=?,description=?,active=?,mrp=?,purchase_price=?,reorder_level=?,sku=?,barcode=? WHERE id=?",
        (data.name, data.category, data.unit, data.price, data.stock, data.image, data.description, 1 if data.active else 0, data.mrp or data.price, data.purchase_price, data.reorder_level, data.sku.strip(), data.barcode.strip(), pid),
    )
    if old and int(old["stock"])!=int(data.stock):
        record_inventory(con,pid,None,"ADMIN_STOCK_EDIT",int(data.stock)-int(old["stock"]),"Stock edited from Admin dashboard")
    audit(con,admin["id"],"PRODUCT_UPDATED","product",pid,f"{data.name} · selling ₹{data.price} · stock {data.stock}")
    con.commit()
    con.close()
    if not cur.rowcount:
        raise HTTPException(404, "Product not found")
    return {"message": "Product updated"}


@app.post("/api/admin/upload-image")
async def upload_product_image(file: UploadFile = File(...), admin=Depends(admin_user)):
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if file.content_type not in allowed:
        raise HTTPException(400, "Please upload a JPG, PNG or WEBP image")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image must be 5 MB or smaller")
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(file.filename or "product").stem).strip("-") or "product"
    filename = f"{stem}-{secrets.token_hex(5)}{allowed[file.content_type]}"
    (UPLOAD_DIR / filename).write_bytes(content)
    return {"url": f"/uploads/{filename}"}


@app.delete("/api/admin/products/{pid}")
def delete_product(pid: int, admin=Depends(admin_user)):
    con = db()
    cur = con.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
    con.commit()
    con.close()
    if not cur.rowcount:
        raise HTTPException(404, "Product not found")
    return {"message": "Product hidden"}
