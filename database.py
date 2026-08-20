import os
import sqlite3
from flask import g, current_app

try:
    import libsql
    LIBSQL_AVAILABLE = True
except ImportError:
    libsql = None
    LIBSQL_AVAILABLE = False


def _handle_libsql_error(e):
    """Translate LibSQL / Hrana error messages into standard sqlite3 exception types."""
    err_str = str(e)
    if any(k in err_str for k in [
        "SQLITE_CONSTRAINT",
        "UNIQUE constraint failed",
        "FOREIGN KEY constraint failed",
        "CHECK constraint failed",
        "NOT NULL constraint failed",
        "PRIMARY KEY must be unique",
    ]):
        raise sqlite3.IntegrityError(err_str) from e
    if any(k in err_str for k in ["SQLITE_BUSY", "SQLITE_LOCKED", "database is locked"]):
        raise sqlite3.OperationalError(err_str) from e
    if any(k in err_str for k in ["no such table", "no such column"]):
        raise sqlite3.OperationalError(err_str) from e
    raise e


class LibsqlRow:
    """Drop-in replacement for sqlite3.Row for LibSQL responses."""
    def __init__(self, cols, values):
        self._cols = list(cols)
        self._col_map = {c.lower(): i for i, c in enumerate(cols)} if cols else {}
        self._values = tuple(values) if values is not None else ()

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, str):
            idx = self._col_map.get(key.lower())
            if idx is not None:
                return self._values[idx]
            raise KeyError(key)
        raise TypeError(f"Row indices must be integers or strings, not {type(key).__name__}")

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __contains__(self, key):
        if isinstance(key, str):
            return key.lower() in self._col_map
        return key in self._values

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return list(self._cols)

    def values(self):
        return list(self._values)

    def items(self):
        return list(zip(self._cols, self._values))

    def __repr__(self):
        return f"<LibsqlRow {dict(self.items())}>"


class LibsqlCursorWrapper:
    """Cursor wrapper that converts tuple results to LibsqlRow instances and maps exceptions."""
    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _wrap_row(self, row):
        if row is None:
            return None
        cols = [col[0] for col in self._cur.description] if self._cur.description else []
        return LibsqlRow(cols, row)

    def execute(self, sql, params=None):
        try:
            if params is not None:
                self._cur.execute(sql, params)
            else:
                self._cur.execute(sql)
            return self
        except Exception as e:
            _handle_libsql_error(e)

    def executemany(self, sql, seq_of_params):
        try:
            self._cur.executemany(sql, seq_of_params)
            return self
        except Exception as e:
            _handle_libsql_error(e)

    def executescript(self, sql_script):
        try:
            self._cur.executescript(sql_script)
            return self
        except Exception as e:
            _handle_libsql_error(e)

    def fetchone(self):
        row = self._cur.fetchone()
        return self._wrap_row(row)

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows:
            return []
        cols = [col[0] for col in self._cur.description] if self._cur.description else []
        return [LibsqlRow(cols, r) for r in rows]

    def fetchmany(self, size=None):
        rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        if not rows:
            return []
        cols = [col[0] for col in self._cur.description] if self._cur.description else []
        return [LibsqlRow(cols, r) for r in rows]

    def __iter__(self):
        for row in self._cur:
            yield self._wrap_row(row)

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass


class LibsqlConnectionWrapper:
    """Connection wrapper supporting standard sqlite3 operations and mapping exceptions."""
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._closed = False
        self.row_factory = None

    def cursor(self):
        return LibsqlCursorWrapper(self._conn.cursor())

    def execute(self, sql, params=None):
        try:
            cur = self._conn.cursor()
            if params is not None:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return LibsqlCursorWrapper(cur)
        except Exception as e:
            _handle_libsql_error(e)

    def executemany(self, sql, seq_of_params):
        try:
            cur = self._conn.cursor()
            cur.executemany(sql, seq_of_params)
            return LibsqlCursorWrapper(cur)
        except Exception as e:
            _handle_libsql_error(e)

    def executescript(self, sql_script):
        try:
            self._conn.executescript(sql_script)
        except Exception as e:
            _handle_libsql_error(e)

    def commit(self):
        try:
            self._conn.commit()
        except Exception as e:
            _handle_libsql_error(e)

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception as e:
            _handle_libsql_error(e)

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._conn.close()
            except Exception:
                pass


def is_turso_configured(app=None):
    """Check if Turso Cloud DB URL and Auth Token are configured."""
    url = (app.config.get('TURSO_DATABASE_URL') if app else None) or os.environ.get('TURSO_DATABASE_URL')
    token = (app.config.get('TURSO_AUTH_TOKEN') if app else None) or os.environ.get('TURSO_AUTH_TOKEN')
    return bool(url and token and url.strip() and token.strip())


def get_db():
    """Retrieve or create the database connection for current request context."""
    db = getattr(g, '_database', None)
    if db is None or getattr(db, '_closed', False):
        url = current_app.config.get('TURSO_DATABASE_URL') or os.environ.get('TURSO_DATABASE_URL')
        token = current_app.config.get('TURSO_AUTH_TOKEN') or os.environ.get('TURSO_AUTH_TOKEN')

        if LIBSQL_AVAILABLE and url and token and url.strip() and token.strip():
            raw_conn = libsql.connect(url.strip(), auth_token=token.strip())
            db = g._database = LibsqlConnectionWrapper(raw_conn)
        else:
            db = g._database = sqlite3.connect(current_app.config['DATABASE'])
            db.row_factory = sqlite3.Row
            # Enable foreign key support
            db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db(app):
    """Initialize database tables and run necessary migrations."""
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())

        # Column migration helpers for existing databases
        cursor = db.cursor()
        cols = [col[1] for col in cursor.execute("PRAGMA table_info(tbl_products)").fetchall()]
        if 'packed_qty' not in cols:
            cursor.execute("ALTER TABLE tbl_products ADD COLUMN packed_qty INTEGER DEFAULT 0")
        if 'show_price' not in cols:
            cursor.execute("ALTER TABLE tbl_products ADD COLUMN show_price INTEGER DEFAULT 1")

        cols_staff = [col[1] for col in cursor.execute("PRAGMA table_info(tbl_staff_accounts)").fetchall()]
        if 'is_active' not in cols_staff:
            cursor.execute("ALTER TABLE tbl_staff_accounts ADD COLUMN is_active INTEGER DEFAULT 1")
        if 'last_active' not in cols_staff:
            cursor.execute("ALTER TABLE tbl_staff_accounts ADD COLUMN last_active DATETIME")

        cols_mgr = [col[1] for col in cursor.execute("PRAGMA table_info(tbl_managers)").fetchall()]
        if 'whatsapp_orders_enabled' not in cols_mgr:
            cursor.execute("ALTER TABLE tbl_managers ADD COLUMN whatsapp_orders_enabled INTEGER DEFAULT 1")
        if 'price_mandatory' not in cols_mgr:
            cursor.execute("ALTER TABLE tbl_managers ADD COLUMN price_mandatory INTEGER DEFAULT 1")
        if 'bulk_upload_enabled' not in cols_mgr:
            cursor.execute("ALTER TABLE tbl_managers ADD COLUMN bulk_upload_enabled INTEGER DEFAULT 1")
        if 'show_price' not in cols_mgr:
            cursor.execute("ALTER TABLE tbl_managers ADD COLUMN show_price INTEGER DEFAULT 1")
        if 'secure_url_mode' not in cols_mgr:
            cursor.execute("ALTER TABLE tbl_managers ADD COLUMN secure_url_mode INTEGER DEFAULT 0")

        # Migrate tbl_products check constraint if needed
        table_sql = cursor.execute("SELECT sql FROM sqlite_master WHERE name='tbl_products'").fetchone()
        if table_sql and "CHECK(status IN ('Active', 'Draft'))" in table_sql[0]:
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute("""
                CREATE TABLE tbl_products_new (
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
                )
            """)
            cursor.execute("""
                INSERT INTO tbl_products_new (product_id, manager_id, sku, name, description, price_inr, stock_qty, packed_qty, show_price, status, image_path)
                SELECT product_id, manager_id, sku, name, description, price_inr, stock_qty, packed_qty, show_price, status, image_path FROM tbl_products
            """)
            cursor.execute("DROP TABLE tbl_products")
            cursor.execute("ALTER TABLE tbl_products_new RENAME TO tbl_products")
            cursor.execute("PRAGMA foreign_keys=ON")

        db.commit()


def close_connection(exception):
    """Clean up active DB connection at the end of the request context."""
    db = g.pop('_database', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
