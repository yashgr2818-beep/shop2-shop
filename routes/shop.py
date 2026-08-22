from flask import Blueprint, render_template, abort, redirect, url_for, request, session, jsonify, flash
from database import get_db
from datetime import datetime, timedelta
from services.qr_service import get_client_ip, get_local_ip
from services.mail_service import send_email_otp, generate_otp, is_mail_configured
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import random
import time
import re
import os
import urllib.parse
from collections import defaultdict

bp = Blueprint('shop', __name__, url_prefix='/shop')

def record_visitor_activity(manager_id, shop_slug, is_scan=False, visited_url=None):
    """Records new visitor scan/visit or refreshes active sliding window (15 mins) with exact URL rendering tracking."""
    db = get_db()
    ip_addr = get_client_ip(request)
    user_agent = request.headers.get('User-Agent', '')
    url_rendered = visited_url or (request.path if request else f"/shop/{shop_slug}")
    now = datetime.utcnow()
    now_ts = now.timestamp()
    expires = (now + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
    
    session_key = f"visitor_token_{shop_slug}"
    refresh_key = f"visitor_refreshed_{shop_slug}"
    existing_token = session.get(session_key)
    last_refresh = session.get(refresh_key, 0)
    
    if existing_token and not is_scan:
        # Refresh if time expired or user navigated to a different page/url
        if (now_ts - last_refresh) > 60 or session.get(f"last_url_{shop_slug}") != url_rendered:
            try:
                db.execute(
                    "UPDATE tbl_visitor_sessions SET expires_at = ?, ip_address = COALESCE(NULLIF(ip_address, ''), ?), visited_url = ? WHERE session_token = ? AND manager_id = ?",
                    (expires, ip_addr, url_rendered, existing_token, manager_id)
                )
                db.commit()
                session[refresh_key] = now_ts
                session[f"last_url_{shop_slug}"] = url_rendered
            except Exception:
                pass
    else:
        # New scan or first visit
        new_token = uuid.uuid4().hex
        session[session_key] = new_token
        session[refresh_key] = now_ts
        session[f"last_url_{shop_slug}"] = url_rendered
        try:
            db.execute('''
                INSERT INTO tbl_visitor_sessions (manager_id, session_token, ip_address, user_agent, visited_url, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (manager_id, new_token, ip_addr, user_agent, url_rendered, expires))
            db.commit()
        except Exception as e:
            print(f"Error logging visitor session: {e}")

def get_or_create_customer_session(shop_slug):
    """Retrieves or creates a persistent customer session with throttled activity timestamps."""
    db = get_db()
    session_key = f"customer_session_{shop_slug}"
    active_key = f"cust_active_{shop_slug}"
    now_ts = datetime.utcnow().timestamp()
    last_active = session.get(active_key, 0)
    
    if session_key not in session:
        session_id = str(uuid.uuid4())
        session[session_key] = session_id
        session[active_key] = now_ts
        try:
            db.execute(
                'INSERT INTO tbl_customer_sessions (session_id, shop_slug) VALUES (?, ?)',
                (session_id, shop_slug)
            )
            db.commit()
        except Exception:
            pass
    elif (now_ts - last_active) > 300: # Only update activity timestamp once every 5 minutes
        session[active_key] = now_ts
        try:
            db.execute(
                "UPDATE tbl_customer_sessions SET last_active = CURRENT_TIMESTAMP WHERE session_id=?",
                (session[session_key],)
            )
            db.commit()
        except Exception:
            pass
    
    return session[session_key]

def _get_customer_session_auth(db, manager_id, shop_slug):
    """Helper: returns (auth_phone, logged_in_customer_dict) for current request session."""
    auth_phone = session.get(f'customer_auth_{shop_slug}')
    if not auth_phone:
        return None, None
    customer = db.execute(
        'SELECT * FROM tbl_customers WHERE manager_id = ? AND phone_number = ?',
        (manager_id, auth_phone)
    ).fetchone()
    return auth_phone, customer


# ── Public catalog ────────────────────────────────────────────────────────
@bp.route('/<shop_slug>')
def catalog(shop_slug):
    db = get_db()
    manager = db.execute(
        'SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0',
        (shop_slug,)
    ).fetchone()
    if manager is None:
        abort(404)

    # Track visitor session
    is_scan = (request.args.get('source') == 'qr')
    record_visitor_activity(manager['manager_id'], shop_slug, is_scan=is_scan)

    products = db.execute(
        "SELECT * FROM tbl_products WHERE manager_id=? AND status='Active'",
        (manager['manager_id'],)
    ).fetchall()

    session_id = get_or_create_customer_session(shop_slug)
    
    # Get cart items map: {product_id: quantity}
    cart_rows = db.execute(
        'SELECT product_id, quantity FROM tbl_cart_items WHERE session_id=?',
        (session_id,)
    ).fetchall()
    cart_quantities = {row['product_id']: row['quantity'] for row in cart_rows}
    cart_count = sum(cart_quantities.values())

    local_ip = get_local_ip()

    # Check customer auth
    auth_phone, logged_in_customer = _get_customer_session_auth(db, manager['manager_id'], shop_slug)

    return render_template(
        'shop/catalog.html',
        manager=manager,
        products=products,
        local_ip=local_ip,
        cart_count=cart_count,
        cart_quantities=cart_quantities,
        auth_phone=auth_phone,
        logged_in_customer=logged_in_customer
    )


# ── Track Product View ───────────────────────────────────────────────────────
@bp.route('/<shop_slug>/view_product', methods=['POST'])
def view_product(shop_slug):
    data = request.get_json() or {}
    product_id = data.get('product_id')
    if product_id:
        db = get_db()
        session_id = get_or_create_customer_session(shop_slug)
        db.execute(
            'INSERT INTO tbl_product_views (product_id, session_id) VALUES (?, ?)',
            (product_id, session_id)
        )
        db.commit()
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Missing product_id'}), 400

# ── Add to Cart ──────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/add_to_cart', methods=['POST'])
def add_to_cart(shop_slug):
    data = request.get_json() or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'status': 'error', 'message': 'Missing product_id'}), 400
    
    db = get_db()
    session_id = get_or_create_customer_session(shop_slug)
    
    # Check if item already in cart
    existing = db.execute(
        'SELECT quantity FROM tbl_cart_items WHERE session_id=? AND product_id=?',
        (session_id, product_id)
    ).fetchone()
    
    if existing:
        db.execute(
            'UPDATE tbl_cart_items SET quantity = quantity + 1 WHERE session_id=? AND product_id=?',
            (session_id, product_id)
        )
        item_qty = existing['quantity'] + 1
    else:
        db.execute(
            'INSERT INTO tbl_cart_items (session_id, product_id, quantity) VALUES (?, ?, 1)',
            (session_id, product_id)
        )
        item_qty = 1
    db.commit()
    
    # Return updated cart count and item quantity
    cart_count = db.execute(
        'SELECT SUM(quantity) as count FROM tbl_cart_items WHERE session_id=?',
        (session_id,)
    ).fetchone()['count'] or 0
    
    return jsonify({'status': 'success', 'cart_count': cart_count, 'item_qty': item_qty})

# ── Update Cart Item ─────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/update_cart', methods=['POST'])
def update_cart(shop_slug):
    data = request.get_json() or {}
    product_id = data.get('product_id')
    action = data.get('action') # 'increase', 'decrease', 'remove'
    if not product_id:
        return jsonify({'status': 'error', 'message': 'Missing product_id'}), 400
    
    db = get_db()
    session_id = get_or_create_customer_session(shop_slug)
    
    if action == 'increase':
        db.execute('UPDATE tbl_cart_items SET quantity = quantity + 1 WHERE session_id=? AND product_id=?', (session_id, product_id))
    elif action == 'decrease':
        # Check current qty
        item = db.execute('SELECT quantity FROM tbl_cart_items WHERE session_id=? AND product_id=?', (session_id, product_id)).fetchone()
        if item and item['quantity'] > 1:
            db.execute('UPDATE tbl_cart_items SET quantity = quantity - 1 WHERE session_id=? AND product_id=?', (session_id, product_id))
        else:
            db.execute('DELETE FROM tbl_cart_items WHERE session_id=? AND product_id=?', (session_id, product_id))
    elif action == 'remove':
        db.execute('DELETE FROM tbl_cart_items WHERE session_id=? AND product_id=?', (session_id, product_id))
        
    db.commit()
    
    # Return updated item quantity and total cart count
    item = db.execute('SELECT quantity FROM tbl_cart_items WHERE session_id=? AND product_id=?', (session_id, product_id)).fetchone()
    item_qty = item['quantity'] if item else 0

    cart_count = db.execute(
        'SELECT SUM(quantity) as count FROM tbl_cart_items WHERE session_id=?',
        (session_id,)
    ).fetchone()['count'] or 0
    
    return jsonify({'status': 'success', 'item_qty': item_qty, 'cart_count': cart_count})

# ── View Cart ────────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/cart')
def view_cart(shop_slug):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        abort(404)
        
    record_visitor_activity(manager['manager_id'], shop_slug, visited_url=f"/shop/{shop_slug}/cart")
    session_id = get_or_create_customer_session(shop_slug)
    
    cart_items = db.execute('''
        SELECT c.*, p.name, p.price_inr, p.image_path, (c.quantity * p.price_inr) as item_total
        FROM tbl_cart_items c
        JOIN tbl_products p ON c.product_id = p.product_id
        WHERE c.session_id=?
    ''', (session_id,)).fetchall()
    
    total_amount = sum(item['item_total'] for item in cart_items)

    # Pass logged-in customer info for auto-fill
    auth_phone, logged_in_customer = _get_customer_session_auth(db, manager['manager_id'], shop_slug)
    
    return render_template(
        'shop/cart.html',
        manager=manager,
        cart_items=cart_items,
        total_amount=total_amount,
        logged_in_customer=logged_in_customer,
        auth_phone=auth_phone
    )

# ── Send Email OTP ────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/send-email-otp', methods=['POST'])
def send_email_otp_route(shop_slug):
    """Generates & emails a 6-digit OTP to the customer's email for verification."""
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        return jsonify({'success': False, 'error': 'Shop not found'}), 404

    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    name  = (data.get('name') or 'Customer').strip()

    if not email or '@' not in email:
        return jsonify({'success': False, 'error': 'Please enter a valid email address'}), 400

    otp = generate_otp(6)
    session[f'email_otp_{shop_slug}'] = {
        'email': email,
        'code': otp,
        'expires_at': time.time() + 600  # 10 minutes
    }
    # Clear any previous verified flag
    session.pop(f'email_verified_{shop_slug}', None)

    bypass_code = os.environ.get('TESTING_OTP_BYPASS', '').strip()
    result = send_email_otp(email, otp, manager['shop_name'], name)
    if result.get('success') or bypass_code:
        return jsonify({'success': True, 'message': f'Verification code sent to {email}'})
    else:
        return jsonify({'success': False, 'error': result.get('error', 'Failed to send email OTP')}), 500


# ── Verify Email OTP ──────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/verify-email-otp', methods=['POST'])
def verify_email_otp_route(shop_slug):
    """Verifies the 6-digit email OTP and marks email as verified in session.
    During testing, TESTING_OTP_BYPASS value (e.g. 'ADMINS') is accepted as a master code.
    """
    data = request.get_json(silent=True) or {}
    otp_input  = (data.get('otp') or '').strip()
    email_input = (data.get('email') or '').strip().lower()

    # ── Developer / testing bypass ────────────────────────────────────────────
    bypass_code = os.environ.get('TESTING_OTP_BYPASS', '').strip()
    if bypass_code and otp_input == bypass_code:
        # Accept any email without a real OTP in session
        target_email = email_input or (session.get(f'email_otp_{shop_slug}') or {}).get('email', '')
        session[f'email_verified_{shop_slug}'] = target_email
        session.pop(f'email_otp_{shop_slug}', None)
        return jsonify({'success': True, 'email': target_email,
                        'message': '✓ [TESTING] Email verified via bypass code.'})

    otp_data = session.get(f'email_otp_{shop_slug}')
    if not otp_data:
        return jsonify({'success': False, 'error': 'No verification code requested. Please click Send Code first.'}), 400

    if time.time() > otp_data.get('expires_at', 0):
        session.pop(f'email_otp_{shop_slug}', None)
        return jsonify({'success': False, 'error': 'Verification code has expired. Please request a new one.'}), 400

    if otp_input != otp_data.get('code'):
        return jsonify({'success': False, 'error': 'Incorrect code. Please check your inbox and try again.'}), 400

    # Mark email as verified in session
    session[f'email_verified_{shop_slug}'] = otp_data['email']
    session.pop(f'email_otp_{shop_slug}', None)

    return jsonify({'success': True, 'email': otp_data['email'], 'message': 'Email verified successfully!'})


# ── Checkout ─────────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/checkout', methods=['POST'])
def checkout(shop_slug):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        abort(404)
        
    session_id = get_or_create_customer_session(shop_slug)
    
    customer_name  = (request.form.get('customer_name') or '').strip()
    customer_phone = (request.form.get('customer_phone') or '').strip()
    customer_email = (request.form.get('customer_email') or '').strip()
    customer_pin   = (request.form.get('customer_pin') or '').strip()
    confirm_pin    = (request.form.get('confirm_pin') or '').strip()

    # ── If customer is already logged in, use session details directly ──────────
    auth_phone = session.get(f'customer_auth_{shop_slug}')
    is_returning = bool(auth_phone)

    if is_returning:
        # Logged-in user: pull details from session/DB, skip email OTP requirement
        customer_phone = auth_phone
        phone_10 = auth_phone
        db_cust = db.execute(
            'SELECT * FROM tbl_customers WHERE manager_id=? AND phone_number=?',
            (manager['manager_id'], auth_phone)
        ).fetchone()
        if db_cust:
            customer_name  = customer_name or db_cust['customer_name']
            customer_email = customer_email or db_cust['email'] or ''
        customer_pin = ''
        if len(customer_pin) == 4 and customer_pin.isdigit() and customer_pin == confirm_pin:
            pass  # allow optional PIN update
    else:
        # ── New/guest customer: validate phone, email, PIN, and email OTP ─────────
        clean_phone = re.sub(r'\D', '', customer_phone)
        if len(clean_phone) < 10:
            flash('Please enter a valid 10-digit mobile number.', 'error')
            return redirect(url_for('shop.view_cart', shop_slug=shop_slug))
        phone_10 = clean_phone[-10:]

        # Email is mandatory
        if not customer_email or '@' not in customer_email:
            flash('A valid email address is required to place an order.', 'error')
            return redirect(url_for('shop.view_cart', shop_slug=shop_slug))

        # Check if this phone is a returning customer (can skip email OTP)
        existing_cust = db.execute(
            'SELECT * FROM tbl_customers WHERE manager_id=? AND phone_number=?',
            (manager['manager_id'], phone_10)
        ).fetchone()

        if existing_cust:
            # Returning customer: email already verified before
            customer_pin = ''
        else:
            # New customer: require email OTP verification
            verified_email = session.get(f'email_verified_{shop_slug}')
            if not verified_email or verified_email.lower() != customer_email.lower():
                flash('Please verify your email address with the OTP code before placing the order.', 'error')
                return redirect(url_for('shop.view_cart', shop_slug=shop_slug))

            # Validate 4-digit PIN
            if len(customer_pin) != 4 or not customer_pin.isdigit():
                flash('Security PIN must be exactly 4 digits (numbers only).', 'error')
                return redirect(url_for('shop.view_cart', shop_slug=shop_slug))
            if customer_pin != confirm_pin:
                flash('4-digit PIN and Confirm PIN do not match!', 'error')
                return redirect(url_for('shop.view_cart', shop_slug=shop_slug))

    # Get cart items
    cart_items = db.execute('''
        SELECT c.*, p.price_inr
        FROM tbl_cart_items c
        JOIN tbl_products p ON c.product_id = p.product_id
        WHERE c.session_id=?
    ''', (session_id,)).fetchall()
    
    if not cart_items:
        return redirect(url_for('shop.catalog', shop_slug=shop_slug))
        
    total_amount = sum(item['quantity'] * item['price_inr'] for item in cart_items)
    order_uuid = str(uuid.uuid4())

    # Create/Update Customer Profile with hashed 4-digit PIN
    pin_hash = generate_password_hash(customer_pin) if customer_pin else ''
    existing_cust = db.execute(
        'SELECT customer_id FROM tbl_customers WHERE manager_id = ? AND phone_number = ?',
        (manager['manager_id'], phone_10)
    ).fetchone()

    if existing_cust:
        if pin_hash:
            db.execute('''
                UPDATE tbl_customers 
                SET customer_name = ?, email = ?, pin_hash = ?, last_login = CURRENT_TIMESTAMP 
                WHERE customer_id = ?
            ''', (customer_name, customer_email, pin_hash, existing_cust['customer_id']))
        else:
            db.execute('''
                UPDATE tbl_customers 
                SET customer_name = ?, email = ?, last_login = CURRENT_TIMESTAMP 
                WHERE customer_id = ?
            ''', (customer_name, customer_email, existing_cust['customer_id']))
    else:
        db.execute('''
            INSERT INTO tbl_customers (manager_id, customer_name, phone_number, email, pin_hash)
            VALUES (?, ?, ?, ?, ?)
        ''', (manager['manager_id'], customer_name, phone_10, customer_email, pin_hash))

    # Create Order
    db.execute('''
        INSERT INTO tbl_orders (order_uuid, manager_id, customer_name, customer_phone, customer_email, customer_pin, total_amount, status, payment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', 'Unpaid')
    ''', (order_uuid, manager['manager_id'], customer_name, phone_10, customer_email, customer_pin or '****', total_amount))
    
    order_rec = db.execute('SELECT order_id FROM tbl_orders WHERE order_uuid = ?', (order_uuid,)).fetchone()
    order_id = order_rec['order_id'] if order_rec else None
    
    # Create Order Items in batch
    if order_id and cart_items:
        items_payload = [(order_id, item['product_id'], item['quantity'], item['price_inr']) for item in cart_items]
        db.executemany(
            'INSERT INTO tbl_order_items (order_id, product_id, quantity, price_at_time) VALUES (?, ?, ?, ?)',
            items_payload
        )

    # Clear Cart
    db.execute('DELETE FROM tbl_cart_items WHERE session_id=?', (session_id,))
    db.commit()

    # Automatically authorize customer on this device session
    session[f'customer_auth_{shop_slug}'] = phone_10
    session[f'customer_name_{shop_slug}'] = customer_name
    session[f'customer_email_{shop_slug}'] = customer_email
    
    return redirect(url_for('shop.order_success', shop_slug=shop_slug, order_uuid=order_uuid))


# ── Order Success ────────────────────────────────────────────────────────────
@bp.route('/<shop_slug>/order/<order_uuid>')
def order_success(shop_slug, order_uuid):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=?', (shop_slug,)).fetchone()
    order = db.execute('SELECT * FROM tbl_orders WHERE order_uuid=?', (order_uuid,)).fetchone()
    
    if not order or not manager:
        abort(404)
        
    items = db.execute('''
        SELECT oi.*, p.name 
        FROM tbl_order_items oi
        JOIN tbl_products p ON oi.product_id = p.product_id
        WHERE oi.order_id=?
    ''', (order['order_id'],)).fetchall()
    
    return render_template('shop/order_success.html', manager=manager, order=order, items=items)


# ── Customer Mobile Order Tracking & PIN Authentication ─────────────────────
@bp.route('/<shop_slug>/track-order', methods=['GET'])
def track_order(shop_slug):
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        abort(404)

    record_visitor_activity(manager['manager_id'], shop_slug, visited_url=f"/shop/{shop_slug}/track-order")

    auth_phone = session.get(f'customer_auth_{shop_slug}')
    customer_info = None
    orders_found = []

    if auth_phone:
        customer_info = db.execute(
            'SELECT * FROM tbl_customers WHERE manager_id = ? AND (phone_number = ? OR phone_number LIKE ?)',
            (manager['manager_id'], auth_phone, f'%{auth_phone[-10:]}%')
        ).fetchone()

        orders = db.execute('''
            SELECT * FROM tbl_orders 
            WHERE manager_id=? AND (customer_phone = ? OR customer_phone LIKE ?)
            ORDER BY created_at DESC
        ''', (manager['manager_id'], auth_phone, f'%{auth_phone[-10:]}%')).fetchall()

        if orders:
            order_ids = [o['order_id'] for o in orders]
            placeholders = ','.join('?' for _ in order_ids)
            all_items = db.execute(f'''
                SELECT oi.*, p.name, p.image_path
                FROM tbl_order_items oi
                JOIN tbl_products p ON oi.product_id = p.product_id
                WHERE oi.order_id IN ({placeholders})
            ''', order_ids).fetchall()

            items_by_order = defaultdict(list)
            for it in all_items:
                items_by_order[it['order_id']].append(it)

            for o in orders:
                orders_found.append({
                    'order': o,
                    'order_items': items_by_order[o['order_id']]
                })

    return render_template(
        'shop/track_order.html',
        manager=manager,
        auth_phone=auth_phone,
        customer_info=customer_info,
        orders_found=orders_found
    )


@bp.route('/<shop_slug>/customer-login', methods=['POST'])
def customer_login(shop_slug):
    """Authenticates customer with their 10-digit mobile number and 4-digit security PIN."""
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        return jsonify({'success': False, 'error': 'Shop not found'}), 404

    data = request.get_json(silent=True) or {}
    raw_phone = data.get('phone', '').strip()
    pin_input = data.get('pin', '').strip()

    clean_phone = re.sub(r'\D', '', raw_phone)
    if len(clean_phone) < 10:
        return jsonify({'success': False, 'error': 'Please enter a valid 10-digit mobile number'}), 400

    phone_10 = clean_phone[-10:]

    if len(pin_input) != 4 or not pin_input.isdigit():
        return jsonify({'success': False, 'error': 'Please enter your 4-digit security PIN'}), 400

    # 1. Look up in tbl_customers
    customer = db.execute(
        'SELECT * FROM tbl_customers WHERE manager_id = ? AND (phone_number = ? OR phone_number LIKE ?)',
        (manager['manager_id'], phone_10, f'%{phone_10}%')
    ).fetchone()

    is_valid = False
    if customer and customer.get('pin_hash'):
        is_valid = check_password_hash(customer['pin_hash'], pin_input) or (customer['pin_hash'] == pin_input)
    else:
        # Fallback check against last order's PIN
        order = db.execute('''
            SELECT customer_pin FROM tbl_orders 
            WHERE manager_id = ? AND (customer_phone = ? OR customer_phone LIKE ?) AND customer_pin IS NOT NULL 
            ORDER BY order_id DESC LIMIT 1
        ''', (manager['manager_id'], phone_10, f'%{phone_10}%')).fetchone()
        if order and order.get('customer_pin') == pin_input:
            is_valid = True

    if not is_valid:
        return jsonify({'success': False, 'error': 'Incorrect 4-digit PIN or phone number not registered.'}), 400

    # Login successful: save customer authorization in session
    session[f'customer_auth_{shop_slug}'] = phone_10
    if customer:
        session[f'customer_name_{shop_slug}'] = customer.get('customer_name')
        session[f'customer_email_{shop_slug}'] = customer.get('email')

    return jsonify({
        'success': True,
        'phone': phone_10,
        'redirect': url_for('shop.track_order', shop_slug=shop_slug)
    })


@bp.route('/<shop_slug>/customer-register', methods=['POST'])
def customer_register(shop_slug):
    """Registers a new customer account directly and auto-logs them in."""
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        return jsonify({'success': False, 'error': 'Shop not found'}), 404

    data = request.get_json(silent=True) or {}
    name        = (data.get('name') or '').strip()
    raw_phone   = (data.get('phone') or '').strip()
    email       = (data.get('email') or '').strip().lower()
    pin         = (data.get('pin') or '').strip()
    confirm_pin = (data.get('confirm_pin') or '').strip()
    otp_code    = (data.get('otp') or '').strip()

    if not name:
        return jsonify({'success': False, 'error': 'Please enter your full name.'}), 400

    clean_phone = re.sub(r'\D', '', raw_phone)
    if len(clean_phone) < 10:
        return jsonify({'success': False, 'error': 'Please enter a valid 10-digit mobile number.'}), 400
    phone_10 = clean_phone[-10:]

    if not email or '@' not in email:
        return jsonify({'success': False, 'error': 'Please enter a valid email address.'}), 400

    if len(pin) != 4 or not pin.isdigit():
        return jsonify({'success': False, 'error': 'Security PIN must be exactly 4 digits.'}), 400

    if pin != confirm_pin:
        return jsonify({'success': False, 'error': '4-digit PIN and Confirm PIN do not match.'}), 400

    # Email OTP check
    bypass_code = os.environ.get('TESTING_OTP_BYPASS', '').strip()
    verified_email = session.get(f'email_verified_{shop_slug}')
    is_verified = (verified_email and verified_email.lower() == email) or (bypass_code and otp_code == bypass_code)

    if not is_verified and otp_code:
        otp_data = session.get(f'email_otp_{shop_slug}')
        if otp_data and otp_data.get('email') == email and otp_data.get('code') == otp_code and time.time() <= otp_data.get('expires_at', 0):
            is_verified = True

    if not is_verified:
        return jsonify({'success': False, 'error': 'Please verify your email address via OTP before creating account.'}), 400

    # Check if account already exists
    existing = db.execute(
        'SELECT customer_id FROM tbl_customers WHERE manager_id = ? AND phone_number = ?',
        (manager['manager_id'], phone_10)
    ).fetchone()

    pin_hash = generate_password_hash(pin)
    if existing:
        db.execute(
            'UPDATE tbl_customers SET customer_name = ?, email = ?, pin_hash = ?, last_login = CURRENT_TIMESTAMP WHERE customer_id = ?',
            (name, email, pin_hash, existing['customer_id'])
        )
    else:
        db.execute(
            'INSERT INTO tbl_customers (manager_id, customer_name, phone_number, email, pin_hash) VALUES (?, ?, ?, ?, ?)',
            (manager['manager_id'], name, phone_10, email, pin_hash)
        )
    db.commit()

    # Clear OTP sessions and auto-login
    session.pop(f'email_verified_{shop_slug}', None)
    session.pop(f'email_otp_{shop_slug}', None)

    session[f'customer_auth_{shop_slug}'] = phone_10
    session[f'customer_name_{shop_slug}'] = name
    session[f'customer_email_{shop_slug}'] = email

    return jsonify({
        'success': True,
        'message': f'Welcome, {name}! Your account has been created.',
        'redirect': url_for('shop.track_order', shop_slug=shop_slug)
    })


@bp.route('/<shop_slug>/track-order/logout')
def track_order_logout(shop_slug):
    """Clears customer authorization on this device."""
    session.pop(f'customer_auth_{shop_slug}', None)
    session.pop(f'customer_name_{shop_slug}', None)
    session.pop(f'customer_email_{shop_slug}', None)
    session.pop(f'verified_phone_{shop_slug}', None)
    session.pop(f'track_otp_{shop_slug}', None)
    return redirect(url_for('shop.track_order', shop_slug=shop_slug))


@bp.route('/<shop_slug>/check-customer', methods=['POST'])
def check_customer_exists(shop_slug):
    """AJAX: Checks if a phone number already has a registered customer account for this shop."""
    db = get_db()
    manager = db.execute('SELECT manager_id FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        return jsonify({'exists': False}), 404

    data = request.get_json(silent=True) or {}
    phone = re.sub(r'\D', '', data.get('phone', ''))
    if len(phone) < 10:
        return jsonify({'exists': False})

    phone_10 = phone[-10:]
    customer = db.execute(
        'SELECT customer_name, email FROM tbl_customers WHERE manager_id = ? AND phone_number = ?',
        (manager['manager_id'], phone_10)
    ).fetchone()

    if customer:
        return jsonify({'exists': True, 'name': customer['customer_name'], 'email': customer['email'] or ''})
    return jsonify({'exists': False})


# ── Forgot PIN — Step 1: Send OTP to registered email ───────────────────────
@bp.route('/<shop_slug>/forgot-pin', methods=['POST'])
def forgot_pin(shop_slug):
    """Sends OTP to the customer's registered email for PIN reset."""
    db = get_db()
    manager = db.execute('SELECT * FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        return jsonify({'success': False, 'error': 'Shop not found'}), 404

    data = request.get_json(silent=True) or {}
    phone = re.sub(r'\D', '', data.get('phone', ''))
    if len(phone) < 10:
        return jsonify({'success': False, 'error': 'Please enter a valid 10-digit phone number'}), 400
    phone_10 = phone[-10:]

    customer = db.execute(
        'SELECT * FROM tbl_customers WHERE manager_id=? AND phone_number=?',
        (manager['manager_id'], phone_10)
    ).fetchone()

    if not customer:
        return jsonify({'success': False, 'error': 'No account found with this phone number.'}), 404

    email = customer['email']
    if not email:
        return jsonify({'success': False, 'error': 'No email linked to this account. Please contact the store.'}), 400

    otp = generate_otp(6)
    session[f'pin_reset_otp_{shop_slug}'] = {
        'phone': phone_10,
        'email': email,
        'code': otp,
        'expires_at': time.time() + 600
    }

    # Testing bypass: skip real email send
    bypass_code = os.environ.get('TESTING_OTP_BYPASS', '').strip()
    if not bypass_code:
        result = send_email_otp(email, otp, manager['shop_name'], customer['customer_name'])
        if not result['success']:
            return jsonify({'success': False, 'error': result['error']}), 500

    masked = email[:2] + '***' + email[email.index('@'):]
    return jsonify({'success': True, 'masked_email': masked,
                    'message': f'Verification code sent to {masked}'})


# ── Reset PIN — Step 2: Verify OTP + set new PIN ─────────────────────────
@bp.route('/<shop_slug>/reset-pin', methods=['POST'])
def reset_pin(shop_slug):
    """Verifies OTP and sets a new 4-digit PIN for the customer."""
    db = get_db()
    manager = db.execute('SELECT manager_id FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    if not manager:
        return jsonify({'success': False, 'error': 'Shop not found'}), 404

    data = request.get_json(silent=True) or {}
    otp_input   = (data.get('otp') or '').strip()
    new_pin     = (data.get('new_pin') or '').strip()
    confirm_pin = (data.get('confirm_pin') or '').strip()

    # Testing bypass
    bypass_code = os.environ.get('TESTING_OTP_BYPASS', '').strip()
    otp_data = session.get(f'pin_reset_otp_{shop_slug}')

    if bypass_code and otp_input == bypass_code:
        if not otp_data:
            return jsonify({'success': False, 'error': 'Please initiate Forgot PIN first.'}), 400
        # Accept bypass
    else:
        if not otp_data:
            return jsonify({'success': False, 'error': 'No PIN reset was requested.'}), 400
        if time.time() > otp_data.get('expires_at', 0):
            session.pop(f'pin_reset_otp_{shop_slug}', None)
            return jsonify({'success': False, 'error': 'Code expired. Please request a new one.'}), 400
        if otp_input != otp_data.get('code'):
            return jsonify({'success': False, 'error': 'Incorrect code. Please check your email.'}), 400

    if len(new_pin) != 4 or not new_pin.isdigit():
        return jsonify({'success': False, 'error': 'New PIN must be exactly 4 digits.'}), 400
    if new_pin != confirm_pin:
        return jsonify({'success': False, 'error': 'PINs do not match.'}), 400

    phone_10 = otp_data['phone']
    pin_hash = generate_password_hash(new_pin)

    db.execute(
        'UPDATE tbl_customers SET pin_hash=?, last_login=CURRENT_TIMESTAMP WHERE manager_id=? AND phone_number=?',
        (pin_hash, manager['manager_id'], phone_10)
    )
    db.commit()
    session.pop(f'pin_reset_otp_{shop_slug}', None)

    return jsonify({'success': True, 'message': 'PIN reset successfully! You can now log in with your new PIN.'})

@bp.route('/scan/<shop_slug>')
def scan(shop_slug):
    db = get_db()
    manager = db.execute('SELECT manager_id FROM tbl_managers WHERE shop_slug=? AND is_suspended=0', (shop_slug,)).fetchone()
    
    if manager:
        record_visitor_activity(manager['manager_id'], shop_slug, is_scan=True)
        
    return redirect(url_for('shop.catalog', shop_slug=shop_slug, source='qr'))

# ── Dynamic Live Shop QR Code (matches live Render/host URL automatically) ───
@bp.route('/<shop_slug>/qr.png')
def shop_qr_image(shop_slug):
    """Dynamically renders the shop QR code PNG with the active live URL."""
    from services.qr_service import generate_qr_image_bytes, get_shop_base_url
    from flask import Response

    origin = request.args.get('origin', '').strip()
    if origin and origin.startswith(('http://', 'https://')):
        base_url = origin.rstrip('/')
    else:
        base_url = get_shop_base_url(request, for_qr_scan=True)

    scan_url = f"{base_url}/scan/{shop_slug}"
    img_bytes = generate_qr_image_bytes(scan_url)
    return Response(img_bytes, mimetype='image/png', headers={
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    })


