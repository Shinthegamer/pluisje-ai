# Pluisje AI 🐹

Een vrolijke AI-app gebouwd met Flask en OpenAI (GPT-4o + DALL·E 3).

## Features
- Chatten met Pluisje de hamster
- Genereert tekst én afbeeldingen
- Herinnert voorgaande gesprekken
- Licht en vrolijk thema

## Installatie
1. Kopieer `.env.template` naar `.env` en vul je eigen API keys in.
2. Installeer afhankelijkheden:
   ```
   pip install -r requirements.txt
   ```
3. Start de app:
   ```
   python app.py
   ```

## Deploy via Render
1. Push deze repo naar GitHub.
2. Maak een nieuwe Web Service op [Render.com](https://render.com).
3. Stel in:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python app.py`
   - Voeg je API-sleutels toe als environment variables
