import os
import secrets
import hashlib
from datetime import datetime
from datetime import timedelta
from functools import wraps

from sqlalchemy import or_
from sqlalchemy import func
from sqlalchemy import text
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from models import AcademicRecord, AdminActivityLog, Notification, PasswordResetToken, User, db
from models import VerificationRequest


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "uploads")
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg"}
MIN_FILE_SIZE = 1024
MAX_FILE_SIZE = 5 * 1024 * 1024


def get_database_uri() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Some platforms expose postgres URLs with an older scheme.
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        return database_url
    return f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'academic_trust.db')}"


app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

db.init_app(app)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def compute_file_hash(file_storage) -> str:
    hasher = hashlib.sha256()
    file_storage.stream.seek(0)
    while True:
        chunk = file_storage.stream.read(8192)
        if not chunk:
            break
        hasher.update(chunk)
    file_storage.stream.seek(0)
    return hasher.hexdigest()


def ensure_schema_updates():
    if db.engine.dialect.name != "sqlite":
        return
    with db.engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(academic_record)"))}
        if "file_hash" not in columns:
            conn.execute(text("ALTER TABLE academic_record ADD COLUMN file_hash TEXT"))
        if "is_duplicate" not in columns:
            conn.execute(text("ALTER TABLE academic_record ADD COLUMN is_duplicate BOOLEAN DEFAULT 0 NOT NULL"))
        if "duplicate_of_id" not in columns:
            conn.execute(text("ALTER TABLE academic_record ADD COLUMN duplicate_of_id INTEGER"))


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def roles_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "user_id" not in session:
                flash("Please login first.", "warning")
                return redirect(url_for("login"))
            if session.get("role") not in allowed_roles:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def create_notification(recipient_id: int, message: str) -> None:
    db.session.add(Notification(recipient_id=recipient_id, message=message, is_read=False))


@app.template_filter("timeago")
def timeago_filter(value):
    if not value:
        return "-"

    now = datetime.utcnow()
    diff = now - value
    seconds = int(diff.total_seconds())

    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute ago" if minutes == 1 else f"{minutes} minutes ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    if seconds < 172800:
        return "Yesterday"
    days = seconds // 86400
    if days < 7:
        return f"{days} days ago"
    return value.strftime("%Y-%m-%d %H:%M")


@app.context_processor
def inject_notification_count():
    count = 0
    if session.get("user_id"):
        count = Notification.query.filter_by(recipient_id=session["user_id"], is_read=False).count()
    return {"unread_notifications": count}


@app.route("/")
def index():
    if session.get("user_id"):
        if session.get("role") in {"admin"}:
            return redirect(url_for("admin_panel"))
        if session.get("role") == "institution":
            return redirect(url_for("institution_dashboard"))
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "user").strip().lower()

        if role not in {"user", "institution"}:
            flash("Invalid role selected for registration.", "danger")
            return redirect(url_for("register"))

        if not name or not email or not password:
            flash("All fields are required.", "danger")
            return redirect(url_for("register"))

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Email is already registered.", "warning")
            return redirect(url_for("register"))

        user = User(name=name, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Registration successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")

        user = User.query.filter_by(email=email, role=role).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            session["name"] = user.name
            session["role"] = user.role
            flash("Login successful.", "success")

            if user.role in {"admin"}:
                return redirect(url_for("admin_panel"))
            if user.role == "institution":
                return redirect(url_for("institution_dashboard"))
            return redirect(url_for("dashboard"))

        flash("Invalid credentials.", "danger")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        # Show same public response to avoid email enumeration.
        if user:
            PasswordResetToken.query.filter_by(user_id=user.id).delete()
            token = secrets.token_urlsafe(24)
            reset_token = PasswordResetToken(
                user_id=user.id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(minutes=30),
            )
            db.session.add(reset_token)
            db.session.commit()
            flash(f"Reset link (demo): {url_for('reset_password', token=token, _external=True)}", "info")
        else:
            flash("If this email exists, a reset link has been generated.", "info")

        return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    token_row = PasswordResetToken.query.filter_by(token=token).first()
    if not token_row or token_row.expires_at < datetime.utcnow():
        if token_row:
            db.session.delete(token_row)
            db.session.commit()
        flash("Reset link is invalid or expired.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return redirect(url_for("reset_password", token=token))
        if new_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("reset_password", token=token))

        user = User.query.get(token_row.user_id)
        user.set_password(new_password)
        db.session.delete(token_row)
        db.session.commit()

        flash("Password reset successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.route("/dashboard")
@login_required
def dashboard():
    if session.get("role") in {"admin"}:
        return redirect(url_for("admin_panel"))
    if session.get("role") == "institution":
        return redirect(url_for("institution_dashboard"))

    records = (
        AcademicRecord.query.filter_by(user_id=session["user_id"])
        .order_by(AcademicRecord.uploaded_at.desc())
        .all()
    )
    return render_template("dashboard.html", records=records)


@app.route("/upload-record", methods=["GET", "POST"])
@login_required
def upload_record():
    if session.get("role") != "user":
        flash("Only users can upload records.", "warning")
        return redirect(url_for("admin_panel"))

    if request.method == "POST":
        document_title = request.form.get("document_title", "").strip()
        institution = request.form.get("institution", "").strip()
        year_of_completion = request.form.get("year_of_completion", "").strip()
        description = request.form.get("description", "").strip()
        document = request.files.get("document")

        if not all([document_title, institution, year_of_completion, document]):
            flash("Please fill all required fields and select a file.", "danger")
            return redirect(url_for("upload_record"))

        if not allowed_file(document.filename):
            flash("Invalid file type. Allowed: pdf, png, jpg", "danger")
            return redirect(url_for("upload_record"))

        document.stream.seek(0, os.SEEK_END)
        file_size = document.stream.tell()
        document.stream.seek(0)
        if file_size < MIN_FILE_SIZE or file_size > MAX_FILE_SIZE:
            flash("Invalid file size. Allowed range: 1KB to 5MB.", "danger")
            return redirect(url_for("upload_record"))

        file_hash = compute_file_hash(document)
        existing_same_hash = AcademicRecord.query.filter_by(
            user_id=session["user_id"],
            file_hash=file_hash
        ).first()
        if existing_same_hash:
            flash("Duplicate upload detected for this user. Upload blocked.", "danger")
            return redirect(url_for("upload_record"))

        filename = secure_filename(document.filename)
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        stored_filename = f"{session['user_id']}_{timestamp}_{filename}"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], stored_filename)
        document.save(save_path)

        record = AcademicRecord(
            user_id=session["user_id"],
            document_title=document_title,
            institution=institution,
            year_of_completion=year_of_completion,
            description=description,
            original_filename=filename,
            stored_filename=stored_filename,
            file_path=save_path,
            file_hash=file_hash,
            is_duplicate=False,
            duplicate_of_id=None,
            status="Pending",
        )
        db.session.add(record)
        db.session.commit()

        flash("Document integrity verified successfully.", "success")
        flash("Academic record uploaded successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("upload_record.html")


@app.route("/records/<int:record_id>/file")
@login_required
def serve_uploaded_file(record_id: int):
    record = AcademicRecord.query.get_or_404(record_id)

    can_view = False
    if session.get("role") == "admin":
        can_view = True
    elif session.get("role") == "institution":
        approved_link = VerificationRequest.query.filter_by(
            student_id=record.user_id,
            institution_id=session.get("user_id"),
            status="AdminConfirmed",
        ).first()
        can_view = bool(approved_link and record.status == "Verified")
    else:
        can_view = record.user_id == session.get("user_id")

    if not can_view:
        flash("You are not authorized to access this file.", "danger")
        return redirect(url_for("dashboard"))

    return send_from_directory(app.config["UPLOAD_FOLDER"], record.stored_filename, as_attachment=False)


@app.route("/admin")
@roles_required("admin")
def admin_panel():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    institution = request.args.get("institution", "").strip()
    year = request.args.get("year", "").strip()

    query = AcademicRecord.query.join(User)

    if status:
        query = query.filter(AcademicRecord.status == status)
    if institution:
        query = query.filter(AcademicRecord.institution == institution)
    if year:
        query = query.filter(AcademicRecord.year_of_completion == year)
    if q:
        like_term = f"%{q}%"
        query = query.filter(
            or_(
                User.name.ilike(like_term),
                User.email.ilike(like_term),
                AcademicRecord.document_title.ilike(like_term),
                AcademicRecord.institution.ilike(like_term),
            )
        )

    records = query.order_by(AcademicRecord.uploaded_at.desc()).all()
    institutions = [
        row[0]
        for row in db.session.query(AcademicRecord.institution)
        .distinct()
        .order_by(AcademicRecord.institution.asc())
        .all()
    ]
    years = [
        row[0]
        for row in db.session.query(AcademicRecord.year_of_completion)
        .distinct()
        .order_by(AcademicRecord.year_of_completion.desc())
        .all()
    ]
    return render_template(
        "admin_panel.html",
        records=records,
        institutions=institutions,
        years=years,
        filters={"q": q, "status": status, "institution": institution, "year": year},
    )


@app.route("/admin/analytics")
@roles_required("admin")
def admin_analytics():
    total_uploads = AcademicRecord.query.count()
    total_verified = AcademicRecord.query.filter_by(status="Verified").count()
    total_pending = AcademicRecord.query.filter_by(status="Pending").count()
    total_rejected = AcademicRecord.query.filter_by(status="Rejected").count()
    total_users = User.query.filter_by(role="user").count()

    verified_percent = round((total_verified / total_uploads) * 100, 1) if total_uploads else 0.0

    monthly_rows = (
        db.session.query(
            func.strftime("%Y-%m", AcademicRecord.uploaded_at).label("month"),
            func.count(AcademicRecord.id),
        )
        .group_by("month")
        .order_by("month")
        .all()
    )
    monthly_labels = [row[0] for row in monthly_rows]
    monthly_counts = [row[1] for row in monthly_rows]

    status_rows = (
        db.session.query(AcademicRecord.status, func.count(AcademicRecord.id))
        .group_by(AcademicRecord.status)
        .all()
    )
    status_breakdown = {row[0]: row[1] for row in status_rows}

    return render_template(
        "admin_analytics.html",
        metrics={
            "total_uploads": total_uploads,
            "total_verified": total_verified,
            "total_pending": total_pending,
            "total_rejected": total_rejected,
            "total_users": total_users,
            "verified_percent": verified_percent,
        },
        monthly_labels=monthly_labels,
        monthly_counts=monthly_counts,
        status_breakdown=status_breakdown,
    )


@app.route("/admin/verify/<int:record_id>", methods=["POST"])
@roles_required("admin")
def verify_record(record_id: int):
    record = AcademicRecord.query.get_or_404(record_id)
    status = request.form.get("status", "Pending")
    admin_note = request.form.get("admin_note", "").strip()
    old_status = record.status

    if status not in {"Pending", "Verified", "Rejected"}:
        flash("Invalid status update.", "danger")
        return redirect(url_for("admin_panel"))

    record.status = status
    record.admin_note = admin_note if admin_note else None
    record.verified_at = datetime.utcnow() if status in {"Verified", "Rejected"} else None

    log = AdminActivityLog(
        admin_id=session["user_id"],
        record_id=record.id,
        old_status=old_status,
        new_status=status,
        admin_note=admin_note if admin_note else None,
    )
    db.session.add(log)
    create_notification(
        recipient_id=record.user_id,
        message=f"New verification update: '{record.document_title}' marked as {status}.",
    )
    db.session.commit()

    flash("Record status updated.", "success")
    return redirect(
        url_for(
            "admin_panel",
            q=request.form.get("filter_q", "").strip(),
            status=request.form.get("filter_status", "").strip(),
            institution=request.form.get("filter_institution", "").strip(),
            year=request.form.get("filter_year", "").strip(),
        )
    )


@app.route("/admin/bulk-update", methods=["POST"])
@roles_required("admin")
def bulk_update_records():
    status = request.form.get("bulk_status", "").strip()
    admin_note = request.form.get("bulk_note", "").strip()
    raw_record_ids = request.form.getlist("record_ids")

    if status not in {"Pending", "Verified", "Rejected"}:
        flash("Choose a valid bulk status.", "danger")
        return redirect(url_for("admin_panel"))

    if not raw_record_ids:
        flash("Select at least one record for bulk update.", "warning")
        return redirect(
            url_for(
                "admin_panel",
                q=request.form.get("filter_q", "").strip(),
                status=request.form.get("filter_status", "").strip(),
                institution=request.form.get("filter_institution", "").strip(),
                year=request.form.get("filter_year", "").strip(),
            )
        )

    record_ids = []
    for item in raw_record_ids:
        try:
            record_ids.append(int(item))
        except ValueError:
            continue

    if not record_ids:
        flash("No valid records selected.", "danger")
        return redirect(url_for("admin_panel"))

    records = AcademicRecord.query.filter(AcademicRecord.id.in_(record_ids)).all()
    if not records:
        flash("Selected records were not found.", "warning")
        return redirect(url_for("admin_panel"))

    for record in records:
        old_status = record.status
        record.status = status
        record.admin_note = admin_note if admin_note else None
        record.verified_at = datetime.utcnow() if status in {"Verified", "Rejected"} else None

        db.session.add(
            AdminActivityLog(
                admin_id=session["user_id"],
                record_id=record.id,
                old_status=old_status,
                new_status=status,
                admin_note=(f"[Bulk] {admin_note}" if admin_note else "[Bulk] Status updated"),
            )
        )
        create_notification(
            recipient_id=record.user_id,
            message=f"New verification update: '{record.document_title}' marked as {status}.",
        )

    db.session.commit()
    flash(f"Bulk update applied to {len(records)} record(s).", "success")

    return redirect(
        url_for(
            "admin_panel",
            q=request.form.get("filter_q", "").strip(),
            status=request.form.get("filter_status", "").strip(),
            institution=request.form.get("filter_institution", "").strip(),
            year=request.form.get("filter_year", "").strip(),
        )
    )


@app.route("/institution/dashboard", methods=["GET", "POST"])
@roles_required("institution")
def institution_dashboard():
    query_email = request.form.get("student_email", "").strip().lower() if request.method == "POST" else ""
    student = None

    if request.method == "POST":
        student = User.query.filter_by(email=query_email, role="user").first()
        if not student:
            flash("Student not found.", "warning")
        else:
            existing = VerificationRequest.query.filter_by(
                student_id=student.id,
                institution_id=session["user_id"],
            ).order_by(VerificationRequest.request_date.desc()).first()
            if existing and existing.status in {"Pending", "StudentApproved", "AdminConfirmed"}:
                flash("An active request already exists for this student.", "warning")
            else:
                req = VerificationRequest(
                    student_id=student.id,
                    institution_id=session["user_id"],
                    status="Pending",
                )
                db.session.add(req)
                create_notification(
                    recipient_id=student.id,
                    message=f"New verification update: Institution '{session['name']}' sent a verification request.",
                )
                db.session.commit()
                flash("Verification request sent to student.", "success")

    requests = VerificationRequest.query.filter_by(
        institution_id=session["user_id"]
    ).order_by(VerificationRequest.request_date.desc()).all()

    return render_template(
        "institution_dashboard.html",
        query_email=query_email,
        student=student,
        requests=requests,
    )


@app.route("/student/verification-requests")
@roles_required("user")
def student_verification_requests():
    requests = VerificationRequest.query.filter_by(
        student_id=session["user_id"]
    ).order_by(VerificationRequest.request_date.desc()).all()
    return render_template("student_verification_requests.html", requests=requests)


@app.route("/student/verification-requests/<int:req_id>/respond", methods=["POST"])
@roles_required("user")
def respond_verification_request(req_id: int):
    action = request.form.get("action", "").strip()
    req = VerificationRequest.query.get_or_404(req_id)

    if req.student_id != session["user_id"]:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("student_verification_requests"))
    if req.status != "Pending":
        flash("This request is already processed.", "warning")
        return redirect(url_for("student_verification_requests"))

    if action == "approve":
        req.status = "StudentApproved"
        flash("Request approved and sent to admin for confirmation.", "success")
    elif action == "reject":
        req.status = "StudentRejected"
        flash("Request rejected.", "info")
    else:
        flash("Invalid action.", "danger")
        return redirect(url_for("student_verification_requests"))

    db.session.commit()
    return redirect(url_for("student_verification_requests"))


@app.route("/admin/verification-requests")
@roles_required("admin")
def admin_verification_requests():
    requests = VerificationRequest.query.order_by(VerificationRequest.request_date.desc()).all()
    return render_template("admin_verification_requests.html", requests=requests)


@app.route("/admin/verification-requests/<int:req_id>/confirm", methods=["POST"])
@roles_required("admin")
def confirm_verification_request(req_id: int):
    action = request.form.get("action", "").strip()
    req = VerificationRequest.query.get_or_404(req_id)

    if req.status != "StudentApproved":
        flash("Only student-approved requests can be admin confirmed/rejected.", "warning")
        return redirect(url_for("admin_verification_requests"))

    if action == "confirm":
        req.status = "AdminConfirmed"
        flash("Request confirmed by admin.", "success")
    elif action == "reject":
        req.status = "AdminRejected"
        flash("Request rejected by admin.", "info")
    else:
        flash("Invalid action.", "danger")
        return redirect(url_for("admin_verification_requests"))

    db.session.commit()
    return redirect(url_for("admin_verification_requests"))


@app.route("/institution/student/<int:student_id>/documents")
@roles_required("institution")
def institution_student_documents(student_id: int):
    approved = VerificationRequest.query.filter_by(
        student_id=student_id,
        institution_id=session["user_id"],
        status="AdminConfirmed",
    ).first()
    if not approved:
        flash("Access denied. Student approval + admin confirmation required.", "danger")
        return redirect(url_for("institution_dashboard"))

    records = AcademicRecord.query.filter_by(
        user_id=student_id,
        status="Verified",
    ).order_by(AcademicRecord.uploaded_at.desc()).all()
    student = User.query.get(student_id)
    return render_template("institution_documents.html", records=records, student=student)


@app.route("/notifications")
@login_required
def notifications():
    notifications_list = Notification.query.filter_by(
        recipient_id=session["user_id"]
    ).order_by(Notification.created_at.desc()).all()

    return render_template("notifications.html", notifications=notifications_list)


@app.route("/notifications/mark-all", methods=["POST"])
@login_required
def mark_all_notifications():
    action = request.form.get("action", "").strip().lower()
    mark_read = action == "read"
    mark_unread = action == "unread"

    if not (mark_read or mark_unread):
        flash("Invalid notification action.", "danger")
        return redirect(url_for("notifications"))

    notifications_list = Notification.query.filter_by(recipient_id=session["user_id"]).all()
    for item in notifications_list:
        item.is_read = mark_read
    db.session.commit()

    flash("Notifications updated.", "success")
    return redirect(url_for("notifications"))


def create_default_staff():
    defaults = [
        ("System Admin", "admin@trust.com", "admin123", "admin"),
    ]

    for name, email, password, role in defaults:
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(name=name, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            print(f"Default {role} created: {email} / {password}")


with app.app_context():
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
    db.create_all()
    ensure_schema_updates()
    create_default_staff()



import os
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
