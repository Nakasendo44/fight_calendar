import os
import sys
import time
import re
import html
import xml.etree.ElementTree as ET
from datetime import datetime
import pytz
import requests
from google import genai
from google.genai import types

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Erreur critique : GEMINI_API_KEY introuvable.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

paris_tz = pytz.timezone("Europe/Paris")
now_paris = datetime.now(paris_tz)
date_str = now_paris.strftime("%d/%m/%Y")
heure_str = now_paris.strftime("%H:%M")

# Collecte de sources fraîches en temps réel
RSS_FEEDS = [
    ("Boxe Mondiale", "https://news.google.com/rss/search?q=boxing+(WBA+OR+WBC+OR+IBF+OR+WBO+OR+Matchroom+OR+Queensberry+OR+%22Top+Rank%22+OR+PBC+OR+%22Golden+Boy%22+OR+%22Riyadh+Season%22+OR+%22The+Ring%22)+when:3d&hl=en-US&gl=US&ceid=US:en"),
    ("Boxe France", "https://news.google.com/rss/search?q=%22boxe+anglaise%22+OR+boxeur+when:4d&hl=fr&gl=FR&ceid=FR:fr"),
    ("Boxe Japon", "https://news.google.com/rss/search?q=%E3%83%9C%E3%82%AF%E3%82%B7%E3%83%B3%E3%82%B0+(%E5%BE%8C%E6%A5%BD%E5%9C%92%E3%83%9B%E3%83%BC%E3%83%AB+OR+%E4%B8%96%E7%95%8C%E6%88%A6+OR+JBC)+when:4d&hl=ja&gl=JP&ceid=JP:ja"),
    ("Calendrier Combats", "https://news.google.com/rss/search?q=(%22fight+card%22+OR+%22main+event%22+OR+%22full+card%22)+(boxing+OR+UFC+OR+BKFC+OR+Glory+OR+Rizin)+when:6d&hl=en-US&gl=US&ceid=US:en"),
    ("Bad Left Hook", "https://www.badlefthook.com/rss/current.xml"),
    ("MMA Fighting", "https://www.mmafighting.com/rss/current.xml"),
]

def clean_text(raw_text):
    if not raw_text:
        return ""
    t = html.unescape(raw_text)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def collect_feeds():
    lines = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for cat, url in RSS_FEEDS:
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                for item in root.findall(".//item")[:8]:
                    title = clean_text(item.findtext("title", ""))
                    pub = clean_text(item.findtext("pubDate", ""))
                    desc = clean_text(item.findtext("description", ""))[:200]
                    if title:
                        lines.append(f"[{cat}] {pub} - {title}: {desc}")
        except Exception:
            pass
    return "\n".join(lines)

feed_data = collect_feeds()

# Modèle et configuration de base
def get_best_model():
    try:
        models = [m.name.replace("models/", "") for m in client.models.list()]
        for target in ["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.5-flash"]:
            if target in models:
                return target
    except Exception:
        pass
    return "gemini-3.8-flash"

ACTIVE_MODEL = get_best_model()

# ==============================================================================
# CSS ÉDITORIAL MODERNE (BEIGE & MARRON CHAUD)
# ==============================================================================
SHARED_CSS = """
    :root {
        --bg-main: #F9F6F0;
        --card-bg: #FFFFFF;
        --card-alt: #F4EFE6;
        --text-main: #2C221E;
        --text-muted: #6B5B52;
        --accent-brown: #8A4B29;
        --accent-dark: #4A2E1B;
        --border-color: #E6DFD3;
        --font-serif: 'Georgia', 'Cambria', serif;
        --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
        margin: 0;
        padding: 0;
        background-color: var(--bg-main);
        color: var(--text-main);
        font-family: var(--font-sans);
        line-height: 1.65;
        font-size: 16px;
    }
    .wrapper {
        max-width: 900px;
        margin: 0 auto;
        padding: 2.5rem 1.25rem;
    }
    header {
        text-align: center;
        margin-bottom: 2rem;
        padding-bottom: 1.5rem;
        border-bottom: 2px solid var(--border-color);
    }
    h1 {
        font-family: var(--font-serif);
        font-size: 2.3rem;
        color: var(--accent-dark);
        margin: 0 0 0.5rem 0;
        letter-spacing: -0.5px;
    }
    .subtitle {
        color: var(--text-muted);
        font-size: 1rem;
        margin-bottom: 0.8rem;
    }
    .badge-date {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        font-size: 0.85rem;
        background-color: var(--card-alt);
        color: var(--accent-dark);
        border: 1px solid var(--border-color);
        border-radius: 20px;
        font-weight: 500;
    }
    /* Barre de navigation moderne */
    nav.main-nav {
        display: flex;
        justify-content: center;
        gap: 1rem;
        margin-bottom: 2.5rem;
    }
    nav.main-nav a {
        text-decoration: none;
        padding: 0.65rem 1.4rem;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.95rem;
        transition: all 0.2s ease;
        border: 1px solid var(--border-color);
        background-color: var(--card-bg);
        color: var(--text-muted);
    }
    nav.main-nav a.active {
        background-color: var(--accent-brown);
        color: #FFFFFF;
        border-color: var(--accent-brown);
    }
    nav.main-nav a:hover:not(.active) {
        background-color: var(--card-alt);
        color: var(--accent-dark);
    }
    /* Typographie des articles */
    h2 {
        font-family: var(--font-serif);
        color: var(--accent-dark);
        font-size: 1.7rem;
        margin-top: 2rem;
        margin-bottom: 1.2rem;
        border-bottom: 1px solid var(--border-color);
        padding-bottom: 0.4rem;
    }
    h3 {
        font-family: var(--font-serif);
        color: var(--accent-brown);
        font-size: 1.35rem;
        margin-top: 1.5rem;
        margin-bottom: 0.6rem;
    }
    p { margin-top: 0; margin-bottom: 1.1rem; }
    article.news-item {
        background-color: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: 10px;
        padding: 1.5rem;
        margin-bottom: 1.6rem;
        box-shadow: 0 2px 6px rgba(74, 46, 27, 0.03);
    }
    blockquote {
        margin: 1.2rem 0;
        padding: 1rem 1.3rem;
        background-color: var(--card-alt);
        border-left: 4px solid var(--accent-brown);
        border-radius: 0 8px 8px 0;
        font-style: italic;
        color: var(--accent-dark);
    }
    /* Cartes du calendrier */
    .event-card {
        background-color: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: 10px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 2px 6px rgba(74, 46, 27, 0.03);
    }
    .event-card .meta-info {
        font-size: 0.9rem;
        color: var(--text-muted);
        margin-bottom: 0.8rem;
    }
    .meta-tag {
        display: inline-block;
        background: var(--card-alt);
        padding: 0.2rem 0.5rem;
        border-radius: 4px;
        margin-right: 0.4rem;
        font-weight: 500;
        color: var(--accent-dark);
    }
    details {
        margin-top: 1.2rem;
        border: 1px solid var(--border-color);
        border-radius: 8px;
        padding: 0.8rem 1.2rem;
        background-color: var(--card-alt);
    }
    summary {
        font-weight: 600;
        color: var(--accent-brown);
        cursor: pointer;
        outline: none;
    }
    details ul {
        margin-top: 1rem;
        padding-left: 1.2rem;
    }
    details li {
        margin-bottom: 0.8rem;
    }
    footer {
        text-align: center;
        margin-top: 3.5rem;
        padding-top: 1.5rem;
        border-top: 1px solid var(--border-color);
        color: var(--text-muted);
        font-size: 0.85rem;
    }
"""

def build_html(page_title, active_tab, main_content):
    actus_active = "active" if active_tab == "actus" else ""
    cal_active = "active" if active_tab == "cal" else ""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title} - Le QG des Sports de Combat</title>
    <style>{SHARED_CSS}</style>
</head>
<body>
    <div class="wrapper">
        <header>
            <h1>🥊 Le QG des Sports de Combat</h1>
            <div class="subtitle">L'actualité décryptée & le radar complet des galas de la semaine</div>
            <span class="badge-date">Édition du {date_str} à {heure_str} (Heure de Paris)</span>
        </header>

        <nav class="main-nav">
            <a href="index.html" class="{actus_active}">📰 Actualités & Décryptages</a>
            <a href="calendrier.html" class="{cal_active}">📅 Calendrier des Combats</a>
        </nav>

        <main>
            {main_content}
        </main>

        <footer>
            <p>Rédaction & Veille automatique multilingue. Unités standardisées (kg, m) et heures calées sur Paris (CET/CEST).</p>
        </footer>
    </div>
</body>
</html>
"""

# ==============================================================================
# PROMPT 1 : ACTUALITÉS EN PROFONDEUR
# ==============================================================================
prompt_actus = f"""Tu es un grand reporter et éditorialiste spécialisé dans les sports de combat.
Date : {date_str}. Rédige la page des actualités en français irréprochable et journalistique.

DIRECTIVES DE PROFONDEUR :
- Ne sois PAS succint. Chaque actualité de boxe doit être un article approfondi composé de 2 à 3 paragraphes détaillés (contexte sportif, enjeux de carrière, coulisses politiques ou financières, impact sur les ceintures et les classements mondiaux).
- Poids impérativement convertis en kg (ex: 63,5 kg) et tailles en mètres (ex: 1m78).
- Conserve les termes techniques du métier (puncher, out-boxer, clinch, southpaw, pound-for-pound, etc.).

STRUCTURE ATTENDUE :
1. "Grand Format : Boxe Anglaise Internationale" (3 à 4 grands articles détaillés sur les ceintures WBA, WBC, IBF, WBO, The Ring, et les promoteurs Matchroom, Queensberry, Top Rank, PBC, Riyadh Season). Encapsule chaque fait dans un <article class="news-item">.
2. "Focus Asie & Japon" (Actualités détaillées sur les champions japonais et les événements au Korakuen Hall).
3. "Scène Française" (Boxeurs tricolores, championnats EBU ou France).
4. "En Bref : MMA, Kickboxing & Lutte" (UFC, Rizin, BKFC, RISE, K-1, Glory, RAF : une synthèse propre mais substantielle).
5. Rumeurs / Déclarations : doivent être insérées dans un <blockquote><strong>[Rumeur / En négociation]</strong> ...</blockquote>.

Voici les dépêches de veille récentes :
{feed_data}

Renvoie STRICTEMENT le contenu HTML interne à insérer dans la page. Pas de balises ```html.
"""

# ==============================================================================
# PROMPT 2 : CALENDRIER FIABLE SANS HALLUCINATIONS
# ==============================================================================
prompt_cal = f"""Tu es un documentaliste rigoureux spécialisé dans les programmes de sports de combat.
Date de référence : {date_str}. Analyse les 7 prochains jours.

RÈGLE D'OR CONTRE LES ERREURS (ANTI-HALLUCINATION) :
- INTERDICTION FORMELLE D'INVENTER DES COMBATS, DES ADVERSAIRES INCONNUS (« TBA ») OU D'ANCIENS RECORDS.
- Pour chaque combat cité, les palmarès doivent être vérifiés et fidèles à la réalité actuelle (ex: Dalton Smith est invaincu à 19-0 et non un ancien record à 16-0). Si l'undercard n'est pas encore rendue publique, écris simplement : "Reste de la carte en cours de finalisation par l'organisation". N'invente pas des boxeurs qui ne boxent pas sur cette carte.
- Heures obligatoirement calées sur le fuseau horaire de Paris.
- Poids en kg et tailles en mètres.

STRUCTURE DU CALENDRIER (avec <div class="event-card">) :
Rubriques (<h3>) :
1. Boxe anglaise - Galas Internationaux
2. Boxe anglaise - Galas au Japon (Korakuen Hall inclus)
3. UFC
4. Rizin
5. BKFC
6. Kickboxing Japonais (RISE, K-1, Krush)
7. Glory Kickboxing
8. RAF (Real American Freestyle wrestling)

Pour chaque gala :
- Titre du gala & Lieu précis
- Heures : Début du gala & Heure estimée du combat principal (Heure de Paris)
- Diffuseurs : Origine + Diffusion officielle en France (ex: DAZN France, RMC Sport, etc.)
- Présentation percutante du choc principal
- <details><summary>Développer la carte complète des combats</summary>
    <ul>
      <!-- Liste exacte et factuelle des combats confirmés avec palmarès réels (V-D-N, KOs) et catégories en kg -->
    </ul>
  </details>
Si aucun gala n'a lieu dans une discipline secondaire, mentionne : <p><em>Aucun événement majeur programmé cette semaine.</em></p>.

Voici les données collectées :
{feed_data}

Renvoie STRICTEMENT le fragment HTML interne. Pas de balises ```html.
"""

def generate_section(prompt_text, title_log):
    print(f"Génération : {title_log}...")
    try:
        # Essai avec Grounding Search pour garantir des palmarès réels et des cartes vérifiées
        resp = client.models.generate_content(
            model=ACTIVE_MODEL,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1
            )
        )
        if resp and resp.text:
            return resp.text.replace("```html", "").replace("```", "").strip()
    except Exception as e:
        print(f"Repli sans tool Google Search ({e})...")
        try:
            resp = client.models.generate_content(
                model=ACTIVE_MODEL,
                contents=prompt_text,
                config=types.GenerateContentConfig(temperature=0.1)
            )
            if resp and resp.text:
                return resp.text.replace("```html", "").replace("```", "").strip()
        except Exception as e2:
            print(f"Erreur définitive de génération : {e2}")
    return "<p><em>Contenu en cours de synchronisation. Veuillez réactualiser dans quelques instants.</em></p>"

# 1. Génération des Actus (index.html)
actus_body = generate_section(prompt_actus, "Page Actualités Approfondies")
page_actus_html = build_html("Actualités & Décryptages", "actus", actus_body)
with open("index.html", "w", encoding="utf-8") as f:
    f.write(page_actus_html)
print("-> Fichier index.html écrit.")

# Pause de sécurité pour préserver le quota gratuit
time.sleep(10)

# 2. Génération du Calendrier (calendrier.html)
cal_body = generate_section(prompt_cal, "Page Calendrier & Fight Cards")
page_cal_html = build_html("Calendrier des Combats", "cal", cal_body)
with open("calendrier.html", "w", encoding="utf-8") as f:
    f.write(page_cal_html)
print("-> Fichier calendrier.html écrit.")

print("Les deux pages ont été générées avec succès !")
