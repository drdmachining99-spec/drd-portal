import os
import json
import urllib.parse
from io import BytesIO
from datetime import datetime

from flask import (
    Flask, request, redirect, url_for, send_from_directory,
    render_template_string, send_file
)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
REQUESTS_FILE = 'requests.json'
SETTINGS_FILE = 'settings.json'
LETTERHEAD_FILE = 'letterhead.pdf'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DEFAULT_SETTINGS = {
    'company_name': 'DRD Machining Services',
    'address': 'Committee Chowk, Rawalpindi',
    'phone': '',
    'email': 'drd.maching99@gmail.com',
    'website': 'www.drdmachining.com',
    'ntn': '',
    'strn': '',
    'bank_name': '',
    'account_title': '',
    'account_number': '',
    'iban': '',
    'payment_instructions': 'Please make payment against the approved quotation and submit the payment confirmation.',
    'gst_enabled': True,
    'gst_percent': 18.0,
    'wht_enabled': False,
    'wht_percent': 0.0,
    'wht_mode': 'deduct'
}


def load_settings():
    data = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            data.update(saved)
        except Exception as e:
            print('Error loading settings:', e)
    return data


def save_settings(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)


def calculate_amounts(base_price, gst_enabled, gst_percent, wht_enabled, wht_percent, wht_mode):
    gst_amount = (base_price * gst_percent / 100.0) if gst_enabled else 0.0
    gross_total = base_price + gst_amount
    wht_amount = (gross_total * wht_percent / 100.0) if wht_enabled else 0.0

    if wht_enabled and wht_mode == 'add':
        total_price = gross_total + wht_amount
        net_payable = total_price
    elif wht_enabled and wht_mode == 'deduct':
        total_price = gross_total
        net_payable = gross_total - wht_amount
    else:
        total_price = gross_total
        net_payable = gross_total

    return gst_amount, gross_total, wht_amount, total_price, net_payable


def make_whatsapp_link(req, settings):
    phone = ''.join(filter(str.isdigit, str(req.get('whatsapp', ''))))
    if not phone:
        return '#'

    base = float(req.get('base_price', 0) or 0)
    gst = float(req.get('gst_amount', 0) or 0)
    wht = float(req.get('wht_amount', 0) or 0)
    total = float(req.get('total_price', 0) or 0)
    net = float(req.get('net_payable', total) or total)

    quote_url = ''
    confirm_url = ''
    if req.get('job_id'):
        quote_url = url_for('quotation_pdf', job_id=req['job_id'], _external=True)
        confirm_url = url_for('payment_confirmation', job_id=req['job_id'], _external=True)

    lines = [
        f"Hello *{req.get('name', '')}*,",
        '',
        f"Your quotation for Job ID *{req.get('job_id', '')}* has been prepared by *{settings.get('company_name', '')}*.",
        '',
        f"*Part:* {req.get('part_name', '')}",
        f"*Quantity:* {req.get('quantity', '')}",
        f"*Delivery:* {req.get('delivery_time', '')}",
        '',
        f"*Base Price:* Rs. {base:,.2f}",
    ]

    if settings.get('gst_enabled'):
        lines.append(f"*GST ({float(req.get('gst_percent', 0) or 0):g}%):* Rs. {gst:,.2f}")

    if settings.get('wht_enabled'):
        mode_text = 'Added' if settings.get('wht_mode') == 'add' else 'Deducted'
        lines.append(f"*Income Tax / WHT ({float(req.get('wht_percent', 0) or 0):g}% - {mode_text}):* Rs. {wht:,.2f}")

    if settings.get('wht_enabled') and settings.get('wht_mode') == 'deduct':
        lines.append(f"*Gross Total:* Rs. {total:,.2f}")
        lines.append(f"*Net Payable:* *Rs. {net:,.2f}*")
    else:
        lines.append(f"*Total Payable:* *Rs. {net:,.2f}*")

    if req.get('remarks'):
        lines.extend(['', f"*Remarks:* {req.get('remarks')}"])

    if quote_url:
        lines.extend(['', f"*Quotation PDF:* {quote_url}"])
    if confirm_url:
        lines.extend(['', f"After confirmation and payment, submit your payment details here:", confirm_url])

    lines.extend(['', 'Please review the quotation and confirm the order to proceed.'])

    encoded = urllib.parse.quote('\n'.join(lines))
    return f"https://wa.me/{phone}?text={encoded}"


def load_requests():
    settings = load_settings()
    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            changed = False

            for req in data:
                defaults = {
                    'base_price': 0,
                    'gst_percent': settings.get('gst_percent', 18),
                    'gst_enabled': settings.get('gst_enabled', True),
                    'gst_amount': 0,
                    'wht_enabled': False,
                    'wht_percent': 0,
                    'wht_mode': 'deduct',
                    'wht_amount': 0,
                    'gross_total': 0,
                    'total_price': 0,
                    'net_payable': 0,
                    'delivery_time': 'Pending',
                    'remarks': '',
                    'quotation_type': 'standard',

                    # New multi-part quotation structure
                    'parts': [],

                    'status': 'New',
                    'customer_confirmed': False,
                    'payment_status': 'Not Submitted',
                    'payment_amount': 0,
                    'transaction_id': '',
                    'payment_date': '',
                    'payment_file': '',
                    'invoice_number': '',
                    'invoice_date': ''
                }

                for key, value in defaults.items():
                    if key not in req:
                        req[key] = value
                        changed = True

                # -------------------------------------------------
                # Backward compatibility:
                # Old single-part orders are converted into
                # the new multi-part structure automatically.
                # -------------------------------------------------
                if not req.get('parts'):
                    old_part = {
                        'part_no': 'P-001',
                        'part_name': req.get('part_name', ''),
                        'description': req.get('requirement', ''),
                        'quantity': req.get('quantity', ''),
                        'material': req.get('material', ''),
                        'price': float(req.get('base_price', 0) or 0)
                    }

                    req['parts'] = [old_part]
                    changed = True

                # Keep old fields synchronized with first part
                # so existing pages/payment system continue working.
                first_part = req['parts'][0] if req['parts'] else {}

                if 'part_name' not in req or not req.get('part_name'):
                    req['part_name'] = first_part.get('part_name', '')
                    changed = True

                if 'quantity' not in req or not req.get('quantity'):
                    req['quantity'] = first_part.get('quantity', '')
                    changed = True

                if 'material' not in req or not req.get('material'):
                    req['material'] = first_part.get('material', '')
                    changed = True

                # Recalculate base price from all quotation parts
                total_parts_price = 0.0

                for part in req.get('parts', []):
                    try:
                        part['price'] = float(part.get('price', 0) or 0)
                    except (ValueError, TypeError):
                        part['price'] = 0.0

                    total_parts_price += part['price']

                # Only synchronize automatically when parts exist.
                req['base_price'] = total_parts_price

                # Keep quotation calculations valid
                try:
                    gst_percent = float(
                        req.get(
                            'gst_percent',
                            settings.get('gst_percent', 18)
                        ) or 0
                    )
                except (ValueError, TypeError):
                    gst_percent = 18.0

                req['gst_percent'] = gst_percent

                gst_enabled = bool(
                    req.get(
                        'gst_enabled',
                        settings.get('gst_enabled', True)
                    )
                )

                req['gst_enabled'] = gst_enabled

                try:
                    wht_percent = float(
                        req.get(
                            'wht_percent',
                            settings.get('wht_percent', 0)
                        ) or 0
                    )
                except (ValueError, TypeError):
                    wht_percent = 0.0

                req['wht_percent'] = wht_percent

                wht_enabled = bool(req.get('wht_enabled', False))
                wht_mode = req.get(
                    'wht_mode',
                    settings.get('wht_mode', 'deduct')
                )

                req['wht_enabled'] = wht_enabled
                req['wht_mode'] = wht_mode

                (
                    gst_amount,
                    gross_total,
                    wht_amount,
                    total_price,
                    net_payable
                ) = calculate_amounts(
                    total_parts_price,
                    gst_enabled,
                    gst_percent,
                    wht_enabled,
                    wht_percent,
                    wht_mode
                )

                req['gst_amount'] = gst_amount
                req['gross_total'] = gross_total
                req['wht_amount'] = wht_amount
                req['total_price'] = total_price
                req['net_payable'] = net_payable

                req['wa_link'] = make_whatsapp_link(req, settings)

            if changed:
                save_all_requests(data)

            return sorted(
                data,
                key=lambda x: x.get('time', ''),
                reverse=True
            )

        except Exception as e:
            print('Error loading requests:', e)

    return []

def save_all_requests(reqs):
    clean_data = []
    for r in reqs:
        r_copy = r.copy()
        r_copy.pop('wa_link', None)
        clean_data.append(r_copy)
    with open(REQUESTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(clean_data, f, indent=4)


def find_request(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req.get('job_id') == job_id:
            return req, all_reqs
    return None, all_reqs


def next_invoice_number(req):
    if req.get('invoice_number'):
        return req['invoice_number']
    date_str = datetime.now().strftime('%y%m%d')
    return f"INV-{date_str}-{req.get('job_id', '0000').split('-')[-1]}"


def build_document_pdf(req, document_type='quotation'):
    """Create PDF using the uploaded DRD letterhead as the page background."""
    settings = load_settings()
    if not os.path.exists(LETTERHEAD_FILE):
        raise FileNotFoundError('letterhead.pdf is missing from the project.')

    letterhead_reader = PdfReader(LETTERHEAD_FILE)
    page = letterhead_reader.pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    overlay = BytesIO()
    c = canvas.Canvas(overlay, pagesize=(width, height))

    # Content starts below the existing blue header.
    top_y = height - 42 * mm
    left = 18 * mm
    right = width - 18 * mm
    usable = right - left

    title = 'QUOTATION' if document_type == 'quotation' else 'INVOICE'
    c.setFillColor(colors.HexColor('#123f5d'))
    c.setFont('Helvetica-Bold', 18)
    c.drawString(left, top_y, title)

    c.setFillColor(colors.black)
    c.setFont('Helvetica', 9)
    ref_no = req.get('job_id', '') if document_type == 'quotation' else next_invoice_number(req)
    c.drawRightString(right, top_y + 2, f"No: {ref_no}")
    c.drawRightString(right, top_y - 11, f"Date: {datetime.now().strftime('%d-%m-%Y')}")

    y = top_y - 30
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, y, 'Bill To / Customer')
    c.setFont('Helvetica', 9)
    y -= 14
    c.drawString(left, y, str(req.get('company', '')))
    y -= 12
    c.drawString(left, y, str(req.get('name', '')))
    y -= 12
    c.drawString(left, y, f"WhatsApp: {req.get('whatsapp', '')}")

    y -= 24
    # Details table
    table_top = y
    row_h = 22
    cols = [left, left + usable * .34, left + usable * .50, left + usable * .64, right]
    c.setFillColor(colors.HexColor('#eaf1f6'))
    c.rect(left, table_top - row_h, usable, row_h, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont('Helvetica-Bold', 8.5)
    headers = ['Description', 'Qty', 'Material', 'Delivery']
    for i, h in enumerate(headers):
        c.drawString(cols[i] + 4, table_top - 14, h)

    y = table_top - row_h
    c.setFont('Helvetica', 8.5)
    c.rect(left, y - row_h, usable, row_h, fill=0, stroke=1)
    values = [str(req.get('part_name', '')), str(req.get('quantity', '')), str(req.get('material', '')), str(req.get('delivery_time', ''))]
    for i, v in enumerate(values):
        c.drawString(cols[i] + 4, y - 14, v[:35])
    for x in cols[1:-1]:
        c.line(x, y, x, y - row_h)

    y -= row_h + 22
    base = float(req.get('base_price', 0) or 0)
    gst = float(req.get('gst_amount', 0) or 0)
    wht = float(req.get('wht_amount', 0) or 0)
    gross = float(req.get('gross_total', base + gst) or (base + gst))
    total = float(req.get('total_price', gross) or gross)
    net = float(req.get('net_payable', total) or total)

    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, y, 'Pricing')
    y -= 17
    c.setFont('Helvetica', 9)
    c.drawString(left, y, 'Base Price')
    c.drawRightString(right, y, f"Rs. {base:,.2f}")
    y -= 15

    if settings.get('gst_enabled'):
        c.drawString(left, y, f"GST ({float(req.get('gst_percent', 0) or 0):g}%)")
        c.drawRightString(right, y, f"Rs. {gst:,.2f}")
        y -= 15

    if settings.get('wht_enabled'):
        mode_text = 'Added' if settings.get('wht_mode') == 'add' else 'Deducted'
        c.drawString(left, y, f"Income Tax / WHT ({float(req.get('wht_percent', 0) or 0):g}% - {mode_text})")
        c.drawRightString(right, y, f"Rs. {wht:,.2f}")
        y -= 15

    c.line(left, y + 6, right, y + 6)
    if settings.get('wht_enabled') and settings.get('wht_mode') == 'deduct':
        c.setFont('Helvetica-Bold', 10)
        c.drawString(left, y - 8, 'Gross Total')
        c.drawRightString(right, y - 8, f"Rs. {gross:,.2f}")
        y -= 25
        c.setFont('Helvetica-Bold', 12)
        c.drawString(left, y - 8, 'NET PAYABLE')
        c.drawRightString(right, y - 8, f"Rs. {net:,.2f}")
        y -= 25
    else:
        c.setFont('Helvetica-Bold', 12)
        c.drawString(left, y - 8, 'TOTAL PAYABLE')
        c.drawRightString(right, y - 8, f"Rs. {net:,.2f}")
        y -= 25

    if document_type == 'invoice':
        c.setFillColor(colors.HexColor('#167a3a'))
        c.setFont('Helvetica-Bold', 11)
        c.drawString(left, y, f"PAYMENT STATUS: {req.get('payment_status', 'PAID').upper()}")
        y -= 18
        if req.get('transaction_id'):
            c.setFillColor(colors.black)
            c.setFont('Helvetica', 9)
            c.drawString(left, y, f"Payment Reference: {req.get('transaction_id')}")
            y -= 14
        if req.get('payment_date'):
            c.drawString(left, y, f"Payment Date: {req.get('payment_date')}")
            y -= 14

    if req.get('remarks'):
        c.setFillColor(colors.black)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(left, y, 'Remarks')
        y -= 13
        c.setFont('Helvetica', 8.5)
        for line in str(req.get('remarks')).splitlines()[:5]:
            c.drawString(left, y, line[:110])
            y -= 11
        y -= 5

    c.setFont('Helvetica-Bold', 9)
    c.drawString(left, y, 'Payment Details')
    y -= 13
    c.setFont('Helvetica', 8.5)
    payment_lines = [
        f"Bank: {settings.get('bank_name', '')}",
        f"Account Title: {settings.get('account_title', '')}",
        f"Account Number: {settings.get('account_number', '')}",
        f"IBAN: {settings.get('iban', '')}"
    ]
    for line in payment_lines:
        if line.split(':', 1)[1].strip():
            c.drawString(left, y, line)
            y -= 11

    if settings.get('payment_instructions'):
        y -= 5
        c.setFont('Helvetica', 8)
        c.drawString(left, y, settings.get('payment_instructions', '')[:115])

    c.save()
    overlay.seek(0)
    overlay_reader = PdfReader(overlay)
    overlay_page = overlay_reader.pages[0]
    page.merge_page(overlay_page)

    writer = PdfWriter()
    writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output


INDEX_PAGE = '''
<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DRD Manufacturing Solutions</title>
<style>
body{font-family:Arial,sans-serif;background:#f4f4f9;margin:0;padding:20px}.container{max-width:600px;background:white;padding:30px;margin:auto;border-radius:8px;box-shadow:0 0 10px rgba(0,0,0,.1)}h2{text-align:center;color:#333}label{font-weight:bold;display:block;margin-top:10px;color:#555}input,select,textarea{width:100%;padding:8px;margin-top:5px;margin-bottom:15px;border:1px solid #ccc;border-radius:4px;box-sizing:border-box}button{background:#007bff;color:white;border:none;padding:10px 15px;width:100%;font-size:16px;border-radius:4px;cursor:pointer}
</style></head><body><div class="container"><h2>DRD Manufacturing Solutions - Order Portal</h2>
<form method="POST" enctype="multipart/form-data">
<label>Company Name:</label><input type="text" name="company" required>
<label>Client Name:</label><input type="text" name="name" required>
<label>WhatsApp Number:</label><div style="display:flex;gap:10px"><select name="country_code" style="width:35%"><option value="92" selected>🇵🇰 +92</option><option value="971">🇦🇪 +971</option><option value="966">🇸🇦 +966</option><option value="44">🇬🇧 +44</option><option value="1">🇺🇸 +1</option></select><input type="text" name="whatsapp_num" required placeholder="3175240272" style="width:65%"></div>
<label>Part Name:</label><input type="text" name="part_name" required>
<label>Quantity:</label><input type="number" name="quantity" required>
<label>Material:</label><select name="material"><option>Aluminum</option><option>Stainless Steel</option><option>Brass</option><option>PETG / PLA Filament</option><option>ABS / TPU</option></select>
<label>Required Date:</label><input type="date" name="req_date" required>
<label>Detailed Requirements / Notes:</label><textarea name="requirement" rows="4"></textarea>
<label>Upload Drawing / CAD File:</label><input type="file" name="drawing_file">
<button type="submit">Submit Request</button></form></div></body></html>
'''

SUCCESS_PAGE = '''
<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Request Submitted</title>
<style>body{font-family:Arial;background:#f4f4f9;text-align:center;padding-top:50px}.box{background:white;max-width:500px;margin:auto;padding:40px;border-radius:8px;box-shadow:0 0 10px rgba(0,0,0,.1)}h2{color:#28a745}a{color:#007bff;font-weight:bold}</style></head>
<body><div class="box"><h2>Successfully Submitted!</h2><p>Your Job ID is: <strong>{{ job_id }}</strong></p><p>We have received your request and will review it shortly.</p><br><a href="/">Submit Another Request</a></div></body></html>
'''

PAYMENT_PAGE = '''
<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Confirm Order & Payment</title>
<style>body{font-family:Arial;background:#f4f4f9;padding:20px}.box{max-width:600px;background:#fff;margin:auto;padding:25px;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.1)}label{font-weight:bold;display:block;margin-top:12px}input,textarea{width:100%;padding:9px;margin:5px 0 12px;box-sizing:border-box}button{background:#28a745;color:#fff;border:0;padding:12px;width:100%;border-radius:5px;font-weight:bold;font-size:16px}.info{background:#f0f7ff;padding:12px;border-radius:6px;margin-bottom:15px}</style></head>
<body><div class="box"><h2>Confirm Order & Payment</h2><div class="info"><b>Job ID:</b> {{ req.job_id }}<br><b>Part:</b> {{ req.part_name }}<br><b>Amount Payable:</b> Rs. {{ '%.2f'|format(req.net_payable) }}</div>
<form action="/payment-submit/{{ req.job_id }}" method="POST" enctype="multipart/form-data"><label>Confirm Order</label><select name="customer_confirmed" style="width:100%;padding:9px"><option value="yes">Yes, please start my order after payment verification</option></select>
<label>Payment Amount</label><input type="number" step="any" name="payment_amount" value="{{ req.net_payable }}" required>
<label>Transaction / Reference ID</label><input type="text" name="transaction_id" required>
<label>Payment Date</label><input type="date" name="payment_date" required>
<label>Payment Screenshot</label><input type="file" name="payment_file" accept="image/*,.pdf">
<button type="submit">Submit Confirmation</button></form></div></body></html>
'''

PAYMENT_SUCCESS = '''
<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Submitted</title><style>body{font-family:Arial;background:#f4f4f9;text-align:center;padding:50px}.box{background:#fff;padding:35px;max-width:500px;margin:auto;border-radius:10px}</style></head><body><div class="box"><h2 style="color:#28a745">Confirmation Submitted</h2><p>Job ID: <b>{{ job_id }}</b></p><p>Your payment details have been received. We will verify the payment and proceed with the order.</p></div></body></html>
'''

SETTINGS_PAGE = '''
<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DRD Settings</title>
<style>body{font-family:Arial;background:#f4f4f9;margin:20px}.box{max-width:900px;background:#fff;margin:auto;padding:25px;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.1)}label{font-weight:bold;display:block;margin-top:10px}input,textarea,select{width:100%;padding:8px;margin:5px 0 10px;box-sizing:border-box}button{background:#007bff;color:#fff;border:0;padding:10px 15px;border-radius:5px;font-weight:bold}.row{display:grid;grid-template-columns:1fr 1fr;gap:15px}.check{width:auto}.back{display:inline-block;margin-bottom:15px}@media(max-width:700px){.row{grid-template-columns:1fr}}</style></head>
<body><div class="box"><a class="back" href="/drd-secure-admin">← Back to Admin</a><h2>⚙️ Admin Settings</h2><form action="/settings" method="POST">
<div class="row"><div><label>Company Name</label><input name="company_name" value="{{ s.company_name }}"></div><div><label>Address</label><input name="address" value="{{ s.address }}"></div></div>
<div class="row"><div><label>Phone</label><input name="phone" value="{{ s.phone }}"></div><div><label>Email</label><input name="email" value="{{ s.email }}"></div></div>
<div class="row"><div><label>Website</label><input name="website" value="{{ s.website }}"></div><div><label>NTN</label><input name="ntn" value="{{ s.ntn }}"></div></div>
<label>GST / STRN</label><input name="strn" value="{{ s.strn }}">
<h3>Payment / Bank Details</h3><div class="row"><div><label>Bank Name</label><input name="bank_name" value="{{ s.bank_name }}"></div><div><label>Account Title</label><input name="account_title" value="{{ s.account_title }}"></div></div>
<div class="row"><div><label>Account Number</label><input name="account_number" value="{{ s.account_number }}"></div><div><label>IBAN</label><input name="iban" value="{{ s.iban }}"></div></div>
<label>Payment Instructions</label><textarea name="payment_instructions" rows="3">{{ s.payment_instructions }}</textarea>
<h3>Tax Settings</h3><label><input class="check" type="checkbox" name="gst_enabled" {% if s.gst_enabled %}checked{% endif %}> Enable GST</label><label>GST Percentage</label><input type="number" step="any" name="gst_percent" value="{{ s.gst_percent }}">
<label><input class="check" type="checkbox" name="wht_enabled" {% if s.wht_enabled %}checked{% endif %}> Enable Income Tax / WHT</label><label>Income Tax / WHT Percentage</label><input type="number" step="any" name="wht_percent" value="{{ s.wht_percent }}"><label>WHT Mode</label><select name="wht_mode"><option value="deduct" {% if s.wht_mode=='deduct' %}selected{% endif %}>Deduct from customer payable</option><option value="add" {% if s.wht_mode=='add' %}selected{% endif %}>Add to amount</option></select>
<p><b>Letterhead:</b> Add the file <code>letterhead.pdf</code> to the project repository. The quotation and invoice will use it as the background.</p>
<button type="submit">Save Settings</button></form></div></body></html>
'''

ADMIN_PAGE = '''
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DRD Admin Dashboard</title>
<style>
body{font-family:Arial,sans-serif;background:#f4f4f9;margin:20px}h2{color:#333}table{width:100%;border-collapse:collapse;background:white;margin-top:15px;box-shadow:0 0 10px rgba(0,0,0,.1)}th,td{padding:10px;border:1px solid #ddd;text-align:left;font-size:13px;vertical-align:top}th{background:#343a40;color:white}tr:nth-child(even){background:#f9f9f9}a{color:#007bff;text-decoration:none}.update-box{background:#f8f9fa;padding:8px;border-radius:4px;border:1px solid #ccc}input,textarea,select{width:100%;padding:5px;margin-top:4px;margin-bottom:6px;box-sizing:border-box}button{background:#28a745;color:white;border:none;padding:6px 12px;cursor:pointer;border-radius:4px;font-weight:bold}.btn-danger{background:#dc3545!important}.btn-info{background:#17a2b8!important;color:white!important}.btn-purple{background:#6f42c1!important;color:white!important}.btn-dark{background:#343a40!important;color:white!important}.section-new{border-left:5px solid #dc3545}.section-processed{border-left:5px solid #ffc107;background:#fffdf5!important}.section-completed{border-left:5px solid #28a745;background:#f1f8f5!important}.tab-bar{display:flex;gap:10px;margin-bottom:20px;border-bottom:2px solid #ccc;padding-bottom:10px;flex-wrap:wrap}.tab-btn{background:#6c757d;color:white;border:none;padding:10px 20px;font-size:15px;border-radius:4px;cursor:pointer;font-weight:bold}.tab-btn.active{background:#007bff}.tab-content{display:none}.tab-content.active{display:block}.payment-box{background:#eefaf2;padding:8px;border:1px solid #b7dfc4;border-radius:5px;margin-top:8px}.warn{background:#fff3cd;padding:8px;border-radius:5px}.verified{color:#198754;font-weight:bold}.pending{color:#d39e00;font-weight:bold}
</style><script>function openTab(evt,tabName){var i,tc,tb;tc=document.getElementsByClassName('tab-content');for(i=0;i<tc.length;i++)tc[i].style.display='none';tb=document.getElementsByClassName('tab-btn');for(i=0;i<tb.length;i++)tb[i].className=tb[i].className.replace(' active','');document.getElementById(tabName).style.display='block';evt.currentTarget.className+=' active';}</script></head>
<body><h2>DRD Manufacturing Solutions - Admin Dashboard</h2><p>Manage orders, quotations, payments and completed invoices.</p><p><a href="/settings" class="btn-dark" style="color:white;padding:8px 12px;border-radius:4px;display:inline-block">⚙️ Settings</a></p>
<div class="tab-bar"><button class="tab-btn active" onclick="openTab(event,'tab-new')">📥 New Requests</button><button class="tab-btn" onclick="openTab(event,'tab-in-progress')">⏳ Quotations Sent / In Progress</button><button class="tab-btn" onclick="openTab(event,'tab-completed')">✅ Completed Orders</button></div>

<div id="tab-new" class="tab-content active"><h3 style="color:#dc3545">📥 New / Pending Requests</h3><table><tr><th>Job ID & Time</th><th>Client & Company</th><th>Part & Material</th><th>Qty & Date</th><th>Drawing</th><th>Quotation</th></tr>{% set n=0 %}{% for req in requests %}{% if req.status=='New' %}{% set n=n+1 %}<tr class="section-new"><td><b>{{ req.job_id }}</b><br><small>{{ req.time }}</small></td><td>{{ req.company }}<br><b>{{ req.name }}</b><br><small>WA: {{ req.whatsapp }}</small></td><td>{{ req.part_name }}<br><small>{{ req.material }}</small></td><td>Qty: {{ req.quantity }}<br><small>{{ req.req_date }}</small></td><td>{% if req.file %}<a href="/uploads/{{ req.file }}" target="_blank">View File</a>{% else %}No File{% endif %}<br><br><small><b>Notes:</b> {{ req.requirement }}</small></td><td><div class="update-box"><form action="/update/{{ req.job_id }}" method="POST"><label>Base Price</label><input type="number" step="any" name="base_price" value="{{ req.base_price if req.base_price!=0 else '' }}" required><label>GST</label><input type="number" step="any" name="gst_percent" value="{{ req.gst_percent }}"><label>Enable GST</label><select name="gst_enabled"><option value="yes">Use current setting</option><option value="no">Disable for this quotation</option></select><label>Income Tax / WHT</label><select name="wht_enabled"><option value="use">Use current setting</option><option value="no">Disable for this quotation</option></select><label>WHT % Override (optional)</label><input type="number" step="any" name="wht_percent" placeholder="Leave blank for setting"><label>Delivery Time</label><input type="text" name="delivery_time" required placeholder="e.g. 3 Days"><label>Remarks</label><textarea name="remarks"></textarea><button type="submit">Save & Send Quotation</button></form></div><br><form action="/delete/{{ req.job_id }}" method="POST" onsubmit="return confirm('Delete this order?');"><button class="btn-danger">🗑️ Delete Order</button></form></td></tr>{% endif %}{% endfor %}{% if n==0 %}<tr><td colspan="6" style="text-align:center;color:#777">No new requests found.</td></tr>{% endif %}</table></div>

<div id="tab-in-progress" class="tab-content"><h3 style="color:#d39e00">⏳ Quotations Sent / In Progress</h3><table><tr><th>Job ID</th><th>Client</th><th>Part</th><th>Quotation</th><th>Payment / Confirmation</th><th>Actions</th></tr>{% set p=0 %}{% for req in requests %}{% if req.status=='Quotation Sent' %}{% set p=p+1 %}<tr class="section-processed"><td><b>{{ req.job_id }}</b><br><small>{{ req.time }}</small></td><td>{{ req.company }}<br><b>{{ req.name }}</b><br><small>WA: {{ req.whatsapp }}</small></td><td>{{ req.part_name }}<br><small>{{ req.material }} - Qty {{ req.quantity }}</small></td><td><div class="update-box"><form action="/update/{{ req.job_id }}" method="POST"><label>Base Price</label><input type="number" step="any" name="base_price" value="{{ req.base_price }}" required><label>GST %</label><input type="number" step="any" name="gst_percent" value="{{ req.gst_percent }}"><label>GST</label><select name="gst_enabled"><option value="yes" {% if req.gst_enabled %}selected{% endif %}>Enabled</option><option value="no" {% if not req.gst_enabled %}selected{% endif %}>Disabled</option></select><label>WHT</label><select name="wht_enabled"><option value="yes" {% if req.wht_enabled %}selected{% endif %}>Enabled</option><option value="no" {% if not req.wht_enabled %}selected{% endif %}>Disabled</option></select><label>WHT %</label><input type="number" step="any" name="wht_percent" value="{{ req.wht_percent }}"><label>Delivery</label><input type="text" name="delivery_time" value="{{ req.delivery_time }}" required><label>Remarks</label><textarea name="remarks">{{ req.remarks }}</textarea><button style="background:#007bff">Update & Re-calculate</button></form></div><hr><b>Base:</b> Rs. {{ '%.2f'|format(req.base_price) }}<br>{% if req.gst_enabled %}<b>GST:</b> Rs. {{ '%.2f'|format(req.gst_amount) }}<br>{% endif %}{% if req.wht_enabled %}<b>WHT:</b> Rs. {{ '%.2f'|format(req.wht_amount) }}<br>{% endif %}<b>Total:</b> Rs. {{ '%.2f'|format(req.net_payable) }}</td><td class="payment-box"><b>Customer:</b> {% if req.customer_confirmed %}<span class="verified">Confirmed ✅</span>{% else %}<span class="pending">Not Confirmed</span>{% endif %}<br><b>Payment:</b> {% if req.payment_status=='Verified' %}<span class="verified">Verified ✅</span>{% elif req.payment_status=='Submitted' %}<span class="pending">Pending Verification ⏳</span>{% else %}<span class="pending">Not Submitted</span>{% endif %}{% if req.payment_status=='Submitted' %}<br><small>Amount: Rs. {{ req.payment_amount }}<br>Ref: {{ req.transaction_id }}<br>Date: {{ req.payment_date }}</small>{% if req.payment_file %}<br><a href="/uploads/{{ req.payment_file }}" target="_blank">View Payment Proof</a>{% endif %}<form action="/verify-payment/{{ req.job_id }}" method="POST" style="margin-top:6px"><button class="btn-info">✅ Verify Payment</button></form>{% endif %}</td><td><a href="{{ req.wa_link }}" target="_blank" style="background:#25d366;color:white;padding:6px 10px;border-radius:4px;display:inline-block;margin-bottom:5px">💬 WhatsApp</a><br><a href="/quotation/{{ req.job_id }}.pdf" target="_blank" style="background:#6f42c1;color:white;padding:6px 10px;border-radius:4px;display:inline-block;margin-bottom:5px">📄 Quotation PDF</a><br>{% if req.payment_status=='Verified' %}<span class="verified">Ready for production ✅</span><br>{% endif %}<form action="/complete/{{ req.job_id }}" method="POST" style="margin-top:5px"><button class="btn-info">✅ Mark as Completed</button></form><form action="/delete/{{ req.job_id }}" method="POST" style="margin-top:5px" onsubmit="return confirm('Delete this order?');"><button class="btn-danger">🗑️ Delete</button></form></td></tr>{% endif %}{% endfor %}{% if p==0 %}<tr><td colspan="6" style="text-align:center;color:#777">No quotations in progress.</td></tr>{% endif %}</table></div>

<div id="tab-completed" class="tab-content"><h3 style="color:#28a745">✅ Completed Orders / Invoices</h3><table><tr><th>Job ID</th><th>Client</th><th>Part</th><th>Final Pricing</th><th>Invoice / Actions</th></tr>{% set c=0 %}{% for req in requests %}{% if req.status=='Completed' %}{% set c=c+1 %}<tr class="section-completed"><td><b>{{ req.job_id }}</b><br><small>{{ req.time }}</small></td><td>{{ req.company }}<br><b>{{ req.name }}</b><br><small>{{ req.whatsapp }}</small></td><td>{{ req.part_name }}<br><small>{{ req.material }} - Qty {{ req.quantity }}</small></td><td>Base: Rs. {{ '%.2f'|format(req.base_price) }}<br>{% if req.gst_enabled %}GST: Rs. {{ '%.2f'|format(req.gst_amount) }}<br>{% endif %}{% if req.wht_enabled %}WHT: Rs. {{ '%.2f'|format(req.wht_amount) }}<br>{% endif %}<b>Total: Rs. {{ '%.2f'|format(req.net_payable) }}</b><br>Payment: {{ req.payment_status }}</td><td><a href="/invoice/{{ req.job_id }}.pdf" target="_blank" style="background:#28a745;color:white;padding:7px 10px;border-radius:4px;display:inline-block;margin-bottom:5px">🧾 View / Print Invoice</a><br><a href="{{ req.wa_link }}" target="_blank" style="background:#25d366;color:white;padding:5px 8px;border-radius:4px;display:inline-block;margin-bottom:5px">💬 WhatsApp</a><form action="/delete/{{ req.job_id }}" method="POST" onsubmit="return confirm('Delete this completed order?');"><button class="btn-danger">🗑️ Delete</button></form></td></tr>{% endif %}{% endfor %}{% if c==0 %}<tr><td colspan="5" style="text-align:center;color:#777">No completed orders yet.</td></tr>{% endif %}</table></div>
</body></html>
'''


@app.route('/', methods=['GET', 'POST'])
def client_form():
    if request.method == 'POST':
        date_str = datetime.now().strftime('%y%m%d')
        existing = load_requests()
        seq = len(existing) + 1
        job_id = f"DRD-{date_str}-{seq:04d}"

        company = request.form.get('company', '')
        name = request.form.get('name', '')
        country_code = request.form.get('country_code', '92')
        w_num = ''.join(filter(str.isdigit, request.form.get('whatsapp_num', '')))
        if w_num.startswith('0'):
            w_num = w_num[1:]
        whatsapp = country_code + w_num
        part_name = request.form.get('part_name', '')
        quantity = request.form.get('quantity', '')
        material = request.form.get('material', '')
        req_date = request.form.get('req_date', '')
        requirement = request.form.get('requirement', '')

        file = request.files.get('drawing_file')
        filename = ''
        if file and file.filename:
            safe_name = os.path.basename(file.filename)
            filename = f"{job_id}_{safe_name}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        settings = load_settings()
        new_entry = {
            'job_id': job_id, 'company': company, 'name': name, 'whatsapp': whatsapp,
            'part_name': part_name, 'quantity': quantity, 'material': material,
            'req_date': req_date, 'requirement': requirement, 'file': filename,
            'status': 'New', 'base_price': 0, 'gst_percent': settings.get('gst_percent', 18),
            'gst_enabled': bool(settings.get('gst_enabled', True)), 'gst_amount': 0,
            'wht_enabled': bool(settings.get('wht_enabled', False)), 'wht_percent': settings.get('wht_percent', 0),
            'wht_mode': settings.get('wht_mode', 'deduct'), 'wht_amount': 0,
            'gross_total': 0, 'total_price': 0, 'net_payable': 0,
            'delivery_time': 'Pending', 'remarks': '', 'customer_confirmed': False,
            'payment_status': 'Not Submitted', 'payment_amount': 0, 'transaction_id': '',
            'payment_date': '', 'payment_file': '', 'invoice_number': '', 'invoice_date': '',
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        existing.append(new_entry)
        save_all_requests(existing)
        return render_template_string(SUCCESS_PAGE, job_id=job_id)
    return render_template_string(INDEX_PAGE)


@app.route('/drd-secure-admin')
def admin_dashboard():
    return render_template_string(ADMIN_PAGE, requests=load_requests())


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        s = load_settings()
        for key in ['company_name', 'address', 'phone', 'email', 'website', 'ntn', 'strn', 'bank_name', 'account_title', 'account_number', 'iban', 'payment_instructions']:
            s[key] = request.form.get(key, '').strip()
        try:
            s['gst_percent'] = float(request.form.get('gst_percent', 18))
        except ValueError:
            s['gst_percent'] = 18.0
        try:
            s['wht_percent'] = float(request.form.get('wht_percent', 0))
        except ValueError:
            s['wht_percent'] = 0.0
        s['gst_enabled'] = request.form.get('gst_enabled') == 'on'
        s['wht_enabled'] = request.form.get('wht_enabled') == 'on'
        s['wht_mode'] = request.form.get('wht_mode', 'deduct')
        save_settings(s)
        return redirect(url_for('settings_page'))
    return render_template_string(SETTINGS_PAGE, s=load_settings())


@app.route('/update/<job_id>', methods=['POST'])
def update_job(job_id):
    all_reqs = load_requests()
    settings = load_settings()
    for req in all_reqs:
        if req['job_id'] == job_id:
            try:
                base_price = float(request.form.get('base_price', 0))
            except ValueError:
                base_price = 0.0
            try:
                gst_percent = float(request.form.get('gst_percent', settings.get('gst_percent', 18)))
            except ValueError:
                gst_percent = float(settings.get('gst_percent', 18))
            gst_enabled = request.form.get('gst_enabled', 'yes') != 'no'
            wht_select = request.form.get('wht_enabled', 'use')
            wht_enabled = settings.get('wht_enabled', False) if wht_select in ('use', 'yes') else False
            try:
                wht_percent = float(request.form.get('wht_percent') or settings.get('wht_percent', 0))
            except ValueError:
                wht_percent = float(settings.get('wht_percent', 0))
            if wht_select == 'yes':
                wht_enabled = True

            wht_mode = settings.get('wht_mode', 'deduct')
            gst_amount, gross_total, wht_amount, total_price, net_payable = calculate_amounts(
                base_price, gst_enabled, gst_percent, wht_enabled, wht_percent, wht_mode
            )

            req['base_price'] = base_price
            req['gst_percent'] = gst_percent
            req['gst_enabled'] = gst_enabled
            req['gst_amount'] = gst_amount
            req['wht_enabled'] = wht_enabled
            req['wht_percent'] = wht_percent
            req['wht_mode'] = wht_mode
            req['wht_amount'] = wht_amount
            req['gross_total'] = gross_total
            req['total_price'] = total_price
            req['net_payable'] = net_payable
            req['delivery_time'] = request.form.get('delivery_time', '')
            req['remarks'] = request.form.get('remarks', '')
            req['status'] = 'Quotation Sent'
            break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/payment/<job_id>')
def payment_confirmation(job_id):
    req, _ = find_request(job_id)
    if not req:
        return 'Job not found', 404
    return render_template_string(PAYMENT_PAGE, req=req)


@app.route('/payment-submit/<job_id>', methods=['POST'])
def payment_submit(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req['job_id'] == job_id:
            req['customer_confirmed'] = request.form.get('customer_confirmed') == 'yes'
            try:
                req['payment_amount'] = float(request.form.get('payment_amount', 0))
            except ValueError:
                req['payment_amount'] = 0
            req['transaction_id'] = request.form.get('transaction_id', '').strip()
            req['payment_date'] = request.form.get('payment_date', '')
            file = request.files.get('payment_file')
            if file and file.filename:
                safe_name = os.path.basename(file.filename)
                filename = f"{job_id}_PAYMENT_{safe_name}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                req['payment_file'] = filename
            req['payment_status'] = 'Submitted'
            break
    save_all_requests(all_reqs)
    return render_template_string(PAYMENT_SUCCESS, job_id=job_id)


@app.route('/verify-payment/<job_id>', methods=['POST'])
def verify_payment(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req['job_id'] == job_id:
            req['payment_status'] = 'Verified'
            break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/complete/<job_id>', methods=['POST'])
def complete_job(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req['job_id'] == job_id:
            req['status'] = 'Completed'
            if not req.get('invoice_number'):
                req['invoice_number'] = next_invoice_number(req)
            req['invoice_date'] = datetime.now().strftime('%Y-%m-%d')
            break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/quotation/<job_id>.pdf')
def quotation_pdf(job_id):
    req, _ = find_request(job_id)
    if not req:
        return 'Job not found', 404
    try:
        pdf = build_document_pdf(req, 'quotation')
        return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f"Quotation_{job_id}.pdf")
    except FileNotFoundError as e:
        return str(e), 500


@app.route('/invoice/<job_id>.pdf')
def invoice_pdf(job_id):
    req, _ = find_request(job_id)
    if not req:
        return 'Job not found', 404
    try:
        pdf = build_document_pdf(req, 'invoice')
        return send_file(pdf, mimetype='application/pdf', as_attachment=False, download_name=f"Invoice_{job_id}.pdf")
    except FileNotFoundError as e:
        return str(e), 500


@app.route('/delete/<job_id>', methods=['POST'])
def delete_job(job_id):
    all_reqs = load_requests()
    all_reqs = [req for req in all_reqs if req['job_id'] != job_id]
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    app.run(debug=False, threaded=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
