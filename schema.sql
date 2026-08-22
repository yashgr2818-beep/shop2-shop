CREATE TABLE IF NOT EXISTS tbl_managers (
    manager_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_name TEXT NOT NULL,
    shop_slug TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    is_suspended INTEGER DEFAULT 0,
    whatsapp_orders_enabled INTEGER DEFAULT 1,
    price_mandatory INTEGER DEFAULT 1,
    bulk_upload_enabled INTEGER DEFAULT 1,
    show_price INTEGER DEFAULT 1,
    secure_url_mode INTEGER DEFAULT 0,
    qr_image_url TEXT
);

CREATE TABLE IF NOT EXISTS tbl_blocked_ips (
    ip_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT UNIQUE NOT NULL,
    reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tbl_staff_accounts (
    staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id INTEGER NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('Order_Only', 'Stock_Only', 'Full')),
    is_active INTEGER DEFAULT 1,
    last_active DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (manager_id) REFERENCES tbl_managers(manager_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_visitor_sessions (
    visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id INTEGER NOT NULL,
    session_token TEXT UNIQUE NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    visited_url TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    FOREIGN KEY (manager_id) REFERENCES tbl_managers(manager_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_customer_sessions (
    session_id TEXT PRIMARY KEY,
    shop_slug TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_active DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tbl_products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id INTEGER,
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    price_inr REAL NOT NULL,
    stock_qty INTEGER DEFAULT 0,
    packed_qty INTEGER DEFAULT 0,
    show_price INTEGER DEFAULT 1,
    status TEXT DEFAULT 'Active' CHECK(status IN ('Active', 'Inactive', 'Suspended', 'Draft')),
    image_path TEXT DEFAULT 'placeholder.jpg',
    FOREIGN KEY(manager_id) REFERENCES tbl_managers(manager_id) ON DELETE CASCADE,
    UNIQUE(manager_id, sku)
);

CREATE TABLE IF NOT EXISTS tbl_cart_items (
    cart_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    product_id INTEGER,
    quantity INTEGER DEFAULT 1,
    FOREIGN KEY(session_id) REFERENCES tbl_customer_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES tbl_products(product_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    email TEXT,
    pin_hash TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(manager_id) REFERENCES tbl_managers(manager_id) ON DELETE CASCADE,
    UNIQUE(manager_id, phone_number)
);

CREATE TABLE IF NOT EXISTS tbl_orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_uuid TEXT UNIQUE,
    manager_id INTEGER,
    customer_name TEXT,
    customer_phone TEXT,
    customer_email TEXT,
    customer_pin TEXT,
    total_amount REAL,
    status TEXT DEFAULT 'Pending',
    payment_status TEXT DEFAULT 'Unpaid',
    payment_method TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(manager_id) REFERENCES tbl_managers(manager_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    product_id INTEGER,
    quantity INTEGER,
    price_at_time REAL,
    FOREIGN KEY(order_id) REFERENCES tbl_orders(order_id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES tbl_products(product_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_product_views (
    view_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    session_id TEXT,
    viewed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES tbl_products(product_id) ON DELETE CASCADE
);

-- Performance indexes (critical for fast lookups at scale)
CREATE INDEX IF NOT EXISTS idx_products_manager_status ON tbl_products(manager_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_manager_created  ON tbl_orders(manager_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status           ON tbl_orders(manager_id, status);
CREATE INDEX IF NOT EXISTS idx_order_items_order       ON tbl_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product     ON tbl_order_items(product_id);
CREATE INDEX IF NOT EXISTS idx_visitor_sessions_mgr    ON tbl_visitor_sessions(manager_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_visitor_token           ON tbl_visitor_sessions(session_token);
CREATE INDEX IF NOT EXISTS idx_cart_session            ON tbl_cart_items(session_id);
CREATE INDEX IF NOT EXISTS idx_product_views_product   ON tbl_product_views(product_id);
CREATE INDEX IF NOT EXISTS idx_staff_manager           ON tbl_staff_accounts(manager_id);
CREATE INDEX IF NOT EXISTS idx_customers_phone         ON tbl_customers(manager_id, phone_number);

