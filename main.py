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

# 1. Vérification de la clé API
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Erreur critique : La variable GEMINI_API_KEY est introuvable.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

# 2. Fuseau horaire de Paris
paris_tz = pytz.timezone("Europe/Paris")
now_paris = datetime.now(paris_tz)
date_str = now_paris.strftime("%d/%m/%Y")
heure_str = now_paris.strftime("%H:%M")

# 3. Collecte autonome des flux d'actualités et programmes (FR, EN, JP)
RSS_FEEDS = [
    ("Boxe Internationale (EN)", "https://news.google.com/rss/search?q=boxing+(WBA+OR+WBC+OR+IBF+OR+WBO+OR+Matchroom+OR+Queensberry+OR+%22Top+Rank%22+OR+PBC+OR+%22Golden+Boy%22+OR+%22Riyadh+Season%22+OR+%22The+Ring%22)+when:3d&hl=en-US&gl=US&ceid=US:en"),
    ("Boxe France (FR)", "https://news.google.com/rss/search?q=%22boxe+anglaise%22+OR+boxeur+when:4d&hl=fr&gl=FR&ceid=FR:fr"),
    ("Boxe Japon & Korakuen Hall (JA)", "https://news.google.com/rss/search?q=%E3%83%9C%E3%82%AF%E3%82%B7%E3%83%B3%E3%82%B0+(%E5%BE%8C%E6%A5%BD%E5%9C%92%E3%83%9B%E3%83%BC%E3%83%AB+OR+%E4%B8%96%E7%95%8C%E6%88%A6+OR+%E4%BA%95%E4%B8%8A%E5%B0%9A%E5%BC%A5+OR+JBC)+when:4d&hl=ja&gl=JP&ceid=JP:ja"),
    ("Calendrier & Fight Cards (EN)", "https://news.google.com/rss/search?q=(%22fight+card%22+OR+%22main+event%22+OR+%22start+time%22+OR+%22schedule%22)+(boxing+OR+UFC+OR+BKFC+OR+Glory+OR+Rizin)+when:6d&hl=en-US&gl=US&ceid=US:en"),
    ("Rizin & Kickboxing Japonais - RISE, K-1, Krush (JA)", "https://news.google.com/rss/search?q=(RIZIN+OR+RISE+OR+K-1+OR+Krush)+(%E5%AF%BE%E6%88%A6%E3%82%AB%E3%83%BC%E3%83%89+OR+%E5%A4%A7%E4%BC%9A+OR+%E6%B1%BA%E5%AE%9A)+when:6d&hl=ja&gl=JP&ceid=JP:ja"),
    ("RAF Wrestling & Glory Kickboxing (EN)", "https://news.google.com/rss/search?q=%22Real+American+Freestyle%22+OR+%22Glory+Kickboxing%22+when:7d&hl=en-US&gl=US&ceid=US:en"),
    ("Bad Left Hook - Boxe (EN)", "https://www.badlefthook.com/rss/current.xml"),
    ("MMA Fighting (EN)", "https://www.mmafighting.com/rss/current.xml"),
]

def clean_html_tags(raw_text):
    if not raw_text:
        return ""
    text = html.unescape(raw_text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def fetch_live_combat_data():
    print("Collecte des dépêches web multilingues (EN / JP / FR)...")
    collected_lines = []
    raw_items_for_fallback = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FightCalendarBot/1.0)"}

    for category, url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                items = root.findall(".//item")[:10]
                if items:
                    collected_lines.append(f"\n--- SOURCE : {category} ---")
                for item in items:
                    title = clean_html_tags(item.findtext("title", ""))
                    pub_date = clean_html_tags(item.findtext("pubDate", ""))
                    desc = clean_html_tags(item.findtext("description", ""))[:250]
                    if title:
                        collected_lines.append(f"- [{pub_date}] {title} | {desc}")
                        raw_items_for_fallback.append((category, title, pub_date))
        except Exception as e:
            print(f"Avertissement flux [{category}] : {e}")

    return "\n".join(collected_lines), raw_items_for_fallback

live_context, fallback_items = fetch_live_combat_data()

# 4. Prompt unifié (1 seul appel API pour économiser le quota)
FULL_PROMPT = f"""Tu es un journaliste et analyste spécialisé dans les sports de combat (boxe anglaise internationale et japonaise, MMA, Kickboxing et lutte).
Date de référence : {date_str} (Fuseau horaire : Europe/Paris).

DIRECTIVES STRICTES DE RÉDACTION ET DE TRADUCTION :
1. Langue finale : Français irréprochable, fluide et naturel.
2. Protocole linguistique en 3 étapes :
   - Étape 1 : Traduis avec exactitude les informations issues des sources en anglais et en japonais.
   - Étape 2 : Reformule dans un français journalistique naturel et agréable à lire.
   - Étape 3 : Vérifie que le sens original, les chiffres et les nuances sont scrupuleusement respectés sans invention.
3. Lexique pugilistique : Conserve tels quels les termes techniques consacrés (puncher, out-boxer, brawler, jab, uppercut, hook, cross, clinch, sparring, ring rust, pound-for-pound, main card, undercard, contender, gatekeeper, undisputed, unified, knockdown, KO, TKO, split decision, majority decision, no contest, southpaw).
4. Unités métriques obligatoires :
   - Poids systématiquement convertis en kilogrammes (kg) arrondis à 0,1 kg près (ex: 61,2 kg).
   - Tailles et allonges au format métrique (ex: 1m85).
5. Horaires : Toutes les heures doivent être converties à l'heure de Paris (CET/CEST).

Voici les dépêches et données récentes collectées aujourd'hui en anglais, japonais et français :
{live_context}

En t'appuyant sur ces données récentes (et sur tes connaissances/recherches), génère le contenu HTML divisé en DEUX parties séparées exactement par la balise <!-- SEPARATEUR_CALENDRIER -->.
N'inclus jamais de balises Markdown ```html ou ```.

================================================================
PARTIE 1 : ACTUALITÉS QUOTIDIENNES (24-48h)
================================================================
- Sous-section 1 : Boxe Anglaise (Couverture approfondie)
  Couvre : ceintures mondiales (WBA, WBC, IBF, WBO, The Ring), top 10 mondial, boxeurs français, boxeurs japonais (incluant le Korakuen Hall), business boxe (signatures, nouveaux diffuseurs, promoteurs : Matchroom, Queensberry, Golden Boy, PBC, Top Rank, Zuffa, Riyadh Season, Ring Magazine).
  Règle pour les rumeurs et déclarations : place-les obligatoirement dans une balise <blockquote> avec la mention "<strong>[Rumeur / Déclaration]</strong>".
- Sous-section 2 : Autres Sports (En bref - couverture synthétique des grandes actualités)
  Couvre sous forme de liste à puces : UFC, Rizin, BKFC, Kickboxing japonais (RISE, K-1, Krush), Glory Kickboxing, RAF (Real American Freestyle wrestling).

Insère ensuite exactement cette ligne :
<!-- SEPARATEUR_CALENDRIER -->

================================================================
PARTIE 2 : CALENDRIER DES 7 PROCHAINS JOURS (du {date_str} à J+7)
================================================================
Crée une rubrique (<h3>) pour chaque sport dans cet ordre exact :
1. Boxe anglaise - Galas Internationaux
2. Boxe anglaise - Galas au Japon (incluant Korakuen Hall)
3. UFC
4. Rizin
5. BKFC
6. Kickboxing Japonais (RISE, K-1, Krush)
7. Glory Kickboxing
8. RAF (Real American Freestyle wrestling)

Règles pour chaque rubrique du calendrier :
- Si aucun événement majeur n'est programmé cette semaine dans un sport secondaire, affiche : <p><em>Aucun événement majeur cette semaine.</em></p>
- Exception Boxe anglaise : il y a toujours des combats sur une semaine ; vise des galas plus petits (nationaux, européens, Korakuen Hall) si aucun gala mondial n'a lieu.
- Pour chaque événement répertorié, affiche dans un bloc <div class="event-card"> :
  * Titre de l'événement et lieu.
  * Date, heure du début de l'événement et heure du combat principal (converties à l'heure de Paris).
  * Diffuseur d'origine + Diffuseur en France (ex: DAZN, RMC Sport, UFC Fight Pass, Abema, etc.).
  * Présentation de l'événement et des combats principaux (1 à 2 phrases).
  * Menu déroulant interactif pour développer l'événement :
    <details>
      <summary><strong>Développer la carte complète, palmarès et présentation des combats</strong></summary>
      <ul>
        <!-- Liste complète des combats avec palmarès (ex: 18-1-0, 12 KOs), catégorie en kg, taille en mètre (ex: 1m85) et 1 à 2 phrases de présentation par combat -->
      </ul>
    </details>
"""

# 5. Découverte dynamique des modèles disponibles sur la clé API
def get_candidate_models():
    preferred_order = [
        "gemini-3.8-flash",
        "gemini-3.8-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.0-flash",
    ]
    discovered = []
    try:
        for m in client.models.list():
            name = getattr(m, "name", "")
            short_name = name.replace("models/", "")
            # Filtrer uniquement les modèles de génération de texte Gemini / Gemma
            if any(k in short_name for k in ["gemini", "gemma"]) and not any(
                bad in short_name for bad in ["embedding", "imagen", "veo", "tts", "audio", "aqa", "vision"]
            ):
                discovered.append(short_name)
        print(f"Modèles détectés sur votre clé API : {discovered}")
    except Exception as e:
        print(f"Impossible de lister les modèles dynamiquement : {e}")

    # Construire la liste finale ordonnée sans doublons
    final_models = []
    for p in preferred_order:
        if not discovered or p in discovered:
            final_models.append(p)
    for d in discovered:
        if d not in final_models:
            final_models.append(d)
    return final_models

candidate_models = get_candidate_models()

# 6. Exécution robuste avec repli automatique (Fallback)
generated_text = None

# Tentative A : Modèle principal avec Google Search Grounding
primary_model = candidate_models[0] if candidate_models else "gemini-3.8-flash"
try:
    print(f"Tentative 1 : {primary_model} avec outil Google Search...")
    resp = client.models.generate_content(
        model=primary_model,
        contents=FULL_PROMPT,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2,
        ),
    )
    if resp and getattr(resp, "text", None):
        generated_text = resp.text
        print(f"Succès avec {primary_model} (Google Search actif) !")
except Exception as e:
    print(f"Échec de la tentative 1 ({primary_model} + Search) : {e}")
    print("Passage automatique au mode sans quota Search (basé sur les flux collectés)...")
    time.sleep(3)

# Tentative B : Boucle sur les modèles disponibles sans l'outil Google Search (évite le blocage 429 Search)
if not generated_text:
    for model_name in candidate_models:
        try:
            print(f"Tentative avec le modèle : {model_name} (sans outil Search)...")
            resp = client.models.generate_content(
                model=model_name,
                contents=FULL_PROMPT,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                ),
            )
            if resp and getattr(resp, "text", None):
                generated_text = resp.text
                print(f"Succès avec le modèle {model_name} !")
                break
        except Exception as e:
            print(f"Modèle {model_name} indisponible ou limité : {e}")
            time.sleep(2)

# 7. Découpage du contenu ou mode de secours si quota 100% bloqué
if generated_text:
    clean_output = generated_text.replace("```html", "").replace("```", "").strip()
    if "<!-- SEPARATEUR_CALENDRIER -->" in clean_output:
        parts = clean_output.split("<!-- SEPARATEUR_CALENDRIER -->", 1)
        actus_html = parts[0].strip()
        calendrier_html = parts[1].strip()
    else:
        actus_html = clean_output
        calendrier_html = "<p><em>Le calendrier détaillé est intégré dans le flux ci-dessus.</em></p>"
else:
    print("Avertissement : Tous les modèles de la clé API ont refusé la connexion (Quota 429). Activation du mode de secours HTML.")
    items_li = "".join(
        f"<li><strong>[{html.escape(cat)}]</strong> {html.escape(title)} <small>({html.escape(pub)})</small></li>"
        for cat, title, pub in fallback_items[:30]
    )
    actus_html = f"""
    <blockquote>
        <strong>[Information Système]</strong> La clé API Google Gemini indique temporairement un quota gratuit épuisé (Erreur 429).
        Voici les dernières dépêches brutes récupérées automatiquement en attendant la réinitialisation du quota :
    </blockquote>
    <ul>{items_li}</ul>
    """
    calendrier_html = "<p><em>Calendrier en attente de réinitialisation du quota API Gemini.</em></p>"

# 8. Assemblage final de la page index.html (Pico.css)
html_complet = f"""<!DOCTYPE html>
<html lang="fr" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>QG Sports de Combat - Actualités & Calendrier</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
    <style>
        :root {{
            --pico-font-size: 95%;
        }}
        header {{
            border-bottom: 2px solid var(--pico-primary);
            margin-bottom: 2rem;
            padding-bottom: 1rem;
        }}
        .badge-update {{
            display: inline-block;
            padding: 0.25rem 0.6rem;
            font-size: 0.8rem;
            border-radius: 4px;
            background-color: var(--pico-card-background-color);
            border: 1px solid var(--pico-muted-border-color);
        }}
        blockquote {{
            border-left: 4px solid var(--pico-primary);
            background-color: rgba(255, 255, 255, 0.04);
            padding: 0.8rem 1.2rem;
            margin: 1rem 0;
            font-style: italic;
        }}
        details {{
            margin-top: 1rem;
            border: 1px solid var(--pico-muted-border-color);
            border-radius: 6px;
            padding: 0.6rem 1rem;
            background-color: rgba(255, 255, 255, 0.02);
        }}
        summary {{
            cursor: pointer;
            color: var(--pico-primary);
        }}
        .event-card {{
            margin-bottom: 1.5rem;
            padding: 1rem;
            border-radius: 8px;
            background-color: var(--pico-card-background-color);
            border: 1px solid var(--pico-muted-border-color);
        }}
    </style>
</head>
<body>
    <main class="container">
        <header>
            <h1>🥊 QG Sports de Combat</h1>
            <p>Actualités quotidiennes & Calendrier hebdomadaire (Boxe, MMA, Kickboxing, Lutte)</p>
            <span class="badge-update">Dernière mise à jour : {date_str} à {heure_str} (Heure de Paris)</span>
        </header>

        <section id="actualites">
            <h2>📰 Actualités & Coulisses (24-48h)</h2>
            {actus_html}
        </section>

        <hr>

        <section id="calendrier">
            <h2>📅 Calendrier des 7 prochains jours</h2>
            {calendrier_html}
        </section>

        <footer>
            <small>Propulsé automatiquement par Gemini & GitHub Pages. Fuseau horaire : Paris (CET/CEST) | Unités : kg & mètres.</small>
        </footer>
    </main>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_complet)

print("Fichier index.html généré et sauvegardé avec succès.")
