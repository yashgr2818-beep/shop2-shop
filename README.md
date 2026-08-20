# Shop2-Shop (QR Catalog) 🛒📱

A modern, lightweight, fast, and feature-rich **QR Code Digital Storefront & Order Management Platform** built with **Flask**, **SQLite**, and **Tailwind CSS**.

---

## 🌟 Key Features

### 🏬 For Store Managers
- **Digital Storefront & QR Code:** Automatic generation of branded QR codes for physical standees and instant customer browsing.
- **Product Management:** Single product addition, image upload/compression, and automatic sequential SKU assignment (`SKU-MGR_ID-001`).
- **Bulk CSV Upload & Stock Management:** Import products via CSV and update inventory in real-time with an interactive grid (`/manager/bulk_stock`).
- **Inline Quick Edit:** Instantly edit product pricing and stock directly from the dashboard table without opening full edit pages.
- **Order Lifecycle & WhatsApp Integration:** Track orders, update statuses (Pending, Packed, Delivered, Cancelled), and send instant updates/inquiries via WhatsApp.
- **Role-Based Staff Access (RBAC):** Create sub-accounts for store staff with restricted access (e.g. Order-Only or Stock-Only roles).
- **Price Visibility Controls:** Toggle price visibility globally for the whole store or individually for specific products.
- **Real-Time Visitor & QR Analytics:** Monitor live active visitors (15-min window), total QR scans, and client device signatures (iOS, Android, Chrome, Safari).

### 🛍️ For Customers
- **Mobile-First Digital Catalog:** Instant loading catalog accessible by scanning store QR codes.
- **Search & Filter:** Fast product search and category filtering.
- **Cart & Direct Order Placement:** Seamless cart management and order placement with instant WhatsApp confirmation.
- **Order Tracking with Mobile Auth:** Track order progress (Pending $\rightarrow$ Packed $\rightarrow$ Delivered) using mobile phone authentication.

### 🛡️ For Super Administrators
- **Admin Control Panel (`/admin`):** Monitor store metrics, suspend/unsuspend managers, and manage global settings.
- **Security & IP Blocking Panel:** Real-time IP address tracking, URL safety monitoring, and instant IP blocking to prevent unauthorized access.

---

## 🛠️ Technology Stack

- **Backend:** Python 3.9+ / Flask
- **Database:** SQLite3 (WAL mode supported)
- **Frontend:** HTML5, Vanilla JavaScript, Tailwind CSS (CDN/Utility classes)
- **Services & Tools:** Pillow (Image Compression), qrcode (QR Generation), WSGI/Gunicorn

---

## 🚀 Quick Start & Installation

### Prerequisites
- Python 3.9 or higher
- `pip` (Python Package Installer)

### 1. Clone & Set Up Environment

```bash
# Clone the repository
git clone https://github.com/your-username/shop2-shop.git
cd shop2-shop

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Initialize Database

Run the database initialization script to create tables and default schema:

```bash
python -c "from database import init_db; init_db()"
```

### 3. Run Locally

Start the Flask development server:

```bash
python app.py
```

Open your browser and navigate to:
- **Manager Portal:** `http://127.0.0.1:5000/manager/login`
- **Super Admin Portal:** `http://127.0.0.1:5000/admin/login`

---

## 📂 Project Structure

```
shop2-shop/
├── app.py                      # Main application entry point & configuration
├── database.py                 # SQLite database connection & initialization
├── schema.sql                  # Database schema & table definitions
├── requirements.txt            # Python dependencies
├── README.md                   # Project documentation
├── routes/
│   ├── admin.py                # Admin routes (Store suspension, IP blocking, security)
│   ├── manager.py              # Manager routes (Dashboard, Product & Order management)
│   └── shop.py                 # Public customer shopfront & order tracking
├── services/
│   ├── qr_service.py           # QR Code generation & IP/UA parsing services
│   └── upload_service.py       # CSV processing & image compression service
├── static/
│   ├── images/                 # Uploaded product images
│   └── qrs/                    # Generated store QR codes
└── templates/
    ├── admin/                  # Super Admin templates
    ├── manager/                # Manager dashboard, bulk stock & order templates
    └── shop/                   # Customer catalog & checkout templates
```

---

## 🔒 Security & Production Deployment Guide

When deploying this application to production servers (e.g. Render, Railway, AWS, DigitalOcean):

1. **Set Environment Variables:**
   - Set `FLASK_ENV=production`
   - Configure a strong `SECRET_KEY` in environment variables.

2. **Run with Production WSGI Server:**
   ```bash
   gunicorn -w 4 -b 0.0.0.0:8000 app:app
   ```

3. **Enable Reverse Proxy Headers:** Ensure `X-Forwarded-For` and `X-Real-IP` headers are enabled in Nginx/Cloudflare for accurate IP tracking and blocking.

---

## 📜 License & Acknowledgments

Developed for **Shop2-Shop QR Catalog Platform**. All rights reserved.
