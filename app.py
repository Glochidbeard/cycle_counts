from flask import Flask, request, jsonify, send_file, render_template
import csv
import io
import json
import os
from datetime import datetime
import zipfile

app = Flask(__name__)

SESSION_FILE = 'audit_session.json'


def load_session():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, 'r') as f:
            return json.load(f)
    return {'inventory': [], 'headers': [], 'audit': {}}


def save_session(session):
    with open(SESSION_FILE, 'w') as f:
        json.dump(session, f)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    content = file.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content))
    headers = list(reader.fieldnames)
    rows = [dict(r) for r in reader]
    session = {'inventory': rows, 'headers': headers, 'audit': {}}
    save_session(session)
    return jsonify({'success': True, 'count': len(rows)})


@app.route('/session')
def get_session_info():
    session = load_session()
    return jsonify({
        'loaded': bool(session['inventory']),
        'count': len(session['inventory']),
        'audited': len(session['audit'])
    })


@app.route('/areas')
def get_areas():
    session = load_session()
    areas = sorted(set(
        row['Locations'].split('>')[0].strip()
        for row in session['inventory']
        if row.get('Locations')
    ))
    audit = session['audit']
    result = {}
    for area in areas:
        area_rows = [
            r for r in session['inventory']
            if r.get('Locations', '').split('>')[0].strip() == area
        ]
        total = len(area_rows)
        done = sum(
            1 for r in area_rows
            if f"{r['Crop code']}||{r['Locations'].strip()}" in audit
        )
        result[area] = {'total': total, 'done': done}
    return jsonify(result)


@app.route('/locations/<area>')
def get_locations(area):
    session = load_session()
    audit = session['audit']
    loc_map = {}
    for row in session['inventory']:
        loc = row.get('Locations', '').strip()
        if loc.split('>')[0].strip() == area:
            loc_map.setdefault(loc, []).append(row)

    result = []
    for loc in sorted(loc_map.keys()):
        entries = []
        for plant in loc_map[loc]:
            key = f"{plant['Crop code']}||{loc}"
            entries.append({'plant': plant, 'audit': audit.get(key)})
        audited_count = sum(1 for e in entries if e['audit'])
        result.append({
            'location': loc,
            'entries': entries,
            'audited': audited_count,
            'total': len(entries)
        })
    return jsonify(result)


@app.route('/audit/save', methods=['POST'])
def save_audit():
    data = request.json
    session = load_session()
    key = f"{data['crop_code']}||{data['original_location']}"
    session['audit'][key] = {
        'crop_code': data['crop_code'],
        'original_location': data['original_location'],
        'entries': data['entries'],
        'timestamp': datetime.now().isoformat()
    }
    save_session(session)
    return jsonify({'success': True})


@app.route('/audit/delete', methods=['POST'])
def delete_audit():
    data = request.json
    session = load_session()
    key = f"{data['crop_code']}||{data['original_location']}"
    session['audit'].pop(key, None)
    save_session(session)
    return jsonify({'success': True})


@app.route('/export')
def export():
    session = load_session()
    inventory = session['inventory']
    headers = session['headers']
    audit = session['audit']

    output1_rows = []
    output2_rows = []

    for key, audit_entry in audit.items():
        crop_code = audit_entry['crop_code']
        orig_loc = audit_entry['original_location']
        timestamp = audit_entry['timestamp']

        original_row = next(
            (r for r in inventory
             if r['Crop code'] == crop_code and r['Locations'].strip() == orig_loc),
            None
        )

        try:
            orig_qty = int(original_row['Available qty']) if original_row else 0
        except (ValueError, TypeError):
            orig_qty = 0

        for entry in audit_entry['entries']:
            try:
                alive = int(entry.get('alive_qty', 0) or 0)
            except (ValueError, TypeError):
                alive = 0
            try:
                dead = int(entry.get('dead_qty', 0) or 0)
            except (ValueError, TypeError):
                dead = 0

            if original_row:
                out1 = dict(original_row)
                out1['Available qty'] = str(alive)
                out1['Locations'] = entry['location']
                output1_rows.append(out1)

            output2_rows.append({
                'Crop code': crop_code,
                'Species': original_row['Species'] if original_row else '',
                'Original location': orig_loc,
                'Audited location': entry['location'],
                'Original Plantiful qty': orig_qty,
                'Alive qty (field)': alive,
                'Dead qty': dead,
                'Total counted': alive + dead,
                'Discrepancy (alive vs Plantiful)': alive - orig_qty,
                'Audit timestamp': timestamp,
            })

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zf:
        o1 = io.StringIO()
        writer = csv.DictWriter(o1, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(output1_rows)
        zf.writestr('plantiful_import.csv', o1.getvalue())

        o2_fields = [
            'Crop code', 'Species', 'Original location', 'Audited location',
            'Original Plantiful qty', 'Alive qty (field)', 'Dead qty',
            'Total counted', 'Discrepancy (alive vs Plantiful)', 'Audit timestamp'
        ]
        o2 = io.StringIO()
        writer2 = csv.DictWriter(o2, fieldnames=o2_fields)
        writer2.writeheader()
        writer2.writerows(output2_rows)
        zf.writestr('audit_analytics.csv', o2.getvalue())

    zip_buffer.seek(0)
    filename = f'cycle_count_{datetime.now().strftime("%Y%m%d_%H%M")}.zip'
    return send_file(zip_buffer, mimetype='application/zip',
                     as_attachment=True, download_name=filename)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
