from flask import Flask, session, render_template, request, jsonify, redirect, url_for
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app.secret_key = os.getenv("FLASK_SECRET_KEY") or "pluisje-supergeheim"

# Welkomstmodus bepalen
@app.before_request
def check_user():
    email = session.get("email")
    if not email:
        session["user"] = "anita"
    elif email.endswith("@bibliotheekzout.nl"):
        session["user"] = "bieb"
    else:
        session["user"] = "anita"

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        session["email"] = email
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/")
def index():
    chat_history = session.get("messages", [])
    user_mode = session.get("user", "anita")
    return render_template("index.html", messages=chat_history, user=user_mode)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/reset")
def reset():
    session.pop("messages", None)
    return redirect(url_for("index"))

@app.route("/generate", methods=["POST"])
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
def generate_image():
    data = request.json
    user_prompt = data.get("prompt", "").strip()

    if not user_prompt:
        return jsonify({"error": "Geen prompt ontvangen"}), 400

    # Zorg dat er een systeemrol is
    if "messages" not in session:
        session["messages"] = [{"role": "system", "content": "Je bent Pluisje de hamster-AI. Je helpt Anita met slimme en lieve antwoorden, soms met een afbeelding."}]

    # Voeg gebruikersvraag toe aan geschiedenis
    session["messages"].append({"role": "user", "content": f"(beeldprompt) {user_prompt}"})

    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=user_prompt,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url

        # Voeg antwoord toe aan chatgeschiedenis
        session["messages"].append({
            "role": "assistant",
            "content": f"Hier is je afbeelding op basis van je prompt: {user_prompt}<br><img src='{image_url}' alt='Pluisje afbeelding' style='max-width:100%; border-radius: 12px; margin-top: 1rem;'>"
        })

        return jsonify({"image_url": image_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
