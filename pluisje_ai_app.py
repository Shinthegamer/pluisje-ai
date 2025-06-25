from flask import Flask, session, render_template, request, jsonify, redirect, url_for, flash
from openai import OpenAI
import os
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app.secret_key = os.getenv("FLASK_SECRET_KEY")
debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"

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
    chat_history = session.get("messages", [])
    user_mode = session.get("user")
    debug = app.debug  # of via environment variable
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

    if not user_input:
        return jsonify({"error": "Geen invoer ontvangen"}), 400

    if len(user_input) > 1000:
        return jsonify({"error": "De invoer is te lang (max. 1000 tekens)."}), 400

    # Voorbeeld van systeemrol aanpassen per gebruiker
    if "messages" not in session:
        if session.get("user") == "bieb":
            role = "Je bent een AI-assistent voor bibliotheekmedewerkers. Gebruik heldere, vriendelijke en toegankelijke Nederlandse taal."
        else:
            role = "Je bent Pluisje de hamster-AI. Je bent nieuwsgierig, speels en helpt Anita met slimme en lieve antwoorden. Spreek op een vrolijke toon en gebruik af en toe een hamstergrapje."
        session["messages"] = [{"role": "system", "content": role}]

    # Voeg de vraag van de gebruiker toe
    session["messages"].append({"role": "user", "content": user_input})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=session["messages"]
        )
        assistant_reply = response.choices[0].message.content
        session["messages"].append({"role": "assistant", "content": assistant_reply})
        return jsonify({"response": assistant_reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/generate-image", methods=["POST"])
@login_required
def generate_image():
    data = request.json
    user_input = data.get("prompt", "").strip()

    if not user_input:
        return jsonify({"error": "Geen prompt ontvangen"}), 400

    # Zorg dat er een systeemrol is
    if "messages" not in session:
        session["messages"] = [{"role": "system", "content": "Je bent Pluisje de hamster-AI. Je helpt Anita met slimme en lieve antwoorden, soms met een afbeelding."}]

    # Voeg de laatste beeldprompt als user input toe (voor transparantie)
    session["messages"].append({"role": "user", "content": user_input})

    try:
        # Vraag GPT om een visuele prompt op basis van de context
        prompt_samenvatting = client.chat.completions.create(
            model="gpt-4o",
            messages=session["messages"] + [
                {"role": "user", "content": "Vat mijn laatste idee samen als duidelijke beeldprompt voor een AI die een afbeelding gaat genereren."}
            ]
        ).choices[0].message.content

        # Genereer afbeelding met DALL-E
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt_samenvatting,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url

        # Voeg antwoord toe aan chatgeschiedenis
        session["messages"].append({
            "role": "assistant",
            "content": f"Hier is je afbeelding:<br><img src='{image_url}' alt='Pluisje afbeelding' style='max-width:100%; border-radius: 12px; margin-top: 1rem;'>"
        })

        return jsonify({"image_url": image_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)