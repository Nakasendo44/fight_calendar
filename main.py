import os
import sys
import time
import json
import re
import html
import urllib.parse
from datetime import datetime, timedelta
import pytz
import requests
from google import genai
from google.genai import types

# ==============================================================================
# 1. INITIALISATION & PARAMÈTRES TEMPORELS STRICTS (PARIS)
# ==============================================================================
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("Erreur critique : La variable GEMINI_API_KEY est absente.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

paris_tz = pytz.timezone("Europe/Paris")
now_paris = datetime.now(paris_tz)
date_str = now_paris.strftime("%d/%m/%Y")
heure_str = now_paris.strftime("%H:%M")

# Génération explicite des dates en Français, ISO et Anglais (pour les sites US comme BKFC/Tapology)
valid_dates_fr = []
valid_dates_iso = []
valid_dates_en = []
for offset in range(8):
    d = now_paris + timedelta(days=offset)
    valid_dates_fr.append(d.strftime("%d/%m/%Y"))
    valid_dates_iso.append(d.strftime("%Y-%m-%d"))
    valid_dates_en.append(d.strftime("%b %d, %Y"))

DATE_WINDOW_STR = (
    f"du {valid_dates_fr[0]} au {valid_dates_fr[-1]} inclus "
    f"(Dates ISO : {', '.join(valid_dates_iso)} | Format US : {', '.join(valid_dates_en)})"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6",
}

# ==============================================================================
# 2. DÉCOUVERTE DYNAMIQUE DES MODÈLES TEXTE ACTIFS (SANS LES MODÈLES 404/IMAGE)
# ==============================================================================
def get_available_models():
    preferred = [
        "gemini-3.8-flash",
        "gemini-3.8-flash-lite",
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-flash-latest",
    ]
    excluded_tokens = [
        "embedding", "imagen", "veo", "tts", "audio", "vision", "aqa",
        "image", "banana", "transcribe", "argon", "customtools", "2.5"
    ]
    discovered = []
    try:
        for m in client.models.list():
            short = getattr(m, "name", "").replace("models/", "")
            if "gemini" in short and not any(ex in short for ex in excluded_tokens):
                discovered.append(short)
        print(f"Modèles texte actifs détectés : {discovered}")
    except Exception as e:
        print(f"Info liste modèles : {e}")

    final_list = [p for p in preferred if not discovered or p in discovered]
    for d in discovered:
        if d not in final_list:
            final_list.append(d)
    return final_list or ["gemini-3.8-flash"]

AVAILABLE_MODELS = get_available_models()

# ==============================================================================
# 3. CONFIGURATION DES 8 MICRO-AGENTS SPÉCIALISÉS
# ==============================================================================
AGENTS_CONFIG = [
    {
        "id": "boxe-monde",
        "title": "🥊 1. Boxe Anglaise — Galas Internationaux",
        "fallback_url": "https://box.live/upcoming-fights-schedule/",
        "empty_msg": "Aucun gala international majeur répertorié dans la fenêtre des 7 prochains jours.",
        "search_hint": (
            "Recherche tous les galas de boxe anglaise prévus cette semaine ET leur carte complète (full fight card / undercard) sur : "
            "box.live, tapology.com/fightcenter, ringtv.com/schedule, matchroomboxing.com/events, queensberry.co.uk, "
            "dazn.com, espn.com/boxing/schedule, fightnights.com, ufcfightpass.com. "
            "Inclus impérativement les cartes Matchroom, Queensberry, Top Rank, PBC, Golden Boy, "
            "les cartes The Ring / Riyadh Season et Zuffa Boxing (UFC Fight Pass)."
        ),
        "specific_rules": (
            "- EXHAUSTIVITÉ DE LA CARTE : Pour chaque gala trouvé (ex: Matchroom, Top Rank, Queensberry, etc.), cherche sa fiche détaillée sur Box.live ou Tapology pour extraire TOUS les combats programmés sur la carte (Main Event, Co-Main Event et tous les combats de l'Undercard, soit généralement 5 à 10 combats par gala).\n"
            "- Vérifie les palmarès exacts à jour (Victoires-Défaites-Nuls, KOs) pour chaque boxeur.\n"
            "- Indique précisément si une ceinture WBA, WBC, IBF, WBO, The Ring ou EBU est en jeu.\n"
            "- Pour les cartes Zuffa Boxing / 360 Promotions, indique 'UFC Fight Pass' comme diffuseur."
        ),
    },
    {
        "id": "boxe-japon",
        "title": "🇯🇵 2. Boxe Anglaise — Galas au Japon (Korakuen Hall & Arènes)",
        "fallback_url": "https://boxmob.jp/sp/schedule/",
        "empty_msg": "Aucun gala professionnel japonais répertorié sur cette période de 7 jours.",
        "search_hint": (
            "Recherche le calendrier de boxe professionnelle japonaise (プロボクシング 日程 後楽園ホール) et les cartes complètes sur : "
            "boxmob.jp (Boxing Mobile), boxingnews.jp, tapology.com/fightcenter, ibu4gin.net, asianboxing.info, box.live. "
            "Cherche les galas au Korakuen Hall (後楽園ホール), Ariake Arena, Yokohama, Osaka (Phoenix Battle, Dynamic Glove, Dynamite Glove, Who's Next, etc.)."
        ),
        "specific_rules": (
            "- Liste TOUS les combats connus de chaque réunion japonaise (Main Event et sous-carte complète).\n"
            "- Transcris tous les noms des boxeurs japonais en alphabet latin (Romaji Hepburn).\n"
            "- Convertis les horaires japonais (JST = UTC+9) en heure de Paris (souvent entre 10h00 et 14h00 heure de Paris pour le Korakuen Hall).\n"
            "- Précise les diffuseurs japonais d'origine (Lemino, Boxing Raise, U-NEXT, ABEMA, Prime Video JP, WOWOW) et la disponibilité en France.\n"
            "- Mentionne les titres nationaux JBC, OPBF ou WBO Asia-Pacific en jeu."
        ),
    },
    {
        "id": "ufc",
        "title": "🔥 3. UFC",
        "fallback_url": "https://www.ufc.com/events",
        "empty_msg": "Aucun événement UFC programmé cette semaine.",
        "search_hint": (
            "Recherche le prochain événement UFC prévu dans les 7 prochains jours et sa carte complète (Main Card, Prelims, Early Prelims) sur : "
            "ufc.com/events, tapology.com/fightcenter, sherdog.com/events."
        ),
        "specific_rules": (
            "- Extrais l'intégralité des combats de la soirée (les 10 à 14 combats : Main Card, Prelims et Early Prelims).\n"
            "- Pour la France, indique systématiquement 'RMC Sport / UFC Fight Pass' comme diffuseur.\n"
            "- Convertis les heures du début des Prelims et de la Main Card à l'heure de Paris."
        ),
    },
    {
        "id": "rizin",
        "title": "🌸 4. Rizin Fighting Federation",
        "fallback_url": "https://jp.rizinff.com/schedules/",
        "empty_msg": "Aucun événement Rizin programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel RIZIN (RIZIN 大会日程 対戦カード) sur : "
            "jp.rizinff.com/schedules, tapology.com/fightcenter, sherdog.com."
        ),
        "specific_rules": (
            "- Vérifie strictement si un gala Rizin (Landmark, numéroté ou Super Rizin) tombe dans la fenêtre des 7 prochains jours.\n"
            "- Si oui, liste tous les combats de la carte. Si aucun gala Rizin n'a lieu dans les 7 prochains jours, renvoie immédiatement une liste vide []."
        ),
    },
    {
        "id": "bkfc",
        "title": "👊 5. BKFC (Bare Knuckle)",
        "fallback_url": "https://www.bkfc.com/events",
        "empty_msg": "Aucun événement BKFC programmé cette semaine.",
        "search_hint": (
            "Recherche tous les événements Bare Knuckle Fighting Championship prévus cette semaine (incluant BKFC numérotés, BKFC Fight Night, BKFC Clearwater, etc.) sur : "
            "bkfc.com/events, tapology.com/fightcenter, box.live."
        ),
        "specific_rules": (
            "- Vérifie attentivement les dates au format US (Oct 2026) sur bkfc.com/events et Tapology pour ne manquer aucun 'BKFC Fight Night' ou gala numéroté de cette semaine.\n"
            "- Liste TOUS les combats confirmés de la carte (Main Event, Co-Main Event et toute l'Undercard).\n"
            "- Indique 'BKFC App / TrillerTV / DAZN' selon le diffuseur officiel de l'événement."
        ),
    },
    {
        "id": "kick-japon",
        "title": "🦵 6. Kickboxing Japonais (RISE, K-1, Krush)",
        "fallback_url": "https://www.k-1.co.jp/schedule/",
        "empty_msg": "Aucun gala majeur RISE, K-1 ou Krush programmé cette semaine.",
        "search_hint": (
            "Recherche les prochains événements K-1, Krush et RISE (K-1 Krush RISE 大会日程 対戦カード) et leur carte complète sur : "
            "k-1.co.jp/schedule, rise-rc.com/schedule, tapology.com/fightcenter."
        ),
        "specific_rules": (
            "- Identifie clairement s'il s'agit de K-1, Krush ou RISE, précise la salle (ex: Korakuen Hall) et liste tous les combats principaux et préliminaires annoncés.\n"
            "- Indique ABEMA TV (格闘チャンネル) comme diffuseur d'origine lorsque applicable et convertis l'heure JST en heure de Paris."
        ),
    },
    {
        "id": "glory",
        "title": "🏆 7. Glory Kickboxing",
        "fallback_url": "https://glorykickboxing.com/events",
        "empty_msg": "Aucun événement Glory Kickboxing programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel Glory Kickboxing et la carte complète sur : "
            "glorykickboxing.com/events, tapology.com/fightcenter."
        ),
        "specific_rules": (
            "- Liste tous les combats annoncés sur la carte (tournois Grand Prix, championnats du monde Glory, Superfights et Prelims)."
        ),
    },
    {
        "id": "raf",
        "title": "🤼 8. RAF (Real American Freestyle Wrestling)",
        "fallback_url": "https://www.realamericanfreestyle.com/events-gallery",
        "empty_msg": "Aucun événement RAF programmé cette semaine.",
        "search_hint": (
            "Recherche les événements officiels Real American Freestyle wrestling (RAF) en priorité sur : "
            "realamericanfreestyle.com/events-gallery, realamericanfreestyle.com, flowrestling.org, themat.com."
        ),
        "specific_rules": (
            "- Vérifie les événements programmés sur realamericanfreestyle.com/events-gallery (ex: RAF 14, etc.) dans la fenêtre des 7 prochains jours.\n"
            "- Liste tous les matchups annoncés sur la carte.\n"
            "- Si aucun événement RAF n'a lieu exactement dans les 7 prochains jours, renvoie une liste vide []."
        ),
    },
]

# ==============================================================================
# 4. MOTEUR D'EXTRACTION D'AFFICHES & RÉSOLUTION DES REDIRECTIONS GOOGLE SEARCH
# ==============================================================================
POSTER_CACHE = {}
RESOLVED_URL_CACHE = {}

# Sites bannis uniquement pour les images (bannières génériques trompeuses)
BLOCKED_IMAGE_DOMAINS = [
    "ringwalks.com",
    "box.live",
    "sherdog.com",
    "boxrec.com",
    "fightnights.com",
    "wikipedia.org",
    "wikimedia.org",
    "reddit.com",
]

# Domaines officiels prioritaires pour la récupération d'affiches
TRUSTED_PROMOTER_DOMAINS = [
    "realamericanfreestyle.com",
    "matchroomboxing.com",
    "queensberry.co.uk",
    "ufc.com",
    "ufcfightpass.com",
    "bkfc.com",
    "glorykickboxing.com",
    "rizinff.com",
    "k-1.co.jp",
    "rise-rc.com",
    "dazn.com",
    "ringtv.com",
    "toprank.com",
    "goldenboy.com",
    "premierboxingchampions.com",
    "boxmob.jp",
    "boxingnews.jp",
    "ibu4gin.net",
    "asianboxing.info",
    "flowrestling.org",
]

def is_url_allowed_for_poster(url):
    if not url or not url.startswith("http"):
        return False
    low = url.lower()
    return not any(b in low for b in BLOCKED_IMAGE_DOMAINS)

def is_valid_poster_image(img_url):
    if not img_url or not img_url.startswith("http"):
        return False
    low = img_url.lower()
    bad_tokens = [
        "ringwalks",
        "favicon",
        "logo.svg",
        "default-share",
        "placeholder",
        "avatar",
        ".svg",
    ]
    return not any(t in low for t in bad_tokens)

def resolve_and_verify_url(url):
    """Suit les redirections (notamment vertexaisearch.cloud.google.com) et vérifie que la page n'est pas en erreur 404."""
    if not url or not url.startswith("http"):
        return None
    if url in RESOLVED_URL_CACHE:
        return RESOLVED_URL_CACHE[url]

    try:
        resp = requests.get(url, headers=HEADERS, timeout=7, allow_redirects=True)
        if resp.status_code < 400 and "google.com/search" not in resp.url:
            RESOLVED_URL_CACHE[url] = resp.url
            return resp.url
    except Exception:
        pass

    RESOLVED_URL_CACHE[url] = None
    return None

def extract_og_image_from_url(url):
    """Récupère l'affiche officielle sur le site du promoteur ou le poster d'événement sur Tapology."""
    resolved = resolve_and_verify_url(url) or url
    if not is_url_allowed_for_poster(resolved):
        return None
    if resolved in POSTER_CACHE:
        return POSTER_CACHE[resolved]

    try:
        resp = requests.get(resolved, headers=HEADERS, timeout=8, allow_redirects=True)
        if resp.status_code == 200:
            final_url = resp.url
            if not is_url_allowed_for_poster(final_url):
                POSTER_CACHE[resolved] = None
                return None

            raw = resp.text

            # Si la page est sur Tapology, on cible le vrai poster situé au-dessus de la Fight Card
            if "tapology.com" in final_url.lower():
                tap_match = re.search(
                    r'(https://images\.tapology\.com/poster_images/[0-9]+/profile/[^"\'\s>]+)',
                    raw,
                    flags=re.IGNORECASE,
                )
                if tap_match:
                    tap_img = html.unescape(tap_match.group(1).strip())
                    if is_valid_poster_image(tap_img):
                        POSTER_CACHE[resolved] = tap_img
                        return tap_img
                POSTER_CACHE[resolved] = None
                return None

            # Extraction standard og:image / twitter:image pour les sites officiels
            patterns = [
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            ]
            for pat in patterns:
                m = re.search(pat, raw, flags=re.IGNORECASE)
                if m:
                    img = html.unescape(m.group(1).strip())
                    if img.startswith("//"):
                        img = "https:" + img
                    elif img.startswith("/"):
                        parsed = urllib.parse.urlparse(final_url)
                        img = f"{parsed.scheme}://{parsed.netloc}{img}"
                    if is_url_allowed_for_poster(img) and is_valid_poster_image(img):
                        POSTER_CACHE[resolved] = img
                        return img
    except Exception:
        pass

    POSTER_CACHE[resolved] = None
    return None

def extract_grounding_urls(response):
    """Extrait et résout les URLs réelles visitées par Google Search."""
    raw_urls = []
    try:
        candidates = getattr(response, "candidates", []) or []
        for cand in candidates:
            gm = getattr(cand, "grounding_metadata", None)
            if not gm:
                continue
            chunks = getattr(gm, "grounding_chunks", []) or []
            for ch in chunks:
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    raw_urls.append(web.uri)
    except Exception:
        pass

    resolved_list = []
    for u in raw_urls[:8]:
        real_u = resolve_and_verify_url(u)
        if real_u and real_u not in resolved_list:
            resolved_list.append(real_u)
    return resolved_list

def parse_agent_json(raw_text):
    """Extrait et parse de manière ultra-tolérante le bloc JSON produit par un agent."""
    if not raw_text:
        return []
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data.get("evenements", [])
            if isinstance(data, list):
                return data
        except Exception:
            pass

    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        candidate = cleaned[first_bracket : last_bracket + 1]
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except Exception:
            pass

    return []

# ==============================================================================
# 5. EXÉCUTION D'UN MICRO-AGENT AVEC RECHERCHE GOOGLE & SOUS-CARTES COMPLÈTES
# ==============================================================================
def run_micro_agent(agent_cfg):
    discipline_id = agent_cfg["id"]
    fallback_url = agent_cfg.get("fallback_url", "https://box.live/upcoming-fights-schedule/")
    print(f"\n--- Lancement Micro-Agent [{discipline_id}] ---")

    prompt = f"""Tu es un agent d'extraction de données factuelles spécialisé dans les sports de combat.
Date actuelle : {date_str} (Fuseau horaire : Europe/Paris).
Fenêtre temporelle STRICTE autorisée : {DATE_WINDOW_STR}.

MISSION :
{agent_cfg['search_hint']}

CONSIGNES IMPÉRATIVES :
{agent_cfg['specific_rules']}
- CARTE COMPLÈTE OBLIGATOIRE : Ne t'arrête JAMAIS au seul Main Event. Pour chaque événement retenu, extrais tous les combats répertoriés sur la carte (Main Event, Co-Main Event, et l'ensemble des combats Undercard / Prelims).
- Ne retiens QUE les événements ayant lieu dans la fenêtre temporelle ({DATE_WINDOW_STR}).
- ZÉRO HALLUCINATION : N'invente jamais de combat, d'adversaire 'TBA' fictif, ni de palmarès. Si tu n'es pas certain d'un palmarès ou d'une taille, mets "N/C".
- Convertis tous les poids en kilogrammes (ex: "-63,5 kg") et les tailles en mètres (ex: "1m78").
- Convertis toutes les heures à l'heure de Paris (CET/CEST).
- Rédige les présentations et analyses en français journalistique précis en conservant les termes techniques (puncher, out-boxer, southpaw, main event, undercard, KO, TKO, undisputed).

FORMAT DE SORTIE OBLIGATOIRE :
Renvoie UNIQUEMENT un objet JSON valide respectant exactement ce schéma (avec TOUS les combats de la carte dans le tableau "combats"), sans aucun texte avant ni après :
{{
  "evenements": [
    {{
      "nom_gala": "Nom officiel du gala : Combattant A vs. Combattant B",
      "date_paris": "Samedi 24/10/2026",
      "lieu": "Salle / Arène, Ville, Pays",
      "url_officielle": "https://...",
      "poster_url": "https://... (ou vide si inconnu)",
      "heure_debut_paris": "19h00",
      "heure_main_event_paris": "23h15",
      "diffuseur_origine": "DAZN Global",
      "diffuseur_france": "DAZN France",
      "enjeu_principal": "Titre mondial WBC Super-Légers (-63,5 kg)",
      "presentation": "2 à 3 phrases de présentation factuelle et dynamique des enjeux de l'événement.",
      "combats": [
        {{
          "statut": "Main Event",
          "combattant_a": "Prénom Nom A",
          "palmares_a": "19-0-0, 14 KOs",
          "taille_a": "1m78",
          "combattant_b": "Prénom Nom B",
          "palmares_b": "25-1-0, 11 KOs",
          "taille_b": "1m75",
          "categorie_kg": "Super-légers (-63,5 kg)",
          "rounds": "12 rounds",
          "enjeu_et_analyse": "Titre en jeu et courte description stylistique du duel."
        }},
        {{
          "statut": "Co-Main Event",
          "combattant_a": "Prénom Nom C",
          "palmares_a": "15-1-0, 9 KOs",
          "taille_a": "1m82",
          "combattant_b": "Prénom Nom D",
          "palmares_b": "18-2-0, 8 KOs",
          "taille_b": "1m80",
          "categorie_kg": "Mi-moyens (-66,7 kg)",
          "rounds": "10 rounds",
          "enjeu_et_analyse": "Enjeu et style de l'affrontement."
        }},
        {{
          "statut": "Undercard",
          "combattant_a": "Prénom Nom E",
          "palmares_a": "10-0-0, 6 KOs",
          "taille_a": "1m73",
          "combattant_b": "Prénom Nom F",
          "palmares_b": "12-3-1, 4 KOs",
          "taille_b": "1m71",
          "categorie_kg": "Légers (-61,2 kg)",
          "rounds": "8 rounds",
          "enjeu_et_analyse": "Combat de la sous-carte."
        }}
      ]
    }}
  ]
}}
Si aucun événement n'est programmé dans cette fenêtre de 7 jours, renvoie exactement : {{"evenements": []}}
"""

    for idx, model_name in enumerate(list(AVAILABLE_MODELS)):
        try:
            print(f"  Appel {model_name} avec Google Search Grounding...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.1,
                ),
            )
            if response and getattr(response, "text", None):
                # On place le modèle réactif en tête pour éviter les attentes sur les agents suivants
                if AVAILABLE_MODELS[0] != model_name and model_name in AVAILABLE_MODELS:
                    AVAILABLE_MODELS.remove(model_name)
                    AVAILABLE_MODELS.insert(0, model_name)

                events = parse_agent_json(response.text)
                grounding_urls = extract_grounding_urls(response)

                for ev in events:
                    raw_ev_url = (ev.get("url_officielle") or "").strip()
                    valid_ev_url = resolve_and_verify_url(raw_ev_url)
                    if not valid_ev_url:
                        valid_ev_url = grounding_urls[0] if grounding_urls else fallback_url
                    ev["url_officielle"] = valid_ev_url

                    poster = (ev.get("poster_url") or "").strip()
                    if not is_url_allowed_for_poster(poster) or not is_valid_poster_image(poster):
                        poster = ""

                    if not poster:
                        # 1. Essai sur l'URL officielle vérifiée
                        found_poster = extract_og_image_from_url(valid_ev_url)
                        # 2. Essai sur les URLs de promoteurs officiels puis Tapology (résolues)
                        if not found_poster:
                            trusted_urls = [
                                u for u in grounding_urls if any(t in u.lower() for t in TRUSTED_PROMOTER_DOMAINS)
                            ]
                            tapology_urls = [
                                u for u in grounding_urls if "tapology.com" in u.lower()
                            ]
                            other_urls = [
                                u for u in grounding_urls
                                if u not in trusted_urls and u not in tapology_urls and is_url_allowed_for_poster(u)
                            ]
                            for g_url in (trusted_urls + tapology_urls + other_urls)[:6]:
                                found_poster = extract_og_image_from_url(g_url)
                                if found_poster:
                                    break
                        poster = found_poster or ""

                    ev["poster_url"] = poster

                print(f"  -> {len(events)} événement(s) vérifié(s) extrait(s) pour [{discipline_id}].")
                return events

        except Exception as e:
            err_str = str(e)
            print(f"  [Avertissement] {model_name} sur [{discipline_id}] : {err_str[:160]}")
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait_time = 25 if idx == 0 else 15
                print(f"  Pause de sécurité de {wait_time}s (quota API) avant rotation de modèle...")
                time.sleep(wait_time)
            else:
                time.sleep(3)

    print(f"  [Info] Aucun résultat récupéré pour [{discipline_id}], affichage de l'état vide sécurisé.")
    return []

# ==============================================================================
# 6. CONSTRUCTION DU HTML PAR SECTION À PARTIR DU JSON VÉRIFIÉ
# ==============================================================================
def render_event_card_html(ev):
    nom_gala = html.escape(ev.get("nom_gala") or "Événement Officiel")
    date_paris = html.escape(ev.get("date_paris") or "")
    lieu = html.escape(ev.get("lieu") or "Lieu à confirmer")
    url_off = (ev.get("url_officielle") or "").strip()
    poster_url = (ev.get("poster_url") or "").strip()
    h_debut = html.escape(ev.get("heure_debut_paris") or "N/C")
    h_main = html.escape(ev.get("heure_main_event_paris") or "N/C")
    diff_orig = html.escape(ev.get("diffuseur_origine") or "International")
    diff_fr = html.escape(ev.get("diffuseur_france") or "Streaming officiel")
    enjeu = html.escape(ev.get("enjeu_principal") or "")
    presentation = html.escape(ev.get("presentation") or "")
    combats = ev.get("combats") or []

    belt_badge = f'<div class="poster-belt">👑 {enjeu}</div>' if enjeu else ""
    if poster_url.startswith("http"):
        poster_block = f"""
        <div class="poster-container">
            <img src="{html.escape(poster_url)}" alt="{nom_gala}" class="event-poster-img"
                 onerror="this.parentElement.classList.add('poster-fallback-mode'); this.style.display='none';">
            <div class="poster-overlay">
                {belt_badge}
                <div class="poster-dynamic-title">{nom_gala}</div>
            </div>
        </div>
        """
    else:
        poster_block = f"""
        <div class="poster-container poster-fallback-mode">
            <div class="poster-overlay">
                {belt_badge}
                <div class="poster-dynamic-title">{nom_gala}</div>
            </div>
        </div>
        """

    source_btn = (
        f'<a href="{html.escape(url_off)}" target="_blank" rel="noopener" class="source-btn">🔗 Source officielle</a>'
        if url_off.startswith("http")
        else ""
    )

    bouts_html_list = []
    for c in combats:
        statut = html.escape(c.get("statut") or "")
        fa = html.escape(c.get("combattant_a") or "")
        pa = html.escape(c.get("palmares_a") or "")
        ta = html.escape(c.get("taille_a") or "")
        fb = html.escape(c.get("combattant_b") or "")
        pb = html.escape(c.get("palmares_b") or "")
        tb = html.escape(c.get("taille_b") or "")
        cat = html.escape(c.get("categorie_kg") or "")
        rds = html.escape(c.get("rounds") or "")
        desc = html.escape(c.get("enjeu_et_analyse") or "")

        meta_a = " | ".join(x for x in [pa, ta] if x and x != "N/C")
        meta_b = " | ".join(x for x in [pb, tb] if x and x != "N/C")
        str_a = f"{fa} ({meta_a})" if meta_a else fa
        str_b = f"{fb} ({meta_b})" if meta_b else fb

        pill_text = " — ".join(x for x in [statut, cat, rds] if x)
        pill_html = f'<span class="weight-pill">{pill_text}</span>' if pill_text else ""

        bouts_html_list.append(f"""
        <div class="bout-item">
            <div class="bout-header">
                <div class="bout-fighters">🥊 <strong>{str_a}</strong> vs. <strong>{str_b}</strong></div>
                {pill_html}
            </div>
            <div class="bout-desc">{desc}</div>
        </div>
        """)

    if bouts_html_list:
        bouts_content = "".join(bouts_html_list)
        count_label = f"({len(bouts_html_list)} combat{'s' if len(bouts_html_list) > 1 else ''} répertorié{'s' if len(bouts_html_list) > 1 else ''})"
    else:
        bouts_content = "<div class='bout-item'><em>Détail complet de la sous-carte en attente de publication officielle par le promoteur.</em></div>"
        count_label = ""

    return f"""
    <article class="event-card">
        {poster_block}
        <div class="card-header-row">
            <h3 class="event-title">{nom_gala}</h3>
            {source_btn}
        </div>
        <div class="meta-grid">
            <div class="meta-item">📅 <strong>Date :</strong> <span class="highlight-date">{date_paris}</span> &nbsp;|&nbsp; 📍 <strong>Lieu :</strong> {lieu}</div>
            <div class="meta-item">⏰ <strong>Horaires (Paris) :</strong> Début du gala à <strong>{h_debut}</strong> &nbsp;|&nbsp; Main Event vers <strong>{h_main}</strong></div>
            <div class="meta-item">📺 <strong>Diffusion :</strong> <span class="badge-tv">Origine : {diff_orig}</span> <span class="badge-tv badge-fr">France : {diff_fr}</span></div>
        </div>
        <p class="event-desc"><strong>🎯 Présentation & Enjeux :</strong> {presentation}</p>
        <details class="card-details">
            <summary>📊 Développer la carte complète, palmarès et analyse des combats {count_label}</summary>
            <div class="fight-list">
                {bouts_content}
            </div>
        </details>
    </article>
    """

# Exécution séquentielle des 8 Micro-Agents avec temporisation anti-quota
sections_html = []
for idx, cfg in enumerate(AGENTS_CONFIG):
    events_list = run_micro_agent(cfg)
    if events_list:
        cards_rendered = "\n".join(render_event_card_html(ev) for ev in events_list)
    else:
        cards_rendered = f'<div class="empty-state">🗓️ {html.escape(cfg["empty_msg"])}</div>'

    section_block = f"""
    <section id="{cfg['id']}" class="sport-section">
        <h2>{cfg['title']}</h2>
        {cards_rendered}
    </section>
    """
    sections_html.append(section_block)

    if idx < len(AGENTS_CONFIG) - 1:
        time.sleep(14)

all_sections_content = "\n".join(sections_html)

# ==============================================================================
# 7. ASSEMBLAGE DU SITE FINAL (DESIGN BEIGE/MARRON + POSTERS CONTAIN SANS ROGNAGE)
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
            padding: 0.48rem 0.85rem;
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
            padding: 0.5rem 0.95rem;
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
        .sport-section {{
            margin-bottom: 3rem;
            scroll-margin-top: 135px;
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
        .event-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-warm);
            border-radius: 16px;
            padding: 1.5rem;
            margin-bottom: 1.8rem;
            box-shadow: var(--shadow-soft);
            overflow: hidden;
        }}
        /* Bannière Poster Officiel avec object-fit: contain (Zéro rognage des portraits ou affiches) */
        .poster-container {{
            position: relative;
            width: 100%;
            height: 320px;
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 1.25rem;
            background: radial-gradient(circle at center, #4E2E1B 0%, #1E1008 100%);
            border: 1px solid #6E452B;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .event-poster-img {{
            width: 100%;
            height: 100%;
            object-fit: contain;
            object-position: center center;
            display: block;
            padding-bottom: 2rem;
        }}
        .poster-overlay {{
            position: absolute;
            inset: 0;
            background: linear-gradient(to top, rgba(24, 12, 5, 0.95) 0%, rgba(24, 12, 5, 0.42) 32%, rgba(24, 12, 5, 0.0) 65%);
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            align-items: flex-start;
            padding: 1rem 1.35rem;
            color: #FFFDF9;
            pointer-events: none;
        }}
        .poster-container.poster-fallback-mode {{
            height: auto;
            min-height: 135px;
            background: linear-gradient(135deg, #2D1B10 0%, #5A351E 50%, #2D1B10 100%);
        }}
        .poster-container.poster-fallback-mode .poster-overlay {{
            position: relative;
            background: none;
            align-items: center;
            text-align: center;
            justify-content: center;
            width: 100%;
            padding: 1.5rem 1rem;
        }}
        .poster-belt {{
            display: inline-block;
            background-color: rgba(196, 136, 43, 0.32);
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
        .poster-dynamic-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.4rem;
            font-weight: 800;
            color: #FFFDF9;
            text-shadow: 0 2px 8px rgba(0,0,0,0.75);
            line-height: 1.25;
        }}
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
            padding: 0.32rem 0.78rem;
            border-radius: 8px;
            transition: all 0.2s ease;
        }}
        .source-btn:hover {{
            background-color: var(--accent-main);
            color: #FFFFFF;
            border-color: var(--accent-main);
        }}
        .meta-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.55rem;
            background-color: var(--bg-details);
            border: 1px solid var(--border-warm);
            border-radius: 10px;
            padding: 0.95rem 1.15rem;
            margin-bottom: 1rem;
            font-size: 0.94rem;
        }}
        .highlight-date {{
            color: var(--accent-main);
            font-weight: 700;
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
            gap: 0.5rem;
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
            white-space: nowrap;
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
            margin-bottom: 0.3rem;
        }}
        .bout-fighters {{
            font-size: 0.98rem;
            color: var(--text-primary);
        }}
        .bout-desc {{
            font-size: 0.91rem;
            color: var(--text-secondary);
        }}
        .weight-pill {{
            display: inline-block;
            background-color: var(--bg-subtle);
            color: var(--accent-dark);
            font-weight: 700;
            font-size: 0.78rem;
            padding: 0.18rem 0.6rem;
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
            .poster-container {{ height: 250px; }}
            .poster-dynamic-title {{ font-size: 1.18rem; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header class="hero">
            <h1>🥊 Calendrier des Sports de Combat</h1>
            <p>Programmation officielle vérifiée : Boxe Mondiale & Japonaise, The Ring, Zuffa, UFC, Rizin, BKFC, Kickboxing & RAF</p>
            <span class="update-pill">🔄 Mis à jour le {date_str} à {heure_str} (Heure de Paris) • Période : {valid_dates_fr[0]} au {valid_dates_fr[-1]}</span>
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
                <input type="search" id="searchInput" class="search-input" placeholder="🔍 Filtrer par combattant, promoteur (Matchroom, Queensberry, The Ring...), ville ou chaîne...">
                <button type="button" id="toggleAllBtn" class="toggle-all-btn">📂 Tout déplier</button>
            </div>
        </div>

        <main id="mainContent">
            {all_sections_content}
        </main>

        <footer>
            <p>Architecture Micro-Agents vérifiée • Fuseau horaire : Paris (CET/CEST) • Système métrique standardisé (kg / m)</p>
        </footer>
    </div>

    <script>
        const toggleBtn = document.getElementById('toggleAllBtn');
        let allOpen = false;
        toggleBtn.addEventListener('click', () => {{
            allOpen = !allOpen;
            document.querySelectorAll('details.card-details').forEach(d => {{
                d.open = allOpen;
            }});
            toggleBtn.textContent = allOpen ? '📁 Tout replier' : '📂 Tout déplier';
        }});

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

print("\nSynchronisation terminée avec succès : index.html et calendrier.html sont à jour.")
