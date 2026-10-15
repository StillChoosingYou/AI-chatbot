"""Published pickup availability and atomic lost ID bookings (school time: UTC+8)."""
from datetime import datetime, timedelta, timezone

from flask import abort, g, jsonify, request, url_for


def school_now():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def slot_usage(db, date, time, excluding=None):
    return db.execute('''SELECT COUNT(*) FROM inventory WHERE schedule_type='Lost ID replacement'
        AND schedule_date=? AND schedule_time=? AND id_status='Lost' AND status!='Released'
        AND (? IS NULL OR id!=?)''', (date, time, excluding, excluding)).fetchone()[0]


def install_pickup(app, connect, record, scan_url):
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS pickup_slots (
            id INTEGER PRIMARY KEY, schedule_date TEXT NOT NULL, schedule_time TEXT NOT NULL,
            capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 500),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            UNIQUE(schedule_date,schedule_time))''')

    def available_slots(row):
        if row['id_status'] != 'Lost' or row['status'] != 'Ready for release':
            return []
        now = school_now().strftime('%Y-%m-%d %H:%M')
        with connect() as db:
            slots = [dict(slot) for slot in db.execute('''SELECT * FROM pickup_slots
                WHERE enabled=1 AND schedule_date||' '||schedule_time>?
                ORDER BY schedule_date,schedule_time''', (now,))]
            result = []
            for slot in slots:
                remaining = slot['capacity'] - slot_usage(db, slot['schedule_date'], slot['schedule_time'], row['id'])
                if remaining > 0:
                    result.append({'id':slot['id'], 'schedule_date':slot['schedule_date'], 'schedule_time':slot['schedule_time'],
                                   'remaining':remaining, 'current':row['schedule_type']=='Lost ID replacement'
                                   and row['schedule_date']==slot['schedule_date'] and row['schedule_time']==slot['schedule_time']})
        return result

    def offer(row):
        ready = row['id_status']=='Lost' and row['status']=='Ready for release'
        slots = available_slots(row)
        if not ready:
            reply = f'{row["name"]}: the replacement ID is not available for pickup yet (processing status: {row["status"]}). Available pickup dates will appear here once it is Ready for release.'
        else:
            reply = f'{row["name"]}: your replacement ID is available for pickup. '
            if row['schedule_type']=='Lost ID replacement' and row['schedule_date']:
                reply += f'Current appointment: {row["schedule_date"]} at {row["schedule_time"]} (school time, UTC+8). '
            else:
                reply += 'No replacement pickup appointment is recorded yet. '
            if slots and any(not slot['current'] for slot in slots):
                dates = ', '.join(f'{slot["schedule_date"]} at {slot["schedule_time"]}' for slot in slots)
                reply += f'Available dates and times (school time, UTC+8): {dates}. Choose a date and time below to book or reschedule.'
            elif slots:
                reply += 'Your current appointment remains booked. No other pickup dates are available yet.'
            else:
                reply += 'There are no available pickup dates right now. New dates will appear here when the coordinator publishes availability.'
        return {'record_id':row['id'], 'ready':ready, 'slots':slots, 'reply':reply,
                'can_book':g.user['role'] in ('student','coordinator')}

    @app.get('/api/pickup-offer')
    def student_pickup_offer():
        if g.user['role'] != 'student':
            abort(403)
        row = record(g.user['record_id'])
        return jsonify(offer=offer(row) if row['id_status']=='Lost' else None, record=row)

    @app.get('/api/inventory/<int:record_id>/pickup-slots')
    def record_pickup_slots(record_id):
        return jsonify(offer=offer(record(record_id)))

    @app.get('/api/pickup-slots')
    def pickup_slots():
        if g.user['role'] != 'coordinator':
            abort(403)
        now = school_now().strftime('%Y-%m-%d %H:%M')
        with connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM pickup_slots ORDER BY schedule_date,schedule_time')]
            for row in rows:
                row['booked'] = slot_usage(db, row['schedule_date'], row['schedule_time'])
                row['expired'] = row['schedule_date']+' '+row['schedule_time'] <= now
        return jsonify(slots=rows)

    @app.post('/api/pickup-slots')
    def publish_pickup_slot():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'schedule_date','schedule_time','capacity'}:
            return jsonify(error='Supply a pickup date, time, and capacity.'), 400
        date, time, capacity = payload['schedule_date'], payload['schedule_time'], payload['capacity']
        if not isinstance(date,str) or not isinstance(time,str) or type(capacity) is not int or not 1 <= capacity <= 500:
            return jsonify(error='Use a valid date/time and a capacity of 1–500 students.'), 400
        try:
            appointment = datetime.strptime(date+' '+time, '%Y-%m-%d %H:%M')
            if appointment.strftime('%Y-%m-%d %H:%M') != date+' '+time or appointment <= school_now():
                raise ValueError
        except ValueError:
            return jsonify(error='Choose a future pickup date (YYYY-MM-DD) and time (HH:MM), in school time (UTC+8).'), 400
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM pickup_slots WHERE schedule_date=? AND schedule_time=?', (date,time)).fetchone():
                return jsonify(error='That date and time already exist. Reopen the slot if it was closed.'), 409
            if slot_usage(db,date,time) > capacity:
                return jsonify(error='Capacity cannot be below the number of existing appointments at that time.'), 409
            cursor = db.execute('INSERT INTO pickup_slots (schedule_date,schedule_time,capacity) VALUES (?,?,?)', (date,time,capacity))
        return jsonify(id=cursor.lastrowid), 201

    @app.patch('/api/pickup-slots/<int:slot_id>')
    def toggle_pickup_slot(slot_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload,dict) or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
            return jsonify(error='Supply enabled as true or false.'), 400
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            slot = db.execute('SELECT * FROM pickup_slots WHERE id=?', (slot_id,)).fetchone()
            if slot is None:
                abort(404)
            if payload['enabled'] and slot['schedule_date']+' '+slot['schedule_time'] <= school_now().strftime('%Y-%m-%d %H:%M'):
                return jsonify(error='An expired pickup time cannot be reopened.'), 409
            db.execute('UPDATE pickup_slots SET enabled=? WHERE id=?', (int(payload['enabled']),slot_id))
        return jsonify(enabled=payload['enabled'])

    @app.post('/api/inventory/<int:record_id>/reschedule')
    def reschedule_lost_id(record_id):
        record(record_id)  # Includes student ownership checks.
        payload = request.get_json(silent=True)
        if not isinstance(payload,dict) or set(payload) != {'slot_id'} or type(payload['slot_id']) is not int:
            return jsonify(error='Choose one of the available pickup dates and times.'), 400
        with connect() as db:
            # Serialize competing bookings; a failed reschedule leaves the previous appointment intact.
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM inventory WHERE id=?', (record_id,)).fetchone()
            if current is None:
                abort(404)
            if current['id_status'] != 'Lost':
                return jsonify(error='This action is for a student whose ID is currently marked Lost.'), 409
            if current['status'] != 'Ready for release':
                return jsonify(error='The replacement ID is not available for pickup yet.'), 409
            slot = db.execute('SELECT * FROM pickup_slots WHERE id=?', (payload['slot_id'],)).fetchone()
            if slot is None or not slot['enabled'] or slot['schedule_date']+' '+slot['schedule_time'] <= school_now().strftime('%Y-%m-%d %H:%M'):
                return jsonify(error='That pickup date is no longer available. Please choose another date.'), 409
            if slot_usage(db,slot['schedule_date'],slot['schedule_time'],record_id) >= slot['capacity']:
                return jsonify(error='That pickup time is now full. Please choose another available date.'), 409
            db.execute("UPDATE inventory SET schedule_type='Lost ID replacement',schedule_date=?,schedule_time=? WHERE id=?",
                       (slot['schedule_date'],slot['schedule_time'],record_id))
        row = record(record_id)
        reply = f'{row["name"]}: replacement pickup confirmed for {row["schedule_date"]} at {row["schedule_time"]} (school time, UTC+8). Your existing QR code shows the updated appointment.'
        return jsonify(record=row, reply=reply, student_name=row['name'],
                       qr_url=url_for('qr_image',record_id=record_id), scan_url=scan_url(row['token']))

    return offer
