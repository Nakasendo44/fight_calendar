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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,ja;q=0.7",
}

# ==============================================================================
# ÉTAPE 1 : EXPLORATION À 2 NIVEAUX (CALENDRIERS + SOUS-PAGES D'ÉVÉNEMENTS)
# ==============================================================================
POSTER_REGISTRY = {}

def clean_text(raw):
    if not raw:
        return ""
    t = html.unescape(raw)
    t = re.sub(r"<script.*?</script>", " ", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def extract_og_image(raw_html, base_url=""):
    """Extrait l'affiche officielle (og:image ou twitter:image) d'une page d'événement."""
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, raw_html, flags=re.IGNORECASE)
        if m:
            img_url = html.unescape(m.group(1).strip())
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/") and base_url:
                parsed = urllib.parse.urlparse(base_url)
                img_url = f"{parsed.scheme}://{parsed.netloc}{img_url}"
            if img_url.startswith("http") and not any(bad in img_url.lower() for bad in ["logo", "favicon", "avatar", ".svg"]):
                return img_url
    return None

def scrape_event_subpage(url, label):
    """Visite la page dédiée d'un événement pour extraire son poster officiel et toute son undercard."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""
        raw_html = resp.text
        poster_url = extract_og_image(raw_html, url)
        if poster_url:
            POSTER_REGISTRY[url] = poster_url

        # Lecture structurée via Jina Reader pour capturer les tableaux de la carte complète
        jina_resp = requests.get(f"https://r.jina.ai/{url}", headers=HEADERS, timeout=12)
        if jina_resp.status_code == 200 and len(jina_resp.text) > 150:
            content = jina_resp.text[:5500]
        else:
            content = clean_text(raw_html)[:5500]

        return (
            f"\n--- PAGE OFFICIELLE D'ÉVÉNEMENT ({label}) ---\n"
            f"URL_SOURCE: {url}\n"
            f"POSTER_OFFICIEL: {poster_url or 'AUCUN'}\n"
            f"CONTENU DE LA CARTE & PALMARÈS :\n{content}\n"
        )
    except Exception as e:
        print(f"  [Info] Impossible de lire la sous-page {url} : {e}")
        return ""

def discover_and_crawl_events():
    print("Collecte approfondie des calendriers et des cartes complètes (Niveau 1 & 2)...")
    collected_blocks = []
    discovered_event_urls = []

    # 1. Scan direct de Matchroom Boxing pour extraire les liens des galas à venir
    try:
        r_mr = requests.get("https://www.matchroomboxing.com/events/", headers=HEADERS, timeout=10)
        if r_mr.status_code == 200:
            links = re.findall(r'https://www\.matchroomboxing\.com/events/[a-z0-9\-]+/', r_mr.text)
            for l in dict.fromkeys(links):
                if l.rstrip("/") != "https://www.matchroomboxing.com/events":
                    discovered_event_urls.append(("Matchroom Boxing", l))
    except Exception as e:
        print(f"  [Info] Scan Matchroom : {e}")

    # 2. Scan direct de UFC.com pour extraire les prochains événements
    try:
        r_ufc = requests.get("https://www.ufc.com/events", headers=HEADERS, timeout=10)
        if r_ufc.status_code == 200:
            links = re.findall(r'/event/[a-z0-9\-]+', r_ufc.text)
            for l in list(dict.fromkeys(links))[:3]:
                discovered_event_urls.append(("UFC Officiel", f"https://www.ufc.com{l}"))
    except Exception as e:
        print(f"  [Info] Scan UFC : {e}")

    # 3. Exploration des sous-pages d'événements découvertes (jusqu'à 8 galas majeurs)
    for label, ev_url in discovered_event_urls[:8]:
        print(f"  -> Extraction de la carte complète : {ev_url}")
        sub_data = scrape_event_subpage(ev_url, label)
        if sub_data:
            collected_blocks.append(sub_data)

    # 4. Lecture des calendriers globaux (Box.live, Tapology, Rizin, Kickboxing)
    OVERVIEW_SOURCES = [
        ("Box.live Calendrier Boxe", "https://r.jina.ai/https://box.live/upcoming-fights-schedule/"),
        ("Tapology Fightcenter", "https://r.jina.ai/https://www.tapology.com/fightcenter"),
        ("Queensberry Promotions", "https://r.jina.ai/https://www.queensberry.co.uk/events/"),
        ("BKFC Events", "https://r.jina.ai/https://www.bkfc.com/events"),
        ("Glory Kickboxing Events", "https://r.jina.ai/https://glorykickboxing.com/events"),
        ("Rizin FF Events", "https://r.jina.ai/https://jp.rizinff.com/schedules/"),
    ]
    for label, url in OVERVIEW_SOURCES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code == 200 and len(resp.text) > 200:
                collected_blocks.append(f"\n=== CALENDRIER GLOBAL : {label} ===\n{resp.text[:6000]}")
                print(f"  [OK] {label}")
        except Exception as e:
            print(f"  [Info] Source ignorée ({label}) : {e}")

    # 5. Flux RSS ciblés sur les cartes complètes ("full card" / "undercard") et le Japon
    RSS_SOURCES = [
        ("Undercards & Full Fight Cards (EN)", "https://news.google.com/rss/search?q=(%22undercard%22+OR+%22full+card%22+OR+%22fight+card%22+OR+%22running+order%22)+(boxing+OR+Matchroom+OR+Queensberry+OR+UFC+OR+BKFC+OR+Glory)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        ("Boxe Japon & Korakuen Hall (JA)", "https://news.google.com/rss/search?q=%E3%83%9C%E3%82%AF%E3%82%B7%E3%83%B3%E3%82%B0+(%E5%BE%8C%E6%A5%BD%E5%9C%92%E3%83%9B%E3%83%BC%E3%83%AB+OR+%E5%85%A8%E5%AF%BE%E6%88%A6%E3%82%AB%E3%83%BC%E3%83%89+OR+%E8%88%88%E8%A1%8C)+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
        ("Rizin, RISE, K-1, Krush (JA)", "https://news.google.com/rss/search?q=(RIZIN+OR+RISE+OR+K-1+OR+Krush)+(%E5%AF%BE%E6%88%A6%E3%82%AB%E3%83%BC%E3%83%89+OR+%E5%A4%A7%E4%BC%9A)+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
        ("RAF Real American Freestyle Wrestling (EN)", "https://news.google.com/rss/search?q=%22Real+American+Freestyle%22+OR+%22RAF+wrestling%22+when:14d&hl=en-US&gl=US&ceid=US:en"),
    ]
    for label, url in RSS_SOURCES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                items = root.findall(".//item")[:12]
                if items:
                    collected_blocks.append(f"\n=== DÉPÊCHES CARTES COMPLÈTES : {label} ===")
                for item in items:
                    t = clean_text(item.findtext("title", ""))
                    d = clean_text(item.findtext("description", ""))[:300]
                    p = clean_text(item.findtext("pubDate", ""))
                    if t:
                        collected_blocks.append(f"- [{p}] {t} — {d}")
        except Exception:
            pass

    return "\n".join(collected_blocks)

verified_schedule_data = discover_and_crawl_events()

# ==============================================================================
# ÉTAPE 2 : RÉSOLUTION AUTOMATIQUE DES AFFICHES OFFICIELLES (POSTERS)
# ==============================================================================
def build_poster_banner(match):
    poster_url = html.unescape(match.group(1)).strip()
    source_url = html.unescape(match.group(2)).strip()
    title_label = html.unescape(match.group(3)).strip()
    belt_label = html.unescape(match.group(4)).strip()

    # Si l'URL du poster n'est pas fournie directement mais qu'on a l'URL de la page officielle, on tente de l'extraire
    if (not poster_url or poster_url == "AUTO") and source_url.startswith("http"):
        if source_url in POSTER_REGISTRY:
            poster_url = POSTER_REGISTRY[source_url]
        else:
            try:
                r = requests.get(source_url, headers=HEADERS, timeout=6)
                if r.status_code == 200:
                    extracted = extract_og_image(r.text, source_url)
                    if extracted:
                        poster_url = extracted
                        POSTER_REGISTRY[source_url] = extracted
            except Exception:
                pass

    belt_html = f'<div class="poster-belt">👑 {html.escape(belt_label)}</div>' if belt_label else ""

    if poster_url and poster_url.startswith("http"):
        return f"""
        <div class="poster-container">
            <img src="{html.escape(poster_url)}" alt="{html.escape(title_label)}" class="event-poster-img"
                 onerror="this.parentElement.classList.add('poster-fallback-mode'); this.style.display='none';">
            <div class="poster-overlay">
                {belt_html}
                <div class="poster-Dynamic-title">{html.escape(title_label)}</div>
            </div>
        </div>
        """
    else:
        return f"""
        <div class="poster-container poster-fallback-mode">
            <div class="poster-overlay">
                {belt_html}
                <div class="poster-Dynamic-title">{html.escape(title_label)}</div>
            </div>
        </div>
        """

def inject_official_posters(raw_html):
    print("Intégration des affiches officielles des événements...")
    pattern = r'<div\s+class="poster-slot"\s+data-poster="([^"]*)"\s+data-source="([^"]*)"\s+data-title="([^"]*)"\s+data-belt="([^"]*)"\s*>\s*</div>'
    return re.sub(pattern, build_poster_banner, raw_html)

# ==============================================================================
# ÉTAPE 3 : PROMPT DE GÉNÉRATION DU CALENDRIER COMPLET
# ==============================================================================
CALENDAR_PROMPT = f"""Tu es un documentaliste sportif d'élite spécialisé en Boxe Anglaise, MMA, Kickboxing et Lutte.
Date actuelle de référence : {date_str} (Fuseau horaire : Europe/Paris).
Ta mission est de produire le CALENDRIER HEBDOMADAIRE COMPLET des combats prévus sur les 7 prochains jours (du {date_str} à J+7).

DIRECTIVES STRICTES POUR LES CARTES COMPLÈTES ET LES PALMARÈS :
1. CARTES COMPLÈTES OBLIGATOIRES : Ne laisse JAMAIS une carte avec uniquement le Main Event si l'undercard a été annoncée. Utilise les données extraites ci-dessous ET ton outil Google Search pour chercher spécifiquement "<Nom du Gala> full fight card undercard" afin de lister TOUS les combats programmés sur la carte.
2. EXACTITUDE DES PALMARÈS : Vérifie systématiquement le palmarès actuel exact de chaque boxeur/combattant (ex: si la page officielle Matchroom indique 19-0, 14 KOs, affiche 19-0-0, 14 KOs et non un ancien chiffre obsolète).
3. ZÉRO INVENTION : N'invente jamais de faux adversaires ni de lignes "vs TBA". Ne liste que les vrais combats réellement programmés sur l'événement.
4. UNITÉS & LANGUE :
   - Français journalistique fluide et précis. Conserve les termes techniques consacrés (puncher, out-boxer, brawler, southpaw, jab, uppercut, hook, knockdown, KO, TKO, main event, co-main event, undercard, contender, undisputed).
   - Tous les poids en kilogrammes (ex: -63,5 kg) et toutes les tailles en mètres (ex: 1m78).
   - Toutes les heures converties à l'heure de Paris (CET/CEST).

STRUCTURE DES 8 RUBRIQUES À GÉNÉRER :
1. <section id="boxe-monde" class="sport-section"><h2>🥊 1. Boxe Anglaise — Galas Internationaux</h2> ... </section>
2. <section id="boxe-japon" class="sport-section"><h2>🇯🇵 2. Boxe Anglaise — Galas au Japon (Korakuen Hall & Arènes)</h2> ... </section>
3. <section id="ufc" class="sport-section"><h2>🔥 3. UFC</h2> ... </section>
4. <section id="rizin" class="sport-section"><h2>🌸 4. Rizin Fighting Federation</h2> ... </section>
5. <section id="bkfc" class="sport-section"><h2>👊 5. BKFC (Bare Knuckle)</h2> ... </section>
6. <section id="kick-japon" class="sport-section"><h2>🦵 6. Kickboxing Japonais (RISE, K-1, Krush)</h2> ... </section>
7. <section id="glory" class="sport-section"><h2>🏆 7. Glory Kickboxing</h2> ... </section>
8. <section id="raf" class="sport-section"><h2>🤼 8. RAF (Real American Freestyle Wrestling)</h2> ... </section>

MODÈLE HTML OBLIGATOIRE POUR CHAQUE GALA (<article class="event-card">) :
En toute première ligne de chaque `<article class="event-card">`, place cette balise (en remplissant `data-poster` avec l'URL `POSTER_OFFICIEL` si présente dans les données brutes, sinon écris `"AUTO"`, et `data-source` avec l'URL officielle de l'événement) :
<div class="poster-slot" data-poster="URL_POSTER_OU_AUTO" data-source="URL_OFFICIELLE_EVENEMENT" data-title=" Combattant A vs. Combattant B " data-belt="Ceinture ou Enjeu & Catégorie en kg"></div>

Puis à l'intérieur de `<article class="event-card">`, affiche :
- <div class="card-header-row">
    <h3 class="event-title">Nom officiel du Gala : Combattant A vs. Combattant B</h3>
    <a href="URL_OFFICIELLE_EVENEMENT" target="_blank" rel="noopener" class="source-btn">🔗 Page officielle</a>
  </div>
- <div class="meta-grid">
    <div class="meta-item">📍 <strong>Lieu :</strong> Arène / Salle, Ville, Pays</div>
    <div class="meta-item">⏰ <strong>Horaires (Paris) :</strong> Début du gala à <strong>XXhXX</strong> | Main Event vers <strong>XXhXX</strong></div>
    <div class="meta-item">📺 <strong>Diffusion :</strong> <span class="badge-tv">Origine : Chaîne</span> <span class="badge-tv badge-fr">France : Chaîne</span></div>
  </div>
- <p class="event-desc"><strong>🎯 Présentation & Enjeux :</strong> Présentation dynamique de l'événement et des combats principaux en 2 à 3 phrases.</p>
- <details class="card-details">
    <summary>📊 Développer la carte complète, palmarès et analyse des combats</summary>
    <div class="fight-list">
      Pour CHAQUE combat de la carte (Main Event, Co-Main Event, et tous les combats de l'Undercard), génère un bloc `<div class="bout-item">` :
      <div class="bout-item">
        <div class="bout-header">
          🥊 <strong>Nom Boxeur A (V-D-N, X KOs | 1mXX) vs. Nom Boxeur B (V-D-N, Y KOs | 1mXX)</strong>
          <span class="weight-pill">Catégorie (XX,X kg) — X rounds</span>
        </div>
        <div class="bout-desc">Enjeu (titre en jeu) et présentation stylistique du combat en 1 à 2 phrases.</div>
      </div>
    </div>
  </details>

Si aucun événement n'a lieu cette semaine dans un sport secondaire, insère :
<div class="empty-state">🗓️ Aucun événement majeur programmé cette semaine dans cette discipline.</div>

DONNÉES BRUTES EXTRAITES DES SITES OFFICIELS AUJOURD'HUI :
{verified_schedule_data}

Renvoie UNIQUEMENT le code HTML des 8 sections (`<section ...>...</section>`). N'inclus jamais de balises Markdown ```html ou ```.
"""

# ==============================================================================
# ÉTAPE 4 : EXÉCUTION DE L'APPEL GEMINI AVEC RECHERCHE ACTIVE
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
calendar_html = None

for model_name in models_to_try[:2]:
    try:
        print(f"Génération du calendrier complet via {model_name} (Search Grounding actif)...")
        resp = client.models.generate_content(
            model=model_name,
            contents=CALENDAR_PROMPT,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
        if resp and getattr(resp, "text", None):
            calendar_html = resp.text
            print(f"Succès avec {model_name} !")
            break
    except Exception as e:
        print(f"Avertissement ({model_name} + Search) : {e}")
        time.sleep(3)

if not calendar_html:
    for model_name in models_to_try:
        try:
            print(f"Repli sur {model_name} (basé sur le Deep Scraping)...")
            resp = client.models.generate_content(
                model=model_name,
                contents=CALENDAR_PROMPT,
                config=types.GenerateContentConfig(temperature=0.1),
            )
            if resp and getattr(resp, "text", None):
                calendar_html = resp.text
                print(f"Succès avec {model_name} !")
                break
        except Exception as e:
            print(f"Modèle {model_name} indisponible : {e}")
            time.sleep(2)

if calendar_html:
    calendar_html = calendar_html.replace("```html", "").replace("```", "").strip()
    calendar_html = inject_official_posters(calendar_html)
else:
    calendar_html = "<div class='empty-state'>⚠️ Synchronisation en cours. Veuillez rafraîchir la page dans quelques instants.</div>"

# ==============================================================================
# ÉTAPE 5 : GABARIT HTML/CSS/JS (BEIGE & MARRON CHAUD + OUTILS INTERACTIFS)
# ==============================================================================
FINAL_HTML = f"""<!DOCTYPE html>
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
            max-width: 960px;
            margin: 0 auto;
            padding: 2rem 1.2rem 4rem 1.2rem;
        }}
        /* En-tête */
        header.hero {{
            text-align: center;
            padding: 2.2rem 1.5rem;
            background: linear-gradient(135deg, #3B2213 0%, #5E341B 100%);
            color: #FFFDF9;
            border-radius: 18px;
            margin-bottom: 1.2rem;
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
        /* Barre de navigation & outils */
        .sticky-bar {{
            position: sticky;
            top: 10px;
            z-index: 50;
            background-color: rgba(255, 253, 249, 0.96);
            backdrop-filter: blur(8px);
            border: 1px solid var(--border-warm);
            border-radius: 14px;
            padding: 0.75rem;
            margin-bottom: 2.2rem;
            box-shadow: var(--shadow-soft);
        }}
        .quick-nav {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            justify-content: center;
            margin-bottom: 0.65rem;
        }}
        .quick-nav a {{
            text-decoration: none;
            color: var(--accent-dark);
            background-color: var(--bg-subtle);
            padding: 0.4rem 0.8rem;
            border-radius: 8px;
            font-size: 0.84rem;
            font-weight: 700;
            transition: all 0.2s ease;
        }}
        .quick-nav a:hover {{
            background-color: var(--accent-main);
            color: #FFFFFF;
        }}
        .toolbar {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.6rem;
            justify-content: space-between;
            align-items: center;
            padding-top: 0.55rem;
            border-top: 1px solid var(--border-warm);
        }}
        .search-input {{
            flex: 1;
            min-width: 200px;
            padding: 0.45rem 0.85rem;
            border: 1px solid var(--border-warm);
            border-radius: 8px;
            background-color: var(--bg-details);
            color: var(--text-primary);
            font-family: inherit;
            font-size: 0.9rem;
        }}
        .search-input:focus {{
            outline: 2px solid var(--accent-main);
        }}
        .toggle-all-btn {{
            background-color: var(--accent-dark);
            color: #FFFDF9;
            border: none;
            padding: 0.48rem 0.95rem;
            border-radius: 8px;
            font-family: inherit;
            font-size: 0.85rem;
            font-weight: 700;
            cursor: pointer;
            transition: background 0.2s ease;
        }}
        .toggle-all-btn:hover {{
            background-color: var(--accent-main);
        }}
        /* Sections par sport */
        .sport-section {{
            margin-bottom: 3rem;
            scroll-margin-top: 130px;
        }}
        .sport-section h2 {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.65rem;
            font-weight: 800;
            color: var(--accent-dark);
            margin-bottom: 1.2rem;
            padding-bottom: 0.5rem;
            border-bottom: 3px solid var(--accent-main);
        }}
        /* Carte d'événement */
        .event-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-warm);
            border-radius: 16px;
            padding: 1.5rem;
            margin-bottom: 1.8rem;
            box-shadow: var(--shadow-soft);
            overflow: hidden;
        }}
        /* Bannière Poster Officiel */
        .poster-container {{
            position: relative;
            width: 100%;
            max-height: 360px;
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 1.25rem;
            background: linear-gradient(135deg, #2D1B10 0%, #5A351E 50%, #2D1B10 100%);
            border: 1px solid #6E452B;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .event-poster-img {{
            width: 100%;
            max-height: 360px;
            object-fit: cover;
            object-position: center top;
            display: block;
        }}
        .poster-overlay {{
            position: absolute;
            inset: 0;
            background: linear-gradient(to top, rgba(30, 16, 8, 0.92) 0%, rgba(30, 16, 8, 0.25) 55%, rgba(30, 16, 8, 0.1) 100%);
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            align-items: flex-start;
            padding: 1.2rem 1.4rem;
            color: #FFFDF9;
        }}
        .poster-container.poster-fallback-mode {{
            min-height: 140px;
        }}
        .poster-container.poster-fallback-mode .poster-overlay {{
            position: relative;
            background: none;
            align-items: center;
            text-align: center;
            justify-content: center;
            width: 100%;
            padding: 1.6rem 1rem;
        }}
        .poster-belt {{
            display: inline-block;
            background-color: rgba(196, 136, 43, 0.3);
            backdrop-filter: blur(4px);
            border: 1px solid #F7D388;
            color: #F7D388;
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 0.2rem 0.75rem;
            border-radius: 99px;
            margin-bottom: 0.45rem;
        }}
        .poster-Dynamic-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.45rem;
            font-weight: 800;
            color: #FFFDF9;
            text-shadow: 0 2px 8px rgba(0,0,0,0.65);
            line-height: 1.25;
        }}
        /* En-tête de carte avec bouton source */
        .card-header-row {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 0.9rem;
        }}
        .event-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.38rem;
            font-weight: 700;
            color: var(--accent-dark);
            margin: 0;
        }}
        .source-btn {{
            text-decoration: none;
            font-size: 0.82rem;
            font-weight: 700;
            color: var(--accent-main);
            background-color: var(--bg-details);
            border: 1px solid var(--border-warm);
            padding: 0.3rem 0.75rem;
            border-radius: 8px;
            transition: all 0.2s ease;
        }}
        .source-btn:hover {{
            background-color: var(--accent-main);
            color: #FFFFFF;
            border-color: var(--accent-main);
        }}
        /* Grille d'infos */
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
            background-color: var(--accent-main);
            color: #FFFFFF;
            border-color: var(--accent-main);
        }}
        .event-desc {{
            color: var(--text-primary);
            margin: 0 0 1.1rem 0;
        }}
        /* Menu déroulant Carte Complète */
        details.card-details {{
            background-color: var(--bg-details);
            border: 1px solid var(--border-warm);
            border-radius: 10px;
            padding: 0.85rem 1.15rem;
        }}
        details.card-details[open] {{
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
            content: "▼ Afficher la carte";
            font-size: 0.8rem;
            background-color: var(--bg-subtle);
            color: var(--accent-dark);
            padding: 0.22rem 0.65rem;
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
            padding: 0.75rem 0;
            border-bottom: 1px dashed var(--border-warm);
        }}
        .bout-item:last-child {{
            border-bottom: none;
            padding-bottom: 0;
        }}
        .bout-header {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 0.5rem;
            margin-bottom: 0.25rem;
        }}
        .bout-desc {{
            font-size: 0.92rem;
            color: var(--text-secondary);
        }}
        .weight-pill {{
            display: inline-block;
            background-color: var(--bg-subtle);
            color: var(--accent-dark);
            font-weight: 700;
            font-size: 0.78rem;
            padding: 0.15rem 0.55rem;
            border-radius: 6px;
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
            .poster-Dynamic-title {{ font-size: 1.2rem; }}
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

        <div class="sticky-bar">
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
            <div class="toolbar">
                <input type="search" id="searchInput" class="search-input" placeholder="🔍 Filtrer par boxeur, gala, ville ou diffuseur (ex: DAZN)...">
                <button type="button" id="toggleAllBtn" class="toggle-all-btn">📂 Tout déplier</button>
            </div>
        </div>

        <main id="mainContent">
            {calendar_html}
        </main>

        <footer>
            <p>Affiches & cartes extraites des pages officielles des promoteurs • Heures de Paris (CET/CEST) • Unités métriques (kg / m)</p>
        </footer>
    </div>

    <script>
        // Bouton Tout déplier / Tout replier
        const toggleBtn = document.getElementById('toggleAllBtn');
        let allOpen = false;
        toggleBtn.addEventListener('click', () => {{
            allOpen = !allOpen;
            document.querySelectorAll('details.card-details').forEach(d => {{
                d.open = allOpen;
            }});
            toggleBtn.textContent = allOpen ? '📁 Tout replier' : '📂 Tout déplier';
        }});

        // Filtre de recherche en temps réel
        const searchInput = document.getElementById('searchInput');
        searchInput.addEventListener('input', (e) => {{
            const query = e.target.value.toLowerCase().trim();
            document.querySelectorAll('.event-card').forEach(card => {{
                const text = card.textContent.toLowerCase();
                card.style.display = text.includes(query) ? '' : 'none';
            }});
        }});
    </script>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(FINAL_HTML)

with open("calendrier.html", "w", encoding="utf-8") as f:
    f.write(FINAL_HTML)

print("Calendrier complet et posters officiels générés avec succès !")
