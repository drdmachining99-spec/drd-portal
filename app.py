import os
import json
import urllib.parse
from io import BytesIO
from datetime import datetime

from flask import Flask, request, redirect, url_for, send_from_directory, render_template_string, send_file
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
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
    'ntn': '', 'strn': '',
    'bank_name': '', 'account_title': '', 'account_number': '', 'iban': '',
    'payment_instructions': 'Please make payment against the approved quotation and submit the payment confirmation.',
    'gst_enabled': True, 'gst_percent': 18.0,
    'wht_enabled': False, 'wht_percent': 0.0, 'wht_mode': 'deduct',
    'payment_terms': '50_50',
    'custom_payment_terms': '',
    'notification_numbers': ['', '', ''],
    'admin_pin': '',
    'default_terms_conditions': '',
    'terms_library': [],
    'welcome_voice_lang': 'en-US',
    'welcome_voice_hint': '',
    'welcome_pitch': 1.1,
    'welcome_rate': 0.93,
    'welcome_volume': 1.0,
    'welcome_message_en': ("Welcome to D R D Manufacturing Solutions! I'm here to help you place your order. "
                            "Tell me, what would you like us to manufacture for you today? Please fill in your details below, "
                            "and use the microphone buttons if you'd rather speak than type."),
    'welcome_message_ur': ("ڈی آر ڈی مینوفیکچرنگ میں خوش آمدید۔ میں آپ کا آرڈر لینے کے لیے حاضر ہوں۔ بتائیں، آج آپ ہم سے کیا بنوانا "
                            "چاہتے ہیں؟ نیچے اپنی تفصیلات پر کریں، اور بول کر بتانا چاہیں تو مائک بٹن استعمال کریں۔"),
    'admin_auto_refresh': True,
    'admin_notify_sound': True,
    'portfolio': []
}

FALLBACK_TERMS = ('Quoted prices are based on the stated scope, quantities and specifications. Any change in drawing, '
                   'material, quantity, finish or scope may affect price and delivery. GST is applied as stated above. Production will proceed '
                   'according to the agreed payment terms. Final delivery is subject to completion of applicable inspection/QC.')


def load_settings():
    data = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            data.update(saved)
        except Exception as e:
            print('Error loading settings:', e)
    nums = data.get('notification_numbers', ['', '', ''])
    if not isinstance(nums, list):
        nums = ['', '', '']
    data['notification_numbers'] = (nums + ['', '', ''])[:3]
    lib = data.get('terms_library', [])
    if not isinstance(lib, list):
        lib = []
    clean_lib = []
    for i, c in enumerate(lib):
        if not isinstance(c, dict):
            continue
        title = str(c.get('title', '')).strip()
        text = str(c.get('text', '')).strip()
        if not title and not text:
            continue
        clean_lib.append({'id': c.get('id') or f't{i+1}', 'title': title or f'Clause {i+1}', 'text': text})
    data['terms_library'] = clean_lib
    data['welcome_pitch'] = min(2.0, max(0.5, safe_float(data.get('welcome_pitch'), 1.1)))
    data['welcome_rate'] = min(1.5, max(0.5, safe_float(data.get('welcome_rate'), 0.93)))
    data['welcome_volume'] = min(1.0, max(0.0, safe_float(data.get('welcome_volume'), 1.0)))
    portfolio = data.get('portfolio', [])
    if not isinstance(portfolio, list):
        portfolio = []
    data['portfolio'] = [{'file': str(p.get('file', '')), 'caption': str(p.get('caption', ''))}
                          for p in portfolio if isinstance(p, dict) and p.get('file')]
    return data


def save_settings(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)


def calculate_amounts(base_price, gst_enabled, gst_percent, wht_enabled, wht_percent, wht_mode):
    gst_amount = base_price * gst_percent / 100.0 if gst_enabled else 0.0
    gross_total = base_price + gst_amount
    wht_amount = gross_total * wht_percent / 100.0 if wht_enabled else 0.0
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


def money(v):
    try:
        return f"Rs. {float(v or 0):,.2f}"
    except Exception:
        return 'Rs. 0.00'


def safe_float(v, default=0.0):
    """Never let a bad/blank number crash a request."""
    try:
        if v is None or str(v).strip() == '':
            return default
        return float(v)
    except Exception:
        return default


def ensure_parts(req):
    parts = req.get('parts')
    if not isinstance(parts, list) or not parts:
        parts = [{
            'part_no': req.get('part_no', 'P-001'),
            'part_name': req.get('part_name', ''),
            'description': req.get('requirement', ''),
            'quantity': req.get('quantity', ''),
            'material': req.get('material', ''),
            'finish': req.get('finish', ''),
            'operations': req.get('operations', ''),
            'inspection': req.get('inspection', ''),
            'price': safe_float(req.get('base_price', 0))
        }]
    clean = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            continue
        price = safe_float(p.get('price', 0))
        clean.append({
            'part_no': str(p.get('part_no', '') or f'P-{i+1:03d}'),
            'part_name': str(p.get('part_name', '')),
            'description': str(p.get('description', '')),
            'quantity': str(p.get('quantity', '')),
            'material': str(p.get('material', '')),
            'finish': str(p.get('finish', '')),
            'operations': str(p.get('operations', '')),
            'inspection': str(p.get('inspection', '')),
            'price': price
        })
    return clean


def payment_terms_text(req, settings):
    mode = req.get('payment_terms', settings.get('payment_terms', '50_50'))
    if mode == '100_advance':
        return '100% Advance payment before production.'
    if mode == '50_50':
        return '50% Advance + 50% before / at delivery.'
    if mode == 'custom':
        return req.get('custom_payment_terms') or settings.get('custom_payment_terms', '') or 'As mutually agreed.'
    return 'As mutually agreed.'


def make_whatsapp_link(req, settings):
    phone = ''.join(filter(str.isdigit, str(req.get('whatsapp', ''))))
    if not phone:
        return '#'
    status = req.get('status', 'New')
    parts = ensure_parts(req)
    heading = {
        'New': "Thank you for your request. We'll share your quotation shortly.",
        'Quotation Sent': "Your quotation is ready — please see the details below.",
        'Completed': "Your order is complete! Please find your invoice details below."
    }.get(status, "Here are your order details.")
    lines = [f"Hello *{req.get('name', '')}*,", '',
             f"{heading} (Job ID: *{req.get('job_id', '')}* — {settings.get('company_name', '')})", '']
    for i, p in enumerate(parts, 1):
        lines += [f"*{i}. {p['part_no']} - {p['part_name']}*",
                  f"Qty: {p['quantity']} | Material: {p['material']}",
                  f"Price: Rs. {p['price']:,.2f}"]
        if p.get('description'):
            lines.append(f"Description: {p['description']}")
    lines += ['', f"*Parts Total:* Rs. {safe_float(req.get('base_price')):,.2f}"]
    if req.get('gst_enabled'):
        lines.append(f"*GST ({safe_float(req.get('gst_percent')):g}%):* Rs. {safe_float(req.get('gst_amount')):,.2f}")
    if req.get('wht_enabled'):
        lines.append(f"*WHT ({safe_float(req.get('wht_percent')):g}%):* Rs. {safe_float(req.get('wht_amount')):,.2f}")
    lines += [f"*Net Payable:* *Rs. {safe_float(req.get('net_payable')):,.2f}*",
              f"*Delivery:* {req.get('delivery_time', 'Pending')}",
              f"*Payment Terms:* {payment_terms_text(req, settings)}"]
    if req.get('remarks'):
        lines += ['', f"*Remarks:* {req['remarks']}"]
    if req.get('job_id'):
        if status == 'Completed':
            # Order is done - the client should be sent the Invoice, not the Quotation.
            lines += ['', f"*Invoice PDF:* {url_for('invoice_pdf', job_id=req['job_id'], _external=True)}"]
            if req.get('balance_due', 0) and safe_float(req.get('balance_due')) > 0:
                lines.append(f"*Payment / Confirmation:* {url_for('payment_confirmation', job_id=req['job_id'], _external=True)}")
        elif status == 'Quotation Sent':
            lines += ['', f"*Quotation PDF:* {url_for('quotation_pdf', job_id=req['job_id'], _external=True)}",
                      f"*Payment / Confirmation:* {url_for('payment_confirmation', job_id=req['job_id'], _external=True)}"]
        # status == 'New': no quotation/invoice file exists yet, so no PDF link is sent
    encoded = urllib.parse.quote('\n'.join(lines))
    return f"https://wa.me/{phone}?text={encoded}"


def load_requests():
    settings = load_settings()
    if not os.path.exists(REQUESTS_FILE):
        return []
    try:
        with open(REQUESTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        changed = False
        for req in data:
            defaults = {
                'base_price': 0, 'gst_percent': settings.get('gst_percent', 18), 'gst_enabled': settings.get('gst_enabled', True), 'gst_amount': 0,
                'wht_enabled': False, 'wht_percent': 0, 'wht_mode': 'deduct', 'wht_amount': 0, 'gross_total': 0, 'total_price': 0, 'net_payable': 0,
                'delivery_time': 'Pending', 'remarks': '', 'status': 'New', 'quotation_type': 'standard', 'parts': [],
                'customer_confirmed': False, 'payment_terms': settings.get('payment_terms', '50_50'), 'custom_payment_terms': '',
                'advance_required': 0, 'advance_paid': 0, 'balance_due': 0, 'payment_status': 'Not Submitted', 'payment_amount': 0,
                'transaction_id': '', 'payment_date': '', 'payment_file': '', 'advance_transaction_id': '', 'balance_transaction_id': '',
                'advance_payment_file': '', 'balance_payment_file': '', 'advance_payment_status': 'Not Submitted', 'balance_payment_status': 'Not Submitted',
                'invoice_number': '', 'invoice_date': '', 'rfq_no': '', 'reference_no': '', 'project_title': '', 'technical_specification': '',
                'material_specification': '', 'manufacturing_operations': '', 'finish_specification': '', 'inspection_qc': '',
                'technical_notes': '', 'selected_terms': [], 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            for k, v in defaults.items():
                if k not in req:
                    req[k] = v
                    changed = True
            old = req.get('parts')
            if not isinstance(old, list) or not old:
                req['parts'] = ensure_parts(req)
                changed = True
            else:
                req['parts'] = ensure_parts(req)
            req['base_price'] = sum(safe_float(p['price']) for p in req['parts'])
            if not req.get('part_name'):
                req['part_name'] = req['parts'][0]['part_name']
                changed = True
            if not req.get('quantity'):
                req['quantity'] = req['parts'][0]['quantity']
                changed = True
            if not req.get('material'):
                req['material'] = req['parts'][0]['material']
                changed = True
            if req.get('status') != 'New' or req.get('base_price', 0):
                ge, gt, wa, tp, npv = calculate_amounts(
                    req['base_price'], bool(req.get('gst_enabled')), safe_float(req.get('gst_percent')),
                    bool(req.get('wht_enabled')), safe_float(req.get('wht_percent')), req.get('wht_mode', 'deduct'))
                req.update(gst_amount=ge, gross_total=gt, wht_amount=wa, total_price=tp, net_payable=npv)
            terms = req.get('payment_terms')
            if terms == '50_50':
                adv_ratio = 0.5
            elif terms == '100_advance':
                adv_ratio = 1.0
            else:
                adv_ratio = 0.0
            req['advance_required'] = round(req['net_payable'] * adv_ratio, 2)
            req['balance_due'] = max(0.0, round(req['net_payable'] - safe_float(req.get('advance_paid')), 2))
            req['wa_link'] = make_whatsapp_link(req, settings)
        if changed:
            save_all_requests(data)
        return sorted(data, key=lambda x: x.get('time', ''), reverse=True)
    except Exception as e:
        print('Error loading requests:', e)
        return []


def save_all_requests(reqs):
    clean = []
    for r in reqs:
        x = r.copy()
        x.pop('wa_link', None)
        clean.append(x)
    with open(REQUESTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(clean, f, indent=4)


def find_request(job_id):
    for req in load_requests():
        if req.get('job_id') == job_id:
            return req
    return None


def next_invoice_number(req):
    if req.get('invoice_number'):
        return req['invoice_number']
    return f"INV-{datetime.now().strftime('%y%m%d')}-{req.get('job_id', '0000').split('-')[-1]}"


def wrap_text(text, font, size, max_width):
    words = str(text or '').split()
    lines = []
    cur = ''
    for word in words:
        test = (cur + ' ' + word).strip()
        if stringWidth(test, font, size) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or ['']


def draw_wrapped(c, text, x, y, width, font='Helvetica', size=7.5, leading=9, max_lines=6):
    c.setFont(font, size)
    lines = []
    for para in str(text or '').splitlines() or ['']:
        lines += wrap_text(para, font, size, width)
    for line in lines[:max_lines]:
        c.drawString(x, y, line)
        y -= leading
    return y, min(len(lines), max_lines)


def build_terms_text(req, settings):
    """Turns the clauses picked (checked) for this specific quotation into the final
    Terms & Conditions text. Falls back to the saved default / built-in wording when
    nothing was picked, so old requests keep working exactly as before."""
    selected_ids = req.get('selected_terms') or []
    lib = {c['id']: c for c in settings.get('terms_library', [])}
    picked = [lib[i]['text'] for i in selected_ids if i in lib and lib[i].get('text')]
    if picked:
        return '\n'.join(f"- {t}" for t in picked)
    return settings.get('default_terms_conditions') or FALLBACK_TERMS


def build_document_pdf(req, document_type='quotation'):
    settings = load_settings()
    if not os.path.exists(LETTERHEAD_FILE):
        raise FileNotFoundError('letterhead.pdf is missing from the project.')
    reader = PdfReader(LETTERHEAD_FILE)
    bg = reader.pages[0]
    width = float(bg.mediabox.width)
    height = float(bg.mediabox.height)
    parts = ensure_parts(req)
    title = 'QUOTATION' if document_type == 'quotation' else 'TAX / COMMERCIAL INVOICE'
    detailed = req.get('quotation_type') in ('detailed', 'technical')
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))
    left = 16 * mm
    right = width - 16 * mm
    top = height - 39 * mm
    bottom = 22 * mm

    def header():
        c.setFillColor(colors.HexColor('#123f5d'))
        c.setFont('Helvetica-Bold', 16)
        c.drawString(left, top, title)
        c.setFillColor(colors.black)
        c.setFont('Helvetica', 8)
        ref = req.get('reference_no') or req.get('rfq_no') or req.get('job_id', '')
        c.drawRightString(right, top + 1, f"No: {ref}")
        c.drawRightString(right, top - 11, f"Date: {datetime.now().strftime('%d-%m-%Y')}")
        # NTN / STRN shown only if the admin actually filled them in
        tax_bits = []
        if settings.get('ntn'):
            tax_bits.append(f"NTN: {settings['ntn']}")
        if settings.get('strn'):
            tax_bits.append(f"STRN: {settings['strn']}")
        if tax_bits:
            c.drawRightString(right, top - 22, ' | '.join(tax_bits))

    def footer(page_no):
        c.setFont('Helvetica', 7)
        c.setFillColor(colors.grey)
        c.drawString(left, 11 * mm, f"Job ID: {req.get('job_id', '')}")
        c.drawRightString(right, 11 * mm, f"Page {page_no}")

    page_no = 1
    header()
    y = top - 28
    c.setFillColor(colors.black)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(left, y, 'CUSTOMER')
    c.setFont('Helvetica', 8)
    y -= 11
    # Hide any customer line that is actually blank
    customer_lines = []
    if req.get('company'):
        customer_lines.append(str(req.get('company')))
    if req.get('name'):
        customer_lines.append(str(req.get('name')))
    if req.get('whatsapp'):
        customer_lines.append(f"WhatsApp: {req.get('whatsapp')}")
    for line in customer_lines:
        c.drawString(left, y, line)
        y -= 10
    if detailed:
        proj_fields = [('Project / Work Title', 'project_title'), ('RFQ / Reference No.', 'rfq_no')]
        proj_fields = [(l, k) for l, k in proj_fields if req.get(k)]
        if proj_fields:
            y -= 4
            c.setFont('Helvetica-Bold', 8.5)
            c.drawString(left, y, 'PROJECT / REFERENCE')
            y -= 11
            c.setFont('Helvetica', 8)
            for label, key in proj_fields:
                c.drawString(left, y, f"{label}: {req[key]}")
                y -= 10
    y -= 8
    headers = ['Sr. No.', 'Part No.', 'Description / Part Name', 'Qty', 'Material', 'Quoted Price', 'GST', 'Total']
    widths = [25, 43, 145, 30, 70, 65, 48, 65]
    scale = (right - left) / sum(widths)
    widths = [w * scale for w in widths]
    rowx = [left]
    for w in widths:
        rowx.append(rowx[-1] + w)

    def draw_table_header(y):
        c.setFillColor(colors.HexColor('#eaf1f6'))
        c.rect(left, y - 20, right - left, 20, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont('Helvetica-Bold', 6.8)
        for i, h in enumerate(headers):
            c.drawCentredString((rowx[i] + rowx[i + 1]) / 2, y - 13, h)
        return y - 20

    y = draw_table_header(y)
    base_total = 0
    gst_percent_val = safe_float(req.get('gst_percent'))
    gst_on = bool(req.get('gst_enabled'))
    for idx, p in enumerate(parts, 1):
        gst_line = p['price'] * gst_percent_val / 100 if gst_on else 0
        line_total = p['price'] + gst_line
        base_total += p['price']
        desc = p['part_name']
        tech = []
        if p.get('description'):
            tech.append(p['description'])
        if p.get('operations'):
            tech.append('Operations: ' + p['operations'])
        if p.get('finish'):
            tech.append('Finish: ' + p['finish'])
        if p.get('inspection'):
            tech.append('Inspection: ' + p['inspection'])
        desc += '\n' + ' | '.join(tech) if tech else ''
        dlines = []
        for para in desc.splitlines():
            dlines += wrap_text(para, 'Helvetica', 6.6, widths[2] - 6)
        shown = dlines[:4]
        rh = max(30, 10 * len(shown) + 10)
        if y - rh < bottom:
            footer(page_no)
            c.showPage()
            page_no += 1
            header()
            y = top - 20
            y = draw_table_header(y)
        c.rect(left, y - rh, right - left, rh, fill=0, stroke=1)
        c.setFont('Helvetica', 6.8)
        vals = [str(idx), p['part_no'], shown, p['quantity'], p['material'],
                f"{p['price']:,.2f}", (f"{gst_line:,.2f}" if gst_on else '-'), f"{line_total:,.2f}"]
        for i in range(8):
            c.line(rowx[i], y, rowx[i], y - rh)
        c.drawCentredString((rowx[0] + rowx[1]) / 2, y - 12, vals[0])
        c.drawString(rowx[1] + 3, y - 11, vals[1])
        yy = y - 10
        for dl in vals[2]:
            c.drawString(rowx[2] + 3, yy, dl)
            yy -= 9
        c.drawCentredString((rowx[3] + rowx[4]) / 2, y - rh / 2, vals[3])
        c.drawString(rowx[4] + 3, y - rh / 2 + 2, vals[4])
        c.drawRightString(rowx[6] - 3, y - rh / 2 + 2, vals[5])
        c.drawRightString(rowx[7] - 3, y - rh / 2 + 2, vals[6])
        c.drawRightString(rowx[8] - 3, y - rh / 2 + 2, vals[7])
        y -= rh

    gst = safe_float(req.get('gst_amount'))
    wht = safe_float(req.get('wht_amount'))
    gross = safe_float(req.get('gross_total'), base_total + gst)
    net = safe_float(req.get('net_payable'), gross)
    if y - 70 < bottom:
        footer(page_no)
        c.showPage()
        page_no += 1
        header()
        y = top - 25
    c.setFont('Helvetica-Bold', 8)
    c.drawRightString(right, y - 12, f"Parts Total: {money(base_total)}")
    y -= 24
    if req.get('gst_enabled'):
        c.setFont('Helvetica', 8)
        c.drawRightString(right, y - 10, f"GST ({gst_percent_val:g}%): {money(gst)}")
        y -= 20
    if req.get('wht_enabled'):
        c.drawRightString(right, y - 10, f"WHT ({safe_float(req.get('wht_percent')):g}%): {money(wht)}")
        y -= 20
    c.setFont('Helvetica-Bold', 10)
    label = 'NET PAYABLE' if (req.get('wht_enabled') and req.get('wht_mode') == 'deduct') else 'TOTAL PAYABLE'
    c.drawRightString(right, y - 10, f"{label}: {money(net)}")
    y -= 25

    sections = []
    if req.get('delivery_time'):
        dt = req['delivery_time']
        sections.append(('Delivery Schedule', f"Delivery within {dt} working days." if str(dt).isdigit() else str(dt)))
    sections.append(('Payment Terms', payment_terms_text(req, settings)))
    if req.get('remarks'):
        sections.append(('Commercial / Technical Notes', req['remarks']))
    if detailed:
        for title2, key in [('Technical Specification', 'technical_specification'), ('Material Specification', 'material_specification'),
                             ('Manufacturing Operations', 'manufacturing_operations'), ('Finish / Surface Treatment', 'finish_specification'),
                             ('Inspection / QC', 'inspection_qc'), ('Technical Notes', 'technical_notes')]:
            if req.get(key):
                sections.append((title2, req[key]))
    sections.append(('Terms & Conditions', build_terms_text(req, settings)))
    for st, txt in sections:
        if y - 35 < bottom:
            footer(page_no)
            c.showPage()
            page_no += 1
            header()
            y = top - 25
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(left, y, st)
        y -= 11
        y, _ = draw_wrapped(c, txt, left, y, right - left, 'Helvetica', 7.5, 9, 8)
        y -= 8

    # Bank details block - only shown if at least one bank field is filled in Settings
    bank_fields = [(label, key) for label, key in [('Bank', 'bank_name'), ('Account Title', 'account_title'),
                                                     ('Account Number', 'account_number'), ('IBAN', 'iban')] if settings.get(key)]
    if bank_fields or settings.get('payment_instructions'):
        if y - 75 < bottom:
            footer(page_no)
            c.showPage()
            page_no += 1
            header()
            y = top - 25
        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(left, y, 'Bank Details')
        y -= 12
        c.setFont('Helvetica', 7.8)
        for label, key in bank_fields:
            c.drawString(left, y, f"{label}: {settings[key]}")
            y -= 10
        if settings.get('payment_instructions'):
            y -= 2
            y, _ = draw_wrapped(c, settings['payment_instructions'], left, y, right - left, 'Helvetica', 7.5, 9, 3)

    if y - 20 < bottom:
        footer(page_no)
        c.showPage()
        page_no += 1
        header()
        y = top - 25
    y -= 10
    c.setFont('Helvetica-Bold', 8)
    c.drawString(left, y, 'Authorized Signatory')
    footer(page_no)
    c.save()
    packet.seek(0)
    overlay = PdfReader(packet)
    bg.merge_page(overlay.pages[0])
    writer = PdfWriter()
    writer.add_page(bg)
    out = BytesIO()
    writer.write(out)
    out.seek(0)
    return out


INDEX_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>DRD Order Portal</title><style>
*{box-sizing:border-box}
body{font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(160deg,#0d2b40,#123f5d 40%,#1d6fa5);min-height:100vh;padding:24px 16px;margin:0}
.box{max-width:640px;margin:20px auto;background:white;padding:30px;border-radius:16px;box-shadow:0 10px 35px rgba(0,0,0,.25)}
.brand{text-align:center;margin-bottom:6px}
.brand h2{margin:6px 0 2px;color:#123f5d}
.brand p{color:#777;margin:0 0 14px;font-size:14px}
.track-link{text-align:right;margin:-6px 0 14px}
.track-link a{color:#1d6fa5;text-decoration:none;font-weight:bold;font-size:13px}
.track-link a:hover{text-decoration:underline}
.option-bar{display:flex;gap:8px;flex-wrap:wrap;margin:-4px 0 14px}
.option-bar a{flex:1;min-width:140px;text-align:center;padding:10px 8px;border-radius:8px;background:#eef3f9;color:#123f5d;text-decoration:none;font-weight:bold;font-size:12.5px;border:1.5px solid #d6e6f5;transition:.15s}
.option-bar a:hover{background:#123f5d;color:white}
.portfolio-wrap{max-width:900px;margin:26px auto 0;overflow:hidden}
.portfolio-wrap h3{text-align:center;color:white;margin:0 0 12px;font-size:17px;opacity:.95}
.portfolio-track{display:flex;gap:16px;width:max-content;animation:scrollLeft 28s linear infinite}
.portfolio-track:hover{animation-play-state:paused}
.portfolio-item{flex:0 0 auto;width:160px;text-align:center;color:white;font-size:12px}
.portfolio-item img{width:160px;height:120px;object-fit:cover;border-radius:10px;box-shadow:0 4px 14px rgba(0,0,0,.35);display:block}
.portfolio-item span{display:block;margin-top:6px;opacity:.85}
@keyframes scrollLeft{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.voicebar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:linear-gradient(135deg,#f0f7ff,#eaf3fb);border:1.5px solid #d6e6f5;border-radius:10px;padding:10px 12px;margin:14px 0}
.voicebar select{width:auto;margin:0;padding:6px 8px;font-size:13px}
#voiceGuideToggle,#replayWelcome{width:auto;margin:0;padding:8px 14px;font-size:13px;background:#6c757d;background-image:none}
#voiceGuideToggle.on{background:linear-gradient(135deg,#123f5d,#1d6fa5)}
#replayWelcome{background:linear-gradient(135deg,#e67e22,#f39c12)}
.voicebar .hint{font-size:12px;color:#667;flex:1;min-width:120px}
label{font-weight:bold;display:flex;align-items:center;gap:8px;margin-top:14px;color:#333;font-size:14px}
.mic-btn{width:auto;margin:0;padding:5px 9px;border-radius:20px;font-size:14px;background:#eef3f9;color:#123f5d;font-weight:normal;box-shadow:none}
.mic-btn.listening{background:#dc3545;color:white;animation:pulse 1s infinite}
@keyframes pulse{0%{opacity:1}50%{opacity:.5}100%{opacity:1}}
input,select,textarea{width:100%;box-sizing:border-box;padding:11px;margin:6px 0 4px;border:1.5px solid #e1e5ec;border-radius:8px;font-size:14px;transition:.15s}
input:focus,select:focus,textarea:focus{outline:none;border-color:#1d6fa5;box-shadow:0 0 0 3px rgba(29,111,165,.12)}
button{width:100%;padding:14px;background:linear-gradient(135deg,#123f5d,#1d6fa5);color:white;border:0;border-radius:8px;font-weight:bold;font-size:15px;margin-top:18px;cursor:pointer;transition:.15s}
button:hover{opacity:.92;transform:translateY(-1px)}
</style></head><body><div class="box"><div class="brand"><h2>⚙️ DRD Manufacturing Solutions</h2><p>Engineering & Manufacturing Order Portal</p></div><div class="option-bar"><a href="#orderForm">📝 Submit New Order</a><a href="/track">📦 Track by Job ID</a><a href="/my-orders">📋 View All My Orders</a></div>

<div class="voicebar"><button type="button" id="voiceGuideToggle" onclick="toggleGuide(true)">🔊 Voice Guide: ON</button><button type="button" id="replayWelcome" onclick="playWelcome()">🔁 Replay Welcome</button><select id="voiceLang" onchange="onLangChange()"><option value="ur-PK" {% if settings.welcome_voice_lang.startswith('ur') %}selected{% endif %}>اردو</option><option value="en-US" {% if settings.welcome_voice_lang=='en-US' %}selected{% endif %}>English (US)</option><option value="en-GB" {% if settings.welcome_voice_lang=='en-GB' %}selected{% endif %}>English (UK)</option><option value="en-IN" {% if settings.welcome_voice_lang=='en-IN' %}selected{% endif %}>English (India)</option></select><span class="hint">If you didn't hear a voice automatically, tap "Replay Welcome" once.</span></div>

<form method="POST" enctype="multipart/form-data" id="orderForm">
<label>Company Name <button type="button" class="mic-btn" onclick="startVoice('company',this)">🎤</button></label><input name="company" id="company" required onfocus="guideField('company')">
<label>Client Name <button type="button" class="mic-btn" onclick="startVoice('name',this)">🎤</button></label><input name="name" id="name" required onfocus="guideField('name')">
<label>WhatsApp</label><div style="display:flex;gap:8px"><select name="country_code" style="width:35%"><option value="92">+92</option><option value="966">+966</option><option value="971">+971</option><option value="44">+44</option><option value="1">+1</option></select><input name="whatsapp_num" id="whatsapp_num" required placeholder="3175240272" onfocus="guideField('whatsapp_num')"></div>
<label>Part Name <button type="button" class="mic-btn" onclick="startVoice('part_name',this)">🎤</button></label><input name="part_name" id="part_name" required onfocus="guideField('part_name')">
<label>Quantity <button type="button" class="mic-btn" onclick="startVoice('quantity',this)">🎤</button></label><input name="quantity" id="quantity" type="number" min="1" required onfocus="guideField('quantity')">
<label>Material</label><select name="material" id="material" onfocus="guideField('material')"><option>Aluminum</option><option>Stainless Steel</option><option>Brass</option><option>Steel</option><option>PETG / PLA</option><option>ABS / TPU</option><option>Other</option></select>
<label>Required Date</label><input name="req_date" type="date">
<label>Requirements / Technical Notes <button type="button" class="mic-btn" onclick="startVoice('requirement',this)">🎤</button></label><textarea name="requirement" id="requirement" rows="5" onfocus="guideField('requirement')"></textarea>
<label>Drawing / CAD / Reference File</label><input type="file" name="drawing_file" accept=".step,.stp,.sldprt,.dxf,.dwg,.stl,.pdf,.zip,image/*">
<button>Submit Request →</button>
</form></div>
{% if settings.portfolio %}<div class="portfolio-wrap"><h3>🛠️ A Glimpse of What We've Built</h3><div class="portfolio-track">{% for item in settings.portfolio %}<div class="portfolio-item"><img src="/uploads/{{item.file}}" loading="lazy" alt="{{item.caption}}"><span>{{item.caption}}</span></div>{% endfor %}{% for item in settings.portfolio %}<div class="portfolio-item"><img src="/uploads/{{item.file}}" loading="lazy" alt="{{item.caption}}"><span>{{item.caption}}</span></div>{% endfor %}</div></div>{% endif %}
<script>
let guideOn = true;
let chosenVoice = null;
const GUIDE = {
  company:{en:"Please type or say your company name.",ur:"اپنی کمپنی کا نام بولیں یا لکھیں۔"},
  name:{en:"Please say your full name.",ur:"اپنا نام بتائیں۔"},
  whatsapp_num:{en:"Enter your WhatsApp number, without the country code.",ur:"اپنا واٹس ایپ نمبر لکھیں، کنٹری کوڈ کے بغیر۔"},
  part_name:{en:"What part or item do you need manufactured? Say its name.",ur:"آپ کو کون سا پرزہ بنوانا ہے؟ اس کا نام بولیں۔"},
  quantity:{en:"How many pieces do you need? Say the number.",ur:"کتنی تعداد چاہیے؟ نمبر بولیں۔"},
  material:{en:"Choose the material from the list.",ur:"فہرست سے میٹیریل منتخب کریں۔"},
  requirement:{en:"Describe your requirement in detail. You can also speak it using the microphone.",ur:"اپنی ضرورت تفصیل سے بتائیں، بول کر بھی بتا سکتے ہیں۔"}
};
const WELCOME = {
  en:{{ settings.welcome_message_en|tojson }},
  ur:{{ settings.welcome_message_ur|tojson }}
};
const VOICE_CFG = {
  hint:{{ settings.welcome_voice_hint|tojson }},
  pitch:{{ settings.welcome_pitch }},
  rate:{{ settings.welcome_rate }},
  volume:{{ settings.welcome_volume }}
};
function pickVoice(lang){
  const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  const base = lang.split('-')[0];
  const candidates = voices.filter(v => v.lang && v.lang.toLowerCase().startsWith(base));
  if(VOICE_CFG.hint){
    const hinted = candidates.find(v => v.name.toLowerCase().includes(VOICE_CFG.hint.toLowerCase()));
    if(hinted) return hinted;
  }
  const premium = candidates.find(v => /google|natural|neural|online|premium/i.test(v.name));
  if(premium) return premium;
  const nicePick = candidates.find(v => /female|zira|samantha|susan|google uk english female|heera|gul/i.test(v.name));
  return nicePick || candidates[0] || voices[0] || null;
}
function speak(text){
  if(!('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const lang = document.getElementById('voiceLang').value;
  u.lang = lang;
  u.pitch = VOICE_CFG.pitch;
  u.rate = VOICE_CFG.rate;
  u.volume = VOICE_CFG.volume;
  const v = pickVoice(lang);
  if(v) u.voice = v;
  window.speechSynthesis.speak(u);
}
function playWelcome(){
  const isUr = document.getElementById('voiceLang').value.startsWith('ur');
  speak(isUr ? WELCOME.ur : WELCOME.en);
}
function onLangChange(){ playWelcome(); }
function toggleGuide(forceOn){
  guideOn = (forceOn === true) ? true : !guideOn;
  const btn = document.getElementById('voiceGuideToggle');
  btn.textContent = (guideOn ? '🔊 Voice Guide: ON' : '🔊 Voice Guide: OFF');
  btn.classList.toggle('on', guideOn);
}
document.getElementById('voiceGuideToggle').onclick = function(){ toggleGuide(); };
function guideField(key){
  if(!guideOn) return;
  const isUr = document.getElementById('voiceLang').value.startsWith('ur');
  const g = GUIDE[key];
  if(g) speak(isUr ? g.ur : g.en);
}
function startVoice(fieldId, btn){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){ alert('Voice input is not supported in this browser. Please try Google Chrome.'); return; }
  const rec = new SR();
  rec.lang = document.getElementById('voiceLang').value;
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  btn.classList.add('listening');
  btn.textContent = '⏺';
  rec.onresult = function(e){
    const text = e.results[0][0].transcript;
    const field = document.getElementById(fieldId);
    if(fieldId === 'quantity'){
      const digits = text.replace(/[^0-9]/g, '');
      if(digits) field.value = digits;
    } else if(field.tagName === 'SELECT'){
      let matched = false;
      for(const opt of field.options){
        if(text.toLowerCase().includes(opt.value.toLowerCase())){ field.value = opt.value; matched = true; break; }
      }
      if(!matched) field.value = text;
    } else {
      field.value = field.value ? (field.value + ' ' + text) : text;
    }
  };
  rec.onend = function(){ btn.classList.remove('listening'); btn.textContent = '🎤'; };
  rec.onerror = function(){ btn.classList.remove('listening'); btn.textContent = '🎤'; };
  rec.start();
}
window.addEventListener('load', function(){
  if('speechSynthesis' in window){
    // voices sometimes load asynchronously - give it a moment, then greet automatically
    if(window.speechSynthesis.getVoices().length){
      setTimeout(playWelcome, 300);
    } else {
      window.speechSynthesis.onvoiceschanged = function(){ setTimeout(playWelcome, 300); };
      setTimeout(playWelcome, 800); // fallback in case the event never fires
    }
  }
});
document.addEventListener('click', function once(){ document.removeEventListener('click', once); }, {once:true});
</script>
</body></html>'''
SUCCESS_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(160deg,#0d2b40,#123f5d 40%,#1d6fa5);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;margin:0}.box{background:white;max-width:460px;padding:40px 35px;border-radius:16px;box-shadow:0 10px 35px rgba(0,0,0,.25);text-align:center}.box h2{color:#198754;margin-top:0}.jobid{background:#f0f7f3;border:1.5px dashed #198754;border-radius:8px;padding:12px;font-size:20px;font-weight:bold;letter-spacing:1px;color:#123f5d;margin:14px 0}a.btnlink{display:block;padding:12px;border-radius:8px;color:white;text-decoration:none;font-weight:bold;margin-top:14px;background:linear-gradient(135deg,#123f5d,#1d6fa5)}a.plain{display:block;margin-top:14px;color:#1d6fa5;text-decoration:none;font-size:14px}</style></head><body><div class="box"><h2>✅ Request Submitted</h2><p>Please save this Job ID — you'll need it for quotation, payment and delivery reference.</p><div class="jobid">{{ job_id }}</div><a class="btnlink" href="/track/{{ job_id }}">📦 Track this order</a><a class="plain" href="/">← Submit another request</a></div></body></html>'''

TRACK_FORM_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(160deg,#0d2b40,#123f5d 40%,#1d6fa5);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;margin:0}.box{max-width:420px;background:white;padding:34px 30px;border-radius:16px;box-shadow:0 10px 35px rgba(0,0,0,.25);text-align:center}input{width:100%;box-sizing:border-box;padding:13px;margin:14px 0;border:1.5px solid #e1e5ec;border-radius:8px;text-align:center;font-size:15px}input:focus{outline:none;border-color:#1d6fa5}button{width:100%;padding:13px;background:linear-gradient(135deg,#123f5d,#1d6fa5);color:white;border:0;border-radius:8px;font-weight:bold;font-size:15px;cursor:pointer}a{color:#1d6fa5;text-decoration:none;font-size:14px}</style></head><body><div class="box"><h2>📦 Track My Order</h2><p style="color:#666;font-size:14px">Enter the Job ID you received after submitting your request.</p><form method="GET" action="/track"><input name="job_id" placeholder="e.g. DRD-260923-0001" required><button>Track Order →</button></form>{% if not_found %}<p style="color:#dc3545;font-size:14px">No order found with that Job ID.</p>{% endif %}<br><a href="/">← Back to request form</a></div></body></html>'''

TRACK_STATUS_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(160deg,#0d2b40,#123f5d 40%,#1d6fa5);min-height:100vh;padding:24px 16px;margin:0}
.box{max-width:520px;margin:20px auto;background:white;padding:30px;border-radius:16px;box-shadow:0 10px 35px rgba(0,0,0,.25)}
.badge{display:inline-block;padding:5px 16px;border-radius:20px;font-size:13px;font-weight:bold;color:white}
.step{display:flex;align-items:center;gap:10px;margin:8px 0;padding:12px 14px;border-radius:10px;background:#f8f9fa;font-size:14px}
.done{color:#198754;font-weight:bold}.pending{color:#999}
.btnlink{display:block;text-align:center;padding:13px;border-radius:8px;color:white;text-decoration:none;margin-top:10px;font-weight:bold}
a.plain{display:block;text-align:center;margin-top:16px;color:#1d6fa5;text-decoration:none;font-size:14px}
</style></head><body><div class="box"><h2 style="margin-top:0">Job ID: {{req.job_id}}</h2><p><span class="badge" style="background:{{ '#dc3545' if req.status=='New' else ('#e6a100' if req.status=='Quotation Sent' else '#198754') }}">{{req.status}}</span></p>
<div class="step"><span class="{{ 'done' if req.status in ['New','Quotation Sent','Completed'] else 'pending' }}">1️⃣ Request Received ✓</span></div>
<div class="step"><span class="{{ 'done' if req.status in ['Quotation Sent','Completed'] else 'pending' }}">2️⃣ Quotation {{ 'Sent ✓' if req.status in ['Quotation Sent','Completed'] else '(pending)' }}</span></div>
<div class="step"><span class="{{ 'done' if req.payment_status=='Verified' else 'pending' }}">3️⃣ Payment {{ 'Verified ✓' if req.payment_status=='Verified' else '(' + req.payment_status + ')' }}</span></div>
<div class="step"><span class="{{ 'done' if req.status=='Completed' else 'pending' }}">4️⃣ Order {{ 'Completed ✓' if req.status=='Completed' else '(in progress)' }}</span></div>
{% if req.status != 'New' %}<p style="font-size:15px"><b>Total:</b> {{ money(req.net_payable) }} &nbsp; <b>Delivery:</b> {{ req.delivery_time }}</p>
<a class="btnlink" style="background:#6f42c1" href="/quotation/{{req.job_id}}.pdf" target="_blank">📄 View Quotation PDF</a>
<a class="btnlink" style="background:linear-gradient(135deg,#123f5d,#1d6fa5)" href="/payment/{{req.job_id}}">💳 Make / Confirm Payment</a>{% endif %}
{% if req.status == 'Completed' %}<a class="btnlink" style="background:#198754" href="/invoice/{{req.job_id}}.pdf" target="_blank">🧾 View Invoice PDF</a>{% endif %}
<a class="plain" href="/track">← Track another order</a></div></body></html>'''

MY_ORDERS_FORM_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(160deg,#0d2b40,#123f5d 40%,#1d6fa5);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;margin:0}.box{max-width:420px;background:white;padding:34px 30px;border-radius:16px;box-shadow:0 10px 35px rgba(0,0,0,.25);text-align:center}input{width:100%;box-sizing:border-box;padding:13px;margin:14px 0;border:1.5px solid #e1e5ec;border-radius:8px;text-align:center;font-size:15px}input:focus{outline:none;border-color:#1d6fa5}button{width:100%;padding:13px;background:linear-gradient(135deg,#123f5d,#1d6fa5);color:white;border:0;border-radius:8px;font-weight:bold;font-size:15px;cursor:pointer}a{color:#1d6fa5;text-decoration:none;font-size:14px}</style></head><body><div class="box"><h2>📋 View All My Orders</h2><p style="color:#666;font-size:14px">Enter the WhatsApp number you used when placing your order(s).</p><form method="GET" action="/my-orders"><input name="phone" placeholder="e.g. 923001234567" required><button>Show My Orders →</button></form>{% if not_found %}<p style="color:#dc3545;font-size:14px">No orders found for that number.</p>{% endif %}<br><a href="/">← Back to request form</a></div></body></html>'''

MY_ORDERS_LIST_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(160deg,#0d2b40,#123f5d 40%,#1d6fa5);min-height:100vh;padding:24px 16px;margin:0}
.box{max-width:640px;margin:20px auto;background:white;padding:28px;border-radius:16px;box-shadow:0 10px 35px rgba(0,0,0,.25)}
.summary-bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.summary-bar div{flex:1;min-width:120px;background:#f0f7ff;border-radius:10px;padding:12px;text-align:center}
.summary-bar b{font-size:20px;color:#123f5d;display:block}
.order-item{border:1px solid #eee;border-radius:10px;padding:12px 14px;margin-bottom:10px}
.badge{display:inline-block;padding:3px 12px;border-radius:20px;font-size:12px;font-weight:bold;color:white}
.btnlink{display:inline-block;padding:8px 12px;border-radius:6px;color:white;text-decoration:none;margin:3px 4px 0 0;font-size:13px;font-weight:bold}
a.plain{display:block;text-align:center;margin-top:16px;color:#1d6fa5;text-decoration:none;font-size:14px}
</style></head><body><div class="box"><h2 style="margin-top:0">👋 Hello {{client_name}}</h2>
<div class="summary-bar"><div><b>{{total_orders}}</b>Total Orders</div><div><b>{{money(total_paid)}}</b>Total Paid</div></div>
{% for o in orders %}<div class="order-item"><b>{{o.job_id}}</b> <span class="badge" style="background:{{ '#dc3545' if o.status=='New' else ('#e6a100' if o.status=='Quotation Sent' else '#198754') }}">{{o.status}}</span><br>
<span style="font-size:13px;color:#666">{{ o.parts[0].part_name if o.parts else '' }}{% if o.parts|length > 1 %} +{{ o.parts|length - 1 }} more{% endif %} | {{o.time}}</span><br>
{% if o.status != 'New' %}<span style="font-size:13px">Total: <b>{{money(o.net_payable)}}</b> | Payment: {{o.payment_status}}</span><br>
<a class="btnlink" style="background:#6f42c1" href="/quotation/{{o.job_id}}.pdf" target="_blank">Quotation</a>
<a class="btnlink" style="background:linear-gradient(135deg,#123f5d,#1d6fa5)" href="/payment/{{o.job_id}}">Payment</a>{% endif %}
{% if o.status == 'Completed' %}<a class="btnlink" style="background:#198754" href="/invoice/{{o.job_id}}.pdf" target="_blank">Invoice</a>{% endif %}
<a class="btnlink" style="background:#6c757d" href="/track/{{o.job_id}}">Track</a>
</div>{% endfor %}
<a class="plain" href="/">← Submit a new order</a></div></body></html>'''
PAYMENT_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;background:#f4f4f9;padding:18px}.box{max-width:600px;margin:auto;background:white;padding:25px;border-radius:10px}input,select{width:100%;box-sizing:border-box;padding:10px;margin:6px 0 12px}button{width:100%;padding:12px;background:#198754;color:white;border:0;border-radius:6px;font-weight:bold}.info{background:#eef6ff;padding:12px;border-radius:7px}</style></head><body><div class="box"><h2>Order Confirmation & Payment</h2><div class="info"><b>Job ID:</b> {{ req.job_id }}<br><b>Total:</b> {{ money(req.net_payable) }}<br><b>Payment Terms:</b> {{ payment_terms_text(req, settings) }}<br><b>Advance Required:</b> {{ money(req.advance_required) }}<br><b>Balance Due:</b> {{ money(req.balance_due) }}</div><form method="POST" action="/payment-submit/{{ req.job_id }}" enctype="multipart/form-data"><label>Payment Stage</label><select name="payment_stage"><option value="advance">Advance Payment</option><option value="balance">Balance / Final Payment</option><option value="full">Full Payment</option></select><label>Payment Amount</label><input type="number" step="any" name="payment_amount" required><label>Transaction / Reference ID</label><input name="transaction_id" required><label>Payment Date</label><input type="date" name="payment_date" required><label>Payment Proof</label><input type="file" name="payment_file" accept="image/*,.pdf"><label>Confirm Order</label><select name="customer_confirmed"><option value="yes">Yes</option></select><button>Submit Payment Confirmation</button></form></div></body></html>'''
PAYMENT_SUCCESS = '''<!doctype html><html><body style="font-family:Arial;text-align:center;background:#f4f4f9;padding:50px"><div style="background:white;max-width:500px;margin:auto;padding:35px;border-radius:10px"><h2 style="color:#198754">Payment Submitted</h2><p>Job ID: <b>{{ job_id }}</b></p><p>Your payment proof and reference have been received for manual verification.</p></div></body></html>'''

SETTINGS_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;background:#f0f2f7;padding:18px}.box{max-width:900px;margin:auto;background:white;padding:25px;border-radius:12px;box-shadow:0 2px 12px #d6d9e2}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:700px){.grid{grid-template-columns:1fr}}label{font-weight:bold;display:block;margin-top:8px}input,select,textarea{width:100%;box-sizing:border-box;padding:8px;margin:4px 0 10px;border:1px solid #dce1ea;border-radius:6px}button{padding:11px 16px;background:#007bff;color:white;border:0;border-radius:6px;font-weight:bold;cursor:pointer}.rowbox{background:#f8f9fa;border:1px solid #ddd;padding:10px;border-radius:8px;margin-bottom:8px}.termrow button{margin-top:4px;padding:6px 10px}</style><script>
function addTermRow(){
  const box=document.getElementById('termsBox');
  const div=document.createElement('div');
  div.className='rowbox termrow';
  div.innerHTML='<input name="term_title[]" placeholder="Clause title e.g. Delivery Delay"><textarea name="term_text[]" rows="2" placeholder="Clause text shown on the PDF"></textarea><button type="button" onclick="this.closest(\\'.termrow\\').remove()" style="background:#dc3545">Remove</button>';
  box.appendChild(div);
}
function pickTestVoice(lang, hint){
  const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  const base = lang.split('-')[0];
  const candidates = voices.filter(v => v.lang && v.lang.toLowerCase().startsWith(base));
  if(hint){
    const hinted = candidates.find(v => v.name.toLowerCase().includes(hint.toLowerCase()));
    if(hinted) return hinted;
  }
  const premium = candidates.find(v => /google|natural|neural|online|premium/i.test(v.name));
  if(premium) return premium;
  const nicePick = candidates.find(v => /female|zira|samantha|susan|heera|gul/i.test(v.name));
  return nicePick || candidates[0] || voices[0] || null;
}
function testVoice(){
  if(!('speechSynthesis' in window)){ alert('This browser does not support voice playback.'); return; }
  const lang = document.getElementById('wvl').value;
  const hint = document.getElementById('wvh').value;
  const text = lang.startsWith('ur') ? document.getElementById('wmu').value : document.getElementById('wme').value;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang;
  u.pitch = parseFloat(document.getElementById('wvp').value);
  u.rate = parseFloat(document.getElementById('wvr').value);
  u.volume = parseFloat(document.getElementById('wvv').value);
  const v = pickTestVoice(lang, hint);
  if(v) u.voice = v;
  window.speechSynthesis.speak(u);
}
</script></head><body><div class="box"><a href="/drd-secure-admin">← Admin</a><h2>Settings</h2><p style="color:#777;font-size:13px">All fields below are optional — leave anything blank and it will simply not appear on quotations/invoices.</p><form method="POST" enctype="multipart/form-data"><div class="grid">{% for key,label in [('company_name','Company Name'),('address','Address'),('phone','Phone'),('email','Email'),('website','Website'),('ntn','NTN'),('strn','STRN / GST'),('bank_name','Bank Name'),('account_title','Account Title'),('account_number','Account Number'),('iban','IBAN')] %}<div><label>{{label}}</label><input name="{{key}}" value="{{s[key]}}"></div>{% endfor %}</div><label>Payment Instructions</label><textarea name="payment_instructions" rows="3">{{s.payment_instructions}}</textarea><h3>Terms & Conditions Library</h3><p style="color:#777;font-size:13px;margin-top:-4px">Add each clause once here. When making a quotation you'll just tick the ones that apply — no retyping every time. Tip: add new clauses at the bottom rather than deleting old ones once a quotation has already been sent to a customer.</p><div id="termsBox">{% for c in s.terms_library %}<div class="rowbox termrow"><input name="term_title[]" value="{{c.title}}" placeholder="Clause title e.g. Delivery Delay"><textarea name="term_text[]" rows="2" placeholder="Clause text shown on the PDF">{{c.text}}</textarea><button type="button" onclick="this.closest('.termrow').remove()" style="background:#dc3545">Remove</button></div>{% endfor %}</div><button type="button" onclick="addTermRow()">+ Add Clause</button><h3>Default / Fallback Wording</h3><p style="color:#777;font-size:13px;margin-top:-4px">Used only when no clause above is ticked on a particular quotation.</p><textarea name="default_terms_conditions" rows="4" placeholder="Leave blank to use the built-in default wording">{{s.default_terms_conditions}}</textarea><h3>Tax</h3><label>GST %</label><input name="gst_percent" type="number" step="any" value="{{s.gst_percent}}"><label><input style="width:auto" type="checkbox" name="gst_enabled" {% if s.gst_enabled %}checked{% endif %}> Enable GST</label><label>WHT %</label><input name="wht_percent" type="number" step="any" value="{{s.wht_percent}}"><label><input style="width:auto" type="checkbox" name="wht_enabled" {% if s.wht_enabled %}checked{% endif %}> Enable WHT</label><label>WHT Mode</label><select name="wht_mode"><option value="deduct" {% if s.wht_mode=='deduct' %}selected{% endif %}>Deduct</option><option value="add" {% if s.wht_mode=='add' %}selected{% endif %}>Add</option></select><h3>Default Payment Terms</h3><select name="payment_terms"><option value="50_50" {% if s.payment_terms=='50_50' %}selected{% endif %}>50% Advance + 50% before/at Delivery</option><option value="100_advance" {% if s.payment_terms=='100_advance' %}selected{% endif %}>100% Advance</option><option value="custom" {% if s.payment_terms=='custom' %}selected{% endif %}>Custom</option></select><label>Custom Payment Terms</label><textarea name="custom_payment_terms">{{s.custom_payment_terms}}</textarea><h3>Notification WhatsApp Numbers</h3>{% for i in range(3) %}<label>Notification Number {{i+1}}</label><input name="notification_{{i}}" value="{{s.notification_numbers[i]}}" placeholder="923175240272">{% endfor %}<p>Normal wa.me links cannot automatically push notifications; these numbers are stored for notification links/manual use. Automatic WhatsApp notifications require an API/provider.</p><h3>🔊 Client Welcome Voice</h3><p style="color:#777;font-size:13px;margin-top:-4px">Controls the voice that greets clients on the order form. Voices come from the visitor's own browser, so use "Test Voice" below (in this browser) to check how it sounds before saving.</p><div class="grid"><div><label>Language</label><select name="welcome_voice_lang" id="wvl"><option value="en-US" {% if s.welcome_voice_lang=='en-US' %}selected{% endif %}>English (US)</option><option value="en-GB" {% if s.welcome_voice_lang=='en-GB' %}selected{% endif %}>English (UK)</option><option value="en-IN" {% if s.welcome_voice_lang=='en-IN' %}selected{% endif %}>English (India)</option><option value="ur-PK" {% if s.welcome_voice_lang=='ur-PK' %}selected{% endif %}>Urdu</option></select></div><div><label>Preferred Voice Name (optional)</label><input name="welcome_voice_hint" id="wvh" value="{{s.welcome_voice_hint}}" placeholder="e.g. Zira, Google, Samantha"></div></div><div class="grid"><div><label>Pitch ({{s.welcome_pitch}})</label><input type="range" name="welcome_pitch" id="wvp" min="0.5" max="2" step="0.05" value="{{s.welcome_pitch}}" oninput="document.getElementById('wvpVal').textContent=this.value"> <span id="wvpVal" style="font-size:12px;color:#777">{{s.welcome_pitch}}</span></div><div><label>Speed ({{s.welcome_rate}})</label><input type="range" name="welcome_rate" id="wvr" min="0.5" max="1.5" step="0.05" value="{{s.welcome_rate}}" oninput="document.getElementById('wvrVal').textContent=this.value"> <span id="wvrVal" style="font-size:12px;color:#777">{{s.welcome_rate}}</span></div><div><label>Volume ({{s.welcome_volume}})</label><input type="range" name="welcome_volume" id="wvv" min="0" max="1" step="0.05" value="{{s.welcome_volume}}" oninput="document.getElementById('wvvVal').textContent=this.value"> <span id="wvvVal" style="font-size:12px;color:#777">{{s.welcome_volume}}</span></div></div><label>Welcome Message (English)</label><textarea name="welcome_message_en" id="wme" rows="3">{{s.welcome_message_en}}</textarea><label>Welcome Message (Urdu)</label><textarea name="welcome_message_ur" id="wmu" rows="3">{{s.welcome_message_ur}}</textarea><button type="button" onclick="testVoice()" style="background:#25d366;margin-bottom:14px">🔊 Test Voice</button><h3>Admin Dashboard Notifications</h3><label><input style="width:auto" type="checkbox" name="admin_auto_refresh" {% if s.admin_auto_refresh %}checked{% endif %}> Auto-refresh admin dashboard when a new order arrives</label><label><input style="width:auto" type="checkbox" name="admin_notify_sound" {% if s.admin_notify_sound %}checked{% endif %}> Play a sound + browser notification on new orders</label><h3>🖼️ Portfolio Gallery (shown as a scrolling slider on the client order page)</h3><p style="color:#777;font-size:13px;margin-top:-4px">Upload photos of parts / jobs you've completed. They'll auto-scroll at the bottom of the client's order form.</p>{% if s.portfolio %}<div class="grid">{% for item in s.portfolio %}<div class="rowbox" style="text-align:center"><img src="/uploads/{{item.file}}" style="width:100%;height:90px;object-fit:cover;border-radius:6px"><input name="portfolio_caption[]" value="{{item.caption}}" placeholder="Caption"><input type="hidden" name="portfolio_file[]" value="{{item.file}}"><label style="font-weight:normal;font-size:12px"><input style="width:auto" type="checkbox" name="remove_portfolio[]" value="{{item.file}}"> Remove this photo</label></div>{% endfor %}</div>{% endif %}<label>Add New Photos</label><input type="file" name="portfolio_images" accept="image/*" multiple><label>Caption for new photo(s) (optional, applies to all newly added)</label><input name="portfolio_new_caption" placeholder="e.g. CNC milled bracket"><button>Save Settings</button></form></div></body></html>'''

ADMIN_PAGE = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>DRD Admin</title><style>
body{font-family:Arial;background:#f0f2f7;margin:0;color:#222}
.hero{background:linear-gradient(135deg,#123f5d,#1d6fa5);color:white;padding:22px 20px;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center}
.hero h2{margin:0}.hero p{margin:3px 0 0;opacity:.85;font-size:13px}
.wrap{padding:15px}
.searchbox{position:sticky;top:0;z-index:20;background:white;border-radius:10px;box-shadow:0 2px 10px #d6d9e2;padding:12px 14px;margin:-28px 0 16px;display:flex;gap:8px;align-items:center}
.searchbox input{border:1px solid #dce1ea;border-radius:8px;padding:11px 14px;font-size:14px;flex:1}
.searchbox span{font-size:18px}
#searchResults{display:none;background:white;border-radius:10px;box-shadow:0 2px 10px #d6d9e2;padding:10px;margin-bottom:18px}
.sr-item{border-bottom:1px solid #eee;padding:10px 4px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
.sr-item:last-child{border-bottom:none}
.sr-left b{font-size:14px}
.sr-meta{font-size:12px;color:#666}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:bold;color:white}
.badge-new{background:#dc3545}.badge-progress{background:#e6a100}.badge-done{background:#198754}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}}
.card{background:white;padding:14px;border-radius:10px;box-shadow:0 1px 6px #dfe2ea;transition:.15s}
.card:hover{box-shadow:0 4px 14px #ccd2de}
.flash-highlight{animation:flashPulse 1.3s ease-in-out 2;}
@keyframes flashPulse{0%{box-shadow:0 0 0 4px rgba(255,193,7,.95)}50%{box-shadow:0 0 0 8px rgba(255,193,7,.25)}100%{box-shadow:0 0 0 4px rgba(255,193,7,0)}}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}
.tabs button{padding:10px 14px;border:0;border-radius:20px;background:#6c757d;color:white;font-weight:bold}
.tabs button.active{background:#007bff}
.tab{display:none}.tab.active{display:block}
table{width:100%;border-collapse:collapse;background:white;margin-bottom:20px}
th,td{border:1px solid #ddd;padding:8px;vertical-align:top;font-size:12px}
th{background:#343a40;color:white}
input,select,textarea{width:100%;box-sizing:border-box;padding:6px;margin:3px 0 6px;border:1px solid #dce1ea;border-radius:5px}
button{background:#198754;color:white;border:0;padding:7px 10px;border-radius:5px;font-weight:bold;cursor:pointer}
.danger{background:#dc3545}.info{background:#17a2b8}.purple{background:#6f42c1}
.rowbox{background:#f8f9fa;border:1px solid #ddd;padding:7px;border-radius:5px;margin-bottom:6px}
.partgrid{display:grid;grid-template-columns:65px 1fr 1.5fr 70px 1fr 100px 30px;gap:4px;align-items:start}
.partgrid-header{display:grid;grid-template-columns:65px 1fr 1.5fr 70px 1fr 100px 30px;gap:4px;align-items:start}
.small{font-size:11px;color:#666}.summary{font-weight:bold;font-size:19px}.scroll{overflow:auto}
.new{border-left:5px solid #dc3545}.progress{border-left:5px solid #ffc107}.done{border-left:5px solid #198754}
.btnlink{display:inline-block;padding:7px 9px;border-radius:6px;color:white;text-decoration:none;margin:2px;font-size:13px}
.termpicker{background:#f8f9fa;border:1px solid #dce1ea;border-radius:8px;padding:10px;margin:6px 0;grid-column:1/-1}
.termpicker label{font-weight:normal;display:flex;gap:6px;align-items:flex-start;margin:5px 0;font-size:13px}
.termpicker input[type=checkbox]{width:auto;margin:2px 0 0}
.client-card details{margin-top:8px}
.client-card summary{cursor:pointer;font-weight:bold;color:#1d6fa5;padding:4px 0}
.client-order{border-top:1px solid #eee;padding:8px 4px;font-size:12px}
</style><script>
const ALL_ORDERS = {{ orders_json|safe }};
const AUTO_REFRESH = {{ settings.admin_auto_refresh|tojson }};
const NOTIFY_SOUND = {{ settings.admin_notify_sound|tojson }};
let LAST_KNOWN_TIME = {{ (requests[0].time if requests else '')|tojson }};
function beep(){
  try{
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = 'sine'; osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    osc.start();
    osc.frequency.setValueAtTime(1046, ctx.currentTime + 0.15);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
    osc.stop(ctx.currentTime + 0.5);
  }catch(e){}
}
if('Notification' in window && Notification.permission === 'default'){
  Notification.requestPermission();
}
function checkForNewOrders(){
  fetch('/api/order-status').then(r=>r.json()).then(d=>{
    if(d.latest_time && d.latest_time > LAST_KNOWN_TIME){
      LAST_KNOWN_TIME = d.latest_time;
      if(NOTIFY_SOUND){
        beep();
        if('Notification' in window && Notification.permission === 'granted'){
          new Notification('📥 New Order Received', {body: 'A new client just submitted a request — refreshing dashboard...'});
        }
      }
      if(AUTO_REFRESH){ setTimeout(()=>location.reload(), 1800); }
    }
  }).catch(()=>{});
}
if(AUTO_REFRESH || NOTIFY_SOUND){ setInterval(checkForNewOrders, 12000); }
function tab(id,b){document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.getElementById(id).classList.add('active');document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active')}
function addPart(id){
  let box=document.getElementById(id);
  let dataRows=box.querySelectorAll('.partgrid');
  let templateRow=dataRows[dataRows.length-1];
  if(!templateRow){ return; }
  let row=templateRow.cloneNode(true);
  row.querySelectorAll('input,textarea').forEach(x=>{
    if(x.name==='part_no[]') x.value='P-'+String(dataRows.length+1).padStart(3,'0');
    else x.value='';
  });
  box.appendChild(row);
}
function delPart(btn){let box=btn.closest('.partsbox');if(box.querySelectorAll('.partgrid').length>1)btn.closest('.partgrid').remove()}
function updateTotal(box){let total=0;box.querySelectorAll('.part-price').forEach(x=>total+=parseFloat(x.value||0));box.parentElement.querySelector('.part-total').textContent='Rs. '+total.toLocaleString(undefined,{minimumFractionDigits:2})}
function badgeFor(s){if(s==='New')return '<span class="badge badge-new">New</span>';if(s==='Quotation Sent')return '<span class="badge badge-progress">In Progress</span>';return '<span class="badge badge-done">Completed</span>'}
function goToOrder(jobId, tabId){
  document.getElementById('searchInput').value='';
  document.getElementById('searchResults').style.display='none';
  document.getElementById('tabsBlock').style.display='block';
  const tabButtons=document.querySelectorAll('.tabs button');
  const idxMap={new:0,progress:1,done:2,clients:3};
  const idx=idxMap[tabId];
  if(tabButtons[idx]) tab(tabId, tabButtons[idx]);
  setTimeout(function(){
    const el=document.getElementById('order-'+jobId);
    if(el){
      el.scrollIntoView({behavior:'smooth', block:'center'});
      el.classList.add('flash-highlight');
      setTimeout(function(){ el.classList.remove('flash-highlight'); }, 2600);
    }
  }, 150);
}
function runSearch(q){
  const panel=document.getElementById('searchResults');
  const tabsBlock=document.getElementById('tabsBlock');
  q=q.trim().toLowerCase();
  if(!q){panel.style.display='none';tabsBlock.style.display='block';return}
  tabsBlock.style.display='none';panel.style.display='block';
  const matches=ALL_ORDERS.filter(o=>o.search.includes(q));
  if(!matches.length){panel.innerHTML='<p style="padding:10px">No order matches "'+q+'". Try a Job ID, name, phone or part name.</p>';return}
  panel.innerHTML=matches.map(o=>`
    <div class="sr-item">
      <div class="sr-left" style="cursor:pointer" onclick="goToOrder('${o.job_id}','${o.tab}')" title="Click to open this order and edit it"><b>${o.job_id}</b> ${badgeFor(o.status)}<br><span class="sr-meta">${o.company} / ${o.name} | ${o.parts} | ${o.total} | Delivery: ${o.delivery}</span></div>
      <div>
        <button type="button" class="btnlink" style="background:#007bff;border:0;cursor:pointer" onclick="goToOrder('${o.job_id}','${o.tab}')">✏️ Open / Edit</button>
        ${o.q_url?`<a class="btnlink purple" href="${o.q_url}" target="_blank">Quotation</a>`:''}
        ${o.i_url?`<a class="btnlink" style="background:#198754" href="${o.i_url}" target="_blank">Invoice</a>`:''}
        ${o.pay_url?`<a class="btnlink" style="background:#6c757d" href="${o.pay_url}" target="_blank">Payment Page</a>`:''}
        ${o.wa_url?`<a class="btnlink" style="background:#25d366" href="${o.wa_url}" target="_blank">WhatsApp</a>`:''}
      </div>
    </div>`).join('');
}
</script></head><body>
<div class="hero"><div><h2>DRD Manufacturing Solutions - Admin</h2><p>Orders, quotations, payments, invoices and history</p></div><div><a class="btnlink" style="background:rgba(255,255,255,.2)" href="/settings">⚙ Settings</a></div></div>
<div class="wrap">
<div class="searchbox"><span>🔎</span><input id="searchInput" placeholder="Search any order — Job ID, client name, phone, part name, status..." oninput="runSearch(this.value)" autocomplete="off"></div>
<div id="searchResults"></div>
<div class="cards"><div class="card"><b>Total Orders</b><div class="summary">{{summary.total_orders}}</div></div><div class="card"><b>Completed</b><div class="summary">{{summary.completed}}</div></div><div class="card"><b>In Progress</b><div class="summary">{{summary.in_progress}}</div></div><div class="card"><b>New</b><div class="summary">{{summary.new}}</div></div><div class="card"><b>Total Quoted</b><div class="summary">{{money(summary.total_quoted)}}</div></div><div class="card"><b>Total Paid</b><div class="summary">{{money(summary.total_paid)}}</div></div><div class="card"><b>Pending Payment</b><div class="summary">{{money(summary.pending_payment)}}</div></div><div class="card"><b>GST</b><div class="summary">{{money(summary.gst)}}</div></div></div>
<div id="tabsBlock"><div class="tabs"><button class="active" onclick="tab('new',this)">📥 New</button><button onclick="tab('progress',this)">⏳ Quotations / Payment</button><button onclick="tab('done',this)">✅ Completed</button><button onclick="tab('clients',this)">👥 Client Files</button></div>
<div id="new" class="tab active"><h3>New Requests</h3>{% for req in requests %}{% if req.status=='New' %}<div class="card new" id="order-{{req.job_id}}"><b>{{req.job_id}}</b> — {{req.company}} / {{req.name}}<br><span class="small">{{req.time}} | {{req.whatsapp}}</span><p><b>Original Request:</b> {{req.part_name}} | Qty {{req.quantity}} | {{req.material}}<br>{{req.requirement}}</p>{% if req.file %}<a href="/uploads/{{req.file}}" target="_blank">View Drawing/File</a>{% endif %}<form action="/update/{{req.job_id}}" method="POST"><div class="grid"><label>Quotation Type<select name="quotation_type"><option value="standard">Standard Quotation</option><option value="detailed">Detailed / Technical & Commercial Proposal</option></select></label><label>RFQ / Reference No.<input name="rfq_no"></label><label>Project / Work Title<input name="project_title"></label></div><h4>Parts</h4><div class="partsbox" id="parts-{{req.job_id}}"><div class="partgrid-header small"><b>Part No.</b><b>Part Name</b><b>Description</b><b>Qty</b><b>Material</b><b>Price</b><b></b></div>{% for p in req.parts %}<div class="partgrid"><input name="part_no[]" value="{{p.part_no}}"><input name="part_name[]" value="{{p.part_name}}" required><textarea name="part_description[]">{{p.description}}</textarea><input name="part_quantity[]" value="{{p.quantity}}"><input name="part_material[]" value="{{p.material}}"><input class="part-price" oninput="updateTotal(this.closest('.partsbox'))" name="part_price[]" type="number" step="any" value="{{p.price}}"><button type="button" class="danger" onclick="delPart(this)">×</button></div>{% endfor %}</div><button type="button" onclick="addPart('parts-{{req.job_id}}')">+ Add Part</button> <span>Parts Total: <b class="part-total">{{money(req.base_price)}}</b></span><div class="grid"><label>GST %<input name="gst_percent" type="number" step="any" value="{{req.gst_percent}}"></label><label>GST<select name="gst_enabled"><option value="yes" {% if req.gst_enabled %}selected{% endif %}>Enabled</option><option value="no" {% if not req.gst_enabled %}selected{% endif %}>Disabled</option></select></label><label>WHT<select name="wht_enabled"><option value="use">Use Setting</option><option value="yes" {% if req.wht_enabled %}selected{% endif %}>Enable</option><option value="no" {% if not req.wht_enabled %}selected{% endif %}>Disable</option></select></label><label>WHT %<input name="wht_percent" type="number" step="any" value="{{req.wht_percent}}"></label><label>Delivery / Schedule<input name="delivery_time" required placeholder="5 working days"></label><label>Payment Terms<select name="payment_terms"><option value="50_50" {% if req.payment_terms=='50_50' %}selected{% endif %}>50% Advance + 50% before/at Delivery</option><option value="100_advance" {% if req.payment_terms=='100_advance' %}selected{% endif %}>100% Advance</option><option value="custom" {% if req.payment_terms=='custom' %}selected{% endif %}>Custom</option></select></label><label>Custom Payment Terms<textarea name="custom_payment_terms"></textarea></label><label>Material Specification<textarea name="material_specification"></textarea></label><label>Manufacturing Operations<textarea name="manufacturing_operations"></textarea></label><label>Finish / Surface Treatment<textarea name="finish_specification"></textarea></label><label>Inspection / QC<textarea name="inspection_qc"></textarea></label><label>Technical Specification<textarea name="technical_specification"></textarea></label><label>Technical Notes<textarea name="technical_notes"></textarea></label></div>{% if settings.terms_library %}<div class="termpicker"><b>Terms & Conditions — tick the ones that apply to this quotation</b>{% for c in settings.terms_library %}<label><input type="checkbox" name="selected_terms[]" value="{{c.id}}"> <span><b>{{c.title}}</b> — {{c.text}}</span></label>{% endfor %}</div>{% endif %}<div class="grid"><label>Remarks<textarea name="remarks"></textarea></label></div><button>Save & Send Quotation</button></form><form action="/delete/{{req.job_id}}" method="POST" style="margin-top:5px"><button class="danger">Delete</button></form></div>{% else %}{% endif %}{% endfor %}</div>
<div id="progress" class="tab"><h3>Quotations Sent / Payment</h3>{% for req in requests %}{% if req.status=='Quotation Sent' %}<div class="card progress" id="order-{{req.job_id}}"><b>{{req.job_id}}</b> — {{req.company}} / {{req.name}}<p>Quotation: {{req.quotation_type}} | Parts: {{req.parts|length}} | Total: <b>{{money(req.net_payable)}}</b> | Delivery: {{req.delivery_time}}</p><a class="btnlink purple" href="/quotation/{{req.job_id}}.pdf" target="_blank">Quotation PDF</a><a class="btnlink" style="background:#25d366" href="{{req.wa_link}}" target="_blank">WhatsApp</a><a class="btnlink" style="background:#6c757d" href="/payment/{{req.job_id}}" target="_blank">Customer Payment Page</a><form action="/update/{{req.job_id}}" method="POST"><input type="hidden" name="quotation_type" value="{{req.quotation_type}}"><h4>Edit Parts / Price</h4><div class="partsbox" id="edit-{{req.job_id}}"><div class="partgrid-header small"><b>Part No.</b><b>Part Name</b><b>Description</b><b>Qty</b><b>Material</b><b>Price</b><b></b></div>{% for p in req.parts %}<div class="partgrid"><input name="part_no[]" value="{{p.part_no}}"><input name="part_name[]" value="{{p.part_name}}" required><textarea name="part_description[]">{{p.description}}</textarea><input name="part_quantity[]" value="{{p.quantity}}"><input name="part_material[]" value="{{p.material}}"><input name="part_price[]" type="number" step="any" value="{{p.price}}"><button type="button" class="danger" onclick="delPart(this)">×</button></div>{% endfor %}</div><button type="button" onclick="addPart('edit-{{req.job_id}}')">+ Add Part</button><div class="grid"><label>GST %<input name="gst_percent" type="number" step="any" value="{{req.gst_percent}}"></label><label>GST<select name="gst_enabled"><option value="yes" {% if req.gst_enabled %}selected{% endif %}>Enabled</option><option value="no" {% if not req.gst_enabled %}selected{% endif %}>Disabled</option></select></label><label>WHT<select name="wht_enabled"><option value="yes" {% if req.wht_enabled %}selected{% endif %}>Enabled</option><option value="no" {% if not req.wht_enabled %}selected{% endif %}>Disabled</option></select></label><label>WHT %<input name="wht_percent" type="number" step="any" value="{{req.wht_percent}}"></label><label>Delivery<input name="delivery_time" value="{{req.delivery_time}}" required></label><label>Payment Terms<select name="payment_terms"><option value="50_50" {% if req.payment_terms=='50_50' %}selected{% endif %}>50/50</option><option value="100_advance" {% if req.payment_terms=='100_advance' %}selected{% endif %}>100% Advance</option><option value="custom" {% if req.payment_terms=='custom' %}selected{% endif %}>Custom</option></select></label><label>Custom Terms<textarea name="custom_payment_terms">{{req.custom_payment_terms}}</textarea></label><label>RFQ No.<input name="rfq_no" value="{{req.rfq_no}}"></label><label>Reference No.<input name="reference_no" value="{{req.reference_no}}"></label><label>Project Title<input name="project_title" value="{{req.project_title}}"></label><label>Technical Specification<textarea name="technical_specification">{{req.technical_specification}}</textarea></label><label>Material Specification<textarea name="material_specification">{{req.material_specification}}</textarea></label><label>Manufacturing Operations<textarea name="manufacturing_operations">{{req.manufacturing_operations}}</textarea></label><label>Finish<textarea name="finish_specification">{{req.finish_specification}}</textarea></label><label>Inspection / QC<textarea name="inspection_qc">{{req.inspection_qc}}</textarea></label><label>Technical Notes<textarea name="technical_notes">{{req.technical_notes}}</textarea></label></div>{% if settings.terms_library %}<div class="termpicker"><b>Terms & Conditions — tick the ones that apply to this quotation</b>{% for c in settings.terms_library %}<label><input type="checkbox" name="selected_terms[]" value="{{c.id}}" {% if c.id in req.selected_terms %}checked{% endif %}> <span><b>{{c.title}}</b> — {{c.text}}</span></label>{% endfor %}</div>{% endif %}<div class="grid"><label>Remarks<textarea name="remarks">{{req.remarks}}</textarea></label></div><button>Update & Recalculate</button></form><hr><b>Payment:</b> {{req.payment_status}} | Advance: {{money(req.advance_paid)}} | Balance Due: {{money(req.balance_due)}}<br><span class="small">Advance stage: {{req.advance_payment_status}} | Balance stage: {{req.balance_payment_status}}</span><br>{% if req.payment_status=='Submitted' %}Ref: {{req.transaction_id}} | {{req.payment_date}} {% if req.payment_file %}<a href="/uploads/{{req.payment_file}}" target="_blank">Proof</a>{% endif %}<form action="/verify-payment/{{req.job_id}}" method="POST"><button class="info">Verify Submitted Payment</button></form>{% endif %}{% if req.payment_status=='Verified' %}<b style="color:#198754">Payment Verified</b>{% endif %}<form action="/complete/{{req.job_id}}" method="POST" style="margin-top:8px"><button class="info">Mark Completed / Invoice</button></form><form action="/delete/{{req.job_id}}" method="POST" style="margin-top:5px"><button class="danger">Delete</button></form></div>{% endif %}{% endfor %}</div>
<div id="done" class="tab"><h3>Completed Orders</h3>{% for req in requests %}{% if req.status=='Completed' %}<div class="card done" id="order-{{req.job_id}}"><b>{{req.job_id}}</b> — {{req.company}} / {{req.name}}<p>Invoice: {{req.invoice_number}} | Total: {{money(req.net_payable)}} | Payment: {{req.payment_status}}</p><a class="btnlink" style="background:#198754" href="/invoice/{{req.job_id}}.pdf" target="_blank">Invoice PDF</a><a class="btnlink" style="background:#25d366" href="{{req.wa_link}}" target="_blank">WhatsApp</a><form action="/delete/{{req.job_id}}" method="POST" style="margin-top:5px"><button class="danger">Delete</button></form></div>{% endif %}{% endfor %}</div>
<div id="clients" class="tab"><h3>Client Files</h3><p class="small">Every client's full order history in one place — no more hunting across tabs.</p>{% for c in clients %}<div class="card client-card"><b>{{c.company or 'Unknown Company'}}</b> — {{c.name}}<br><span class="small">📱 {{c.whatsapp or '—'}}</span><p><b>{{c.total_orders}}</b> order(s) &nbsp;|&nbsp; <b>{{c.completed}}</b> completed &nbsp;|&nbsp; Total value: <b>{{c.total_value_fmt}}</b></p><details><summary>View order history ({{c.orders|length}})</summary>{% for o in c.orders %}<div class="client-order"><b>{{o.job_id}}</b> — {{o.parts}} — <span class="small">{{o.status}} | {{o.total}} | {{o.time}}</span><br>{% if o.q_url %}<a class="btnlink purple" href="{{o.q_url}}" target="_blank">Quotation</a>{% endif %}{% if o.i_url %}<a class="btnlink" style="background:#198754" href="{{o.i_url}}" target="_blank">Invoice</a>{% endif %}{% if o.wa_url and o.wa_url != '#' %}<a class="btnlink" style="background:#25d366" href="{{o.wa_url}}" target="_blank">WhatsApp</a>{% endif %}</div>{% endfor %}</details></div>{% else %}<p class="small">No clients yet.</p>{% endfor %}</div>
</div></div></body></html>'''


def summary_data(reqs):
    return {
        'total_orders': len(reqs),
        'new': sum(r.get('status') == 'New' for r in reqs),
        'in_progress': sum(r.get('status') == 'Quotation Sent' for r in reqs),
        'completed': sum(r.get('status') == 'Completed' for r in reqs),
        'total_quoted': sum(safe_float(r.get('net_payable')) for r in reqs),
        'total_paid': sum(safe_float(r.get('payment_amount')) for r in reqs if r.get('payment_status') == 'Verified'),
        'pending_payment': sum(safe_float(r.get('balance_due', r.get('net_payable', 0))) for r in reqs if r.get('status') != 'Completed'),
        'gst': sum(safe_float(r.get('gst_amount')) for r in reqs)
    }


@app.template_global('money')
def money_global(v):
    return money(v)


@app.template_global('payment_terms_text')
def payment_terms_global(req, settings):
    return payment_terms_text(req, settings)


@app.route('/', methods=['GET', 'POST'])
def client_form():
    if request.method == 'POST':
        existing = load_requests()
        today = datetime.now().strftime('%y%m%d')
        nums = []
        for r in existing:
            jid = str(r.get('job_id', ''))
            if jid.startswith(f'DRD-{today}-'):
                try:
                    nums.append(int(jid.split('-')[-1]))
                except Exception:
                    pass
        job_id = f"DRD-{today}-{(max(nums) if nums else 0)+1:04d}"
        cc = request.form.get('country_code', '92')
        num = ''.join(filter(str.isdigit, request.form.get('whatsapp_num', '')))
        num = num[1:] if num.startswith('0') else num
        settings = load_settings()
        part_name = request.form.get('part_name', '').strip()
        qty = request.form.get('quantity', '').strip()
        mat = request.form.get('material', '').strip()
        desc = request.form.get('requirement', '').strip()
        filename = ''
        file = request.files.get('drawing_file')
        if file and file.filename:
            safe = os.path.basename(file.filename)
            filename = f"{job_id}_{safe}"
            file.save(os.path.join(UPLOAD_FOLDER, filename))
        new = {
            'job_id': job_id, 'company': request.form.get('company', '').strip(), 'name': request.form.get('name', '').strip(),
            'whatsapp': cc + num, 'part_name': part_name, 'quantity': qty, 'material': mat, 'req_date': request.form.get('req_date', ''),
            'requirement': desc, 'file': filename, 'part_no': 'P-001',
            'parts': [{'part_no': 'P-001', 'part_name': part_name, 'description': desc, 'quantity': qty, 'material': mat, 'finish': '',
                       'operations': '', 'inspection': '', 'price': 0.0}],
            'status': 'New', 'base_price': 0.0, 'gst_percent': settings.get('gst_percent', 18), 'gst_enabled': settings.get('gst_enabled', True),
            'gst_amount': 0, 'wht_enabled': settings.get('wht_enabled', False), 'wht_percent': settings.get('wht_percent', 0),
            'wht_mode': settings.get('wht_mode', 'deduct'), 'wht_amount': 0, 'gross_total': 0, 'total_price': 0, 'net_payable': 0,
            'delivery_time': 'Pending', 'remarks': '', 'quotation_type': 'standard', 'payment_terms': settings.get('payment_terms', '50_50'),
            'custom_payment_terms': '', 'customer_confirmed': False, 'payment_status': 'Not Submitted', 'payment_amount': 0, 'payment_date': '',
            'transaction_id': '', 'payment_file': '', 'advance_paid': 0, 'advance_transaction_id': '', 'balance_transaction_id': '',
            'advance_payment_file': '', 'balance_payment_file': '', 'advance_payment_status': 'Not Submitted', 'balance_payment_status': 'Not Submitted',
            'invoice_number': '', 'invoice_date': '', 'rfq_no': '', 'reference_no': '', 'project_title': '', 'technical_specification': '',
            'material_specification': '', 'manufacturing_operations': '', 'finish_specification': '', 'inspection_qc': '', 'technical_notes': '',
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        existing.append(new)
        save_all_requests(existing)
        return render_template_string(SUCCESS_PAGE, job_id=job_id)
    return render_template_string(INDEX_PAGE, settings=load_settings())


def build_orders_index(reqs):
    """Compact, pre-linked order list used by the admin search bar so any order
    can be found instantly by Job ID, client name, phone, part name or status."""
    out = []
    for r in reqs:
        part_names = ', '.join(p.get('part_name', '') for p in r.get('parts', []) if p.get('part_name'))
        search_bits = ' '.join(str(x) for x in [
            r.get('job_id', ''), r.get('company', ''), r.get('name', ''), r.get('whatsapp', ''),
            part_names, r.get('status', ''), r.get('invoice_number', ''), r.get('rfq_no', ''),
            r.get('reference_no', ''), r.get('project_title', '')
        ]).lower()
        out.append({
            'job_id': r.get('job_id', ''), 'company': r.get('company', ''), 'name': r.get('name', ''),
            'status': r.get('status', 'New'), 'parts': part_names or '—',
            'tab': ('new' if r.get('status') == 'New' else 'progress' if r.get('status') == 'Quotation Sent' else 'done'),
            'total': money(r.get('net_payable')), 'delivery': r.get('delivery_time') or 'Pending',
            'search': search_bits,
            'q_url': url_for('quotation_pdf', job_id=r['job_id']) if r.get('status') != 'New' else '',
            'i_url': url_for('invoice_pdf', job_id=r['job_id']) if r.get('status') == 'Completed' else '',
            'pay_url': url_for('payment_confirmation', job_id=r['job_id']) if r.get('status') != 'New' else '',
            'wa_url': r.get('wa_link') or ''
        })
    return out


def build_clients_index(reqs):
    """Groups every order by client (WhatsApp number is the unique key, since
    that's what's guaranteed to be filled in) so the admin can see one client's
    whole order history in a single file instead of hunting through every tab."""
    clients = {}
    for r in reqs:
        key = r.get('whatsapp') or f"{r.get('company','')}|{r.get('name','')}"
        c = clients.setdefault(key, {
            'key': key, 'company': r.get('company', ''), 'name': r.get('name', ''),
            'whatsapp': r.get('whatsapp', ''), 'orders': [], 'total_orders': 0,
            'completed': 0, 'total_value': 0.0, 'last_time': ''
        })
        if r.get('company'):
            c['company'] = r['company']
        if r.get('name'):
            c['name'] = r['name']
        part_names = ', '.join(p.get('part_name', '') for p in r.get('parts', []) if p.get('part_name'))
        c['orders'].append({
            'job_id': r.get('job_id', ''), 'status': r.get('status', 'New'),
            'parts': part_names or '—', 'total': money(r.get('net_payable')),
            'time': r.get('time', ''),
            'q_url': url_for('quotation_pdf', job_id=r['job_id']) if r.get('status') != 'New' else '',
            'i_url': url_for('invoice_pdf', job_id=r['job_id']) if r.get('status') == 'Completed' else '',
            'wa_url': r.get('wa_link') or ''
        })
        c['total_orders'] += 1
        if r.get('status') == 'Completed':
            c['completed'] += 1
        c['total_value'] += safe_float(r.get('net_payable'))
        if r.get('time', '') > c['last_time']:
            c['last_time'] = r.get('time', '')
    out = list(clients.values())
    for c in out:
        c['total_value_fmt'] = money(c['total_value'])
        c['orders'].sort(key=lambda o: o['time'], reverse=True)
    out.sort(key=lambda c: c['last_time'], reverse=True)
    return out


@app.route('/track')
def track_order():
    job_id = request.args.get('job_id', '').strip()
    if not job_id:
        return render_template_string(TRACK_FORM_PAGE, not_found=False)
    req = find_request(job_id)
    if not req:
        return render_template_string(TRACK_FORM_PAGE, not_found=True)
    return render_template_string(TRACK_STATUS_PAGE, req=req, money=money)


@app.route('/track/<job_id>')
def track_order_direct(job_id):
    req = find_request(job_id)
    if not req:
        return render_template_string(TRACK_FORM_PAGE, not_found=True)
    return render_template_string(TRACK_STATUS_PAGE, req=req, money=money)


@app.route('/my-orders')
def my_orders():
    """Lets a client see every order they've placed with us (matched by their
    WhatsApp number), not just a single Job ID — handy once they have several."""
    phone_raw = request.args.get('phone', '').strip()
    if not phone_raw:
        return render_template_string(MY_ORDERS_FORM_PAGE, not_found=False)
    phone = ''.join(filter(str.isdigit, phone_raw))
    phone = phone[1:] if phone.startswith('0') and len(phone) > 10 else phone
    reqs = load_requests()
    matches = [r for r in reqs if ''.join(filter(str.isdigit, str(r.get('whatsapp', '')))).endswith(phone[-9:])] if phone else []
    if not matches:
        return render_template_string(MY_ORDERS_FORM_PAGE, not_found=True)
    matches.sort(key=lambda x: x.get('time', ''), reverse=True)
    total_orders = len(matches)
    total_paid = sum(safe_float(r.get('advance_paid')) for r in matches)
    return render_template_string(MY_ORDERS_LIST_PAGE, orders=matches, money=money, total_orders=total_orders,
                                   total_paid=total_paid, client_name=matches[0].get('name', ''))


@app.route('/drd-secure-admin')
def admin_dashboard():
    reqs = load_requests()
    orders_json = json.dumps(build_orders_index(reqs))
    clients = build_clients_index(reqs)
    return render_template_string(ADMIN_PAGE, requests=reqs, summary=summary_data(reqs), settings=load_settings(),
                                   orders_json=orders_json, clients=clients)


@app.route('/api/order-status')
def order_status_api():
    """Tiny, cheap endpoint the admin dashboard polls to detect new orders
    without re-downloading the whole page each time."""
    reqs = load_requests()
    latest_time = reqs[0].get('time', '') if reqs else ''
    return {'total_orders': len(reqs), 'new_count': sum(r.get('status') == 'New' for r in reqs), 'latest_time': latest_time}


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        s = load_settings()
        for k in ['company_name', 'address', 'phone', 'email', 'website', 'ntn', 'strn', 'bank_name', 'account_title',
                  'account_number', 'iban', 'payment_instructions', 'custom_payment_terms', 'default_terms_conditions']:
            s[k] = request.form.get(k, '').strip()
        s['gst_percent'] = safe_float(request.form.get('gst_percent'), 18.0)
        s['wht_percent'] = safe_float(request.form.get('wht_percent'), 0.0)
        s['gst_enabled'] = request.form.get('gst_enabled') == 'on'
        s['wht_enabled'] = request.form.get('wht_enabled') == 'on'
        s['wht_mode'] = request.form.get('wht_mode', 'deduct')
        s['payment_terms'] = request.form.get('payment_terms', '50_50')
        s['notification_numbers'] = [request.form.get(f'notification_{i}', '').strip() for i in range(3)]
        titles = request.form.getlist('term_title[]')
        texts = request.form.getlist('term_text[]')
        lib = []
        for i in range(max(len(titles), len(texts))):
            title = (titles[i].strip() if i < len(titles) else '')
            text = (texts[i].strip() if i < len(texts) else '')
            if not title and not text:
                continue
            lib.append({'id': f't{i+1}', 'title': title or f'Clause {i+1}', 'text': text})
        s['terms_library'] = lib
        s['welcome_voice_lang'] = request.form.get('welcome_voice_lang', 'en-US')
        s['welcome_voice_hint'] = request.form.get('welcome_voice_hint', '').strip()
        s['welcome_pitch'] = min(2.0, max(0.5, safe_float(request.form.get('welcome_pitch'), 1.1)))
        s['welcome_rate'] = min(1.5, max(0.5, safe_float(request.form.get('welcome_rate'), 0.93)))
        s['welcome_volume'] = min(1.0, max(0.0, safe_float(request.form.get('welcome_volume'), 1.0)))
        s['welcome_message_en'] = request.form.get('welcome_message_en', '').strip() or DEFAULT_SETTINGS['welcome_message_en']
        s['welcome_message_ur'] = request.form.get('welcome_message_ur', '').strip() or DEFAULT_SETTINGS['welcome_message_ur']
        s['admin_auto_refresh'] = request.form.get('admin_auto_refresh') == 'on'
        s['admin_notify_sound'] = request.form.get('admin_notify_sound') == 'on'
        # Portfolio gallery: keep existing photos (with edited captions) minus any removed,
        # then append newly uploaded photos.
        remove_set = set(request.form.getlist('remove_portfolio[]'))
        existing_files = request.form.getlist('portfolio_file[]')
        existing_captions = request.form.getlist('portfolio_caption[]')
        portfolio = []
        for i, fname in enumerate(existing_files):
            if fname in remove_set:
                continue
            caption = existing_captions[i].strip() if i < len(existing_captions) else ''
            portfolio.append({'file': fname, 'caption': caption})
        new_caption = request.form.get('portfolio_new_caption', '').strip()
        for f in request.files.getlist('portfolio_images'):
            if f and f.filename:
                safe_name = os.path.basename(f.filename)
                stored_name = f"portfolio_{datetime.now().strftime('%y%m%d%H%M%S%f')}_{safe_name}"
                f.save(os.path.join(UPLOAD_FOLDER, stored_name))
                portfolio.append({'file': stored_name, 'caption': new_caption})
        s['portfolio'] = portfolio
        save_settings(s)
        return redirect(url_for('settings_page'))
    return render_template_string(SETTINGS_PAGE, s=load_settings())


@app.route('/update/<job_id>', methods=['POST'])
def update_job(job_id):
    all_reqs = load_requests()
    settings = load_settings()
    for req in all_reqs:
        if req.get('job_id') != job_id:
            continue
        pno = request.form.getlist('part_no[]')
        pname = request.form.getlist('part_name[]')
        pdesc = request.form.getlist('part_description[]')
        pqty = request.form.getlist('part_quantity[]')
        pmat = request.form.getlist('part_material[]')
        pprice = request.form.getlist('part_price[]')
        parts = []
        n = max(len(pno), len(pname), len(pdesc), len(pqty), len(pmat), len(pprice), 0)
        for i in range(n):
            name = pname[i].strip() if i < len(pname) else ''
            if not name:
                continue
            price = safe_float(pprice[i]) if i < len(pprice) else 0.0
            parts.append({
                'part_no': (pno[i].strip() if i < len(pno) and pno[i].strip() else f'P-{len(parts)+1:03d}'),
                'part_name': name, 'description': pdesc[i].strip() if i < len(pdesc) else '',
                'quantity': pqty[i].strip() if i < len(pqty) else '', 'material': pmat[i].strip() if i < len(pmat) else '',
                'finish': '', 'operations': '', 'inspection': '', 'price': price
            })
        if not parts:
            parts = ensure_parts(req)
        base = sum(p['price'] for p in parts)
        gp = safe_float(request.form.get('gst_percent'), settings.get('gst_percent', 18))
        ge = request.form.get('gst_enabled', 'yes') != 'no'
        ws = request.form.get('wht_enabled', 'use')
        we = settings.get('wht_enabled', False) if ws == 'use' else ws == 'yes'
        wp = safe_float(request.form.get('wht_percent'), settings.get('wht_percent', 0))
        wt = settings.get('wht_mode', 'deduct')
        ga, gt, wa, tp, npv = calculate_amounts(base, ge, gp, we, wp, wt)
        req['parts'] = parts
        req['base_price'] = base
        req['part_name'] = parts[0]['part_name']
        req['quantity'] = parts[0]['quantity']
        req['material'] = parts[0]['material']
        req['requirement'] = parts[0]['description']
        req['gst_percent'] = gp
        req['gst_enabled'] = ge
        req['gst_amount'] = ga
        req['wht_enabled'] = we
        req['wht_percent'] = wp
        req['wht_mode'] = wt
        req['wht_amount'] = wa
        req['gross_total'] = gt
        req['total_price'] = tp
        req['net_payable'] = npv
        req['delivery_time'] = request.form.get('delivery_time', '').strip()
        req['remarks'] = request.form.get('remarks', '').strip()
        req['quotation_type'] = request.form.get('quotation_type', 'standard')
        req['payment_terms'] = request.form.get('payment_terms', settings.get('payment_terms', '50_50'))
        req['custom_payment_terms'] = request.form.get('custom_payment_terms', '').strip()
        req['selected_terms'] = request.form.getlist('selected_terms[]')
        for k in ['rfq_no', 'reference_no', 'project_title', 'technical_specification', 'material_specification',
                  'manufacturing_operations', 'finish_specification', 'inspection_qc', 'technical_notes']:
            req[k] = request.form.get(k, req.get(k, '')).strip()
        terms = req['payment_terms']
        adv_ratio = 0.5 if terms == '50_50' else 1.0 if terms == '100_advance' else 0.0
        req['advance_required'] = round(npv * adv_ratio, 2)
        req['balance_due'] = max(0, round(npv - safe_float(req.get('advance_paid')), 2))
        req['status'] = 'Quotation Sent'
        break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/payment/<job_id>')
def payment_confirmation(job_id):
    req = find_request(job_id)
    if not req:
        return 'Job not found', 404
    return render_template_string(PAYMENT_PAGE, req=req, settings=load_settings(), money=money, payment_terms_text=payment_terms_text)


@app.route('/payment-submit/<job_id>', methods=['POST'])
def payment_submit(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req.get('job_id') != job_id:
            continue
        stage = request.form.get('payment_stage', 'advance')
        amount = safe_float(request.form.get('payment_amount'))
        tx = request.form.get('transaction_id', '').strip()
        date = request.form.get('payment_date', '')
        file = request.files.get('payment_file')
        filename = ''
        if file and file.filename:
            safe = os.path.basename(file.filename)
            filename = f"{job_id}_{stage.upper()}_{safe}"
            file.save(os.path.join(UPLOAD_FOLDER, filename))
        req['customer_confirmed'] = request.form.get('customer_confirmed') == 'yes'
        req['payment_amount'] = amount
        req['transaction_id'] = tx
        req['payment_date'] = date
        req['payment_file'] = filename
        req['payment_status'] = 'Submitted'
        if stage in ('advance', 'full'):
            req['advance_transaction_id'] = tx
            req['advance_payment_file'] = filename
            req['advance_payment_status'] = 'Submitted'
        if stage in ('balance', 'full'):
            req['balance_transaction_id'] = tx
            req['balance_payment_file'] = filename
            req['balance_payment_status'] = 'Submitted'
        break
    save_all_requests(all_reqs)
    return render_template_string(PAYMENT_SUCCESS, job_id=job_id)


@app.route('/verify-payment/<job_id>', methods=['POST'])
def verify_payment(job_id):
    """Marks the payment verified and correctly tracks which stage (advance/balance)
    it applies to, for both 50/50 and 100% advance plans."""
    all_reqs = load_requests()
    for req in all_reqs:
        if req.get('job_id') != job_id:
            continue
        req['payment_status'] = 'Verified'
        amount = safe_float(req.get('payment_amount'))
        req['advance_paid'] = round(safe_float(req.get('advance_paid')) + amount, 2)
        req['balance_due'] = max(0, round(safe_float(req.get('net_payable')) - req['advance_paid'], 2))
        terms = req.get('payment_terms')
        if terms == '100_advance':
            req['advance_payment_status'] = 'Verified'
            if req['balance_due'] <= 0:
                req['balance_payment_status'] = 'Verified'
        else:
            # 50/50 or custom: verify whichever stage hasn't been verified yet,
            # and mark both done once nothing is left owing.
            if req['balance_due'] <= 0:
                req['advance_payment_status'] = 'Verified'
                req['balance_payment_status'] = 'Verified'
            elif req.get('advance_payment_status') != 'Verified':
                req['advance_payment_status'] = 'Verified'
            else:
                req['balance_payment_status'] = 'Verified'
        break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/complete/<job_id>', methods=['POST'])
def complete_job(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req.get('job_id') == job_id:
            req['status'] = 'Completed'
            req['invoice_number'] = req.get('invoice_number') or next_invoice_number(req)
            req['invoice_date'] = datetime.now().strftime('%Y-%m-%d')
            break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/quotation/<job_id>.pdf')
def quotation_pdf(job_id):
    req = find_request(job_id)
    if not req:
        return 'Job not found', 404
    try:
        resp = send_file(build_document_pdf(req, 'quotation'), mimetype='application/pdf', as_attachment=False,
                          download_name=f'Quotation_{job_id}.pdf', conditional=False, etag=False, last_modified=None, max_age=0)
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    except Exception as e:
        return f'PDF error: {e}', 500


@app.route('/invoice/<job_id>.pdf')
def invoice_pdf(job_id):
    req = find_request(job_id)
    if not req:
        return 'Job not found', 404
    try:
        resp = send_file(build_document_pdf(req, 'invoice'), mimetype='application/pdf', as_attachment=False,
                          download_name=f'Invoice_{job_id}.pdf', conditional=False, etag=False, last_modified=None, max_age=0)
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    except Exception as e:
        return f'PDF error: {e}', 500


@app.route('/delete/<job_id>', methods=['POST'])
def delete_job(job_id):
    reqs = [r for r in load_requests() if r.get('job_id') != job_id]
    save_all_requests(reqs)
    return redirect(url_for('admin_dashboard'))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.errorhandler(404)
def not_found(e):
    return 'Page not found', 404


@app.errorhandler(500)
def server_error(e):
    # Keep the app from hard-crashing / hanging on unexpected input
    return f'Something went wrong: {e}', 500


if __name__ == '__main__':
    app.run(debug=False, threaded=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
