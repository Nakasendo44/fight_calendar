import os
import sys
from datetime import datetime
import pytz
from google import genai
from google.genai import types

# Initialisation du client Gemini avec la clé secrète
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Erreur : La variable d'environnement GEMINI_API_KEY est introuvable.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

# Date et heure de référence calées sur le fuseau horaire de Paris
paris_tz = pytz.timezone("Europe/Paris")
now_paris = datetime.now(paris_tz)
date_str = now_paris.strftime("%d/%m/%Y")
heure_str = now_paris.strftime("%H:%M")

SYSTEM_INSTRUCTION = """
Tu es un journaliste et analyste spécialisé dans les sports de combat, doté d'une expertise reconnue en boxe anglaise internationale et japonaise, MMA, Kickboxing et lutte.

DIRECTIVES LINGUISTIQUES ET LEXICALES :
1. Langue d'écriture : Français irréprochable, fluide et naturel.
2. Protocole de traitement :
   - Traduis les faits bruts extraits de tes recherches en anglais, japonais et français.
   - Reformule dans un style journalistique dynamique adapté à la presse spécialisée.
   - Auto-vérifie l'exactitude sémantique entre les données sources et ta restitution finale.
3. Préservation du lexique technique pugilistique :
   - Conserve intacts les termes techniques et anglicismes consacrés du milieu : puncher, out-boxer, slugger, brawler, jab, direct, uppercut, hook/crochet, cross, clinch, sparring, ring rust, pound-for-pound / P4P, main card, undercard, contender, gatekeeper, undisputed, unified, knockdown, KO, TKO, split decision, unanime, majority decision, no contest.
   - Emploie indifféremment 'fausse-patte' ou 'southpaw'.
4. Standardisation métrique impérative :
   - Tous les poids doivent obligatoirement être convertis en kilogrammes (kg), arrondis à 0,1 kg près (ex: 61,2 kg pour 135 lbs).
   - Toutes les tailles et allonges doivent être converties au format métrique en mètres (ex: 1m85, 1m78).
5. Fuseau horaire :
   - Toutes les heures de galas et de combats doivent être systématiquement converties et indiquées à l'heure de Paris (CET/CEST).
"""

# -------------------------------------------------------------
# APPEL 1 : Actualités du jour (Focus profond Boxe, synthèse autres)
# -------------------------------------------------------------
prompt_actus = f"""
Date actuelle de référence : {date_str}.
Effectue une recherche approfondie sur le web concernant les actualités des dernières 24 à 48 heures dans les sports de combat.

Périmètre prioritaire - Boxe Anglaise (Couverture détaillée en profondeur) :
- Ceintures mondiales majeures (WBA, WBC, IBF, WBO) et classements The Ring.
- Boxeurs du top 10 mondial, boxeurs français (toutes catégories) et boxeurs japonais (incluant les réunions au Korakuen Hall de Tokyo).
- Business et coulisses : négociations de contrats, diffuseurs, promoteurs majeurs (Matchroom, Queensberry, Top Rank, PBC, Golden Boy, Zuffa Boxing, Riyadh Season).
- Consigne de mise en forme pour les rumeurs : Si une information relève d'une déclaration verbale ou d'une rumeur non officialisée par contrat, place-la obligatoirement dans une balise HTML <blockquote> avec la mention expresse '[Rumeur / Déclaration]'.

Périmètre secondaire - Autres sports (Couverture synthétique / grands titres uniquement) :
- UFC, Rizin, BKFC, Kickboxing japonais (RISE, K-1, Krush), Real American Freestyle wrestling (RAF).
- Mentionne uniquement les annonces majeures de combats, blessures critiques ou résultats décisifs de ceintures.

Génère UNIQUEMENT un fragment HTML contenant :
- Une sous-section pour la boxe anglaise (titres, paragraphes clairs, encadrés pour les rumeurs).
- Une sous-section intitulée 'En Bref : MMA, Kickboxing & Lutte' sous forme de liste à puces structurée.
N'inclus aucune balise ```html ou ``` dans ta réponse.
"""

# -------------------------------------------------------------
# APPEL 2 : Calendrier des 7 prochains jours
# -------------------------------------------------------------
prompt_calendrier = f"""
Date actuelle de référence : {date_str}.
Génère le calendrier exhaustif des galas prévus pour les 7 prochains jours (du {date_str} jusqu'à J+7) pour les catégories suivantes :
1. Boxe anglaise - Galas Internationaux
2. Boxe anglaise - Galas au Japon (notamment Korakuen Hall)
3. UFC
4. Rizin
5. BKFC
6. Kickboxing Japonais (RISE, K-1, Krush)
7. Glory Kickboxing
8. RAF (Real American Freestyle wrestling)

Consignes impératives d'affichage :
- Place systématiquement la boxe anglaise en haut.
- Si aucun gala n'est prévu dans une discipline : affiche la mention stricte '<p><em>Aucun événement majeur répertorié cette semaine.</em></p>'. Exception pour la boxe : si aucun gala d'envergure mondiale n'est programmé, répertorie les galas nationaux, européens ou régionaux.
- Pour chaque événement, fournis dans le bloc visible :
  * Le titre du gala.
  * La date et l'heure de début de l'événement (Heure de Paris).
  * L'heure estimée du main event (Heure de Paris).
  * Les diffuseurs : diffuseur d'origine ET diffuseur officiel en France (ex: 'DAZN France / Canal+ / RMC Sport' ou 'Aucun diffuseur TV officiel en France - disponible via PPV/Stream officiel').
  * Une présentation synthétique des enjeux du combat principal (1 à 2 phrases percutantes).
- Fonction 'Développer' (Carte complète) :
  * Encapsule l'intégralité des combats de la carte (y compris préliminaires si disponibles) dans une balise HTML <details><summary><strong>Développer la carte complète des combats</strong></summary> ... </details>.
  * Pour chaque combat déplié, affiche : Nom des combattants, catégorie de poids en kg, palmarès complet (ex: 22-1-0, 16 KOs), taille en mètres, titre ou enjeu en jeu, et une description technique du match en 1 à 2 phrases.

Génère UNIQUEMENT le fragment HTML des rubriques du calendrier. N'inclus aucune balise ```html ou ```.
"""

print("Collecte et analyse des actualités en cours...")
response_actus = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt_actus,
    config=types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.2,
    ),
)

print("Génération du calendrier hebdomadaire en cours...")
response_calendrier = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt_calendrier,
    config=types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.2,
    ),
)

# Nettoyage d'éventuels résidus de balises Markdown
actus_html = response_actus.text.replace("```html", "").replace("```", "").strip()
calendrier_html = response_calendrier.text.replace("```html", "").replace("```", "").strip()

# Gabarit HTML final avec Pico.css (Mode sombre par défaut, typographie soignée)
html_complet = f"""<!DOCTYPE html>
<html lang="fr" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sports de Combat - Actualités & Calendrier</title>
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
            background-color: rgba(255, 255, 255, 0.03);
            padding: 0.8rem 1.2rem;
            margin: 1rem 0;
            font-style: italic;
        }}
        details {{
            margin-top: 1rem;
            border: 1px solid var(--pico-muted-border-color);
            border-radius: 6px;
            padding: 0.5rem 1rem;
        }}
        summary {{
            cursor: pointer;
            color: var(--pico-primary);
        }}
        .event-card {{
            margin-bottom: 2rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--pico-muted-border-color);
        }}
    </style>
</head>
<body>
    <main class="container">
        <header>
            <h1>🥊 QG Sports de Combat</h1>
            <p>Le radar d'actualités et d'événements : Boxe, MMA, Kickboxing & Lutte</p>
            <span class="badge-update">Dernière mise à jour : {date_str} à {heure_str} (Heure de Paris)</span>
        </header>

        <section id="actualites">
            <h2>📰 Actualités & Analyses (Dernières 24-48h)</h2>
            {actus_html}
        </section>

        <hr>

        <section id="calendrier">
            <h2>📅 Programme des 7 prochains jours</h2>
            {calendrier_html}
        </section>

        <footer>
            <small>Système automatisé via Gemini API & GitHub Pages. Heures converties à Paris (CET/CEST).</small>
        </footer>
    </main>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_complet)

print("Page index.html générée avec succès.")
