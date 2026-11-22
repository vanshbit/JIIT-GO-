from datetime import datetime, timedelta
import os
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_


app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("JIITGO_SECRET_KEY", "dev-only-change-this-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///jiitgo.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins=None, async_mode="threading")


# =========================
# DATABASE MODELS
# =========================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(
        db.String(150), unique=True, nullable=False, index=True
    )
    password_hash = db.Column(db.String(255), nullable=False)


class Ride(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    host_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )

    source = db.Column(db.String(160), nullable=False)
    destination = db.Column(db.String(160), nullable=False)

    ride_date = db.Column(db.String(10), nullable=False)
    ride_time = db.Column(db.String(5), nullable=False)

    vehicle = db.Column(db.String(80), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)

    total_fare = db.Column(db.Float, nullable=False, default=0)

    # Optional cab details
    car_number = db.Column(db.String(30))
    ride_otp = db.Column(db.String(20))

    status = db.Column(db.String(20), default="open")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    host = db.relationship("User", foreign_keys=[host_id])


class RideMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    ride_id = db.Column(
        db.Integer, db.ForeignKey("ride.id"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )

    role = db.Column(db.String(20), default="passenger")
    payment_received = db.Column(db.Boolean, default=False)

    user = db.relationship("User")
    ride = db.relationship("Ride")


class JoinRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    ride_id = db.Column(
        db.Integer, db.ForeignKey("ride.id"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )

    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    ride = db.relationship("Ride")


class Invitation(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    ride_id = db.Column(
        db.Integer, db.ForeignKey("ride.id"), nullable=False
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )

    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ride = db.relationship("Ride")
    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    ride_id = db.Column(
        db.Integer, db.ForeignKey("ride.id"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )

    message = db.Column(db.String(1500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


# =========================
# HELPER FUNCTIONS
# =========================

def current_user():
    uid = session.get("user_id")

    if uid:
        return db.session.get(User, uid)

    return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please login first.", "error")
            return redirect(url_for("login"))

        return fn(*args, **kwargs)

    return wrapper


def get_member(ride_id, user_id):
    return RideMember.query.filter_by(
        ride_id=ride_id,
        user_id=user_id
    ).first()


def member_count(ride_id):
    return RideMember.query.filter_by(
        ride_id=ride_id
    ).count()


def seats_left(ride):
    return max(0, ride.capacity - member_count(ride.id))


def is_host(ride, user):
    return bool(user and ride.host_id == user.id)


def can_chat(ride_id, user_id):
    return get_member(ride_id, user_id) is not None


def invite_or_join(ride, user):
    if get_member(ride.id, user.id):
        return True

    if seats_left(ride) <= 0 or ride.status != "open":
        return False

    db.session.add(
        RideMember(
            ride_id=ride.id,
            user_id=user.id,
            role="passenger"
        )
    )

    if seats_left(ride) == 0:
        ride.status = "full"
    else:
        ride.status = "open"

    return True


@app.context_processor
def inject_user():
    return dict(current_user=current_user())


# =========================
# HOME PAGE
# =========================

@app.route("/")
def index():

    rides = Ride.query.filter(
        Ride.status.in_(["open", "full"])
    ).order_by(
        Ride.ride_date.asc(),
        Ride.ride_time.asc()
    ).all()

    ride_data = []

    for ride in rides:

        try:
            departure = datetime.strptime(
                f"{ride.ride_date} {ride.ride_time}", "%Y-%m-%d %H:%M"
            )
        except (TypeError, ValueError):
            continue
        if departure <= datetime.now() and ride.status not in ("completed", "cancelled", "expired"):
            continue

        ride_data.append({
            "ride": ride,
            "left": seats_left(ride),
            "member": bool(
                current_user()
                and get_member(ride.id, current_user().id)
            )
        })

    return render_template(
        "index.html",
        ride_data=ride_data
    )


# =========================
# REGISTER
# =========================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if (
            not name
            or not email.endswith("@mail.jiit.ac.in")
            or len(password) < 8
        ):
            flash(
                "Name, @mail.jiit.ac.in email and password "
                "(minimum 8 characters) required.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="register"
            )

        if User.query.filter_by(email=email).first():
            flash(
                "Email already registered. Please login.",
                "error"
            )
            return redirect(url_for("login"))

        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password)
        )

        db.session.add(user)
        db.session.commit()

        session.clear()
        session["user_id"] = user.id

        flash(
            "Account created. Email verification is disabled "
            "in this prototype.",
            "success"
        )

        return redirect(url_for("index"))

    return render_template("auth.html", mode="register")


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if (
            not user
            or not check_password_hash(
                user.password_hash, password
            )
        ):
            flash("Invalid email or password.", "error")

            return render_template(
                "auth.html",
                mode="login"
            )

        session.clear()
        session["user_id"] = user.id

        return redirect(url_for("index"))

    return render_template("auth.html", mode="login")


# =========================
# LOGOUT
# =========================

@app.route("/logout", methods=["POST"])
@login_required
def logout():

    session.clear()

    return redirect(url_for("index"))


# =========================
# CREATE RIDE
# =========================

@app.route("/ride/create", methods=["GET", "POST"])
@login_required
def create_ride():

    if request.method == "POST":

        source = request.form.get("source", "").strip()
        destination = request.form.get(
            "destination", ""
        ).strip()

        ride_date = request.form.get("ride_date", "")
        ride_time = request.form.get("ride_time", "")

        vehicle = request.form.get("vehicle", "").strip()

        capacity = request.form.get(
            "capacity", type=int
        )

        fare = request.form.get("total_fare", type=float)
        if fare is None:
            fare = -1

        car_number = (
            request.form.get("car_number", "").strip()
            or None
        )

        ride_otp = (
            request.form.get("ride_otp", "").strip()
            or None
        )

        if (
            not all([
                source, destination, ride_date,
                ride_time, vehicle
            ])
            or capacity not in [3, 4, 6]
            or fare < 0
        ):
            flash(
                "Fill all required fields and select "
                "capacity 3, 4 or 6.",
                "error"
            )

            return render_template("create_ride.html")

        try:
            datetime.strptime(ride_date, "%Y-%m-%d")
            datetime.strptime(ride_time, "%H:%M")

        except ValueError:
            flash("Invalid date or time.", "error")
            return render_template("create_ride.html")

        departure = datetime.strptime(
            f"{ride_date} {ride_time}", "%Y-%m-%d %H:%M"
        )
        if departure <= datetime.now():
            flash("Ride departure must be in the future.", "error")
            return render_template("create_ride.html")

        ride = Ride(
            host_id=current_user().id,
            source=source,
            destination=destination,
            ride_date=ride_date,
            ride_time=ride_time,
            vehicle=vehicle,
            capacity=capacity,
            total_fare=fare,
            car_number=car_number,
            ride_otp=ride_otp
        )

        db.session.add(ride)
        db.session.flush()

        # Host occupies the first seat
        db.session.add(
            RideMember(
                ride_id=ride.id,
                user_id=current_user().id,
                role="host"
            )
        )

        db.session.commit()

        flash("Ride created and published!", "success")

        return redirect(
            url_for("ride_detail", ride_id=ride.id)
        )

    return render_template("create_ride.html")


# =========================
# RIDE DETAILS
# =========================

@app.route("/ride/<int:ride_id>")
@login_required
def ride_detail(ride_id):

    ride = Ride.query.get_or_404(ride_id)
    user = current_user()

    member = get_member(ride.id, user.id)

    members = RideMember.query.filter_by(
        ride_id=ride.id
    ).all()

    requests_list = []

    if is_host(ride, user):
        requests_list = JoinRequest.query.filter_by(
            ride_id=ride.id,
            status="pending"
        ).all()

    friends = User.query.filter(
        User.id != user.id
    ).order_by(User.name).all()

    invited_ids = {
        i.recipient_id
        for i in Invitation.query.filter_by(
            ride_id=ride.id,
            sender_id=user.id
        ).all()
    }

    count = member_count(ride.id)

    share = (
        ride.total_fare / count
        if count and ride.total_fare
        else 0
    )

    return render_template(
        "ride.html",
        ride=ride,
        member=member,
        members=members,
        left=seats_left(ride),
        requests_list=requests_list,
        friends=friends,
        invited_ids=invited_ids,
        host=is_host(ride, user),
        share=share
    )


# =========================
# REQUEST TO JOIN
# =========================

@app.route(
    "/ride/<int:ride_id>/request",
    methods=["POST"]
)
@login_required
def request_join(ride_id):

    ride = Ride.query.get_or_404(ride_id)
    user = current_user()

    if (
        ride.host_id == user.id
        or get_member(ride.id, user.id)
    ):
        flash("You are already part of this ride.", "error")

    elif ride.status != "open" or seats_left(ride) <= 0:
        flash("No seats available.", "error")

    elif JoinRequest.query.filter_by(
        ride_id=ride.id,
        user_id=user.id,
        status="pending"
    ).first():

        flash("You already have a pending request.", "error")

    else:
        db.session.add(
            JoinRequest(
                ride_id=ride.id,
                user_id=user.id
            )
        )

        db.session.commit()

        flash("Join request sent to host.", "success")

    return redirect(
        url_for("ride_detail", ride_id=ride.id)
    )


# =========================
# ACCEPT / REJECT REQUEST
# =========================

@app.route(
    "/request/<int:req_id>/<action>",
    methods=["POST"]
)
@login_required
def manage_request(req_id, action):

    req = JoinRequest.query.get_or_404(req_id)

    if req.ride.host_id != current_user().id:
        abort(403)

    if req.status != "pending":

        flash(
            "This request has already been handled.",
            "error"
        )

    elif action == "accept":

        if invite_or_join(req.ride, req.user):

            req.status = "accepted"
            db.session.commit()

            flash("Passenger accepted.", "success")

        else:
            flash(
                "No seats left; request not accepted.",
                "error"
            )

    elif action == "reject":

        req.status = "rejected"
        db.session.commit()

        flash("Request rejected.", "success")

    else:
        abort(400)

    return redirect(
        url_for("ride_detail", ride_id=req.ride_id)
    )


# =========================
# UPDATE CAR NUMBER / OTP
# =========================

@app.route(
    "/ride/<int:ride_id>/details",
    methods=["POST"]
)
@login_required
def update_ride_details(ride_id):

    ride = Ride.query.get_or_404(ride_id)

    if not is_host(ride, current_user()):
        abort(403)

    ride.car_number = (
        request.form.get("car_number", "").strip()
        or None
    )

    ride.ride_otp = (
        request.form.get("ride_otp", "").strip()
        or None
    )

    db.session.commit()

    flash("Cab details updated.", "success")

    return redirect(
        url_for("ride_detail", ride_id=ride.id)
    )


# =========================
# MARK PAYMENT RECEIVED
# =========================

@app.route(
    "/ride/<int:ride_id>/payment/<int:member_id>",
    methods=["POST"]
)
@login_required
def mark_payment(ride_id, member_id):

    ride = Ride.query.get_or_404(ride_id)

    if not is_host(ride, current_user()):
        abort(403)

    member = RideMember.query.filter_by(
        ride_id=ride.id,
        id=member_id
    ).first_or_404()

    member.payment_received = not member.payment_received

    db.session.commit()

    return redirect(
        url_for("ride_detail", ride_id=ride.id)
    )


# =========================
# INVITE FRIEND TO RIDE
# =========================

@app.route(
    "/ride/<int:ride_id>/invite",
    methods=["POST"]
)
@login_required
def invite_friend(ride_id):

    ride = Ride.query.get_or_404(ride_id)
    user = current_user()

    if not is_host(ride, user):
        abort(403)

    friend_id = request.form.get(
        "friend_id", type=int
    )

    friend = (
        db.session.get(User, friend_id)
        if friend_id else None
    )

    if not friend or friend.id == user.id:

        flash("Select a valid friend.", "error")

    elif get_member(ride.id, friend.id):

        flash(
            "This user is already in the ride.",
            "error"
        )

    elif seats_left(ride) <= 0 or ride.status != "open":

        flash("No seats available.", "error")

    elif Invitation.query.filter_by(
        ride_id=ride.id,
        recipient_id=friend.id,
        status="pending"
    ).first():

        flash("Invitation already pending.", "error")

    else:

        db.session.add(
            Invitation(
                ride_id=ride.id,
                sender_id=user.id,
                recipient_id=friend.id
            )
        )

        db.session.commit()

        flash(
            f"Invitation sent to {friend.name}.",
            "success"
        )

    return redirect(
        url_for("ride_detail", ride_id=ride.id)
    )


# =========================
# FIND FRIENDS
# =========================

@app.route("/friends")
@login_required
def friends():

    q = request.args.get("q", "").strip()
    results = []

    if q:
        results = User.query.filter(
            User.id != current_user().id,
            or_(
                User.name.ilike(f"%{q}%"),
                User.email.ilike(f"%{q}%")
            )
        ).limit(30).all()

    return render_template(
        "friends.html",
        q=q,
        results=results
    )


# =========================
# VIEW INVITATIONS
# =========================

@app.route("/invitations")
@login_required
def invitations():

    incoming = Invitation.query.filter_by(
        recipient_id=current_user().id,
        status="pending"
    ).order_by(
        Invitation.created_at.desc()
    ).all()

    outgoing = Invitation.query.filter_by(
        sender_id=current_user().id
    ).order_by(
        Invitation.created_at.desc()
    ).limit(30).all()

    return render_template(
        "invitations.html",
        incoming=incoming,
        outgoing=outgoing
    )


# =========================
# ACCEPT / DECLINE INVITATION
# =========================

@app.route(
    "/invitation/<int:inv_id>/<action>",
    methods=["POST"]
)
@login_required
def handle_invitation(inv_id, action):

    inv = Invitation.query.get_or_404(inv_id)

    if inv.recipient_id != current_user().id:
        abort(403)

    if inv.status != "pending":

        flash("Invitation already handled.", "error")

    elif action == "accept":

        if invite_or_join(inv.ride, current_user()):

            inv.status = "accepted"
            db.session.commit()

            flash(
                "Invitation accepted. You are now "
                "a ride member.",
                "success"
            )

        else:

            inv.status = "declined"
            db.session.commit()

            flash(
                "No seats available; invitation declined.",
                "error"
            )

    elif action == "decline":

        inv.status = "declined"
        db.session.commit()

        flash("Invitation declined.", "success")

    else:
        abort(400)

    return redirect(url_for("invitations"))


# =========================
# PRIVATE RIDE CHAT
# =========================

@app.route("/ride/<int:ride_id>/chat")
@login_required
def ride_chat(ride_id):

    ride = Ride.query.get_or_404(ride_id)

    if not can_chat(ride.id, current_user().id):
        abort(403)

    # Delete messages older than 30 days
    cutoff = datetime.utcnow() - timedelta(days=30)

    ChatMessage.query.filter(
        ChatMessage.ride_id == ride.id,
        ChatMessage.created_at < cutoff
    ).delete()

    db.session.commit()

    messages = ChatMessage.query.filter_by(
        ride_id=ride.id
    ).order_by(
        ChatMessage.created_at.asc()
    ).all()

    return render_template(
        "chat.html",
        ride=ride,
        messages=messages
    )


# =========================
# SOCKET.IO CHAT EVENTS
# =========================

@socketio.on("join_ride_chat")
def socket_join(data):

    user = current_user()
    try:
        ride_id = int((data or {}).get("ride_id", 0))
    except (TypeError, ValueError):
        return

    ride = db.session.get(Ride, ride_id)
    if user and ride and can_chat(ride_id, user.id):

        join_room(f"ride_{ride_id}")

        emit(
            "chat_notice",
            {"message": "Connected to ride chat."}
        )


@socketio.on("send_ride_message")
def socket_message(data):

    user = current_user()
    try:
        ride_id = int((data or {}).get("ride_id", 0))
    except (TypeError, ValueError):
        return

    message = ((data or {}).get("message") or "").strip()
    ride = db.session.get(Ride, ride_id)

    if not user or not ride or not can_chat(ride_id, user.id) or not message:
        return

    message = message[:1500]

    msg = ChatMessage(
        ride_id=ride_id,
        user_id=user.id,
        message=message
    )

    db.session.add(msg)
    db.session.commit()

    emit(
        "new_ride_message",
        {
            "name": user.name,
            "message": message,
            "time": msg.created_at.strftime("%d %b, %H:%M"),
            "user_id": user.id
        },
        room=f"ride_{ride_id}"
    )


# =========================
# CLEANUP COMMAND
# =========================

@app.cli.command("cleanup")
def cleanup():

    cutoff = datetime.utcnow() - timedelta(days=30)

    ChatMessage.query.filter(
        ChatMessage.created_at < cutoff
    ).delete()

    old_cutoff = datetime.utcnow() - timedelta(minutes=20)

    old_rides = Ride.query.filter(
        Ride.created_at < old_cutoff,
        Ride.status == "open"
    ).all()

    for ride in old_rides:

        if member_count(ride.id) == 1:
            ride.status = "expired"

    db.session.commit()

    print("Old chats cleaned and unjoined rides expired.")


# =========================
# DATABASE INITIALIZATION
# =========================

with app.app_context():
    db.create_all()


# =========================
# RUN APPLICATION
# =========================


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False,
        allow_unsafe_werkzeug=True
    )

