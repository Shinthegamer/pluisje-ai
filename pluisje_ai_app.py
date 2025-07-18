from flask import Flask, session, render_template, request, jsonify, redirect, url_for, flash
from openai import OpenAI
from flask_sqlalchemy import SQLAlchemy
import os
from dotenv import load_dotenv
from functools import wraps
import smtplib
from email.message import EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
from flask_migrate import Migrate
from itsdangerous import URLSafeTimedSerializer
from markupsafe import Markup

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")  # Eerst instellen
serializer = URLSafeTimedSerializer(app.secret_key)  # Dan gebruiken

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)


class ChatMessage(db.Model):
    __tablename__ = 'chatmessages'  # sluit aan bij bestaande tabel
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.path.startswith("/static/"):
            return f(*args, **kwargs)
        if not session.get("email"):
            # Redirect naar login ipv JSON voor normale routes
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": "Niet ingelogd"}), 401
            else:
                flash("Log eerst in om deze pagina te bezoeken.", "warning")
                return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# Welkomstmodus bepalen
@app.before_request
def check_user():
    email = session.get("email")
    if email:
        session["user"] = email.split('@')[0]  # toont alleen 'pluis' i.p.v. hele emailadres
    else:
        session["user"] = None

@app.route("/login", methods=["GET", "POST"])
def login():
    show_reset_link = False

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()

        if not user:
            flash("Geen account gevonden met dit e-mailadres.", "danger")
            return render_template("login.html", show_reset_link=False)

        if not user.is_verified:
            flash("Bevestig eerst je e-mailadres via de mail.", "warning")
            return render_template("login.html", show_reset_link=False)

        if not check_password_hash(user.password_hash, password):
            flash("Wachtwoord klopt niet.", "danger")
            return render_template("login.html", show_reset_link=True)

        session["email"] = user.email
        return redirect(url_for("index"))

    return render_template("login.html", show_reset_link=False)

@app.route("/")
@login_required
def index():
    email = session.get("email")
    chat_history = []

    if email:
        # Query alle berichten van deze gebruiker, chronologisch
        db_msgs = ChatMessage.query.filter_by(user_email=email).order_by(ChatMessage.timestamp).all()

        # Omzetten naar lijst dicts voor template, of direct doorgeven (kan ook in Jinja)
        chat_history = [{"role": m.role, "content": m.content} for m in db_msgs]

    user_mode = session.get("user")
    debug = app.debug
    return render_template("index.html", messages=chat_history, user=user_mode, debug=debug)
   
     
@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/reset")
@login_required
def reset():
    session.pop("messages", None)
    return redirect(url_for("index"))

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    data = request.json
    user_input = data.get("prompt", "").strip()
    email = session.get("email", "onbekend")

    if not user_input:
        return jsonify({"error": "Geen invoer ontvangen"}), 400

    # --------------------------
    # 1. Sessie + geheugen herstellen
    # --------------------------
    if "messages" not in session:
        # Basisprompt voor Pluisje
        session["messages"] = [{
            "role": "system",
            "content": (
                "Je bent Pluisje, een vriendelijke, slimme assistent. "
                "Beantwoord vragen in JSON-formaat met:\n\n"
                "{\n"
                "  \"long_response\": \"uitgebreide uitleg\",\n"
                "  \"short_response\": \"korte zin voor spraak\"\n"
                "}"
            )
        }]

        # Herstel recente context uit database (laatste 20)
        db_msgs = ChatMessage.query.filter_by(user_email=email).order_by(ChatMessage.timestamp).all()
        for msg in db_msgs[-20:]:
            session["messages"].append({"role": msg.role, "content": msg.content})

    # Voeg gebruikersprompt toe
    session["messages"].append({"role": "user", "content": user_input})

    try:
        # --------------------------
        # 2. GPT met JSON-output
        # --------------------------
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=session["messages"],
        )

        parsed = json.loads(response.choices[0].message.content)
        long_response = parsed.get("long_response", "").strip()
        short_response = parsed.get("short_response", "").strip()

        session["messages"].append({"role": "assistant", "content": long_response})

        # --------------------------
        # 3. Max. 50 berichten per gebruiker in DB
        # --------------------------
        MAX_MESSAGES = 12  # bijvoorbeeld
        existing_msgs = ChatMessage.query.filter_by(user_email=email).order_by(ChatMessage.id.asc()).all()

        if len(existing_msgs) >= MAX_MESSAGES:
            session["messages"] = session["messages"][-MAX_MESSAGES:]
            to_delete = existing_msgs[:len(existing_msgs) - MAX_MESSAGES + 2]  # +2 voor nieuwe paar
            for msg in to_delete:
                db.session.delete(msg)
            db.session.commit()

        # Nieuw paar opslaan
        db.session.add(ChatMessage(user_email=email, role="user", content=user_input))
        db.session.add(ChatMessage(user_email=email, role="assistant", content=long_response))
        db.session.commit()

        # --------------------------
        # 4. Terug naar frontend
        # --------------------------
        return jsonify({
            "long_response": long_response,
            "short_response": short_response
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
        

@app.route("/generate-image", methods=["POST"])
@login_required
def generate_image():
    data = request.json
    user_input = data.get("prompt", "").strip()
    email = session.get("email") or "onbekend"

    if not user_input:
        return jsonify({"error": "Geen prompt ontvangen"}), 400

    if "messages" not in session:
        session["messages"] = [{"role": "system", "content": "Je bent Pluisje de hamster-AI..."}]

    session["messages"].append({"role": "user", "content": user_input})

    try:
        prompt_samenvatting = client.chat.completions.create(
            model="gpt-4o",
            messages=session["messages"] + [
                {"role": "user", "content": "Vat mijn laatste idee samen als duidelijke beeldprompt voor een AI die een afbeelding gaat genereren."}
            ]
        ).choices[0].message.content

        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt_samenvatting,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url

        session["messages"].append({
            "role": "assistant",
            "content": f"Hier is je afbeelding:<br><img src='{image_url}' alt='Pluisje afbeelding' style='max-width:100%; border-radius: 12px; margin-top: 1rem;'>"
        })

        # Opslaan in database
        user_msg = ChatMessage(user_email=email, role="user", content=user_input)
        assistant_msg = ChatMessage(user_email=email, role="assistant", content=session["messages"][-1]["content"])
        db.session.add(user_msg)
        db.session.add(assistant_msg)
        db.session.commit()

        return jsonify({"image_url": image_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")

        if not email or not password:
            flash("Vul zowel e-mailadres als wachtwoord in.", "danger")
            return redirect(url_for("register"))

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Dit e-mailadres is al geregistreerd.", "warning")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)
        token = str(uuid.uuid4())

        new_user = User(
            email=email,
            password_hash=password_hash,
            is_verified=False,
            verification_token=token
        )

        db.session.add(new_user)
        db.session.commit()

        # Verstuur verificatiemail
        verify_link = url_for("verify_email", email=email, token=token, _external=True)

        msg = EmailMessage()
        msg["Subject"] = "Bevestig je registratie bij Pluisje.ai"
        msg["From"] = os.getenv("SMTP_USERNAME")
        msg["To"] = email
        msg.set_content("Je e-mailclient ondersteunt geen HTML.")

        msg.add_alternative(f"""\
        <html lang="nl">
            <head>
                    <meta charset="UTF-8">
                    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
                    <meta http-equiv="Content-Language" content="nl">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: sans-serif; background-color: #fffafc; padding: 2rem;">
                <div style="max-width: 500px; margin: auto; background: white; padding: 2rem; border-radius: 1rem; box-shadow: 0 0 10px rgba(0,0,0,0.05);">
                    <h1 style="color: #a0528c;">Welkom bij Pluisje.ai 🐹</h1>
                    <p>Hoi {email},</p>
                    <p>Klik op onderstaande knop om je e-mailadres te bevestigen:</p>
                    <a href="{verify_link}" style="display: inline-block; padding: 1rem 2rem; background-color: #ffb6c1; color: white; border-radius: 1rem; text-decoration: none; font-weight: bold;">Bevestig mijn account</a>
                    <p style="margin-top: 2rem; font-size: 0.9rem; color: #999;">Geen idee waar dit over gaat? Negeer deze mail dan gewoon.</p>
                    <p style="color:#888; font-size:0.85rem;">
                        Deze e-mail is automatisch gegenereerd. Antwoorden is niet mogelijk.
                    </p>
                </div>
            </body>
        </html>
        """, subtype="html")

        try:
            with smtplib.SMTP(os.getenv("SMTP_SERVER"), int(os.getenv("SMTP_PORT", 587))) as server:
                server.starttls()
                server.login(os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD"))
                server.send_message(msg)
            flash("Verificatielink verzonden naar je e-mail.", "success")
        except Exception as e:
            flash(f"Fout bij verzenden verificatiemail: {e}", "danger")

        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if not user:
            flash("Geen account gevonden met dit e-mailadres.", "warning")
            return redirect(url_for("forgot_password"))

        token = serializer.dumps(email, salt="reset")
        reset_link = url_for("reset_password", token=token, _external=True)

        msg = EmailMessage()
        msg["Subject"] = "Nieuw wachtwoord instellen voor Pluisje.ai"
        msg["From"] = os.getenv("SMTP_USERNAME")
        msg["To"] = email
        msg.set_content("Je e-mailclient ondersteunt geen HTML. Bezoek Pluisje.ai om je wachtwoord te wijzigen.")

        msg.add_alternative(f"""
            <!DOCTYPE html>
            <html lang="nl">
            <head>
                <meta charset="UTF-8">
                <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
                <meta http-equiv="Content-Language" content="nl">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: sans-serif; background-color: #fffafc; padding: 2rem;">
                <div style="max-width: 500px; margin: auto; background: white; padding: 2rem; border-radius: 1rem; box-shadow: 0 0 10px rgba(0,0,0,0.05);">
                    <h1 style="color: #a0528c;">Wachtwoord opnieuw instellen 🐹</h1>
                    <p>Hallo {email},</p>
                    <p>Je hebt aangegeven dat je je wachtwoord wilt aanpassen. Geen zorgen, dat is zo geregeld.</p>
                    <p>Klik op de knop hieronder om een nieuw wachtwoord in te stellen:</p>
                    <a href="{reset_link}" style="display: inline-block; padding: 1rem 2rem; background-color: #ffb6c1; color: white; border-radius: 1rem; text-decoration: none; font-weight: bold;">Nieuw wachtwoord instellen</a>
                    <p style="margin-top: 2rem; font-size: 0.9rem; color: #999;">Heb je dit verzoek niet gedaan? Dan kun je deze e-mail gewoon negeren.</p>
                    <p style="margin-top: 1rem; font-size: 0.85rem; color: #888;">Deze e-mail is automatisch verzonden. Je kunt niet reageren op dit bericht.</p>
                </div>
            </body>
        </html>
        """, subtype="html")

        try:
            with smtplib.SMTP(os.getenv("SMTP_SERVER"), int(os.getenv("SMTP_PORT", 587))) as server:
                server.starttls()
                server.login(os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD"))
                server.send_message(msg)
            flash("Een herstelmail is verzonden naar je inbox.", "success")
        except Exception as e:
            flash(f"Fout bij verzenden van de resetmail: {e}", "danger")

        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        email = serializer.loads(token, salt="reset", max_age=3600)  # 1 uur geldig
    except Exception:
        flash("Ongeldige of verlopen resetlink.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        new_pw = request.form.get("password")
        if not new_pw:
            flash("Voer een nieuw wachtwoord in.", "warning")
            return redirect(request.url)

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Gebruiker niet gevonden.", "danger")
            return redirect(url_for("login"))

        user.password_hash = generate_password_hash(new_pw)
        db.session.commit()
        flash("Je wachtwoord is bijgewerkt. Je kunt nu inloggen.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.route("/verify")
def verify():
    email = request.args.get("email")
    token = request.args.get("token")
    user = User.query.filter_by(email=email, verification_token=token).first()

    if not user:
        flash("Ongeldige of verlopen verificatielink.", "danger")
        return redirect(url_for("login"))

    if user.is_verified:
        flash("Je account is al geverifieerd. Je kunt inloggen.")
        return redirect(url_for("login"))

    user.is_verified = True
    db.session.commit()
    return render_template("welcome.html", email=email)

if __name__ == "__main__":
    # Alleen voor lokaal testen, handmatig draaien
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)