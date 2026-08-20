import os, sys
sys.path.insert(0, r'c:/Users/digital/Downloads/shop2-shop')
os.chdir(r'c:/Users/digital/Downloads/shop2-shop')
from dotenv import load_dotenv
load_dotenv()
from database import get_db
from app import create_app
import cloudinary
import cloudinary.uploader
from services.upload_service import is_cloudinary_configured, init_cloudinary, upload_shop_qr_to_cloudinary, upload_product_image_file
from services.qr_service import get_shop_base_url

app = create_app()
with app.app_context():
    db = get_db()
    
    # Check Cloudinary configuration
    if not is_cloudinary_configured():
        print("ERROR: Cloudinary is not configured in .env!")
        sys.exit(1)
        
    init_cloudinary()
    
    # ── 1. PUBLISH ALL QR CODES TO CLOUDINARY IN 'qrs' FOLDER ──────────────
    print("=== 1. SYNCING ALL SHOP QR CODES TO CLOUDINARY (folder: 'qrs') ===")
    managers = db.execute('SELECT manager_id, shop_name, shop_slug, qr_image_url FROM tbl_managers').fetchall()
    base_url = get_shop_base_url()
    
    for m in managers:
        slug = m['shop_slug']
        scan_url = f"{base_url}/scan/{slug}"
        cloud_url, err = upload_shop_qr_to_cloudinary(slug, scan_url)
        if cloud_url:
            db.execute('UPDATE tbl_managers SET qr_image_url = ? WHERE manager_id = ?', (cloud_url, m['manager_id']))
            db.commit()
            print(f"  [QR OK] {m['shop_name']} ({slug}) -> {cloud_url}")
        else:
            print(f"  [QR FAIL] {m['shop_name']} ({slug}) -> {err}")
            
    # ── 2. CHECK & UPLOAD ALL LOCAL PRODUCT IMAGES TO CLOUDINARY ───────────
    print("\n=== 2. CHECKING LOCAL PRODUCT IMAGES TO UPLOAD TO CLOUDINARY ===")
    img_folder = app.config['IMAGE_FOLDER']
    local_files_on_disk = os.listdir(img_folder) if os.path.exists(img_folder) else []
    print(f"Files found in static/images: {local_files_on_disk}")
    
    products = db.execute('''
        SELECT p.product_id, p.manager_id, p.sku, p.name, p.image_path, m.shop_name, m.shop_slug
        FROM tbl_products p
        JOIN tbl_managers m ON p.manager_id = m.manager_id
        ORDER BY p.product_id ASC
    ''').fetchall()
    
    uploaded_count = 0
    for p in products:
        img_path = p['image_path'] or 'placeholder.jpg'
        
        # If product has a local file name (not http and not placeholder)
        if not img_path.startswith('http') and img_path != 'placeholder.jpg':
            local_full_path = os.path.join(img_folder, img_path)
            if os.path.exists(local_full_path):
                with open(local_full_path, 'rb') as f:
                    cloud_url, err = upload_product_image_file(
                        f, p['manager_id'], p['sku'],
                        shop_slug=p['shop_slug']
                    )
                    if cloud_url:
                        db.execute('UPDATE tbl_products SET image_path = ? WHERE product_id = ?', (cloud_url, p['product_id']))
                        db.commit()
                        uploaded_count += 1
                        print(f"  [PROD IMG OK] Uploaded {p['name']} ({p['sku']}) in {p['shop_name']} -> {cloud_url}")
                    else:
                        print(f"  [PROD IMG FAIL] {p['name']} ({p['sku']}): {err}")
            else:
                print(f"  [PROD IMG NOT FOUND ON DISK] {p['name']} ({p['sku']}): {img_path}")
                
        # Also check if there is an image on disk matching product ID or SKU even if placeholder in DB
        elif img_path == 'placeholder.jpg':
            # Check if matching image file exists on disk
            candidates = [f"{p['manager_id']}_{p['sku']}.jpg", f"{p['manager_id']}_{p['product_id']}.jpg"]
            for cand in candidates:
                cand_path = os.path.join(img_folder, cand)
                if os.path.exists(cand_path):
                    with open(cand_path, 'rb') as f:
                        cloud_url, err = upload_product_image_file(
                            f, p['manager_id'], p['sku'],
                            shop_slug=p['shop_slug']
                        )
                        if cloud_url:
                            db.execute('UPDATE tbl_products SET image_path = ? WHERE product_id = ?', (cloud_url, p['product_id']))
                            db.commit()
                            uploaded_count += 1
                            print(f"  [PROD DISK MATCH OK] Found & Uploaded {p['name']} ({p['sku']}) -> {cloud_url}")
                            break

    print(f"\nCompleted: {uploaded_count} product images synced to Cloudinary per-shop folders.")
    
    # ── 3. FINAL VERIFICATION SUMMARY ──────────────────────────────────────
    print("\n=== 3. VERIFYING ALL SHOPS IN DATABASE ===")
    all_managers = db.execute('SELECT manager_id, shop_name, shop_slug, qr_image_url FROM tbl_managers').fetchall()
    for m in all_managers:
        prod_count = db.execute('SELECT COUNT(*) as c FROM tbl_products WHERE manager_id = ?', (m['manager_id'],)).fetchone()['c']
        cloud_prods = db.execute("SELECT COUNT(*) as c FROM tbl_products WHERE manager_id = ? AND image_path LIKE 'https://res.cloudinary.com%'", (m['manager_id'],)).fetchone()['c']
        print(f"Shop: {m['shop_name']} (@{m['shop_slug']})")
        print(f"  QR Code Cloudinary URL : {m['qr_image_url']}")
        print(f"  Products with Cloudinary Photos: {cloud_prods} / {prod_count}")
