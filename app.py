import os
import json
import urllib.parse
from datetime import datetime
from flask import Flask, request, redirect, url_for, send_from_directory, render_template_string

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
REQUESTS_FILE = 'requests.json'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def load_requests():
    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, 'r') as f:
                data = json.load(f)
                for req in data:
                    if 'base_price' not in req:
                        req['base_price'] = 0
                    if 'gst_percent' not in req:
                        req['gst_percent'] = 18
                    if 'gst_amount' not in req:
                        req['gst_amount'] = 0
                    if 'total_price' not in req:
                        req['total_price'] = 0
                    if 'delivery_time' not in req:
                        req['delivery_time'] = 'Pending'
                    if 'remarks' not in req:
                        req['remarks'] = ''
                    if 'status' not in req:
                        req['status'] = 'New'

                    phone = ''.join(filter(str.isdigit, str(req.get('whatsapp', ''))))
                    
                    msg = (
                        f"Hello *{req.get('name')}*,\n\n"
                        f"Here is the quotation for your order *{req.get('job_id')}*:\n"
                        f"- Part: {req.get('part_name')}\n"
                        f"- Base Price: Rs. {req.get('base_price')}\n"
                        f"- GST ({req.get('gst_percent')}%): Rs. {req.get('gst_amount'):.2f}\n"
                        f"- *Total Price*: *Rs. {req.get('total_price'):.2f}*\n"
                        f"- Delivery Time: {req.get('delivery_time')}\n\n"
                        f"Kindly confirm so we can proceed!"
                    )
                    encoded_msg = urllib.parse.quote(msg)
                    req['wa_link'] = f"https://wa.me/{phone}?text={encoded_msg}"

                return sorted(data, key=lambda x: x.get('time', ''), reverse=True)
        except Exception as e:
            print("Error loading requests:", e)
    return []

def save_all_requests(reqs):
    clean_data = []
    for r in reqs:
        r_copy = r.copy()
        r_copy.pop('wa_link', None)
        clean_data.append(r_copy)
    with open(REQUESTS_FILE, 'w') as f:
        json.dump(clean_data, f, indent=4)

INDEX_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>DRD Manufacturing Solutions</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f4f9; margin: 0; padding: 20px; }
        .container { max-width: 600px; background: white; padding: 30px; margin: auto; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
        h2 { color: #333; text-align: center; }
        label { font-weight: bold; display: block; margin-top: 10px; color: #555; }
        input, select, textarea { width: 100%; padding: 8px; margin-top: 5px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background: #007bff; color: white; border: none; padding: 10px 15px; width: 100%; font-size: 16px; border-radius: 4px; cursor: pointer; }
        button:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="container">
        <h2>DRD Manufacturing Solutions - Order Portal</h2>
        <form method="POST" enctype="multipart/form-data">
            <label>Company Name:</label>
            <input type="text" name="company" required placeholder="e.g. DRD Engineering">
            
            <label>Client Name:</label>
            <input type="text" name="name" required placeholder="e.g. Daniyal Jameel">
            
            <label>WhatsApp Number (Country Code + Number):</label>
            <div style="display: flex; gap: 10px; margin-top: 5px; margin-bottom: 15px;">
                <select name="country_code" style="width: 35%; margin-bottom: 0;">
                    <option value="92" selected>🇵🇰 +92 (Pakistan)</option>
                    <option value="971">🇦🇪 +971 (UAE)</option>
                    <option value="966">🇸🇦 +966 (KSA)</option>
                    <option value="44">🇬🇧 +44 (UK)</option>
                    <option value="1">🇺🇸 +1 (USA)</option>
                </select>
                <input type="text" name="whatsapp_num" required placeholder="3175240272" style="width: 65%; margin-bottom: 0;">
            </div>
            
            <label>Part Name:</label>
            <input type="text" name="part_name" required placeholder="e.g. Bracket Machining">
            
            <label>Quantity:</label>
            <input type="number" name="quantity" required placeholder="e.g. 5">
            
            <label>Material:</label>
            <select name="material">
                <option value="Aluminum">Aluminum</option>
                <option value="Stainless Steel">Stainless Steel (SS 316 / 310)</option>
                <option value="Brass">Brass</option>
                <option value="PETG / PLA Filament">PETG / PLA Filament</option>
                <option value="ABS / TPU">ABS / TPU</option>
            </select>
            
            <label>Required Date:</label>
            <input type="date" name="req_date" required>
            
            <label>Detailed Requirements / Notes:</label>
            <textarea name="requirement" rows="4" placeholder="Specify tolerances, surface finish, etc."></textarea>
            
            <label>Upload Drawing / CAD File (.step, .sldprt, .dxf, .pdf, .zip):</label>
            <input type="file" name="drawing_file">
            
            <button type="submit">Submit Request</button>
        </form>
    </div>
</body>
</html>
'''

SUCCESS_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Request Submitted</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f4f9; text-align: center; padding-top: 50px; }
        .box { background: white; max-width: 500px; margin: auto; padding: 40px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
        h2 { color: #28a745; }
        a { color: #007bff; text-decoration: none; font-weight: bold; }
    </style>
</head>
<body>
    <div class="box">
        <h2>Successfully Submitted!</h2>
        <p>Your Job ID is: <strong>{{ job_id }}</strong></p>
        <p>We have received your request and will review it shortly.</p>
        <br>
        <a href="/">Submit Another Request</a>
    </div>
</body>
</html>
'''

ADMIN_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>DRD Admin Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f4f9; margin: 20px; }
        h2 { color: #333; }
        table { width: 100%; border-collapse: collapse; background: white; margin-top: 15px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
        th, td { padding: 10px; border: 1px solid #ddd; text-align: left; font-size: 13px; vertical-align: top; }
        th { background: #343a40; color: white; }
        tr:nth-child(even) { background: #f9f9f9; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .update-box { background: #f8f9fa; padding: 8px; border-radius: 4px; border: 1px solid #ccc; }
        input, textarea { width: 100%; padding: 5px; margin-top: 4px; margin-bottom: 6px; box-sizing: border-box; }
        button { background: #28a745; color: white; border: none; padding: 6px 12px; cursor: pointer; border-radius: 4px; font-weight: bold; }
        button:hover { background: #218838; }
        .btn-danger { background: #dc3545 !important; }
        .btn-danger:hover { background: #c82333 !important; }
        .btn-info { background: #17a2b8 !important; color: white !important; }
        .btn-info:hover { background: #138496 !important; }
        .section-new { border-left: 5px solid #dc3545; }
        .section-processed { border-left: 5px solid #ffc107; background: #fffdf5 !important; }
        .section-completed { border-left: 5px solid #28a745; background: #f1f8f5 !important; }
        
        /* Tab Bar Styling */
        .tab-bar { display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 2px solid #ccc; padding-bottom: 10px; }
        .tab-btn { background: #6c757d; color: white; border: none; padding: 10px 20px; font-size: 15px; border-radius: 4px; cursor: pointer; font-weight: bold; }
        .tab-btn:hover { background: #5a6268; }
        .tab-btn.active { background: #007bff; }
        
        .tab-content { display: none; }
        .tab-content.active { display: block; }
    </style>
    <script>
        function openTab(evt, tabName) {
            var i, tabcontent, tablinks;
            tabcontent = document.getElementsByClassName("tab-content");
            for (i = 0; i < tabcontent.length; i++) {
                tabcontent[i].style.display = "none";
            }
            tablinks = document.getElementsByClassName("tab-btn");
            for (i = 0; i < tablinks.length; i++) {
                tablinks[i].className = tablinks[i].className.replace(" active", "");
            }
            document.getElementById(tabName).style.display = "block";
            evt.currentTarget.className += " active";
        }
    </script>
</head>
<body>
    <h2>DRD Manufacturing Solutions - Admin Dashboard</h2>
    <p>Manage orders, calculate GST, track quotations, and complete projects.</p>

    <!-- Navigation Tab Buttons Bar -->
    <div class="tab-bar">
        <button class="tab-btn active" onclick="openTab(event, 'tab-new')">📥 New Requests</button>
        <button class="tab-btn" onclick="openTab(event, 'tab-in-progress')">⏳ Quotations Sent / In Progress</button>
        <button class="tab-btn" onclick="openTab(event, 'tab-completed')">✅ Completed Orders</button>
    </div>

    <!-- 1. NEW REQUESTS TAB -->
    <div id="tab-new" class="tab-content active">
        <h3 style="color: #dc3545; margin-top:0;">📥 New / Pending Requests</h3>
        <table>
            <tr>
                <th>Job ID & Time</th>
                <th>Client & Company</th>
                <th>Part & Material</th>
                <th>Qty & Date</th>
                <th>Drawing File</th>
                <th>Action / Quotation (with GST)</th>
            </tr>
            {% set new_count = 0 %}
            {% for req in requests %}
            {% if req.status == 'New' %}
            {% set new_count = new_count + 1 %}
            <tr class="section-new">
                <td><strong>{{ req.job_id }}</strong><br><small>{{ req.time }}</small></td>
                <td>{{ req.company }}<br><strong>{{ req.name }}</strong><br><small>WA: {{ req.whatsapp }}</small></td>
                <td>{{ req.part_name }}<br><small>Material: {{ req.material }}</small></td>
                <td>Qty: {{ req.quantity }}<br><small>Req: {{ req.req_date }}</small></td>
                <td>
                    {% if req.file %}<a href="/uploads/{{ req.file }}" target="_blank">Download File</a>{% else %}No File{% endif %}
                    <br><br><small><b>Notes:</b> {{ req.requirement }}</small>
                </td>
                <td>
                    <div class="update-box">
                        <form action="/update/{{ req.job_id }}" method="POST">
                            <label>Base Price (Rs):</label>
                            <input type="number" step="any" name="base_price" value="{{ req.base_price if req.base_price != 0 else '' }}" required placeholder="e.g. 5000">
                            <label>GST (%):</label>
                            <input type="number" step="any" name="gst_percent" value="{{ req.gst_percent if req.gst_percent != 0 else 18 }}" required placeholder="18">
                            <label>Delivery Time:</label>
                            <input type="text" name="delivery_time" value="{{ req.delivery_time if req.delivery_time != 'Pending' else '' }}" required placeholder="e.g. 3 Days">
                            <label>Remarks:</label>
                            <textarea name="remarks" placeholder="Optional notes">{{ req.remarks }}</textarea>
                            <button type="submit">Save & Send Quotation</button>
                        </form>
                    </div>
                    <br>
                    <form action="/delete/{{ req.job_id }}" method="POST" onsubmit="return confirm('Are you sure you want to delete this order?');">
                        <button type="submit" class="btn-danger">🗑️ Delete Order</button>
                    </form>
                </td>
            </tr>
            {% endif %}
            {% endfor %}
            {% if new_count == 0 %}
            <tr><td colspan="6" style="text-align: center; color: #777;">No new requests found.</td></tr>
            {% endif %}
        </table>
    </div>

    <!-- 2. IN PROGRESS / QUOTATION SENT TAB -->
    <div id="tab-in-progress" class="tab-content">
        <h3 style="color: #d39e00; margin-top:0;">⏳ Quotations Sent / In Progress</h3>
        <table>
            <tr>
                <th>Job ID & Time</th>
                <th>Client & Company</th>
                <th>Part & Material</th>
                <th>Quotation Details</th>
                <th>Actions (WhatsApp / Complete / Delete)</th>
            </tr>
            {% set prog_count = 0 %}
            {% for req in requests %}
            {% if req.status == 'Quotation Sent' %}
            {% set prog_count = prog_count + 1 %}
            <tr class="section-processed">
                <td><strong>{{ req.job_id }}</strong><br><small>{{ req.time }}</small></td>
                <td>{{ req.company }}<br><strong>{{ req.name }}</strong><br><small>WA: {{ req.whatsapp }}</small></td>
                <td>{{ req.part_name }}<br><small>({{ req.material }} - Qty: {{ req.quantity }})</small></td>
                <td>
                    <div class="update-box">
                        <form action="/update/{{ req.job_id }}" method="POST">
                            <label><b>Base Price (Rs):</b></label>
                            <input type="number" step="any" name="base_price" value="{{ req.base_price }}" required>
                            <label><b>GST (%):</b></label>
                            <input type="number" step="any" name="gst_percent" value="{{ req.gst_percent }}" required>
                            <label><b>Delivery:</b></label>
                            <input type="text" name="delivery_time" value="{{ req.delivery_time }}" required>
                            <label><b>Remarks:</b></label>
                            <textarea name="remarks">{{ req.remarks }}</textarea>
                            <button type="submit" style="background: #007bff;">Update & Re-calculate</button>
                        </form>
                    </div>
                    <hr style="margin: 5px 0;">
                    <small>
                        <b>Base:</b> Rs. {{ req.base_price }}<br>
                        <b>GST ({{ req.gst_percent }}%):</b> Rs. {{ "%.2f"|format(req.gst_amount) }}<br>
                        <b>Total Price:</b> <span style="color: #d9534f; font-weight: bold;">Rs. {{ "%.2f"|format(req.total_price) }}</span>
                    </small>
                </td>
                <td>
                    <span style="color: #d39e00; font-weight: bold;">Status: Quotation Sent</span><br><br>
                    <a href="{{ req.wa_link }}" target="_blank" style="background: #25d366; color: white; padding: 6px 10px; border-radius: 4px; display: inline-block; font-weight: bold; margin-bottom: 5px;">
                       💬 Open WhatsApp
                    </a><br>
                    <form action="/complete/{{ req.job_id }}" method="POST" style="display:inline;">
                        <button type="submit" class="btn-info" style="margin-bottom: 5px;">✅ Mark as Completed</button>
                    </form>
                    <form action="/delete/{{ req.job_id }}" method="POST" onsubmit="return confirm('Delete this order?');" style="display:inline;">
                        <button type="submit" class="btn-danger">🗑️ Delete</button>
                    </form>
                </td>
            </tr>
            {% endif %}
            {% endfor %}
            {% if prog_count == 0 %}
            <tr><td colspan="5" style="text-align: center; color: #777;">No quotations in progress.</td></tr>
            {% endif %}
        </table>
    </div>

    <!-- 3. COMPLETED ORDERS TAB -->
    <div id="tab-completed" class="tab-content">
        <h3 style="color: #28a745; margin-top:0;">✅ Completed Orders / Invoices</h3>
        <table>
            <tr>
                <th>Job ID & Time</th>
                <th>Client & Company</th>
                <th>Part & Material</th>
                <th>Final Pricing (Base + GST)</th>
                <th>Status / Delete</th>
            </tr>
            {% set comp_count = 0 %}
            {% for req in requests %}
            {% if req.status == 'Completed' %}
            {% set comp_count = comp_count + 1 %}
            <tr class="section-completed">
                <td><strong>{{ req.job_id }}</strong><br><small>{{ req.time }}</small></td>
                <td>{{ req.company }}<br><strong>{{ req.name }}</strong><br><small>WA: {{ req.whatsapp }}</small></td>
                <td>{{ req.part_name }}<br><small>({{ req.material }} - Qty: {{ req.quantity }})</small></td>
                <td>
                    <small>
                        <b>Base:</b> Rs. {{ req.base_price }}<br>
                        <b>GST ({{ req.gst_percent }}%):</b> Rs. {{ "%.2f"|format(req.gst_amount) }}<br>
                        <b>Total Invoiced:</b> <span style="color: #28a745; font-weight: bold;">Rs. {{ "%.2f"|format(req.total_price) }}</span>
                        {% if req.delivery_time %}<br><b>Delivery:</b> {{ req.delivery_time }}{% endif %}
                    </small>
                </td>
                <td>
                    <span style="color: green; font-weight: bold;">Completed ✅</span><br><br>
                    <a href="{{ req.wa_link }}" target="_blank" style="background: #25d366; color: white; padding: 4px 8px; border-radius: 4px; display: inline-block; font-size: 11px; font-weight: bold; margin-bottom: 5px;">
                       💬 WhatsApp
                    </a><br>
                    <form action="/delete/{{ req.job_id }}" method="POST" onsubmit="return confirm('Delete this completed order?');">
                        <button type="submit" class="btn-danger" style="font-size: 11px; padding: 4px 8px;">🗑️ Delete</button>
                    </form>
                </td>
            </tr>
            {% endif %}
            {% endfor %}
            {% if comp_count == 0 %}
            <tr><td colspan="5" style="text-align: center; color: #777;">No completed orders yet.</td></tr>
            {% endif %}
        </table>
    </div>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def client_form():
    if request.method == 'POST':
        date_str = datetime.now().strftime('%y%m%d')
        existing = load_requests()
        seq = len(existing) + 1
        job_id = f"DRD-{date_str}-{seq:04d}"
        
        company = request.form.get('company')
        name = request.form.get('name')
        
        country_code = request.form.get('country_code', '92')
        w_num = ''.join(filter(str.isdigit, request.form.get('whatsapp_num', '')))
        if w_num.startswith('0'):
            w_num = w_num[1:]
        whatsapp = country_code + w_num
        
        part_name = request.form.get('part_name')
        quantity = request.form.get('quantity')
        material = request.form.get('material')
        req_date = request.form.get('req_date')
        requirement = request.form.get('requirement')
        
        file = request.files.get('drawing_file')
        filename = ""
        if file and file.filename != '':
            filename = f"{job_id}_{file.filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
        new_entry = {
            "job_id": job_id,
            "company": company,
            "name": name,
            "whatsapp": whatsapp,
            "part_name": part_name,
            "quantity": quantity,
            "material": material,
            "req_date": req_date,
            "requirement": requirement,
            "file": filename,
            "status": "New",
            "base_price": 0,
            "gst_percent": 18,
            "gst_amount": 0,
            "total_price": 0,
            "delivery_time": "Pending",
            "remarks": "",
            "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        existing.append(new_entry)
        save_all_requests(existing)
        return render_template_string(SUCCESS_PAGE, job_id=job_id)
        
    return render_template_string(INDEX_PAGE)

# Secure Admin Route (Hidden from clients)
@app.route('/drd-secure-admin')
def admin_dashboard():
    all_reqs = load_requests()
    return render_template_string(ADMIN_PAGE, requests=all_reqs)

@app.route('/update/<job_id>', methods=['POST'])
def update_job(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req['job_id'] == job_id:
            try:
                base_price = float(request.form.get('base_price', 0))
                gst_percent = float(request.form.get('gst_percent', 18))
            except ValueError:
                base_price = 0
                gst_percent = 18
                
            gst_amount = (base_price * gst_percent) / 100.0
            total_price = base_price + gst_amount
            
            req['base_price'] = base_price
            req['gst_percent'] = gst_percent
            req['gst_amount'] = gst_amount
            req['total_price'] = total_price
            req['delivery_time'] = request.form.get('delivery_time')
            req['remarks'] = request.form.get('remarks')
            req['status'] = 'Quotation Sent'
            break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))

@app.route('/complete/<job_id>', methods=['POST'])
def complete_job(job_id):
    all_reqs = load_requests()
    for req in all_reqs:
        if req['job_id'] == job_id:
            req['status'] = 'Completed'
            break
    save_all_requests(all_reqs)
    return redirect(url_for('admin_dashboard'))

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
    app.run(debug=True, threaded=True, host='0.0.0.0', port=5000)