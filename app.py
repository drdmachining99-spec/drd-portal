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
    'admin_pin': ''
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
    nums = data.get('notification_numbers', ['', '', ''])
    if not isinstance(nums, list): nums = ['', '', '']
    data['notification_numbers'] = (nums + ['', '', ''])[:3]
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
    try: return f"Rs. {float(v or 0):,.2f}"
    except Exception: return 'Rs. 0.00'


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
            'price': float(req.get('base_price', 0) or 0)
        }]
    clean = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict): continue
        try: price = float(p.get('price', 0) or 0)
        except Exception: price = 0.0
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
    if mode == '100_advance': return '100% Advance payment before production.'
    if mode == '50_50': return '50% Advance + 50% before / at delivery.'
    if mode == 'custom': return req.get('custom_payment_terms') or settings.get('custom_payment_terms', '') or 'As mutually agreed.'
    return 'As mutually agreed.'


def make_whatsapp_link(req, settings):
    phone = ''.join(filter(str.isdigit, str(req.get('whatsapp', ''))))
    if not phone: return '#'
    parts = ensure_parts(req)
    lines = [f"Hello *{req.get('name', '')}*,", '',
             f"Your quotation for Job ID *{req.get('job_id', '')}* has been prepared by *{settings.get('company_name', '')}*.", '']
    for i, p in enumerate(parts, 1):
        lines += [f"*{i}. {p['part_no']} - {p['part_name']}*",
                  f"Qty: {p['quantity']} | Material: {p['material']}",
                  f"Price: Rs. {p['price']:,.2f}"]
        if p.get('description'): lines.append(f"Description: {p['description']}")
    lines += ['', f"*Parts Total:* Rs. {float(req.get('base_price',0) or 0):,.2f}"]
    if req.get('gst_enabled'):
        lines.append(f"*GST ({float(req.get('gst_percent',0) or 0):g}%):* Rs. {float(req.get('gst_amount',0) or 0):,.2f}")
    if req.get('wht_enabled'):
        lines.append(f"*WHT ({float(req.get('wht_percent',0) or 0):g}%):* Rs. {float(req.get('wht_amount',0) or 0):,.2f}")
    lines += [f"*Net Payable:* *Rs. {float(req.get('net_payable',0) or 0):,.2f}*",
              f"*Delivery:* {req.get('delivery_time','Pending')}",
              f"*Payment Terms:* {payment_terms_text(req, settings)}"]
    if req.get('remarks'): lines += ['', f"*Remarks:* {req['remarks']}"]
    if req.get('job_id'):
        lines += ['', f"*Quotation PDF:* {url_for('quotation_pdf', job_id=req['job_id'], _external=True)}",
                  f"*Payment / Confirmation:* {url_for('payment_confirmation', job_id=req['job_id'], _external=True)}"]
    encoded = urllib.parse.quote('\n'.join(lines))
    return f"https://wa.me/{phone}?text={encoded}"


def load_requests():
    settings = load_settings()
    if not os.path.exists(REQUESTS_FILE): return []
    try:
        with open(REQUESTS_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
        changed = False
        for req in data:
            defaults = {
                'base_price':0,'gst_percent':settings.get('gst_percent',18),'gst_enabled':settings.get('gst_enabled',True),'gst_amount':0,
                'wht_enabled':False,'wht_percent':0,'wht_mode':'deduct','wht_amount':0,'gross_total':0,'total_price':0,'net_payable':0,
                'delivery_time':'Pending','remarks':'','status':'New','quotation_type':'standard','parts':[],
                'customer_confirmed':False,'payment_terms':settings.get('payment_terms','50_50'),'custom_payment_terms':'',
                'advance_required':0,'advance_paid':0,'balance_due':0,'payment_status':'Not Submitted','payment_amount':0,
                'transaction_id':'','payment_date':'','payment_file':'','advance_transaction_id':'','balance_transaction_id':'',
                'advance_payment_file':'','balance_payment_file':'','advance_payment_status':'Not Submitted','balance_payment_status':'Not Submitted',
                'invoice_number':'','invoice_date':'','rfq_no':'','reference_no':'','project_title':'','technical_specification':'',
                'material_specification':'','manufacturing_operations':'','finish_specification':'','inspection_qc':'',
                'technical_notes':'','time':datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            for k,v in defaults.items():
                if k not in req: req[k]=v; changed=True
            old = req.get('parts')
            if not isinstance(old,list) or not old:
                req['parts'] = ensure_parts(req); changed=True
            else:
                req['parts'] = ensure_parts(req)
            req['base_price'] = sum(float(p['price']) for p in req['parts'])
            if not req.get('part_name'): req['part_name']=req['parts'][0]['part_name']; changed=True
            if not req.get('quantity'): req['quantity']=req['parts'][0]['quantity']; changed=True
            if not req.get('material'): req['material']=req['parts'][0]['material']; changed=True
            if req.get('status') != 'New' or req.get('base_price',0):
                ge,gt,wa,tp,np = calculate_amounts(req['base_price'],bool(req.get('gst_enabled')),float(req.get('gst_percent',0) or 0),bool(req.get('wht_enabled')),float(req.get('wht_percent',0) or 0),req.get('wht_mode','deduct'))
                req.update(gst_amount=ge,gross_total=gt,wht_amount=wa,total_price=tp,net_payable=np)
            req['advance_required'] = round(req['net_payable'] * (0.5 if req.get('payment_terms')=='50_50' else 1.0 if req.get('payment_terms')=='100_advance' else 0.0),2)
            req['balance_due'] = max(0.0, round(req['net_payable'] - float(req.get('advance_paid',0) or 0),2))
            req['wa_link'] = make_whatsapp_link(req, settings)
        if changed: save_all_requests(data)
        return sorted(data,key=lambda x:x.get('time',''),reverse=True)
    except Exception as e:
        print('Error loading requests:',e); return []


def save_all_requests(reqs):
    clean=[]
    for r in reqs:
        x=r.copy(); x.pop('wa_link',None); clean.append(x)
    with open(REQUESTS_FILE,'w',encoding='utf-8') as f: json.dump(clean,f,indent=4)


def find_request(job_id):
    for req in load_requests():
        if req.get('job_id')==job_id: return req
    return None


def next_invoice_number(req):
    if req.get('invoice_number'): return req['invoice_number']
    return f"INV-{datetime.now().strftime('%y%m%d')}-{req.get('job_id','0000').split('-')[-1]}"


def wrap_text(text, font, size, max_width):
    words=str(text or '').split(); lines=[]; cur=''
    for word in words:
        test=(cur+' '+word).strip()
        if stringWidth(test,font,size)<=max_width: cur=test
        else:
            if cur: lines.append(cur)
            cur=word
    if cur: lines.append(cur)
    return lines or ['']


def draw_wrapped(c,text,x,y,width,font='Helvetica',size=7.5,leading=9,max_lines=6):
    c.setFont(font,size)
    lines=[]
    for para in str(text or '').splitlines() or ['']:
        lines += wrap_text(para,font,size,width)
    for line in lines[:max_lines]:
        c.drawString(x,y,line); y-=leading
    return y, min(len(lines),max_lines)


def build_document_pdf(req, document_type='quotation'):
    settings=load_settings()
    if not os.path.exists(LETTERHEAD_FILE): raise FileNotFoundError('letterhead.pdf is missing from the project.')
    reader=PdfReader(LETTERHEAD_FILE); bg=reader.pages[0]
    width=float(bg.mediabox.width); height=float(bg.mediabox.height)
    parts=ensure_parts(req)
    title = 'QUOTATION' if document_type=='quotation' else 'TAX / COMMERCIAL INVOICE'
    detailed = req.get('quotation_type') in ('detailed','technical')
    packet=BytesIO(); c=canvas.Canvas(packet,pagesize=(width,height))
    left=16*mm; right=width-16*mm; top=height-39*mm; bottom=22*mm

    def header():
        c.setFillColor(colors.HexColor('#123f5d')); c.setFont('Helvetica-Bold',16); c.drawString(left,top,title)
        c.setFillColor(colors.black); c.setFont('Helvetica',8)
        ref=req.get('reference_no') or req.get('rfq_no') or req.get('job_id','')
        c.drawRightString(right,top+1,f"No: {ref}"); c.drawRightString(right,top-11,f"Date: {datetime.now().strftime('%d-%m-%Y')}")
    def footer(page_no):
        c.setFont('Helvetica',7); c.setFillColor(colors.grey); c.drawString(left,11*mm,f"Job ID: {req.get('job_id','')}"); c.drawRightString(right,11*mm,f"Page {page_no}")

    page_no=1; header(); y=top-28
    c.setFillColor(colors.black); c.setFont('Helvetica-Bold',9); c.drawString(left,y,'CUSTOMER');
    c.setFont('Helvetica',8); y-=11
    for line in [req.get('company',''),req.get('name',''),f"WhatsApp: {req.get('whatsapp','')}"]:
        c.drawString(left,y,str(line)); y-=10
    if detailed:
        y-=4; c.setFont('Helvetica-Bold',8.5); c.drawString(left,y,'PROJECT / REFERENCE'); y-=11; c.setFont('Helvetica',8)
        for label,key in [('Project / Work Title','project_title'),('RFQ / Reference No.','rfq_no')]:
            if req.get(key): c.drawString(left,y,f"{label}: {req[key]}"); y-=10
    y-=8
    headers=['Sr. No.','Part No.','Description / Part Name','Qty','Material','Quoted Price','GST','Total']
    widths=[25,43,145,30,70,65,48,65]
    scale=(right-left)/sum(widths); widths=[w*scale for w in widths]
    rowx=[left]
    for w in widths: rowx.append(rowx[-1]+w)
    def draw_table_header(y):
        c.setFillColor(colors.HexColor('#eaf1f6')); c.rect(left,y-20,right-left,20,fill=1,stroke=1); c.setFillColor(colors.black); c.setFont('Helvetica-Bold',6.8)
        for i,h in enumerate(headers): c.drawCentredString((rowx[i]+rowx[i+1])/2,y-13,h)
        return y-20
    y=draw_table_header(y)
    base_total=0
    for idx,p in enumerate(parts,1):
        gst_line=p['price']*float(req.get('gst_percent',0) or 0)/100 if req.get('gst_enabled') else 0
        line_total=p['price']+gst_line; base_total+=p['price']
        desc=p['part_name']
        tech=[]
        if p.get('description'): tech.append(p['description'])
        if p.get('operations'): tech.append('Operations: '+p['operations'])
        if p.get('finish'): tech.append('Finish: '+p['finish'])
        if p.get('inspection'): tech.append('Inspection: '+p['inspection'])
        desc+='\n'+' | '.join(tech) if tech else ''
        dlines=[]
        for para in desc.splitlines(): dlines += wrap_text(para,'Helvetica',6.6,widths[2]-6)
        rh=max(30,10*min(len(dlines),4)+10)
        if y-rh<bottom:
            footer(page_no); c.showPage(); page_no+=1; header(); y=top-20; y=draw_table_header(y)
        c.rect(left,y-rh,right-left,rh,fill=0,stroke=1); c.setFont('Helvetica',6.8)
        vals=[str(idx),p['part_no'],dlines[:4],p['quantity'],p['material'],f"{p['price']:,.2f}",f"{gst_line:,.2f}",f"{line_total:,.2f}"]
        for i in range(8): c.line(rowx[i],y,rowx[i],y-rh)
        c.drawCentredString((rowx[0]+rowx[1])/2,y-12,vals[0]); c.drawString(rowx[1]+3,y-11,vals[1]);
        yy=y-10
        for dl in vals[2]: c.drawString(rowx[2]+3,yy,dl); yy-=9
        c.drawCentredString((rowx[3]+rowx[4])/2,y-rh/2,vals[3]); c.drawString(rowx[4]+3,y-rh/2+2,vals[4]); c.drawRightString(rowx[5+1]-3,y-rh/2+2,vals[5]); c.drawRightString(rowx[6+1]-3,y-rh/2+2,vals[6]); c.drawRightString(rowx[7+1]-3,y-rh/2+2,vals[7]);
        y-=rh
    gst=float(req.get('gst_amount',0) or 0); wht=float(req.get('wht_amount',0) or 0); gross=float(req.get('gross_total',base_total+gst) or base_total+gst); net=float(req.get('net_payable',gross) or gross)
    if y-70<bottom:
        footer(page_no); c.showPage(); page_no+=1; header(); y=top-25
    c.setFont('Helvetica-Bold',8); c.drawRightString(right,y-12,f"Parts Total: {money(base_total)}"); y-=24
    if req.get('gst_enabled'): c.setFont('Helvetica',8); c.drawRightString(right,y-10,f"GST ({float(req.get('gst_percent',0) or 0):g}%): {money(gst)}"); y-=20
    if req.get('wht_enabled'): c.drawRightString(right,y-10,f"WHT ({float(req.get('wht_percent',0) or 0):g}%): {money(wht)}"); y-=20
    c.setFont('Helvetica-Bold',10); c.drawRightString(right,y-10,('NET PAYABLE' if req.get('wht_enabled') and req.get('wht_mode')=='deduct' else 'TOTAL PAYABLE')+f": {money(net)}"); y-=25

    sections=[]
    if req.get('delivery_time'): sections.append(('Delivery Schedule',f"Delivery within {req['delivery_time']} working days." if str(req['delivery_time']).isdigit() else str(req['delivery_time'])))
    sections.append(('Payment Terms',payment_terms_text(req,settings)))
    if req.get('remarks'): sections.append(('Commercial / Technical Notes',req['remarks']))
    if detailed:
        for title2,key in [('Technical Specification','technical_specification'),('Material Specification','material_specification'),('Manufacturing Operations','manufacturing_operations'),('Finish / Surface Treatment','finish_specification'),('Inspection / QC','inspection_qc'),('Technical Notes','technical_notes')]:
            if req.get(key): sections.append((title2,req[key]))
    sections.append(('Terms & Conditions','Quoted prices are based on the stated scope, quantities and specifications. Any change in drawing, material, quantity, finish or scope may affect price and delivery. GST is applied as stated above. Production will proceed according to the agreed payment terms. Final delivery is subject to completion of applicable inspection/QC.'))
    for st,txt in sections:
        if y-35<bottom:
            footer(page_no); c.showPage(); page_no+=1; header(); y=top-25
        c.setFont('Helvetica-Bold',8.5); c.drawString(left,y,st); y-=11
        y,_=draw_wrapped(c,txt,left,y,right-left,'Helvetica',7.5,9,8); y-=8
    if y-75<bottom:
        footer(page_no); c.showPage(); page_no+=1; header(); y=top-25
    c.setFont('Helvetica-Bold',8.5); c.drawString(left,y,'Bank Details'); y-=12; c.setFont('Helvetica',7.8)
    for label,key in [('Bank','bank_name'),('Account Title','account_title'),('Account Number','account_number'),('IBAN','iban')]:
        if settings.get(key): c.drawString(left,y,f"{label}: {settings[key]}"); y-=10
    if settings.get('payment_instructions'): y-=2; y,_=draw_wrapped(c,settings['payment_instructions'],left,y,right-left,'Helvetica',7.5,9,3)
    y-=10; c.setFont('Helvetica-Bold',8); c.drawString(left,y,'Authorized Signatory'); footer(page_no); c.save(); packet.seek(0)
    overlay=PdfReader(packet); bg.merge_page(overlay.pages[0])
    writer=PdfWriter(); writer.add_page(bg); out=BytesIO(); writer.write(out); out.seek(0); return out


INDEX_PAGE='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>DRD Order Portal</title><style>body{font-family:Arial;background:#f4f4f9;padding:18px}.box{max-width:650px;margin:auto;background:white;padding:25px;border-radius:12px;box-shadow:0 2px 12px #ccc}label{font-weight:bold;display:block;margin-top:10px}input,select,textarea{width:100%;box-sizing:border-box;padding:10px;margin:5px 0 10px;border:1px solid #ccc;border-radius:6px}button{width:100%;padding:12px;background:#007bff;color:white;border:0;border-radius:6px;font-weight:bold}</style></head><body><div class="box"><h2>DRD Manufacturing Solutions</h2><p>Engineering & Manufacturing Order Portal</p><form method="POST" enctype="multipart/form-data"><label>Company Name</label><input name="company" required><label>Client Name</label><input name="name" required><label>WhatsApp</label><div style="display:flex;gap:8px"><select name="country_code" style="width:35%"><option value="92">+92</option><option value="966">+966</option><option value="971">+971</option><option value="44">+44</option><option value="1">+1</option></select><input name="whatsapp_num" required placeholder="3175240272"></div><label>Part Name</label><input name="part_name" required><label>Quantity</label><input name="quantity" type="number" min="1" required><label>Material</label><select name="material"><option>Aluminum</option><option>Stainless Steel</option><option>Brass</option><option>Steel</option><option>PETG / PLA</option><option>ABS / TPU</option><option>Other</option></select><label>Required Date</label><input name="req_date" type="date"><label>Requirements / Technical Notes</label><textarea name="requirement" rows="5"></textarea><label>Drawing / CAD / Reference File</label><input type="file" name="drawing_file" accept=".step,.stp,.sldprt,.dxf,.dwg,.stl,.pdf,.zip,image/*"><button>Submit Request</button></form></div></body></html>'''
SUCCESS_PAGE='''<!doctype html><html><body style="font-family:Arial;background:#f4f4f9;text-align:center;padding:50px"><div style="background:white;max-width:500px;margin:auto;padding:35px;border-radius:10px"><h2 style="color:#198754">Request Submitted</h2><p>Job ID: <b>{{ job_id }}</b></p><p>Please keep this Job ID for quotation, payment and delivery reference.</p><a href="/">Submit Another Request</a></div></body></html>'''
PAYMENT_PAGE='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;background:#f4f4f9;padding:18px}.box{max-width:600px;margin:auto;background:white;padding:25px;border-radius:10px}input,select{width:100%;box-sizing:border-box;padding:10px;margin:6px 0 12px}button{width:100%;padding:12px;background:#198754;color:white;border:0;border-radius:6px;font-weight:bold}.info{background:#eef6ff;padding:12px;border-radius:7px}</style></head><body><div class="box"><h2>Order Confirmation & Payment</h2><div class="info"><b>Job ID:</b> {{ req.job_id }}<br><b>Total:</b> {{ money(req.net_payable) }}<br><b>Payment Terms:</b> {{ payment_terms_text(req, settings) }}<br><b>Advance Required:</b> {{ money(req.advance_required) }}<br><b>Balance Due:</b> {{ money(req.balance_due) }}</div><form method="POST" action="/payment-submit/{{ req.job_id }}" enctype="multipart/form-data"><label>Payment Stage</label><select name="payment_stage"><option value="advance">Advance Payment</option><option value="balance">Balance / Final Payment</option><option value="full">Full Payment</option></select><label>Payment Amount</label><input type="number" step="any" name="payment_amount" required><label>Transaction / Reference ID</label><input name="transaction_id" required><label>Payment Date</label><input type="date" name="payment_date" required><label>Payment Proof</label><input type="file" name="payment_file" accept="image/*,.pdf"><label>Confirm Order</label><select name="customer_confirmed"><option value="yes">Yes</option></select><button>Submit Payment Confirmation</button></form></div></body></html>'''
PAYMENT_SUCCESS='''<!doctype html><html><body style="font-family:Arial;text-align:center;background:#f4f4f9;padding:50px"><div style="background:white;max-width:500px;margin:auto;padding:35px;border-radius:10px"><h2 style="color:#198754">Payment Submitted</h2><p>Job ID: <b>{{ job_id }}</b></p><p>Your payment proof and reference have been received for manual verification.</p></div></body></html>'''

SETTINGS_PAGE='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;background:#f4f4f9;padding:18px}.box{max-width:900px;margin:auto;background:white;padding:25px;border-radius:10px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:700px){.grid{grid-template-columns:1fr}}label{font-weight:bold;display:block;margin-top:8px}input,select,textarea{width:100%;box-sizing:border-box;padding:8px;margin:4px 0 10px}button{padding:11px 16px;background:#007bff;color:white;border:0;border-radius:5px;font-weight:bold}</style></head><body><div class="box"><a href="/drd-secure-admin">← Admin</a><h2>Settings</h2><form method="POST"><div class="grid">{% for key,label in [('company_name','Company Name'),('address','Address'),('phone','Phone'),('email','Email'),('website','Website'),('ntn','NTN'),('strn','STRN / GST'),('bank_name','Bank Name'),('account_title','Account Title'),('account_number','Account Number'),('iban','IBAN')]}<div><label>{{label}}</label><input name="{{key}}" value="{{s[key]}}"></div>{% endfor %}</div><label>Payment Instructions</label><textarea name="payment_instructions" rows="3">{{s.payment_instructions}}</textarea><h3>Tax</h3><label>GST %</label><input name="gst_percent" type="number" step="any" value="{{s.gst_percent}}"><label><input style="width:auto" type="checkbox" name="gst_enabled" {% if s.gst_enabled %}checked{% endif %}> Enable GST</label><label>WHT %</label><input name="wht_percent" type="number" step="any" value="{{s.wht_percent}}"><label><input style="width:auto" type="checkbox" name="wht_enabled" {% if s.wht_enabled %}checked{% endif %}> Enable WHT</label><label>WHT Mode</label><select name="wht_mode"><option value="deduct" {% if s.wht_mode=='deduct' %}selected{% endif %}>Deduct</option><option value="add" {% if s.wht_mode=='add' %}selected{% endif %}>Add</option></select><h3>Default Payment Terms</h3><select name="payment_terms"><option value="50_50" {% if s.payment_terms=='50_50' %}selected{% endif %}>50% Advance + 50% before/at Delivery</option><option value="100_advance" {% if s.payment_terms=='100_advance' %}selected{% endif %}>100% Advance</option><option value="custom" {% if s.payment_terms=='custom' %}selected{% endif %}>Custom</option></select><label>Custom Payment Terms</label><textarea name="custom_payment_terms">{{s.custom_payment_terms}}</textarea><h3>Notification WhatsApp Numbers</h3>{% for i in range(3) %}<label>Notification Number {{i+1}}</label><input name="notification_{{i}}" value="{{s.notification_numbers[i]}}" placeholder="923175240272">{% endfor %}<p>Normal wa.me links cannot automatically push notifications; these numbers are stored for notification links/manual use. Automatic WhatsApp notifications require an API/provider.</p><button>Save Settings</button></form></div></body></html>'''

ADMIN_PAGE='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>DRD Admin</title><style>body{font-family:Arial;background:#f4f4f9;margin:15px;color:#222}.top{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}}.card{background:white;padding:14px;border-radius:8px;box-shadow:0 1px 5px #ddd}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}.tabs button{padding:10px;border:0;border-radius:5px;background:#6c757d;color:white}.tabs button.active{background:#007bff}.tab{display:none}.tab.active{display:block}table{width:100%;border-collapse:collapse;background:white;margin-bottom:20px}th,td{border:1px solid #ddd;padding:8px;vertical-align:top;font-size:12px}th{background:#343a40;color:white}input,select,textarea{width:100%;box-sizing:border-box;padding:6px;margin:3px 0 6px}button{background:#198754;color:white;border:0;padding:7px 10px;border-radius:4px;font-weight:bold}.danger{background:#dc3545}.info{background:#17a2b8}.purple{background:#6f42c1}.rowbox{background:#f8f9fa;border:1px solid #ddd;padding:7px;border-radius:5px;margin-bottom:6px}.partgrid{display:grid;grid-template-columns:65px 1fr 1.5fr 70px 1fr 100px 30px;gap:4px;align-items:start}.small{font-size:11px;color:#666}.summary{font-weight:bold}.scroll{overflow:auto}.new{border-left:5px solid #dc3545}.progress{border-left:5px solid #ffc107}.done{border-left:5px solid #198754}.btnlink{display:inline-block;padding:7px 9px;border-radius:4px;color:white;text-decoration:none;margin:2px}</style><script>function tab(id,b){document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.getElementById(id).classList.add('active');document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active')}function addPart(id){let box=document.getElementById(id), row=box.querySelector('.partgrid').cloneNode(true);row.querySelectorAll('input,textarea').forEach(x=>{if(x.name==='part_no[]')x.value='P-'+String(box.querySelectorAll('.partgrid').length+1).padStart(3,'0');else x.value=''});box.appendChild(row)}function delPart(btn){let box=btn.closest('.partsbox');if(box.querySelectorAll('.partgrid').length>1)btn.closest('.partgrid').remove()}function updateTotal(box){let total=0;box.querySelectorAll('.part-price').forEach(x=>total+=parseFloat(x.value||0));box.parentElement.querySelector('.part-total').textContent='Rs. '+total.toLocaleString(undefined,{minimumFractionDigits:2})}</script></head><body><div class="top"><div><h2>DRD Manufacturing Solutions - Admin</h2><p>Orders, quotations, payments, invoices and history</p></div><div><a class="btnlink" style="background:#343a40" href="/settings">⚙ Settings</a></div></div><div class="cards"><div class="card"><b>Total Orders</b><div class="summary">{{summary.total_orders}}</div></div><div class="card"><b>Completed</b><div class="summary">{{summary.completed}}</div></div><div class="card"><b>In Progress</b><div class="summary">{{summary.in_progress}}</div></div><div class="card"><b>New</b><div class="summary">{{summary.new}}</div></div><div class="card"><b>Total Quoted</b><div class="summary">{{money(summary.total_quoted)}}</div></div><div class="card"><b>Total Paid</b><div class="summary">{{money(summary.total_paid)}}</div></div><div class="card"><b>Pending Payment</b><div class="summary">{{money(summary.pending_payment)}}</div></div><div class="card"><b>GST</b><div class="summary">{{money(summary.gst)}}</div></div></div><div class="tabs"><button class="active" onclick="tab('new',this)">📥 New</button><button onclick="tab('progress',this)">⏳ Quotations / Payment</button><button onclick="tab('done',this)">✅ Completed</button></div>
<div id="new" class="tab active"><h3>New Requests</h3>{% for req in requests %}{% if req.status=='New' %}<div class="card new"><b>{{req.job_id}}</b> — {{req.company}} / {{req.name}}<br><span class="small">{{req.time}} | {{req.whatsapp}}</span><p><b>Original Request:</b> {{req.part_name}} | Qty {{req.quantity}} | {{req.material}}<br>{{req.requirement}}</p>{% if req.file %}<a href="/uploads/{{req.file}}" target="_blank">View Drawing/File</a>{% endif %}<form action="/update/{{req.job_id}}" method="POST"><div class="grid"><label>Quotation Type<select name="quotation_type"><option value="standard">Standard Quotation</option><option value="detailed">Detailed / Technical & Commercial Proposal</option></select></label><label>RFQ / Reference No.<input name="rfq_no"></label><label>Project / Work Title<input name="project_title"></label></div><h4>Parts</h4><div class="partsbox" id="parts-{{req.job_id}}"><div class="partgrid small"><b>Part No.</b><b>Part Name</b><b>Description</b><b>Qty</b><b>Material</b><b>Price</b><b></b></div>{% for p in req.parts %}<div class="partgrid"><input name="part_no[]" value="{{p.part_no}}"><input name="part_name[]" value="{{p.part_name}}" required><textarea name="part_description[]">{{p.description}}</textarea><input name="part_quantity[]" value="{{p.quantity}}"><input name="part_material[]" value="{{p.material}}"><input class="part-price" oninput="updateTotal(this.closest('.partsbox'))" name="part_price[]" type="number" step="any" value="{{p.price}}"><button type="button" class="danger" onclick="delPart(this)">×</button></div>{% endfor %}</div><button type="button" onclick="addPart('parts-{{req.job_id}}')">+ Add Part</button> <span>Parts Total: <b class="part-total">{{money(req.base_price)}}</b></span><div class="grid"><label>GST %<input name="gst_percent" type="number" step="any" value="{{req.gst_percent}}"></label><label>GST<select name="gst_enabled"><option value="yes">Enabled</option><option value="no">Disabled</option></select></label><label>WHT<select name="wht_enabled"><option value="use">Use Setting</option><option value="yes">Enable</option><option value="no">Disable</option></select></label><label>WHT %<input name="wht_percent" type="number" step="any" value="{{req.wht_percent}}"></label><label>Delivery / Schedule<input name="delivery_time" required placeholder="5 working days"></label><label>Payment Terms<select name="payment_terms"><option value="50_50">50% Advance + 50% before/at Delivery</option><option value="100_advance">100% Advance</option><option value="custom">Custom</option></select></label><label>Custom Payment Terms<textarea name="custom_payment_terms"></textarea></label><label>Material Specification<textarea name="material_specification"></textarea></label><label>Manufacturing Operations<textarea name="manufacturing_operations"></textarea></label><label>Finish / Surface Treatment<textarea name="finish_specification"></textarea></label><label>Inspection / QC<textarea name="inspection_qc"></textarea></label><label>Technical Specification<textarea name="technical_specification"></textarea></label><label>Technical Notes<textarea name="technical_notes"></textarea></label><label>Remarks<textarea name="remarks"></textarea></label></div><button>Save & Send Quotation</button></form><form action="/delete/{{req.job_id}}" method="POST" style="margin-top:5px"><button class="danger">Delete</button></form></div>{% else %}{% endif %}{% endfor %}</div>
<div id="progress" class="tab"><h3>Quotations Sent / Payment</h3>{% for req in requests %}{% if req.status=='Quotation Sent' %}<div class="card progress"><b>{{req.job_id}}</b> — {{req.company}} / {{req.name}}<p>Quotation: {{req.quotation_type}} | Parts: {{req.parts|length}} | Total: <b>{{money(req.net_payable)}}</b> | Delivery: {{req.delivery_time}}</p><a class="btnlink purple" href="/quotation/{{req.job_id}}.pdf" target="_blank">Quotation PDF</a><a class="btnlink" style="background:#25d366" href="{{req.wa_link}}" target="_blank">WhatsApp</a><a class="btnlink" style="background:#6c757d" href="/payment/{{req.job_id}}" target="_blank">Customer Payment Page</a><form action="/update/{{req.job_id}}" method="POST"><input type="hidden" name="quotation_type" value="{{req.quotation_type}}"><h4>Edit Parts / Price</h4><div class="partsbox" id="edit-{{req.job_id}}"><div class="partgrid small"><b>Part No.</b><b>Part Name</b><b>Description</b><b>Qty</b><b>Material</b><b>Price</b><b></b></div>{% for p in req.parts %}<div class="partgrid"><input name="part_no[]" value="{{p.part_no}}"><input name="part_name[]" value="{{p.part_name}}" required><textarea name="part_description[]">{{p.description}}</textarea><input name="part_quantity[]" value="{{p.quantity}}"><input name="part_material[]" value="{{p.material}}"><input name="part_price[]" type="number" step="any" value="{{p.price}}"><button type="button" class="danger" onclick="delPart(this)">×</button></div>{% endfor %}</div><button type="button" onclick="addPart('edit-{{req.job_id}}')">+ Add Part</button><div class="grid"><label>GST %<input name="gst_percent" type="number" step="any" value="{{req.gst_percent}}"></label><label>GST<select name="gst_enabled"><option value="yes" {% if req.gst_enabled %}selected{% endif %}>Enabled</option><option value="no" {% if not req.gst_enabled %}selected{% endif %}>Disabled</option></select></label><label>WHT<select name="wht_enabled"><option value="yes" {% if req.wht_enabled %}selected{% endif %}>Enabled</option><option value="no" {% if not req.wht_enabled %}selected{% endif %}>Disabled</option></select></label><label>WHT %<input name="wht_percent" type="number" step="any" value="{{req.wht_percent}}"></label><label>Delivery<input name="delivery_time" value="{{req.delivery_time}}" required></label><label>Payment Terms<select name="payment_terms"><option value="50_50" {% if req.payment_terms=='50_50' %}selected{% endif %}>50/50</option><option value="100_advance" {% if req.payment_terms=='100_advance' %}selected{% endif %}>100% Advance</option><option value="custom" {% if req.payment_terms=='custom' %}selected{% endif %}>Custom</option></select></label><label>Custom Terms<textarea name="custom_payment_terms">{{req.custom_payment_terms}}</textarea></label><label>RFQ No.<input name="rfq_no" value="{{req.rfq_no}}"></label><label>Reference No.<input name="reference_no" value="{{req.reference_no}}"></label><label>Project Title<input name="project_title" value="{{req.project_title}}"></label><label>Technical Specification<textarea name="technical_specification">{{req.technical_specification}}</textarea></label><label>Material Specification<textarea name="material_specification">{{req.material_specification}}</textarea></label><label>Manufacturing Operations<textarea name="manufacturing_operations">{{req.manufacturing_operations}}</textarea></label><label>Finish<textarea name="finish_specification">{{req.finish_specification}}</textarea></label><label>Inspection / QC<textarea name="inspection_qc">{{req.inspection_qc}}</textarea></label><label>Technical Notes<textarea name="technical_notes">{{req.technical_notes}}</textarea></label><label>Remarks<textarea name="remarks">{{req.remarks}}</textarea></label></div><button>Update & Recalculate</button></form><hr><b>Payment:</b> {{req.payment_status}} | Advance: {{money(req.advance_paid)}} | Balance Due: {{money(req.balance_due)}}<br>{% if req.payment_status=='Submitted' %}Ref: {{req.transaction_id}} | {{req.payment_date}} {% if req.payment_file %}<a href="/uploads/{{req.payment_file}}" target="_blank">Proof</a>{% endif %}<form action="/verify-payment/{{req.job_id}}" method="POST"><button class="info">Verify Submitted Payment</button></form>{% endif %}{% if req.payment_status=='Verified' %}<b style="color:#198754">Payment Verified</b>{% endif %}<form action="/complete/{{req.job_id}}" method="POST" style="margin-top:8px"><button class="info">Mark Completed / Invoice</button></form><form action="/delete/{{req.job_id}}" method="POST" style="margin-top:5px"><button class="danger">Delete</button></form></div>{% endif %}{% endfor %}</div>
<div id="done" class="tab"><h3>Completed Orders</h3>{% for req in requests %}{% if req.status=='Completed' %}<div class="card done"><b>{{req.job_id}}</b> — {{req.company}} / {{req.name}}<p>Invoice: {{req.invoice_number}} | Total: {{money(req.net_payable)}} | Payment: {{req.payment_status}}</p><a class="btnlink" style="background:#198754" href="/invoice/{{req.job_id}}.pdf" target="_blank">Invoice PDF</a><a class="btnlink" style="background:#25d366" href="{{req.wa_link}}" target="_blank">WhatsApp</a><form action="/delete/{{req.job_id}}" method="POST" style="margin-top:5px"><button class="danger">Delete</button></form></div>{% endif %}{% endfor %}</div></body></html>'''


def summary_data(reqs):
    return {'total_orders':len(reqs),'new':sum(r.get('status')=='New' for r in reqs),'in_progress':sum(r.get('status')=='Quotation Sent' for r in reqs),'completed':sum(r.get('status')=='Completed' for r in reqs),'total_quoted':sum(float(r.get('net_payable',0) or 0) for r in reqs),'total_paid':sum(float(r.get('payment_amount',0) or 0) for r in reqs if r.get('payment_status')=='Verified'),'pending_payment':sum(float(r.get('balance_due',r.get('net_payable',0)) or 0) for r in reqs if r.get('status')!='Completed'),'gst':sum(float(r.get('gst_amount',0) or 0) for r in reqs)}

@app.template_global('money')
def money_global(v): return money(v)
@app.template_global('payment_terms_text')
def payment_terms_global(req,settings): return payment_terms_text(req,settings)

@app.route('/',methods=['GET','POST'])
def client_form():
    if request.method=='POST':
        existing=load_requests(); today=datetime.now().strftime('%y%m%d'); nums=[]
        for r in existing:
            jid=str(r.get('job_id',''))
            if jid.startswith(f'DRD-{today}-'):
                try: nums.append(int(jid.split('-')[-1]))
                except Exception: pass
        job_id=f"DRD-{today}-{(max(nums) if nums else 0)+1:04d}"
        cc=request.form.get('country_code','92'); num=''.join(filter(str.isdigit,request.form.get('whatsapp_num',''))); num=num[1:] if num.startswith('0') else num
        settings=load_settings(); part_name=request.form.get('part_name','').strip(); qty=request.form.get('quantity','').strip(); mat=request.form.get('material','').strip(); desc=request.form.get('requirement','').strip()
        filename=''; file=request.files.get('drawing_file')
        if file and file.filename:
            safe=os.path.basename(file.filename); filename=f"{job_id}_{safe}"; file.save(os.path.join(UPLOAD_FOLDER,filename))
        new={'job_id':job_id,'company':request.form.get('company','').strip(),'name':request.form.get('name','').strip(),'whatsapp':cc+num,'part_name':part_name,'quantity':qty,'material':mat,'req_date':request.form.get('req_date',''),'requirement':desc,'file':filename,'part_no':'P-001','parts':[{'part_no':'P-001','part_name':part_name,'description':desc,'quantity':qty,'material':mat,'finish':'','operations':'','inspection':'','price':0.0}],'status':'New','base_price':0.0,'gst_percent':settings.get('gst_percent',18),'gst_enabled':settings.get('gst_enabled',True),'gst_amount':0,'wht_enabled':settings.get('wht_enabled',False),'wht_percent':settings.get('wht_percent',0),'wht_mode':settings.get('wht_mode','deduct'),'wht_amount':0,'gross_total':0,'total_price':0,'net_payable':0,'delivery_time':'Pending','remarks':'','quotation_type':'standard','payment_terms':settings.get('payment_terms','50_50'),'custom_payment_terms':'','customer_confirmed':False,'payment_status':'Not Submitted','payment_amount':0,'payment_date':'','transaction_id':'','payment_file':'','advance_paid':0,'advance_transaction_id':'','balance_transaction_id':'','advance_payment_file':'','balance_payment_file':'','advance_payment_status':'Not Submitted','balance_payment_status':'Not Submitted','invoice_number':'','invoice_date':'','rfq_no':'','reference_no':'','project_title':'','technical_specification':'','material_specification':'','manufacturing_operations':'','finish_specification':'','inspection_qc':'','technical_notes':'','time':datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        existing.append(new); save_all_requests(existing); return render_template_string(SUCCESS_PAGE,job_id=job_id)
    return render_template_string(INDEX_PAGE)

@app.route('/drd-secure-admin')
def admin_dashboard():
    reqs=load_requests(); return render_template_string(ADMIN_PAGE,requests=reqs,summary=summary_data(reqs),settings=load_settings())

@app.route('/settings',methods=['GET','POST'])
def settings_page():
    if request.method=='POST':
        s=load_settings()
        for k in ['company_name','address','phone','email','website','ntn','strn','bank_name','account_title','account_number','iban','payment_instructions','custom_payment_terms']: s[k]=request.form.get(k,'').strip()
        try:s['gst_percent']=float(request.form.get('gst_percent',18) or 0)
        except:s['gst_percent']=18.0
        try:s['wht_percent']=float(request.form.get('wht_percent',0) or 0)
        except:s['wht_percent']=0.0
        s['gst_enabled']=request.form.get('gst_enabled')=='on'; s['wht_enabled']=request.form.get('wht_enabled')=='on'; s['wht_mode']=request.form.get('wht_mode','deduct'); s['payment_terms']=request.form.get('payment_terms','50_50'); s['notification_numbers']=[request.form.get(f'notification_{i}','').strip() for i in range(3)]
        save_settings(s); return redirect(url_for('settings_page'))
    return render_template_string(SETTINGS_PAGE,s=load_settings())

@app.route('/update/<job_id>',methods=['POST'])
def update_job(job_id):
    all_reqs=load_requests(); settings=load_settings()
    for req in all_reqs:
        if req.get('job_id')!=job_id: continue
        pno=request.form.getlist('part_no[]'); pname=request.form.getlist('part_name[]'); pdesc=request.form.getlist('part_description[]'); pqty=request.form.getlist('part_quantity[]'); pmat=request.form.getlist('part_material[]'); pprice=request.form.getlist('part_price[]')
        parts=[]; n=max(len(pno),len(pname),len(pdesc),len(pqty),len(pmat),len(pprice),0)
        for i in range(n):
            name=pname[i].strip() if i<len(pname) else ''
            if not name: continue
            try: price=float(pprice[i]) if i<len(pprice) and pprice[i].strip() else 0.0
            except: price=0.0
            parts.append({'part_no':(pno[i].strip() if i<len(pno) and pno[i].strip() else f'P-{len(parts)+1:03d}'),'part_name':name,'description':pdesc[i].strip() if i<len(pdesc) else '','quantity':pqty[i].strip() if i<len(pqty) else '','material':pmat[i].strip() if i<len(pmat) else '','finish':'','operations':'','inspection':'','price':price})
        if not parts: parts=ensure_parts(req)
        base=sum(p['price'] for p in parts)
        try: gp=float(request.form.get('gst_percent',settings.get('gst_percent',18)) or 0)
        except: gp=18.0
        ge=request.form.get('gst_enabled','yes')!='no'; ws=request.form.get('wht_enabled','use'); we=settings.get('wht_enabled',False) if ws=='use' else ws=='yes'
        try: wp=float(request.form.get('wht_percent',settings.get('wht_percent',0)) or 0)
        except: wp=0.0
        wt=settings.get('wht_mode','deduct'); ga,gt,wa,tp,np=calculate_amounts(base,ge,gp,we,wp,wt)
        req['parts']=parts; req['base_price']=base; req['part_name']=parts[0]['part_name']; req['quantity']=parts[0]['quantity']; req['material']=parts[0]['material']; req['requirement']=parts[0]['description']; req['gst_percent']=gp; req['gst_enabled']=ge; req['gst_amount']=ga; req['wht_enabled']=we; req['wht_percent']=wp; req['wht_mode']=wt; req['wht_amount']=wa; req['gross_total']=gt; req['total_price']=tp; req['net_payable']=np; req['delivery_time']=request.form.get('delivery_time','').strip(); req['remarks']=request.form.get('remarks','').strip(); req['quotation_type']=request.form.get('quotation_type','standard'); req['payment_terms']=request.form.get('payment_terms',settings.get('payment_terms','50_50')); req['custom_payment_terms']=request.form.get('custom_payment_terms','').strip();
        for k in ['rfq_no','reference_no','project_title','technical_specification','material_specification','manufacturing_operations','finish_specification','inspection_qc','technical_notes']: req[k]=request.form.get(k,req.get(k,'' )).strip()
        req['advance_required']=round(np*(0.5 if req['payment_terms']=='50_50' else 1.0 if req['payment_terms']=='100_advance' else 0),2); req['balance_due']=max(0,round(np-float(req.get('advance_paid',0) or 0),2)); req['status']='Quotation Sent'; break
    save_all_requests(all_reqs); return redirect(url_for('admin_dashboard'))

@app.route('/payment/<job_id>')
def payment_confirmation(job_id):
    req=find_request(job_id)
    if not req:return 'Job not found',404
    return render_template_string(PAYMENT_PAGE,req=req,settings=load_settings(),money=money,payment_terms_text=payment_terms_text)

@app.route('/payment-submit/<job_id>',methods=['POST'])
def payment_submit(job_id):
    all_reqs=load_requests()
    for req in all_reqs:
        if req.get('job_id')!=job_id: continue
        stage=request.form.get('payment_stage','advance'); amount=float(request.form.get('payment_amount',0) or 0); tx=request.form.get('transaction_id','').strip(); date=request.form.get('payment_date',''); file=request.files.get('payment_file'); filename=''
        if file and file.filename:
            safe=os.path.basename(file.filename); filename=f"{job_id}_{stage.upper()}_{safe}"; file.save(os.path.join(UPLOAD_FOLDER,filename))
        req['customer_confirmed']=request.form.get('customer_confirmed')=='yes'; req['payment_amount']=amount; req['transaction_id']=tx; req['payment_date']=date; req['payment_file']=filename; req['payment_status']='Submitted'
        if stage in ('advance','full'): req['advance_transaction_id']=tx; req['advance_payment_file']=filename; req['advance_payment_status']='Submitted'
        if stage in ('balance','full'): req['balance_transaction_id']=tx; req['balance_payment_file']=filename; req['balance_payment_status']='Submitted'
        break
    save_all_requests(all_reqs); return render_template_string(PAYMENT_SUCCESS,job_id=job_id)

@app.route('/verify-payment/<job_id>',methods=['POST'])
def verify_payment(job_id):
    all_reqs=load_requests()
    for req in all_reqs:
        if req.get('job_id')!=job_id: continue
        req['payment_status']='Verified'; amount=float(req.get('payment_amount',0) or 0); req['advance_paid']=round(float(req.get('advance_paid',0) or 0)+amount,2); req['balance_due']=max(0,round(float(req.get('net_payable',0) or 0)-req['advance_paid'],2));
        if req['balance_due']<=0: req['balance_payment_status']='Verified'
        elif req.get('payment_terms')=='50_50': req['advance_payment_status']='Verified'
        break
    save_all_requests(all_reqs); return redirect(url_for('admin_dashboard'))

@app.route('/complete/<job_id>',methods=['POST'])
def complete_job(job_id):
    all_reqs=load_requests()
    for req in all_reqs:
        if req.get('job_id')==job_id:
            req['status']='Completed'; req['invoice_number']=req.get('invoice_number') or next_invoice_number(req); req['invoice_date']=datetime.now().strftime('%Y-%m-%d'); break
    save_all_requests(all_reqs); return redirect(url_for('admin_dashboard'))

@app.route('/quotation/<job_id>.pdf')
def quotation_pdf(job_id):
    req=find_request(job_id)
    if not req:return 'Job not found',404
    try:return send_file(build_document_pdf(req,'quotation'),mimetype='application/pdf',as_attachment=False,download_name=f'Quotation_{job_id}.pdf')
    except Exception as e:return f'PDF error: {e}',500

@app.route('/invoice/<job_id>.pdf')
def invoice_pdf(job_id):
    req=find_request(job_id)
    if not req:return 'Job not found',404
    try:return send_file(build_document_pdf(req,'invoice'),mimetype='application/pdf',as_attachment=False,download_name=f'Invoice_{job_id}.pdf')
    except Exception as e:return f'PDF error: {e}',500

@app.route('/delete/<job_id>',methods=['POST'])
def delete_job(job_id):
    reqs=[r for r in load_requests() if r.get('job_id')!=job_id]; save_all_requests(reqs); return redirect(url_for('admin_dashboard'))

@app.route('/uploads/<filename>')
def uploaded_file(filename): return send_from_directory(UPLOAD_FOLDER,filename)

if __name__=='__main__': app.run(debug=False,threaded=True,host='0.0.0.0',port=int(os.environ.get('PORT',5000)))
