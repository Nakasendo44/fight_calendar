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

models_to_try = get_available_models
