"""
Mail Service — SMTP Email OTP Sender
Uses Python's built-in smtplib (no extra pip install needed).
Configure via environment variables:
    MAIL_HOST   — SMTP host (default: smtp.gmail.com)
    MAIL_PORT   — SMTP port (default: 587)
    MAIL_USER   — sender email address
    MAIL_PASS   — Gmail App Password (or SMTP password)
    MAIL_FROM   — display name + address (default: MAIL_USER)
"""
import smtplib
import os
import random
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _smtp_config():
    return {
        'host': os.environ.get('MAIL_HOST', 'smtp.gmail.com'),
        'port': int(os.environ.get('MAIL_PORT', 587)),
        'user': os.environ.get('MAIL_USER', ''),
        'password': os.environ.get('MAIL_PASS', ''),
        'from': os.environ.get('MAIL_FROM', os.environ.get('MAIL_USER', '')),
    }


def is_mail_configured():
    cfg = _smtp_config()
    return bool(cfg['user'] and cfg['password'])


def send_email_otp(to_email: str, otp: str, shop_name: str, customer_name: str = '') -> dict:
    """
    Sends a 6-digit OTP to the given email via SMTP.
    Returns {'success': True} or {'success': False, 'error': '...'}.
    """
    cfg = _smtp_config()

    if not cfg['user'] or not cfg['password']:
        return {
            'success': False,
            'error': 'Email service not configured. Please set MAIL_USER and MAIL_PASS in .env'
        }

    greeting = f"Hi {customer_name}," if customer_name else "Hello,"

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f8fafc; margin: 0; padding: 0; }}
  .container {{ max-width: 480px; margin: 32px auto; background: #fff; border-radius: 20px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #1e293b 0%, #334155 100%); padding: 32px 32px 24px; text-align: center; }}
  .header h1 {{ color: #fff; font-size: 20px; font-weight: 800; margin: 0; letter-spacing: -0.5px; }}
  .header p {{ color: #94a3b8; font-size: 12px; margin: 6px 0 0; }}
  .body {{ padding: 32px; }}
  .greeting {{ font-size: 15px; color: #334155; margin-bottom: 16px; }}
  .otp-box {{ background: #f1f5f9; border: 2px dashed #cbd5e1; border-radius: 16px; padding: 24px; text-align: center; margin: 20px 0; }}
  .otp-label {{ font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 1px; }}
  .otp-code {{ font-size: 42px; font-weight: 900; color: #0f172a; letter-spacing: 0.3em; font-family: 'Courier New', monospace; margin: 8px 0 0; }}
  .expiry {{ font-size: 12px; color: #ef4444; font-weight: 600; margin-top: 8px; }}
  .note {{ font-size: 12px; color: #94a3b8; margin-top: 20px; line-height: 1.6; }}
  .footer {{ background: #f8fafc; padding: 16px 32px; text-align: center; border-top: 1px solid #e2e8f0; }}
  .footer p {{ font-size: 11px; color: #94a3b8; margin: 0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🛍️ {shop_name}</h1>
    <p>Order Account Verification</p>
  </div>
  <div class="body">
    <p class="greeting">{greeting}</p>
    <p style="font-size:14px;color:#475569;">Your email verification code for placing an order at <strong>{shop_name}</strong> is:</p>
    <div class="otp-box">
      <p class="otp-label">Verification Code</p>
      <p class="otp-code">{otp}</p>
      <p class="expiry">⏱ Expires in 10 minutes</p>
    </div>
    <p class="note">
      Enter this 6-digit code on the checkout page to verify your email and place your order.
      <br><br>
      If you did not request this, please ignore this email.
    </p>
  </div>
  <div class="footer">
    <p>This is an automated message from {shop_name} · Powered by QR Shop</p>
  </div>
</div>
</body>
</html>
"""

    text_body = f"{greeting}\n\nYour verification code for {shop_name} is: {otp}\n\nThis code expires in 10 minutes."

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🔐 {otp} — Your {shop_name} Email Verification Code"
    msg['From'] = cfg['from'] or cfg['user']
    msg['To'] = to_email

    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP(cfg['host'], cfg['port'], timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(cfg['user'], cfg['password'])
            smtp.sendmail(cfg['user'], to_email, msg.as_string())
        return {'success': True}
    except smtplib.SMTPAuthenticationError:
        return {
            'success': False,
            'error': 'Email authentication failed. Check MAIL_USER and MAIL_PASS in .env (use Gmail App Password, not your main password).'
        }
    except smtplib.SMTPException as e:
        return {'success': False, 'error': f'SMTP error: {str(e)}'}
    except Exception as e:
        return {'success': False, 'error': f'Failed to send email: {str(e)}'}


def generate_otp(length: int = 6) -> str:
    """Generates a cryptographically safe numeric OTP."""
    return ''.join([str(random.SystemRandom().randint(0, 9)) for _ in range(length)])
