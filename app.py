from datetime import date, datetime, timedelta
import secrets
import re

import mysql.connector
from flask import Flask, flash, redirect, render_template, request, session, url_for, abort
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config

app = Flask(__name__)
app.config.from_object(Config)

BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
AVAILABILITY = ['Available', 'Maybe Available', 'Not Available']
PRIORITIES = ['Normal', 'Urgent', 'Critical']
STATUSES = ['Searching', 'Donor Found', 'Donor Responded', 'Hospital Confirmed', 'Completed', 'Cancelled']

# Red-cell donor compatibility: requested recipient group -> acceptable donor groups.
COMPATIBLE_DONORS = {
    'A+': ['A+', 'A-', 'O+', 'O-'],
    'A-': ['A-', 'O-'],
    'B+': ['B+', 'B-', 'O+', 'O-'],
    'B-': ['B-', 'O-'],
    'AB+': BLOOD_GROUPS,
    'AB-': ['AB-', 'A-', 'B-', 'O-'],
    'O+': ['O+', 'O-'],
    'O-': ['O-'],
}


def db():
    return mysql.connector.connect(
        host=app.config['MYSQL_HOST'],
        user=app.config['MYSQL_USER'],
        password=app.config['MYSQL_PASSWORD'],
        database=app.config['MYSQL_DATABASE'],
    )


def query(sql, params=(), one=False, commit=False):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        rows = cur.fetchone() if one else cur.fetchall()
        if commit:
            conn.commit()
        return rows
    finally:
        cur.close()
        conn.close()


def execute(sql, params=(), many=False):
    conn = db()
    cur = conn.cursor()
    try:
        if many:
            cur.executemany(sql, params)
        else:
            cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return query('SELECT * FROM users WHERE id=%s', (uid,), one=True)


def role_required(*roles):
    def decorator(view):
        from functools import wraps
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash('Please login to continue.', 'warning')
                return redirect(url_for('login'))
            if user['role'] not in roles:
                flash('You are not authorized to access this page.', 'danger')
                return redirect(url_for('home'))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def valid_mobile(value):
    return bool(re.fullmatch(r'[6-9]\d{9}', value or ''))


def valid_email(value):
    return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value or ''))


def validate_user_fields(form):
    required = [
        'full_name',
        'email',
        'mobile',
        'password',
        'confirm_password',
        'city',
        'area',
        'blood_group',
        'age'
    ]

    if any(not (form.get(k) or '').strip() for k in required):
        return 'Please fill all required fields.'

    if not valid_email(form['email'].strip()):
        return 'Please enter a valid email address.'

    if not valid_mobile(form['mobile'].strip()):
        return 'Please enter a valid mobile number.'

    if form['password'] != form['confirm_password']:
        return 'Passwords do not match.'

    if len(form['password']) < 6:
        return 'Password must be at least 6 characters.'

    if form['blood_group'] not in BLOOD_GROUPS:
        return 'Please select a valid blood group.'

    try:
        age = int(form['age'])
    except (TypeError, ValueError):
        return 'Please enter a valid age.'

    if age < 18 or age > 100:
        return 'Age must be between 18 and 100.'

    return None

def add_notification(user_id, title, message):
    execute('INSERT INTO notifications (user_id, title, message) VALUES (%s,%s,%s)', (user_id, title, message))


def request_code(request_id):
    year = datetime.now().year
    return f'BC-{year}-{request_id:05d}'


def stock_status(quantity):
    if quantity <= app.config['STOCK_CRITICAL']:
        return 'Critical'
    if quantity <= app.config['STOCK_LOW']:
        return 'Low'
    return 'Available'


def donor_matches(blood_group, city, area, availability=None):
    donors = COMPATIBLE_DONORS.get(blood_group, [blood_group])
    sql = '''
        SELECT d.id AS donor_id, u.id AS user_id, u.full_name, u.blood_group,
               u.city, u.area, u.mobile, d.availability, d.last_donation_date, d.verified
        FROM donors d JOIN users u ON u.id=d.user_id
        WHERE u.blood_group IN (%s) AND d.availability <> 'Not Available' AND d.verified=1
    ''' % ','.join(['%s'] * len(donors))
    params = donors
    if availability in AVAILABILITY:
        sql += ' AND d.availability=%s'
        params = params + [availability]
    sql += ''' ORDER BY CASE WHEN d.availability='Available' THEN 0 ELSE 1 END,
                      CASE WHEN u.city=%s AND u.area=%s THEN 0
                           WHEN u.city=%s THEN 1 ELSE 2 END,
                      u.full_name'''
    params = params + [city, area, city]
    return query(sql, tuple(params))


@app.context_processor
def inject_globals():
    user = current_user()
    unread = 0
    if user:
        row = query('SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s AND is_read=0', (user['id'],), one=True)
        unread = row['c']
    return {'current_user': user, 'unread_notifications': unread, 'blood_groups': BLOOD_GROUPS}


@app.route('/')
def home():
    stats = {
    'users': query(
        "SELECT COUNT(*) AS c FROM users WHERE role='USER'",
        one=True
    )['c'],

    'donors': query(
        "SELECT COUNT(*) AS c FROM donors WHERE verified=1",
        one=True
    )['c'],

    'hospitals': query(
        "SELECT COUNT(*) AS c FROM hospitals WHERE verified=1",
        one=True
    )['c'],

    'doctors': query(
        "SELECT COUNT(*) AS c FROM doctors WHERE verified=1",
        one=True
    )['c'],

    'requests': query(
        "SELECT COUNT(*) AS c FROM blood_requests",
        one=True
    )['c'],

    'completed': query(
        "SELECT COUNT(*) AS c FROM blood_requests WHERE status='Completed'",
        one=True
    )['c'],

    'camps': query(
        "SELECT COUNT(*) AS c FROM blood_camps WHERE camp_date >= CURDATE()",
        one=True
    )['c'],

    'requirements': query(
        """SELECT COUNT(*) AS c
           FROM blood_requests
           WHERE status IN (
               'Searching',
               'Donor Found',
               'Donor Responded',
               'Hospital Confirmed'
           )""",
        one=True
    )['c']
}
    recent = query('''SELECT br.*, u.full_name FROM blood_requests br JOIN users u ON u.id=br.user_id
                      WHERE br.status='Completed' ORDER BY br.created_at DESC LIMIT 5''')
    camps = query('''SELECT bc.*, h.hospital_name FROM blood_camps bc
                     LEFT JOIN hospitals h ON h.id=bc.hospital_id
                     WHERE bc.camp_date >= CURDATE() ORDER BY bc.camp_date, bc.camp_time LIMIT 5''')
    return render_template('public/home.html', stats=stats, recent=recent, camps=camps)


@app.route('/about')
def about():
    return render_template('public/info.html', title='About Us', page='about')


@app.route('/how-it-works')
def how_it_works():
    return render_template('public/info.html', title='How It Works', page='how_it_works')


@app.route('/blood-groups')
def blood_groups():
    return render_template('public/blood_groups.html')


@app.route('/donation-guide')
def donation_guide():
    return render_template('public/info.html', title='Blood Donation Guide', page='donation_guide')


@app.route('/eligibility')
def eligibility():
    return render_template('public/info.html', title='Eligibility Information', page='eligibility')


@app.route('/blood-donation-assistant')
def assistant():
    return render_template('public/info.html', title='Blood Donation Assistant', page='assistant')


@app.route('/faq')
def faq():
    return render_template('public/info.html', title='FAQ', page='faq')


@app.route('/contact')
def contact():
    return render_template('public/info.html', title='Contact Us', page='contact')


@app.route('/privacy')
def privacy():
    return render_template('public/info.html', title='Privacy Policy', page='privacy')


@app.route('/terms')
def terms():
    return render_template('public/info.html', title='Terms & Conditions', page='terms')


@app.route('/find-blood', methods=['GET', 'POST'])
def find_blood():
    group = request.values.get('blood_group', '')
    city = request.values.get('city', '').strip()
    area = request.values.get('area', '').strip()
    availability = request.values.get('availability', '')
    donors = []
    hospitals = []
    if group:
        donor_conditions = donor_matches(group, city or '', area or '', availability or None)
        donors = donor_conditions
        hospitals = query('''SELECT h.id, h.hospital_name, u.city, u.area, h.address, h.pincode,
                                    bs.quantity, bs.blood_group
                             FROM hospitals h JOIN users u ON u.id=h.user_id
                             LEFT JOIN blood_stock bs ON bs.hospital_id=h.id AND bs.blood_group=%s
                             WHERE h.verified=1 AND (u.city=%s OR %s='')
                             ORDER BY CASE WHEN bs.quantity IS NULL THEN 1 ELSE 0 END, bs.quantity DESC, h.hospital_name''',
                           (group, city, city))
    return render_template('public/find_blood.html', donors=donors, hospitals=hospitals,
                           selected_group=group, city=city, area=area, selected_availability=availability)


@app.route('/hospitals')
def hospitals():
    city = request.args.get('city', '').strip()
    hospitals = query('''SELECT h.*, u.city, u.area FROM hospitals h JOIN users u ON u.id=h.user_id
                         WHERE h.verified=1 AND (u.city=%s OR %s='') ORDER BY h.hospital_name''', (city, city))
    return render_template('public/hospitals.html', hospitals=hospitals, city=city)


@app.route('/camps', methods=['GET', 'POST'])
def camps():

    if request.method == 'POST':

        user = current_user()

        if not user:
            flash(
                'Please login before registering for a camp.',
                'warning'
            )
            return redirect(url_for('login'))

        camp_id = request.form.get('camp_id', type=int)

        camp = query(
            '''SELECT
                   id,
                   name,
                   camp_date,
                   location,
                   registration_open
               FROM blood_camps
               WHERE id=%s
                 AND camp_date >= CURDATE()
                 AND registration_open=1''',
            (camp_id,),
            one=True
        )

        if not camp:
            flash(
                'This camp is no longer available for registration.',
                'danger'
            )
            return redirect(url_for('camps'))

        try:

            execute(
                '''INSERT INTO camp_registrations
                   (camp_id, user_id)
                   VALUES (%s, %s)''',
                (
                    camp_id,
                    user['id']
                )
            )

            add_notification(
                user['id'],
                'Camp Registration Confirmed',
                (
                    f'You joined "{camp["name"]}". '
                    f'Camp date: {camp["camp_date"]}. '
                    f'Location: {camp["location"]}.'
                )
            )

            flash(
                'Camp registration completed successfully.',
                'success'
            )

        except mysql.connector.Error as e:

            if e.errno == 1062:
                flash(
                    'You are already registered for this camp.',
                    'info'
                )

            else:
                raise

        return redirect(url_for('camps'))

    rows = query(
        '''SELECT
               bc.*,
               h.hospital_name
           FROM blood_camps bc
           LEFT JOIN hospitals h
               ON h.id = bc.hospital_id
           WHERE bc.camp_date >= CURDATE()
           ORDER BY bc.camp_date ASC, bc.camp_time ASC'''
    )

    return render_template(
        'public/camps.html',
        camps=rows
    )


@app.route('/emergency', methods=['GET', 'POST'])
@role_required('USER', 'DONOR', 'HOSPITAL', 'ADMIN')
def emergency():
    if request.method == 'POST':
        user = current_user()
        group = request.form.get('blood_group')
        quantity = request.form.get('quantity', type=int)
        city = (request.form.get('city') or '').strip()
        area = (request.form.get('area') or '').strip()
        required_date = request.form.get('required_date')
        priority = request.form.get('priority', 'Critical')
        reason = (request.form.get('reason') or '').strip()
        hospital_id = request.form.get('hospital_id', type=int)
        if group not in BLOOD_GROUPS or quantity is None or quantity <= 0 or not city or not area or not required_date:
            flash('Please fill all emergency request fields correctly.', 'danger')
            return render_template('public/emergency.html', hospitals=hospital_list())
        request_id = execute('''INSERT INTO blood_requests
            (user_id,hospital_id,blood_group,quantity,required_date,city,area,priority,notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (user['id'], hospital_id, group, quantity, required_date, city, area, priority, reason))
        execute('INSERT INTO emergency_requests (request_id, emergency_reason) VALUES (%s,%s)', (request_id, reason))
        donors = donor_matches(group, city, area)

        if donors:
            execute('UPDATE blood_requests SET status=\'Donor Found\' WHERE id=%s', (request_id,))
            seen = set()
            for d in donors:
                if d['user_id'] not in seen:
                    add_notification(d['user_id'], 'Emergency blood request',
                                     f"A {group} blood request needs help in {city}/{area}. Request {request_code(request_id)}.")
                    seen.add(d['user_id'])
            flash(f'Emergency request {request_code(request_id)} created. Matching donors were notified.', 'success')
        else:
            flash(f'Emergency request {request_code(request_id)} created. No verified matching donors were found yet.', 'warning')
        return redirect(url_for('request_details', request_id=request_id))
    return render_template('public/emergency.html', hospitals=hospital_list())


def hospital_list():
    return query('''SELECT h.id, h.hospital_name, u.city FROM hospitals h JOIN users u ON u.id=h.user_id
                    WHERE h.verified=1 ORDER BY h.hospital_name''')


# ----------------------------- Authentication -----------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        err = validate_user_fields(request.form)

        if err:
            flash(err, 'danger')
            return render_template(
                'auth/register.html',
                blood_groups=BLOOD_GROUPS
            )

        f = request.form

        try:
            execute(
                '''INSERT INTO users
                   (full_name, email, mobile, password_hash,
                    city, area, blood_group, age, role)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'USER')''',
                (
                    f['full_name'].strip(),
                    f['email'].strip().lower(),
                    f['mobile'].strip(),
                    generate_password_hash(f['password']),
                    f['city'].strip(),
                    f['area'].strip(),
                    f['blood_group'],
                    int(f['age'])
                )
            )

            flash(
                'Registration successful. Please login.',
                'success'
            )

            return redirect(url_for('login'))

        except mysql.connector.Error as e:
            if e.errno == 1062:
                flash(
                    'This email or mobile number is already registered.',
                    'danger'
                )

                return render_template(
                    'auth/register.html',
                    blood_groups=BLOOD_GROUPS
                )

            raise

    return render_template(
        'auth/register.html',
        blood_groups=BLOOD_GROUPS
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identity = (request.form.get('identity') or '').strip()
        password = request.form.get('password') or ''
        user = query('SELECT * FROM users WHERE email=%s OR mobile=%s', (identity.lower(), identity), one=True)
        if not user or not check_password_hash(user['password_hash'], password):
            flash('Invalid email/mobile number or password.', 'danger')
            return render_template('auth/login.html')
        session.clear()
        session['user_id'] = user['id']
        session['role'] = user['role']
        target = {
            'USER': 'user_dashboard',
            'DONOR': 'donor_dashboard',
            'HOSPITAL': 'hospital_dashboard',
            'ADMIN': 'admin_dashboard',
        }.get(user['role'], 'home')
        return redirect(url_for(target))
    return render_template('auth/login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identity = (request.form.get('identity') or '').strip()
        user = query('SELECT id FROM users WHERE email=%s OR mobile=%s', (identity.lower(), identity), one=True)
        if not user:
            flash('No matching account was found.', 'danger')
        else:
            token = secrets.token_urlsafe(24)
            expires = datetime.now() + timedelta(minutes=15)
            execute('INSERT INTO password_resets (user_id,token,expires_at) VALUES (%s,%s,%s)', (user['id'], token, expires))
            flash('Demo reset link: ' + url_for('reset_password', token=token, _external=True), 'info')
        return redirect(url_for('forgot_password'))
    return render_template('auth/forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    reset = query('''SELECT * FROM password_resets WHERE token=%s AND used=0 AND expires_at>NOW()''', (token,), one=True)
    if not reset:
        flash('This reset link is invalid or expired.', 'danger')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        pw = request.form.get('password') or ''
        cpw = request.form.get('confirm_password') or ''
        if len(pw) < 6 or pw != cpw:
            flash('Passwords must match and be at least 6 characters.', 'danger')
            return render_template('auth/reset_password.html')
        execute('UPDATE users SET password_hash=%s WHERE id=%s', (generate_password_hash(pw), reset['user_id']))
        execute('UPDATE password_resets SET used=1 WHERE id=%s', (reset['id'],))
        flash('Password reset successful. Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('auth/reset_password.html')


# ----------------------------- User -----------------------------

@app.route('/user/dashboard')
@role_required('USER')
def user_dashboard():
    user = current_user()
    reqs = query('SELECT * FROM blood_requests WHERE user_id=%s ORDER BY created_at DESC LIMIT 6', (user['id'],))
    return render_template('user/dashboard.html', requests=reqs)


@app.route('/user/profile', methods=['GET', 'POST'])
@role_required('USER')
def user_profile():
    user = current_user()
    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        city = (request.form.get('city') or '').strip()
        area = (request.form.get('area') or '').strip()
        bg = request.form.get('blood_group')
        if not full_name or not city or not area or bg not in BLOOD_GROUPS:
            flash('Please enter valid profile details.', 'danger')
        else:
            execute('UPDATE users SET full_name=%s, city=%s, area=%s, blood_group=%s WHERE id=%s', (full_name, city, area, bg, user['id']))
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('user_profile'))
    return render_template('user/profile.html', user=current_user())


@app.route('/user/requests')
@role_required('USER')
def user_requests():
    rows = query('SELECT * FROM blood_requests WHERE user_id=%s ORDER BY created_at DESC', (current_user()['id'],))
    return render_template('user/requests.html', requests=rows)


@app.route('/user/request/create', methods=['GET', 'POST'])
@role_required('USER')
def create_request():

    if request.method == 'POST':

        user = current_user()

        group = request.form.get('blood_group', '').strip()
        quantity = request.form.get('required_quantity', type=int)
        required_date = request.form.get('required_date', '').strip()
        city = (request.form.get('city') or '').strip()
        area = (request.form.get('area') or '').strip()
        priority = request.form.get('priority', 'Normal').strip()
        notes = (request.form.get('notes') or '').strip()

        if (
            group not in BLOOD_GROUPS
            or not quantity
            or quantity <= 0
            or not required_date
            or not city
            or not area
            or priority not in ('Normal', 'Urgent', 'Critical')
        ):
            flash(
                'Please fill the request form correctly.',
                'danger'
            )

            return render_template(
                'user/create_request.html',
                user=user
            )

        rid = execute(
            '''INSERT INTO blood_requests
               (user_id, blood_group, quantity,
                required_date, city, area, priority,
                notes, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (
                user['id'],
                group,
                quantity,
                required_date,
                city,
                area,
                priority,
                notes or None,
                'Searching'
            )
        )

        donors = donor_matches(group, city, area)

        for donor in donors:
            add_notification(
                donor['user_id'],
                'New Blood Request',
                (
                    f'New {group} blood request needs {quantity} unit(s) '
                    f'in {city}, {area}. '
                    f'Request ID: BC-{rid:05d}. '
                    f'Please open BloodConnect to respond.'
                )
            )

        if donors:
            execute(
                '''UPDATE blood_requests
                   SET status='Donor Found'
                   WHERE id=%s''',
                (rid,)
            )

        flash(
            f'Blood request #{rid} created successfully.',
            'success'
        )

        return redirect(
            url_for(
                'request_details',
                request_id=rid
            )
        )

    return render_template(
        'user/create_request.html',
        user=current_user()
    )

@app.route('/user/emergency', methods=['GET', 'POST'])
@role_required('USER')
def user_emergency():
    return emergency()


@app.route('/request/<int:request_id>')
def request_details(request_id):
    user = current_user()
    req = query('''SELECT br.*, u.full_name requester_name, h.hospital_name
                   FROM blood_requests br JOIN users u ON u.id=br.user_id
                   LEFT JOIN hospitals h ON h.id=br.hospital_id WHERE br.id=%s''', (request_id,), one=True)
    if not req:
        abort(404)
    if user and user['role'] not in ('ADMIN',):
        allowed = req['user_id'] == user['id']
        if user['role'] == 'DONOR':
            d = query('SELECT id FROM donors WHERE user_id=%s', (user['id'],), one=True)
            allowed = bool(d)
        if user['role'] == 'HOSPITAL':
            h = query('SELECT id FROM hospitals WHERE user_id=%s', (user['id'],), one=True)
            allowed = allowed or bool(h and req['hospital_id'] == h['id'])
        if not allowed:
            flash('You are not authorized to access this request.', 'danger')
            return redirect(url_for('home'))
    matches = []
    if req['status'] not in ('Completed', 'Cancelled'):
        matches = donor_matches(req['blood_group'], req['city'], req['area'])
    responses = query('''SELECT rr.*, u.full_name, u.blood_group, u.city, u.area, d.availability
                        FROM request_responses rr JOIN donors d ON d.id=rr.donor_id
                        JOIN users u ON u.id=d.user_id WHERE rr.request_id=%s ORDER BY rr.responded_at DESC''', (request_id,))
    return render_template('user/request_details.html', req=req, matches=matches, responses=responses,
                           request_code=request_code(request_id))


@app.route('/user/notifications')
@role_required('USER')
def user_notifications():
    rows = query('SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC', (current_user()['id'],))
    return render_template('user/notifications.html', notifications=rows)


@app.route('/notifications/<int:notification_id>/read', methods=['POST'])
@role_required('USER', 'DONOR', 'HOSPITAL', 'ADMIN')
def mark_notification(notification_id):
    execute('UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s', (notification_id, current_user()['id']))
    return redirect(request.referrer or url_for('home'))


# ----------------------------- Donor -----------------------------

@app.route('/donor/register', methods=['GET', 'POST'])
def donor_register():
    if request.method == 'POST':
        err = validate_user_fields(request.form)

        if err:
            flash(err, 'danger')
            return render_template(
                'donor/register.html',
                blood_groups=BLOOD_GROUPS
            )

        f = request.form

        if f.get('availability') not in AVAILABILITY:
            flash('Please select availability.', 'danger')
            return render_template(
                'donor/register.html',
                blood_groups=BLOOD_GROUPS
            )

        conn = db()
        cur = conn.cursor()

        try:
            cur.execute(
                '''INSERT INTO users
                   (full_name, email, mobile, password_hash,
                    city, area, blood_group, age, role)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'DONOR')''',
                (
                    f['full_name'].strip(),
                    f['email'].strip().lower(),
                    f['mobile'].strip(),
                    generate_password_hash(f['password']),
                    f['city'].strip(),
                    f['area'].strip(),
                    f['blood_group'],
                    int(f['age'])
                )
            )

            uid = cur.lastrowid

            last_date = f.get('last_donation_date') or None

            cur.execute(
                '''INSERT INTO donors
                   (user_id, last_donation_date, availability, verified)
                   VALUES (%s, %s, %s, 1)''',
                (
                    uid,
                    last_date,
                    f['availability']
                )
            )

            conn.commit()

        except mysql.connector.Error as e:
            conn.rollback()

            if e.errno == 1062:
                flash(
                    'This email or mobile number is already registered.',
                    'danger'
                )

                return render_template(
                    'donor/register.html',
                    blood_groups=BLOOD_GROUPS
                )

            raise

        finally:
            cur.close()
            conn.close()

        flash(
            'Donor registration successful. Please login.',
            'success'
        )

        return redirect(url_for('login'))

    return render_template(
        'donor/register.html',
        blood_groups=BLOOD_GROUPS
    )


@app.route('/donor/dashboard')
@role_required('DONOR')
def donor_dashboard():
    user = current_user()
    donor = query('SELECT * FROM donors WHERE user_id=%s', (user['id'],), one=True)
    notifications = query('SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 5', (user['id'],))
    donations = query('SELECT * FROM donations WHERE donor_id=%s ORDER BY donation_date DESC LIMIT 5', (donor['id'],))
    return render_template('donor/dashboard.html', donor=donor, notifications=notifications, donations=donations)


@app.route('/donor/availability', methods=['GET', 'POST'])
@role_required('DONOR')
def donor_availability():
    donor = query('SELECT * FROM donors WHERE user_id=%s', (current_user()['id'],), one=True)
    if request.method == 'POST':
        availability = request.form.get('availability')
        if availability not in AVAILABILITY:
            flash('Invalid availability.', 'danger')
        else:
            execute('UPDATE donors SET availability=%s WHERE id=%s', (availability, donor['id']))
            flash('Availability updated.', 'success')
            return redirect(url_for('donor_availability'))
    return render_template('donor/availability.html', donor=donor)


@app.route('/donor/requests')
@role_required('DONOR')
def donor_requests():
    user = current_user()
    donor = query('SELECT * FROM donors WHERE user_id=%s', (user['id'],), one=True)
    if not donor:
        abort(403)
    compatible_requests = [g for g, donors in COMPATIBLE_DONORS.items() if current_user()['blood_group'] in donors]
    placeholders = ','.join(['%s'] * len(compatible_requests))
    rows = query(f'''SELECT br.*, u.full_name requester_name FROM blood_requests br
                    JOIN users u ON u.id=br.user_id
                    LEFT JOIN request_responses rr ON rr.request_id=br.id AND rr.donor_id=%s
                    WHERE br.status NOT IN ('Completed','Cancelled') AND br.blood_group IN ({placeholders})
                      AND rr.id IS NULL
                    ORDER BY CASE WHEN br.priority='Critical' THEN 0 WHEN br.priority='Urgent' THEN 1 ELSE 2 END,
                             CASE WHEN br.city=%s AND br.area=%s THEN 0 WHEN br.city=%s THEN 1 ELSE 2 END,
                             br.created_at DESC''',
                 tuple([donor['id']] + compatible_requests + [current_user()['city'], current_user()['area'], current_user()['city']]))
    return render_template('donor/requests.html', requests=rows, donor=donor)


@app.route('/donor/request/<int:request_id>/<action>', methods=['POST'])
@role_required('DONOR')
def donor_respond(request_id, action):
    if action not in ('accept', 'decline'):
        abort(400)
    donor = query('SELECT * FROM donors WHERE user_id=%s', (current_user()['id'],), one=True)
    req = query('SELECT * FROM blood_requests WHERE id=%s', (request_id,), one=True)
    if not donor or not req or req['status'] in ('Completed', 'Cancelled'):
        flash('Request is no longer available.', 'danger')
        return redirect(url_for('donor_requests'))
    response = 'Accepted' if action == 'accept' else 'Declined'
    try:
        execute('INSERT INTO request_responses (request_id,donor_id,response) VALUES (%s,%s,%s)', (request_id, donor['id'], response))
    except mysql.connector.Error as e:
        if e.errno == 1062:
            execute('UPDATE request_responses SET response=%s, responded_at=NOW() WHERE request_id=%s AND donor_id=%s', (response, request_id, donor['id']))
        else:
            raise
    execute('UPDATE blood_requests SET status=%s WHERE id=%s', ('Donor Responded' if action == 'accept' else req['status'], request_id))
    add_notification(req['user_id'], 'Donor response', f'A donor has {response.lower()} request {request_code(request_id)}.')
    flash(f'You {response.lower()} the request.', 'success')
    return redirect(url_for('request_details', request_id=request_id))


@app.route('/donor/history')
@role_required('DONOR')
def donor_history():
    donor = query('SELECT * FROM donors WHERE user_id=%s', (current_user()['id'],), one=True)
    donations = query('SELECT * FROM donations WHERE donor_id=%s ORDER BY donation_date DESC', (donor['id'],))
    count = len(donations)
    badges = []
    if count >= 1: badges.append('First Donation')
    if count >= 3: badges.append('3 Donations')
    if count >= 5: badges.append('5 Donations')
    if count >= 10: badges.append('Regular Donor')
    for b in badges:
        execute('INSERT IGNORE INTO donor_badges (donor_id,badge_name) VALUES (%s,%s)', (donor['id'], b))
    db_badges = query('SELECT * FROM donor_badges WHERE donor_id=%s ORDER BY awarded_at DESC', (donor['id'],))
    return render_template('donor/history.html', donations=donations, badges=db_badges)


@app.route('/donor/notifications')
@role_required('DONOR')
def donor_notifications():
    rows = query('SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC', (current_user()['id'],))
    return render_template('donor/notifications.html', notifications=rows)


# ----------------------------- Hospital -----------------------------

@app.route('/hospital/register', methods=['GET', 'POST'])
def hospital_register():
    if request.method == 'POST':
        f = request.form
        full_name = (f.get('contact_person') or '').strip()
        email = (f.get('email') or '').strip().lower()
        mobile = (f.get('mobile') or '').strip()
        password = f.get('password') or ''
        city = (f.get('city') or '').strip()
        area = (f.get('area') or '').strip()
        group = f.get('blood_group')
        hospital_name = (f.get('hospital_name') or '').strip()
        reg_no = (f.get('registration_no') or '').strip()
        address = (f.get('address') or '').strip()
        if not all([full_name,email,mobile,password,city,area,group,hospital_name,reg_no,address]) or not valid_email(email) or not valid_mobile(mobile) or len(password)<6 or group not in BLOOD_GROUPS:
            flash('Please fill all hospital registration fields correctly.', 'danger')
            return render_template('hospital/register.html')
        conn = db(); cur = conn.cursor()
        try:
            cur.execute('''INSERT INTO users (full_name,email,mobile,password_hash,city,area,blood_group,role)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,'HOSPITAL')''',
                        (full_name,email,mobile,generate_password_hash(password),city,area,group))
            uid = cur.lastrowid
            cur.execute('''INSERT INTO hospitals (user_id,hospital_name,registration_no,address,pincode,contact_person,verified)
                           VALUES (%s,%s,%s,%s,%s,%s,0)''',
                        (uid,hospital_name,reg_no,address,(f.get('pincode') or '').strip(),full_name))
            conn.commit()
        except mysql.connector.Error as e:
            conn.rollback()
            if e.errno == 1062:
                flash('Email, mobile number, or registration number is already registered.', 'danger')
                return render_template('hospital/register.html')
            raise
        finally:
            cur.close(); conn.close()
        flash('Hospital registered. An admin must verify the hospital before stock is shown publicly.', 'success')
        return redirect(url_for('login'))
    return render_template('hospital/register.html')


@app.route('/hospital/dashboard')
@role_required('HOSPITAL')
def hospital_dashboard():
    h = query('''SELECT h.*, u.city, u.area FROM hospitals h JOIN users u ON u.id=h.user_id WHERE h.user_id=%s''', (current_user()['id'],), one=True)
    stock = query('SELECT * FROM blood_stock WHERE hospital_id=%s ORDER BY blood_group', (h['id'],)) if h else []
    requirements = query('SELECT * FROM blood_requests WHERE hospital_id=%s ORDER BY created_at DESC LIMIT 8', (h['id'],)) if h else []
    return render_template('hospital/dashboard.html', hospital=h, stock=stock, requirements=requirements, stock_status=stock_status)


@app.route('/hospital/stock')
@role_required('HOSPITAL')
def hospital_stock():
    h = query('SELECT * FROM hospitals WHERE user_id=%s', (current_user()['id'],), one=True)
    rows = query('SELECT * FROM blood_stock WHERE hospital_id=%s ORDER BY blood_group', (h['id'],))
    mapped = {r['blood_group']: r for r in rows}
    return render_template('hospital/stock.html', stock=mapped, stock_status=stock_status)


@app.route('/hospital/stock/add', methods=['GET','POST'])
@role_required('HOSPITAL')
def add_stock():
    h = query('SELECT * FROM hospitals WHERE user_id=%s', (current_user()['id'],), one=True)
    if not h:
        abort(403)
    if request.method == 'POST':
        group = request.form.get('blood_group')
        quantity = request.form.get('quantity', type=int)
        if group not in BLOOD_GROUPS or quantity is None or quantity < 0:
            flash('Enter a valid blood group and non-negative quantity.', 'danger')
            return render_template('hospital/add_stock.html')
        execute('''INSERT INTO blood_stock (hospital_id,blood_group,quantity) VALUES (%s,%s,%s)
                   ON DUPLICATE KEY UPDATE quantity=VALUES(quantity), updated_at=NOW()''', (h['id'],group,quantity))
        flash(f'{group} stock updated.', 'success')
        return redirect(url_for('hospital_stock'))
    return render_template('hospital/add_stock.html')


@app.route('/hospital/requirements')
@role_required('HOSPITAL')
def hospital_requirements():
    h = query('SELECT * FROM hospitals WHERE user_id=%s', (current_user()['id'],), one=True)
    rows = query('SELECT * FROM blood_requests WHERE hospital_id=%s ORDER BY created_at DESC', (h['id'],))
    return render_template('hospital/requirements.html', requirements=rows)


@app.route('/hospital/requirements/create', methods=['GET','POST'])
@role_required('HOSPITAL')
def create_requirement():
    h = query('SELECT * FROM hospitals WHERE user_id=%s', (current_user()['id'],), one=True)
    u = current_user()
    if request.method == 'POST':
        group = request.form.get('blood_group')
        qty = request.form.get('quantity', type=int)
        req_date = request.form.get('required_date')
        priority = request.form.get('priority')
        notes = (request.form.get('notes') or '').strip()
        if group not in BLOOD_GROUPS or not qty or qty <= 0 or not req_date or priority not in PRIORITIES:
            flash('Please enter a valid blood requirement.', 'danger')
            return render_template('hospital/create_requirement.html')
        rid = execute('''INSERT INTO blood_requests
                        (user_id,hospital_id,blood_group,quantity,required_date,city,area,priority,notes)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                      (u['id'],h['id'],group,qty,req_date,u['city'],u['area'],priority,notes))
        flash(f'Blood requirement {request_code(rid)} created.', 'success')
        return redirect(url_for('hospital_requirements'))
    return render_template('hospital/create_requirement.html')


@app.route('/hospital/requests')
@role_required('HOSPITAL')
def hospital_requests():
    h = query('SELECT * FROM hospitals WHERE user_id=%s', (current_user()['id'],), one=True)
    rows = query('''SELECT br.*, u.full_name requester_name FROM blood_requests br JOIN users u ON u.id=br.user_id
                    WHERE br.hospital_id=%s ORDER BY br.created_at DESC''', (h['id'],))
    return render_template('hospital/requests.html', requests=rows)


@app.route('/hospital/request/<int:request_id>/<action>', methods=['POST'])
@role_required('HOSPITAL')
def hospital_request_action(request_id, action):
    if action not in ('confirm','complete','cancel'):
        abort(400)
    h = query('SELECT * FROM hospitals WHERE user_id=%s', (current_user()['id'],), one=True)
    req = query('SELECT * FROM blood_requests WHERE id=%s AND hospital_id=%s', (request_id,h['id']), one=True)
    if not req:
        flash('Request not found for your hospital.', 'danger')
        return redirect(url_for('hospital_requests'))
    status_map = {'confirm':'Hospital Confirmed','complete':'Completed','cancel':'Cancelled'}
    execute('UPDATE blood_requests SET status=%s WHERE id=%s', (status_map[action],request_id))
    add_notification(req['user_id'], 'Request status updated', f'Request {request_code(request_id)} is now {status_map[action]}.')
    flash(f'Request marked as {status_map[action]}.', 'success')
    return redirect(url_for('hospital_requests'))


@app.route('/hospital/notifications')
@role_required('HOSPITAL')
def hospital_notifications():
    rows = query('SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC', (current_user()['id'],))
    return render_template('hospital/notifications.html', notifications=rows)


# ----------------------------- Admin -----------------------------

@app.route('/admin/dashboard')
@role_required('ADMIN')
def admin_dashboard():
    stats = {
        'users': query("SELECT COUNT(*) c FROM users WHERE role='USER'", one=True)['c'],
        'donors': query("SELECT COUNT(*) c FROM donors", one=True)['c'],
        'hospitals': query("SELECT COUNT(*) c FROM hospitals", one=True)['c'],
        'requests': query("SELECT COUNT(*) c FROM blood_requests", one=True)['c'],
        'emergency': query("SELECT COUNT(*) c FROM emergency_requests", one=True)['c'],
        'completed': query("SELECT COUNT(*) c FROM blood_requests WHERE status='Completed'", one=True)['c'],
        'camps': query("SELECT COUNT(*) c FROM blood_camps", one=True)['c'],
        'pending_hospitals': query("SELECT COUNT(*) c FROM hospitals WHERE verified=0", one=True)['c'],
        'pending_donors': query("SELECT COUNT(*) c FROM donors WHERE verified=0", one=True)['c'],
    }
    return render_template('admin/dashboard.html', stats=stats)


@app.route('/admin/users')
@role_required('ADMIN')
def admin_users():
    rows = query('SELECT id,full_name,email,mobile,city,area,blood_group,role,is_verified,created_at FROM users ORDER BY created_at DESC')
    return render_template('admin/users.html', users=rows)


@app.route('/admin/donors', methods=['GET','POST'])
@role_required('ADMIN')
def admin_donors():
    if request.method == 'POST':
        donor_id = request.form.get('donor_id', type=int)
        action = request.form.get('action')
        if action in ('verify','unverify'):
            val = 1 if action == 'verify' else 0
            execute('UPDATE donors SET verified=%s WHERE id=%s', (val,donor_id))
            flash('Donor verification updated.', 'success')
        return redirect(url_for('admin_donors'))
    rows = query('''SELECT d.id,u.full_name,u.email,u.mobile,u.city,u.area,u.blood_group,d.availability,d.verified
                    FROM donors d JOIN users u ON u.id=d.user_id ORDER BY d.created_at DESC''')
    return render_template('admin/donors.html', donors=rows)


@app.route('/admin/hospitals', methods=['GET','POST'])
@role_required('ADMIN')
def admin_hospitals():
    if request.method == 'POST':
        hid = request.form.get('hospital_id', type=int)
        action = request.form.get('action')
        if action in ('verify','unverify'):
            val = 1 if action == 'verify' else 0
            execute('UPDATE hospitals SET verified=%s WHERE id=%s', (val,hid))
            flash('Hospital verification updated.', 'success')
        return redirect(url_for('admin_hospitals'))
    rows = query('''SELECT h.*,u.city,u.area,u.email,u.mobile FROM hospitals h JOIN users u ON u.id=h.user_id
                    ORDER BY h.created_at DESC''')
    return render_template('admin/hospitals.html', hospitals=rows)


@app.route('/admin/requests')
@role_required('ADMIN')
def admin_requests():
    rows = query('''SELECT br.*,u.full_name requester_name,h.hospital_name FROM blood_requests br
                    JOIN users u ON u.id=br.user_id LEFT JOIN hospitals h ON h.id=br.hospital_id
                    ORDER BY br.created_at DESC''')
    return render_template('admin/requests.html', requests=rows, request_code=request_code)


@app.route('/admin/camps', methods=['GET','POST'])
@role_required('ADMIN')
def admin_camps():
    if request.method == 'POST':
        name=(request.form.get('name') or '').strip(); camp_date=request.form.get('camp_date'); camp_time=request.form.get('camp_time')
        location=(request.form.get('location') or '').strip(); organizer=(request.form.get('organizer') or '').strip()
        hospital_id=request.form.get('hospital_id',type=int); description=(request.form.get('description') or '').strip(); contact=(request.form.get('contact_information') or '').strip()
        if not all([name,camp_date,camp_time,location,organizer]):
            flash('Please fill the required camp fields.', 'danger')
        else:
            execute('''INSERT INTO blood_camps (name,camp_date,camp_time,location,organizer,hospital_id,description,contact_information)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''', (name,camp_date,camp_time,location,organizer,hospital_id,description,contact))
            flash('Blood camp created.', 'success')
        return redirect(url_for('admin_camps'))
    rows=query('''SELECT bc.*,h.hospital_name FROM blood_camps bc LEFT JOIN hospitals h ON h.id=bc.hospital_id
                  ORDER BY bc.camp_date DESC,bc.camp_time DESC''')
    return render_template('admin/camps.html', camps=rows, hospitals=hospital_list())


# Demo admin creation helper: run once from Python shell if needed.
@app.cli.command('create-admin')
def create_admin():
    """Create an administrator account interactively from the terminal."""
    print('Create BloodConnect admin account')
    full_name = input('Full name: ').strip()
    email = input('Email: ').strip().lower()
    mobile = input('Mobile (10 digits): ').strip()
    password = input('Password (6+ chars): ')
    city = input('City: ').strip() or 'Pune'
    area = input('Area: ').strip() or 'Central'
    bg = input('Blood group (e.g. O+): ').strip().upper()
    if not all([full_name, email, mobile, password, city, area]) or bg not in BLOOD_GROUPS or not valid_mobile(mobile):
        print('Invalid input.')
        return
    try:
        uid = execute('''INSERT INTO users (full_name,email,mobile,password_hash,city,area,blood_group,role,is_verified)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,'ADMIN',1)''',
                      (full_name,email,mobile,generate_password_hash(password),city,area,bg))
        execute('INSERT INTO admin_users (user_id) VALUES (%s)', (uid,))
        print('Admin created successfully.')
    except mysql.connector.Error as e:
        print('Could not create admin:', e)


@app.errorhandler(404)
def not_found(_):
    return render_template('public/error.html', code=404, message='The page you are looking for was not found.'), 404


@app.errorhandler(500)
def server_error(_):
    return render_template('public/error.html', code=500, message='Something went wrong on the server. Check the Flask terminal for details.'), 500




# ----------------------------- Convenience pages required by the project brief -----------------------------

@app.route('/user/history')
@role_required('USER')
def user_history():
    rows = query("SELECT * FROM blood_requests WHERE user_id=%s AND status IN ('Completed','Cancelled') ORDER BY created_at DESC", (current_user()['id'],))
    return render_template('user/history.html', requests=rows)


@app.route('/user/track/<int:request_id>')
@role_required('USER')
def track_request(request_id):
    return redirect(url_for('request_details', request_id=request_id))


@app.route('/user/settings')
@role_required('USER')
def user_settings():
    return render_template('user/settings.html')


@app.route('/donor/profile')
@role_required('DONOR')
def donor_profile():
    donor = query('''SELECT d.*,u.full_name,u.email,u.mobile,u.city,u.area,u.blood_group
                     FROM donors d JOIN users u ON u.id=d.user_id WHERE d.user_id=%s''', (current_user()['id'],), one=True)
    return render_template('donor/profile.html', donor=donor)


@app.route('/donor/edit-profile', methods=['GET','POST'])
@role_required('DONOR')
def donor_edit_profile():
    user = current_user()
    if request.method == 'POST':
        name=(request.form.get('full_name') or '').strip(); city=(request.form.get('city') or '').strip(); area=(request.form.get('area') or '').strip(); bg=request.form.get('blood_group')
        if not name or not city or not area or bg not in BLOOD_GROUPS:
            flash('Please enter valid profile details.', 'danger')
        else:
            execute('UPDATE users SET full_name=%s,city=%s,area=%s,blood_group=%s WHERE id=%s',(name,city,area,bg,user['id']))
            flash('Donor profile updated.', 'success')
            return redirect(url_for('donor_profile'))
    return render_template('donor/edit_profile.html', user=user)


@app.route('/donor/donation/<int:donation_id>')
@role_required('DONOR')
def donation_details(donation_id):
    donor = query('SELECT id FROM donors WHERE user_id=%s',(current_user()['id'],),one=True)
    donation = query('SELECT * FROM donations WHERE id=%s AND donor_id=%s',(donation_id,donor['id']),one=True)
    if not donation:
        flash('Donation record not found.', 'danger'); return redirect(url_for('donor_history'))
    return render_template('donor/donation_details.html', donation=donation)


@app.route('/hospital/profile')
@role_required('HOSPITAL')
def hospital_profile():
    h = query('SELECT h.*,u.city,u.area,u.email,u.mobile FROM hospitals h JOIN users u ON u.id=h.user_id WHERE h.user_id=%s',(current_user()['id'],),one=True)
    return render_template('hospital/profile.html', hospital=h)


@app.route('/hospital/edit-profile', methods=['GET','POST'])
@role_required('HOSPITAL')
def hospital_edit_profile():
    h = query('SELECT h.*,u.city,u.area FROM hospitals h JOIN users u ON u.id=h.user_id WHERE h.user_id=%s',(current_user()['id'],),one=True)
    if request.method=='POST':
        name=(request.form.get('hospital_name') or '').strip(); address=(request.form.get('address') or '').strip(); city=(request.form.get('city') or '').strip(); area=(request.form.get('area') or '').strip(); pin=(request.form.get('pincode') or '').strip()
        if not all([name,address,city,area]):
            flash('Please enter valid hospital profile details.','danger')
        else:
            execute('UPDATE hospitals SET hospital_name=%s,address=%s,pincode=%s WHERE id=%s',(name,address,pin,h['id']))
            execute('UPDATE users SET city=%s,area=%s WHERE id=%s',(city,area,current_user()['id']))
            flash('Hospital profile updated.','success'); return redirect(url_for('hospital_profile'))
    return render_template('hospital/edit_profile.html', hospital=h)


@app.route('/hospital/reports')
@role_required('HOSPITAL')
def hospital_reports():
    h=query('SELECT id FROM hospitals WHERE user_id=%s',(current_user()['id'],),one=True)
    report={
        'stock_rows': query('SELECT COUNT(*) c FROM blood_stock WHERE hospital_id=%s',(h['id'],),one=True)['c'],
        'requirements': query('SELECT COUNT(*) c FROM blood_requests WHERE hospital_id=%s',(h['id'],),one=True)['c'],
        'completed': query("SELECT COUNT(*) c FROM blood_requests WHERE hospital_id=%s AND status='Completed'",(h['id'],),one=True)['c'],
        'critical': query("SELECT COUNT(*) c FROM blood_requests WHERE hospital_id=%s AND priority='Critical' AND status NOT IN ('Completed','Cancelled')",(h['id'],),one=True)['c']
    }
    return render_template('hospital/reports.html', report=report)


@app.route('/admin/emergency-requests')
@role_required('ADMIN')
def admin_emergency_requests():
    rows=query('''SELECT er.*,br.blood_group,br.quantity,br.city,br.area,br.priority,br.status, u.full_name
                  FROM emergency_requests er JOIN blood_requests br ON br.id=er.request_id JOIN users u ON u.id=br.user_id
                  ORDER BY er.created_at DESC''')
    return render_template('admin/emergency_requests.html', requests=rows, request_code=request_code)
@app.route('/ai-assistant', methods=['GET', 'POST'])
@app.route('/ai-assistant', methods=['GET', 'POST'])
def ai_assistant():
    answer = None
    topic = None
    question = ''

    if request.method == 'POST':
        question = (request.form.get('question') or '').strip()
        q = question.lower()

        if not question:
            answer = 'Please enter a question.'
            topic = 'General'

        elif 'before' in q and 'donat' in q:
            answer = (
                'Before donating blood, generally have a balanced meal, '
                'drink enough fluids and follow the instructions given by '
                'the blood donation centre.'
            )
            topic = 'Before Blood Donation'

        elif 'after' in q and 'donat' in q:
            answer = (
                'After donating blood, rest for a short period, drink fluids '
                'and follow the instructions provided by the donation staff. '
                'If you feel unwell, contact a qualified healthcare professional.'
            )
            topic = 'After Blood Donation'

        elif 'blood group' in q or 'blood groups' in q or 'blood type' in q:
            answer = (
                'The main blood groups are A, B, AB and O. Each can also '
                'be positive or negative depending on the Rh factor. '
                'Common groups include A+, A-, B+, B-, AB+, AB-, O+ and O-.'
            )
            topic = 'Blood Groups'

        elif 'o negative' in q or 'o-' in q:
            answer = (
                'O negative is an important red-cell donor blood type. '
                'It may be used in some emergency transfusion situations. '
                'Actual transfusion decisions are made by medical professionals.'
            )
            topic = 'O Negative'

        elif 'eligible' in q or 'eligibility' in q or 'can i donate' in q:
            answer = (
                'Blood donation eligibility depends on factors such as health, '
                'age, weight, recent illness, medicines and local donation rules. '
                'This assistant cannot make a final eligibility decision. '
                'Please speak with qualified donation staff.'
            )
            topic = 'Donation Eligibility'

        elif 'find blood' in q or 'find donor' in q:
            answer = (
                'Open the Find Blood page and search using blood group, city '
                'and area. BloodConnect uses registered donor information '
                'stored in its database.'
            )
            topic = 'Find Blood'

        elif 'emergency' in q and 'blood' in q:
            answer = (
                'For a real medical emergency, contact the hospital or emergency '
                'medical services immediately. In BloodConnect, you can also '
                'create an Emergency Blood Request.'
            )
            topic = 'Emergency Blood'

        elif 'bloodconnect' in q or 'how does this website work' in q:
            answer = (
                'BloodConnect is a Flask and MySQL college-project system that '
                'connects users, donors, hospitals, blood requests and blood '
                'donation camps.'
            )
            topic = 'BloodConnect'

        elif 'what is blood donation' in q or 'meaning of blood donation' in q:
            answer = (
                'Blood donation is the voluntary process of giving blood so '
                'that it can help patients who need blood or blood components. '
                'The process is carried out under trained staff supervision.'
            )
            topic = 'Blood Donation'

        else:
            answer = (
                'I can answer general questions about blood donation, blood '
                'groups, preparing for donation, after-donation care, '
                'eligibility information, finding blood and using BloodConnect.'
            )
            topic = 'General Information'

    return render_template(
        'public/ai_assistant.html',
        answer=answer,
        topic=topic,
        question=question
    )
@app.route('/people')
def people():
    people_list = query(
        '''SELECT
               id,
               full_name,
               age,
               blood_group,
               city,
               area
           FROM users
           WHERE role='USER'
           ORDER BY id DESC'''
    )

    return render_template(
        'public/people.html',
        people=people_list
    )
@app.route('/donors')
def donors():
    donor_list = query(
        '''SELECT
               d.id,
               u.full_name,
               u.age,
               u.blood_group,
               u.city,
               u.area,
               d.availability,
               d.last_donation_date,
               d.verified
           FROM donors d
           JOIN users u
             ON u.id = d.user_id
           ORDER BY d.id DESC'''
    )

    return render_template(
        'public/donors.html',
        donors=donor_list
    )
@app.route('/doctors')
def doctors():
    doctor_list = query(
        '''SELECT
               d.id,
               d.full_name,
               d.age,
               d.experience_years,
               d.specialization,
               d.city,
               d.bio,
               h.hospital_name
           FROM doctors d
           JOIN hospitals h
               ON h.id = d.hospital_id
           WHERE d.verified = 1
           ORDER BY d.full_name'''
    )

    return render_template(
        'public/doctors.html',
        doctors=doctor_list
    )
@app.route('/user/create-request', methods=['GET', 'POST'])
def create_blood_request():

    # User must be logged in
    if 'user_id' not in session:
        flash('Please login to create a blood request.', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':

        blood_group = request.form.get('blood_group', '').strip()
        required_quantity = request.form.get('required_quantity', '').strip()
        required_date = request.form.get('required_date', '').strip()
        city = request.form.get('city', '').strip()
        area = request.form.get('area', '').strip()
        priority = request.form.get('priority', 'Normal').strip()
        notes = request.form.get('notes', '').strip()

        # Basic validation
        if not blood_group:
            flash('Please select a blood group.', 'danger')
            return render_template('user/create_request.html')

        try:
            required_quantity = int(required_quantity)
        except ValueError:
            flash('Please enter a valid quantity.', 'danger')
            return render_template('user/create_request.html')

        if required_quantity <= 0:
            flash('Quantity must be greater than 0.', 'danger')
            return render_template('user/create_request.html')

        if not required_date:
            flash('Please select the required date.', 'danger')
            return render_template('user/create_request.html')

        if not city or not area:
            flash('Please enter city and area.', 'danger')
            return render_template('user/create_request.html')

        if priority not in ('Normal', 'Urgent', 'Critical'):
            flash('Invalid priority selected.', 'danger')
            return render_template('user/create_request.html')

        conn = db()
        cur = conn.cursor()

        try:
            cur.execute(
                '''INSERT INTO blood_requests
                   (user_id, blood_group, required_quantity,
                    required_date, city, area, priority,
                    notes, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                (
                    session['user_id'],
                    blood_group,
                    required_quantity,
                    required_date,
                    city,
                    area,
                    priority,
                    notes or None,
                    'Searching'
                )
            )

            conn.commit()

            request_id = cur.lastrowid

        except mysql.connector.Error:
            conn.rollback()
            flash('Unable to create the blood request. Please try again.', 'danger')

            return render_template(
                'user/create_request.html'
            )

        finally:
            cur.close()
            conn.close()

        flash(
            f'Blood request created successfully. Request ID: BC-{request_id:05d}',
            'success'
        )

        return redirect(url_for('create_blood_request'))

    return render_template('user/create_request.html')
@app.route('/blood-requests')
def blood_requests():
    request_list = query(
        '''SELECT
               id,
               blood_group,
               quantity,
               required_date,
               city,
               area,
               priority,
               notes,
               status
           FROM blood_requests
           ORDER BY id DESC'''
    )

    return render_template(
        'public/blood_requests.html',
        requests=request_list
    )
@app.route('/camps/create', methods=['GET', 'POST'])
def create_camp():

    if request.method == 'POST':

        name = (request.form.get('name') or '').strip()
        camp_date = (request.form.get('camp_date') or '').strip()
        camp_time = (request.form.get('camp_time') or '').strip()
        location = (request.form.get('location') or '').strip()
        organizer = (request.form.get('organizer') or '').strip()
        contact_information = (
            request.form.get('contact_information') or ''
        ).strip()
        description = (
            request.form.get('description') or ''
        ).strip()

        registration_open = 1 if request.form.get(
            'registration_open'
        ) == '1' else 0

        if not name or not camp_date or not camp_time or not location or not organizer:
            flash(
                'Please fill all required camp fields.',
                'danger'
            )
            return render_template('hospital/create_camp.html')

        execute(
            '''INSERT INTO blood_camps
               (name, camp_date, camp_time, location, organizer,
                hospital_id, registration_open, description,
                contact_information)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (
                name,
                camp_date,
                camp_time,
                location,
                organizer,
                None,
                registration_open,
                description or None,
                contact_information or None
            )
        )

        flash(
            'Blood donation camp added successfully.',
            'success'
        )

        return redirect(url_for('create_camp'))

    return render_template('hospital/create_camp.html')


    return render_template(
        'public/camps.html',
        camps=upcoming_camps
    )
@app.route('/camps/<int:camp_id>/join', methods=['POST'])
def join_camp(camp_id):

    # User must be logged in
    if 'user_id' not in session:
        flash('Please login to join a blood camp.', 'warning')
        return redirect(url_for('login'))

    user_id = session['user_id']

    # Check that the camp is still upcoming and registration is open
    camp = query(
        '''SELECT
               id,
               name,
               camp_date,
               location
           FROM blood_camps
           WHERE id=%s
             AND camp_date >= CURDATE()
             AND registration_open=1''',
        (camp_id,),
        one=True
    )

    if not camp:
        flash(
            'This camp is no longer available for registration.',
            'danger'
        )
        return redirect(url_for('camps'))

    # Check whether this user already joined
    already_registered = query(
        '''SELECT id
           FROM camp_registrations
           WHERE camp_id=%s
             AND user_id=%s''',
        (camp_id, user_id),
        one=True
    )

    if already_registered:
        flash(
            'You have already joined this camp.',
            'info'
        )
        return redirect(url_for('camps'))

    # Save registration
    execute(
        '''INSERT INTO camp_registrations
           (camp_id, user_id)
           VALUES (%s, %s)''',
        (camp_id, user_id)
    )

    # Create in-app notification
    add_notification(
        user_id,
        'Camp Registration Confirmed',
        (
            f'You have successfully joined "{camp["name"]}". '
            f'The camp is on {camp["camp_date"]} '
            f'at {camp["location"]}.'
        )
    )

    flash(
        'You have successfully joined the blood donation camp.',
        'success'
    )

    return redirect(url_for('camps'))

if __name__ == '__main__':
    app.run(debug=True)