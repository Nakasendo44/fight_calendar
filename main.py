import os
import sys
import time
import re
import html
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
import pytz
import requests
from google import genai
from google.genai import types

# 1. Vérification de la clé API
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Erreur critique : GEMINI_API_KEY introuvable.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

# 2. Fuseau horaire de Paris
paris_tz = pytz.timezone("Europe/Paris")
now_paris = datetime.now(paris_tz)
date_str = now_paris.strftime("%d/%m/%Y")
heure_str = now_paris.strftime("%H:%M")

HEADERS = {
    "User-Agent": "FightCalendarApp/2.0 (https://github.com/Nakasendo44/fight_calendar; contact@github.com)"
}

# ==============================================================================
# ÉTAPE 1 : EXTRACTION DIRECTE DES CALENDRIERS OFFICIELS (ANTI-HALLUCINATION)
# ==============================================================================
DIRECT_SCHEDULE_SOURCES = [
    ("Calendrier Boxe Officiel (Box.live)", "https://r.jina.ai/https://box.live/upcoming-fights-schedule/"),
    ("Matchroom Boxing Events", "https://r.jina.ai/https://www.matchroomboxing.com/events/"),
    ("Tapology Fightcenter (MMA & Boxe)", "https://r.jina.ai/https://www.tapology.com/fightcenter"),
    ("UFC Events Officiel", "https://r.jina.ai/https://www.ufc.com/events"),
]

RSS_SCHEDULE_SOURCES = [
    ("Programmes & Fight Cards (Global)", "https://news.google.com/rss/search?q=(%22fight+card%22+OR+%22undercard%22+OR+%22main+event%22+OR+%22start+time%22)+(boxing+OR+Matchroom+OR+Queensberry+OR+UFC+OR+BKFC+OR+Glory)+when:7d&hl=en-US&gl=US&ceid=US:en"),
    ("Boxe Japon & Korakuen Hall", "https://news.google.com/rss/search?q=%E3%83%9C%E3%82%AF%E3%82%B7%E3%83%B3%E3%82%B0+(%E5%BE%8C%E6%A5%BD%E5%9C%92%E3%83%9B%E3%83%BC%E3%83%AB+OR+%E8%88%88%E8%A1%8C+OR+%E6%97%A5%E7%A8%8B+OR+%E5%AF%BE%E6%88%A6%E3%82%AB%E3%83%BC%E3%83%89)+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
    ("Rizin & Kickboxing Japonais (RISE, K-1, Krush)", "https://news.google.com/rss/search?q=(RIZIN+OR+RISE+OR+K-1+OR+Krush)+(%E5%AF%BE%E6%88%A6%E3%82%AB%E3%83%BC%E3%83%89+OR+%E5%A4%A7%E4%BC%9A+OR+%E9%96%8B%E5%82%AC)+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
    ("RAF Wrestling & Glory Kickboxing", "https://news.google.com/rss/search?q=%22Real+American+Freestyle%22+OR+%22Glory+Kickboxing%22+when:10d&hl=en-US&gl=US&ceid=US:en"),
]

def clean_text(raw):
    if not raw:
        return ""
    t = html.unescape(raw)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def gather_verified_schedules():
    print("Extraction des calendriers officiels en cours...")
    chunks = []

    # 1. Lecture des pages de calendriers via lecteur Markdown
    for label, url in DIRECT_SCHEDULE_SOURCES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code == 200 and len(resp.text) > 200:
                snippet = resp.text[:6000]
                chunks.append(f"\n=== SOURCE DIRECTE : {label} ===\n{snippet}")
                print(f"  [OK] {label}")
        except Exception as e:
            print(f"  [Info] Source directe ignorée ({label}) : {e}")

    # 2. Lecture des flux RSS de programmation
    for label, url in RSS_SCHEDULE_SOURCES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                items = root.findall(".//item")[:12]
                if items:
                    chunks.append(f"\n=== FLUX PROGRAMMATION : {label} ===")
                for item in items:
                    title = clean_text(item.findtext("title", ""))
                    pub = clean_text(item.findtext("pubDate", ""))
                    desc = clean_text(item.findtext("description", ""))[:250]
                    if title:
                        chunks.append(f"- [{pub}] {title} | {desc}")
        except Exception as e:
            print(f"  [Info] Flux ignoré ({label}) : {e}")

    return "\n".join(chunks)

verified_raw_data = gather_verified_schedules()

# ==============================================================================
# ÉTAPE 2 : RÉCUPÉRATION AUTOMATIQUE DES PHOTOS DES COMBATTANTS (WIKIPÉDIA EN/JA)
# ==============================================================================
PHOTO_CACHE = {}

def fetch_fighter_photo(fighter_name):
    name = fighter_name.strip()
    if not name or name.upper() in ["TBA", "TBD", "INCONNU"]:
        return None
    if name in PHOTO_CACHE:
        return PHOTO_CACHE[name]

    # Recherche sur Wikipédia Anglais puis Japonais
    for lang in ["en", "ja", "fr"]:
        try:
            # 1. Recherche du titre exact de la page du combattant
            search_url = (
                f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search"
                f"&srsearch={urllib.parse.quote(name)}&srlimit=1&format=json"
            )
            r_search = requests.get(search_url, headers=HEADERS, timeout=5).json()
            search_results = r_search.get("query", {}).get("search", [])
            if not search_results:
                continue

            page_title = search_results[0]["title"]

            # 2. Récupération de l'image miniature haute qualité (largeur 400px)
            img_url = (
                f"https://{lang}.wikipedia.org/w/api.php?action=query"
                f"&titles={urllib.parse.quote(page_title)}&prop=pageimages"
                f"&piprop=thumbnail&pithumbsize=400&format=json"
            )
            r_img = requests.get(img_url, headers=HEADERS, timeout=5).json()
            pages = r_img.get("query", {}).get("pages", {})
            for _, page_data in pages.items():
                thumb = page_data.get("thumbnail", {}).get("source")
                if thumb and not any(ext in thumb.lower() for ext in [".svg", "flag_of", "icon", "replace_this"]):
                    PHOTO_CACHE[name] = thumb
                    return thumb
        except Exception:
            continue

    PHOTO_CACHE[name] = None
    return None

def get_initials(name):
    parts = [p for p in re.split(r"[\s\-]+", name.strip()) if p]
    if not parts:
        return "🥊"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()

def build_faceoff_banner(match):
    f1 = html.unescape(match.group(1)).strip()
    f2 = html.unescape(match.group(2)).strip()
    belt_label = html.unescape(match.group(3)).strip()

    img1 = fetch_fighter_photo(f1)
    img2 = fetch_fighter_photo(f2)

    init1 = get_initials(f1)
    init2 = get_initials(f2)

    avatar1_html = (
        f'<img src="{img1}" alt="{html.escape(f1)}" class="fighter-img" '
        f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">'
        f'<div class="fighter-fallback" style="display:none;">{init1}</div>'
        if img1 else f'<div class="fighter-fallback">{init1}</div>'
    )

    avatar2_html = (
        f'<img src="{img2}" alt="{html.escape(f2)}" class="fighter-img" '
        f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">'
        f'<div class="fighter-fallback" style="display:none;">{init2}</div>'
        if img2 else f'<div class="fighter-fallback">{init2}</div>'
    )

    belt_badge = f'<div class="faceoff-belt">👑 {html.escape(belt_label)}</div>' if belt_label else ""

    return f"""
    <div class="faceoff-banner">
        {belt_badge}
        <div class="faceoff-grid">
            <div class="fighter-col">
                <div class="avatar-wrap">{avatar1_html}</div>
                <span class="fighter-name">{html.escape(f1)}</span>
            </div>
            <div class="vs-col">
                <span class="vs-badge">VS</span>
            </div>
            <div class="fighter-col">
                <div class="avatar-wrap">{avatar2_html}</div>
                <span class="fighter-name">{html.escape(f2)}</span>
            </div>
        </div>
    </div>
    """

def inject_fighter_photos(raw_html):
    print("Recherche et intégration des photos des combattants principaux...")
    pattern = r'<div\s+class="faceoff-slot"\s+data-f1="([^"]*)"\s+data-f2="([^"]*)"\s+data-label="([^"]*)"\s*>\s*</div>'
    return re.sub(pattern, build_faceoff_banner, raw_html)

# ==============================================================================
# ÉTAPE 3 : PROMPT STRICT DE GÉNÉRATION DU CALENDRIER
# ==============================================================================
CALENDAR_PROMPT = f"""Tu es un rédacteur en chef et documentaliste sportif spécialisé en Boxe Anglaise, MMA, Kickboxing et Lutte.
Date actuelle de référence : {date_str} (Fuseau horaire : Europe/Paris).
Ton unique mission est de générer le CALENDRIER HEBDOMADAIRE ULTRA-FIABLE des combats prévus sur les 7 prochains jours (du {date_str} à J+7).

RÈGLES ABSOLUES DE FIABILITÉ (ZÉRO HALLUCINATION) :
1. Vérifie chaque gala, chaque palmarès (Victoires-Défaites-Nuls, KOs) et chaque carte complète (undercard) en croisant ta recherche Google en direct avec les données brutes ci-dessous.
2. INTERDICTION STRICTE d'inventer des combats sur une sous-carte ou d'écrire des "vs TBA" fictifs. Affiche la vraie carte dévoilée par le promoteur (ex: pour un gala Matchroom, Queensberry, Top Rank, UFC, Rizin, affiche les vrais combats officiels de la carte).
3. Langue et style : Français journalistique élégant, dynamique et agréable à lire. Conserve les termes techniques du noble art (puncher, out-boxer, brawler, southpaw, jab, uppercut, hook, knockdown, KO, TKO, main event, co-main event, undercard, contender, undisputed).
4. Unités : Tous les poids doivent être convertis en kilogrammes (ex: -63,5 kg) et toutes les tailles en mètres (ex: 1m78).
5. Horaires : Toutes les heures (début du gala et heure du combat principal) doivent être converties à l'heure de Paris (CET/CEST).
6. Mise en forme riche : Utilise des émojis pertinents (👑, 📍, ⏰, 📺, 🥊, 🔥, 📊) et mets en **gras** (`<strong>`) les éléments importants (noms, records, enjeux, chaînes).

STRUCTURE EXACTE DES 8 RUBRIQUES À PRODUIRE :
Utilise exactement ces identifiants HTML pour chaque section :
1. <section id="boxe-monde" class="sport-section"><h2>🥊 1. Boxe Anglaise — Galas Internationaux</h2> ... </section>
2. <section id="boxe-japon" class="sport-section"><h2>🇯🇵 2. Boxe Anglaise — Galas au Japon (Korakuen Hall & Arènes)</h2> ... </section>
3. <section id="ufc" class="sport-section"><h2>🔥 3. UFC</h2> ... </section>
4. <section id="rizin" class="sport-section"><h2>🌸 4. Rizin Fighting Federation</h2> ... </section>
5. <section id="bkfc" class="sport-section"><h2>👊 5. BKFC (Bare Knuckle)</h2> ... </section>
6. <section id="kick-japon" class="sport-section"><h2>🦵 6. Kickboxing Japonais (RISE, K-1, Krush)</h2> ... </section>
7. <section id="glory" class="sport-section"><h2>🏆 7. Glory Kickboxing</h2> ... </section>
8. <section id="raf" class="sport-section"><h2>🤼 8. RAF (Real American Freestyle Wrestling)</h2> ... </section>

MODÈLE OBLIGATOIRE POUR CHAQUE ÉVÉNEMENT (<article class="event-card">) :
À l'intérieur de chaque `<article class="event-card">`, insère OBLIGATOIREMENT en toute première ligne cette balise exacte (le script Python l'utilisera pour afficher les photos des 2 combattants du Main Event) :
<div class="faceoff-slot" data-f1="Prénom Nom du Combattant 1" data-f2="Prénom Nom du Combattant 2" data-label="Catégorie en kg & Ceinture en jeu"></div>

Puis affiche :
- <h3 class="event-title">Nom officiel du Gala : Combattant 1 vs. Combattant 2</h3>
- <div class="meta-grid">
    <div class="meta-item">📍 <strong>Lieu :</strong> Salle, Ville, Pays</div>
    <div class="meta-item">⏰ <strong>Horaires (Paris) :</strong> Début à <strong>XXhXX</strong> | Main Event vers <strong>XXhXX</strong></div>
    <div class="meta-item">📺 <strong>Diffusion :</strong> <span class="badge-tv">Origine : Nom Chaîne</span> <span class="badge-tv badge-fr">France : Nom Chaîne</span></div>
  </div>
- <p class="event-desc"><strong>🎯 Présentation & Enjeux :</strong> Présentation claire et percutante de l'événement et des combats principaux en 2 à 3 phrases.</p>
- <details>
    <summary>📊 Développer la carte complète, palmarès et analyse des combats</summary>
    <div class="fight-list">
      Pour chaque combat confirmé de la carte (du Main Event jusqu'aux préliminaires connus), crée un bloc `<div class="bout-item">` contenant :
      * 🥊 <strong>Nom A (Record V-D-N, X KOs | 1mXX) vs. Nom B (Record V-D-N, Y KOs | 1mXX)</strong> — <span class="weight-pill">Catégorie (XX,X kg)</span>
      * Courte présentation stylistique et enjeu du combat en 1 à 2 phrases.
    </div>
  </details>

Si aucun événement n'est programmé cette semaine dans un sport secondaire, affiche dans la section :
<div class="empty-state">🗓️ Aucun événement majeur programmé cette semaine dans cette catégorie.</div>
(Rappel : pour la boxe anglaise internationale et japonaise, il y a toujours des réunions chaque semaine ; répertorie les galas continentaux, nationaux ou du Korakuen Hall).

DONNÉES BRUTES DE RÉFÉRENCE COLLECTÉES AUJOURD'HUI :
{verified_raw_data}

Renvoie UNIQUEMENT le code HTML des 8 sections (`<section ...>...</section>`). N'inclus jamais de balises Markdown ```html ou ```.
"""

# ==============================================================================
# ÉTAPE 4 : APPEL API GEMINI AVEC REPLI AUTOMATIQUE
# ==============================================================================
def get_available_models():
    preferred = ["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.5-flash"]
    found = []
    try:
        for m in client.models.list():
            name = getattr(m, "name", "").replace("models/", "")
            if "gemini" in name and not any(x in name for x in ["embedding", "vision", "imagen", "audio", "tts"]):
                found.append(name)
    except Exception:
        pass
    ordered = [p for p in preferred if not found or p in found]
    for f in found:
        if f not in ordered:
            ordered.append(f)
    return ordered or ["gemini-3.8-flash"]

models_to_try = get_available_models()
calendar_content = None

# Tentative avec Google Search activé pour vérifier chaque carte en direct
for model_name in models_to_try[:2]:
    try:
        print(f"Génération du calendrier vérifié via {model_name} (Search Grounding actif)...")
        resp = client.models.generate_content(
            model=model_name,
            contents=CALENDAR_PROMPT,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
        if resp and getattr(resp, "text", None):
            calendar_content = resp.text
            print(f"Succès avec {model_name} !")
            break
    except Exception as e:
        print(f"Avertissement ({model_name} + Search) : {e}")
        time.sleep(3)

# Repli sans l'outil Search si le quota Search est temporairement saturé
if not calendar_content:
    for model_name in models_to_try:
        try:
            print(f"Repli sur {model_name} avec les données extraites...")
            resp = client.models.generate_content(
                model=model_name,
                contents=CALENDAR_PROMPT,
                config=types.GenerateContentConfig(temperature=0.1),
            )
            if resp and getattr(resp, "text", None):
                calendar_content = resp.text
                print(f"Succès avec {model_name} !")
                break
        except Exception as e:
            print(f"Modèle {model_name} indisponible : {e}")
            time.sleep(2)

if calendar_content:
    calendar_content = calendar_content.replace("```html", "").replace("```", "").strip()
    calendar_content = inject_fighter_photos(calendar_content)
else:
    calendar_content = "<div class='empty-state'>⚠️ Synchronisation en cours. Veuillez rafraîchir la page d'ici quelques minutes.</div>"

# ==============================================================================
# ÉTAPE 5 : DESIGN MODERNE BEIGE / MARRON CHAUD & TYPOGRAPHIE SOIGNÉE
# ==============================================================================
HTML_TEMPLATE = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Calendrier des Combats — Boxe, MMA & Kickboxing</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-page: #F6F1E7;
            --bg-card: #FFFDF9;
            --bg-subtle: #EDE5D6;
            --bg-details: #F9F5ED;
            --text-primary: #261A13;
            --text-secondary: #5E4B3E;
            --accent-main: #9A4E24;
            --accent-dark: #4A2812;
            --accent-gold: #C4882B;
            --border-warm: #DFD4C1;
            --shadow-soft: 0 8px 24px rgba(58, 35, 18, 0.06);
        }}
        * {{
            box-sizing: border-box;
            scroll-behavior: smooth;
        }}
        body {{
            margin: 0;
            padding: 0;
            background-color: var(--bg-page);
            background-image: radial-gradient(circle at 50% 0%, #FFFDF8 0%, #F6F1E7 70%);
            color: var(--text-primary);
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            font-size: 16px;
            line-height: 1.65;
        }}
        .container {{
            max-width: 940px;
            margin: 0 auto;
            padding: 2rem 1.2rem 4rem 1.2rem;
        }}
        /* En-tête */
        header.hero {{
            text-align: center;
            padding: 2rem 1.5rem;
            background: linear-gradient(135deg, #3B2213 0%, #5E341B 100%);
            color: #FFFDF9;
            border-radius: 18px;
            margin-bottom: 1.5rem;
            box-shadow: 0 10px 28px rgba(59, 34, 19, 0.18);
        }}
        header.hero h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.3rem;
            font-weight: 800;
            margin: 0 0 0.5rem 0;
            letter-spacing: -0.5px;
            color: #FFFDF9;
        }}
        header.hero p {{
            margin: 0 0 1rem 0;
            color: #E8DACA;
            font-size: 1.02rem;
        }}
        .update-pill {{
            display: inline-block;
            background-color: rgba(255, 253, 249, 0.14);
            border: 1px solid rgba(255, 253, 249, 0.25);
            color: #FFFDF9;
            padding: 0.35rem 0.9rem;
            border-radius: 99px;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        /* Barre d'accès rapide par sport */
        .quick-nav {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            justify-content: center;
            margin-bottom: 2.5rem;
            padding: 0.8rem;
            background-color: var(--bg-card);
            border: 1px solid var(--border-warm);
            border-radius: 14px;
            box-shadow: var(--shadow-soft);
            position: sticky;
            top: 10px;
            z-index: 50;
        }}
        .quick-nav a {{
            text-decoration: none;
            color: var(--accent-dark);
            background-color: var(--bg-subtle);
            padding: 0.45rem 0.85rem;
            border-radius: 8px;
            font-size: 0.86rem;
            font-weight: 700;
            transition: all 0.2s ease;
        }}
        .quick-nav a:hover {{
            background-color: var(--accent-main);
            color: #FFFFFF;
            transform: translateY(-1px);
        }}
        /* Sections par sport */
        .sport-section {{
            margin-bottom: 3rem;
            scroll-margin-top: 85px;
        }}
        .sport-section h2 {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.65rem;
            font-weight: 800;
            color: var(--accent-dark);
            margin-bottom: 1.2rem;
            padding-bottom: 0.5rem;
            border-bottom: 3px solid var(--accent-main);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        /* Carte d'événement */
        .event-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-warm);
            border-radius: 16px;
            padding: 1.5rem;
            margin-bottom: 1.6rem;
            box-shadow: var(--shadow-soft);
            overflow: hidden;
        }}
        /* Bannière Photos Face-Off */
        .faceoff-banner {{
            background: linear-gradient(135deg, #2D1B10 0%, #4E2E1B 50%, #2D1B10 100%);
            border-radius: 12px;
            padding: 1.2rem 1rem;
            margin-bottom: 1.25rem;
            color: #FFFDF9;
            text-align: center;
            border: 1px solid #6E452B;
        }}
        .faceoff-belt {{
            display: inline-block;
            background-color: rgba(196, 136, 43, 0.22);
            border: 1px solid var(--accent-gold);
            color: #F7D388;
            font-size: 0.8rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 0.2rem 0.75rem;
            border-radius: 99px;
            margin-bottom: 0.9rem;
        }}
        .faceoff-grid {{
            display: flex;
            align-items: center;
            justify-content: space-around;
            gap: 0.75rem;
        }}
        .fighter-col {{
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 40%;
        }}
        .avatar-wrap {{
            width: 88px;
            height: 88px;
            border-radius: 50%;
            border: 3px solid var(--accent-gold);
            overflow: hidden;
            background-color: #3D2415;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 0.5rem;
            box-shadow: 0 4px 12px rgba(0,0,0,0.35);
        }}
        .fighter-img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: top center;
        }}
        .fighter-fallback {{
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Outfit', sans-serif;
            font-size: 1.6rem;
            font-weight: 800;
            color: #F7D388;
            background: radial-gradient(circle, #5A351E 0%, #2D1B10 100%);
        }}
        .fighter-name {{
            font-family: 'Outfit', sans-serif;
            font-weight: 700;
            font-size: 1.05rem;
            color: #FFFDF9;
            line-height: 1.25;
        }}
        .vs-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 42px;
            height: 42px;
            border-radius: 50%;
            background-color: var(--accent-main);
            border: 2px solid #F7D388;
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 0.95rem;
            color: #FFFFFF;
        }}
        .event-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.4rem;
            font-weight: 700;
            color: var(--accent-dark);
            margin: 0 0 0.9rem 0;
        }}
        /* Grille d'infos (Lieu, Heures, TV) */
        .meta-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.55rem;
            background-color: var(--bg-details);
            border: 1px solid var(--border-warm);
            border-radius: 10px;
            padding: 0.9rem 1.1rem;
            margin-bottom: 1rem;
            font-size: 0.94rem;
        }}
        .badge-tv {{
            display: inline-block;
            background-color: var(--bg-subtle);
            color: var(--accent-dark);
            padding: 0.15rem 0.6rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.85rem;
            margin-right: 0.35rem;
            border: 1px solid var(--border-warm);
        }}
        .badge-fr {{
            background-color: #9A4E24;
            color: #FFFFFF;
            border-color: #9A4E24;
        }}
        .event-desc {{
            color: var(--text-primary);
            margin: 0 0 1rem 0;
        }}
        /* Menu déroulant Carte Complète */
        details {{
            background-color: var(--bg-details);
            border: 1px solid var(--border-warm);
            border-radius: 10px;
            padding: 0.85rem 1.15rem;
            transition: all 0.2s ease;
        }}
        details[open] {{
            background-color: #F4EFE4;
            border-color: var(--accent-main);
        }}
        summary {{
            font-weight: 700;
            color: var(--accent-main);
            cursor: pointer;
            font-size: 0.98rem;
            list-style: none;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        summary::-webkit-details-marker {{
            display: none;
        }}
        summary::after {{
            content: "▼ Afficher";
            font-size: 0.8rem;
            background-color: var(--bg-subtle);
            color: var(--accent-dark);
            padding: 0.2rem 0.6rem;
            border-radius: 6px;
        }}
        details[open] summary::after {{
            content: "▲ Masquer";
            background-color: var(--accent-main);
            color: #FFFFFF;
        }}
        .fight-list {{
            margin-top: 1rem;
            padding-top: 0.8rem;
            border-top: 1px solid var(--border-warm);
        }}
        .bout-item {{
            padding: 0.7rem 0;
            border-bottom: 1px dashed var(--border-warm);
        }}
        .bout-item:last-child {{
            border-bottom: none;
            padding-bottom: 0;
        }}
        .weight-pill {{
            display: inline-block;
            background-color: var(--bg-subtle);
            color: var(--accent-dark);
            font-weight: 700;
            font-size: 0.8rem;
            padding: 0.1rem 0.5rem;
            border-radius: 5px;
        }}
        .empty-state {{
            background-color: var(--bg-card);
            border: 1px dashed var(--border-warm);
            border-radius: 12px;
            padding: 1.2rem;
            color: var(--text-secondary);
            font-style: italic;
            text-align: center;
        }}
        footer {{
            text-align: center;
            margin-top: 3.5rem;
            padding-top: 1.5rem;
            border-top: 1px solid var(--border-warm);
            color: var(--text-secondary);
            font-size: 0.88rem;
        }}
        @media (max-width: 600px) {{
            header.hero h1 {{ font-size: 1.75rem; }}
            .avatar-wrap {{ width: 70px; height: 70px; }}
            .fighter-name {{ font-size: 0.92rem; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header class="hero">
            <h1>🥊 Calendrier des Sports de Combat</h1>
            <p>Le programme officiel vérifié : Boxe Anglaise, Japon, UFC, Rizin, BKFC, Kickboxing & RAF</p>
            <span class="update-pill">🔄 Mis à jour le {date_str} à {heure_str} (Heure de Paris)</span>
        </header>

        <nav class="quick-nav">
            <a href="#boxe-monde">🥊 Boxe Monde</a>
            <a href="#boxe-japon">🇯🇵 Boxe Japon</a>
            <a href="#ufc">🔥 UFC</a>
            <a href="#rizin">🌸 Rizin</a>
            <a href="#bkfc">👊 BKFC</a>
            <a href="#kick-japon">🦵 Kick JP</a>
            <a href="#glory">🏆 Glory</a>
            <a href="#raf">🤼 RAF</a>
        </nav>

        <main>
            {calendar_content}
        </main>

        <footer>
            <p>Données croisées depuis les calendriers officiels des promoteurs • Horaires convertis sur Paris (CET/CEST) • Système métrique (kg / m)</p>
        </footer>
    </div>
</body>
</html>
"""

# Sauvegarde dans index.html et calendrier.html (compatibilité totale avec le workflow existant)
with open("index.html", "w", encoding="utf-8") as f:
    f.write(HTML_TEMPLATE)

with open("calendrier.html", "w", encoding="utf-8") as f:
    f.write(HTML_TEMPLATE)

print("Calendrier généré avec succès dans index.html et calendrier.html !")
