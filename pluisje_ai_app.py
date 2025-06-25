from flask import Flask, session, render_template, request, jsonify, redirect, url_for, flash
from openai import OpenAI
from flask_sqlalchemy import SQLAlchemy
import os
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app.secret_key = os.getenv("FLASK_SECRET_KEY")
debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')  # Database URL van Render
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class ChatMessage(db.Model):
    __tablename__ = 'chatmessages'  # sluit aan bij bestaande tabel
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

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
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        session["email"] = email
        return redirect(url_for("index"))
    return render_template("login.html")

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
    email = session.get("email") or "onbekend"

    if not user_input:
        return jsonify({"error": "Geen invoer ontvangen"}), 400

    if "messages" not in session:
        if session.get("user") == "bieb":
            role = "Je bent een AI-assistent voor bibliotheekmedewerkers..."
        else:
            role = "Je bent Pluisje de hamster-AI..."
        session["messages"] = [{"role": "system", "content": role}]

    session["messages"].append({"role": "user", "content": user_input})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=session["messages"]
        )
        assistant_reply = response.choices[0].message.content
        session["messages"].append({"role": "assistant", "content": assistant_reply})

        # Opslaan in database
        user_msg = ChatMessage(user_email=email, role="user", content=user_input)
        assistant_msg = ChatMessage(user_email=email, role="assistant", content=assistant_reply)
        db.session.add(user_msg)
        db.session.add(assistant_msg)
        db.session.commit()

        return jsonify({"response": assistant_reply})
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


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)