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

# Fenêtre de J-1 (veille, pour conserver les galas US de la nuit de vendredi à samedi) à J+7
valid_dates_fr = []
valid_dates_iso = []
valid_dates_us = []
valid_dates_jp = []
for offset in range(-1, 8):
    d = now_paris + timedelta(days=offset)
    valid_dates_fr.append(d.strftime("%d/%m/%Y"))
    valid_dates_iso.append(d.strftime("%Y-%m-%d"))
    valid_dates_us.append(d.strftime("%m.%d.%Y"))
    valid_dates_jp.append(f"{d.month}月{d.day}日")

DATE_WINDOW_STR = (
    f"du {valid_dates_fr[0]} au {valid_dates_fr[-1]} inclus "
    f"(Dates ISO : {', '.join(valid_dates_iso)} | "
    f"Format US : {', '.join(valid_dates_us)} | "
    f"Format JP : {', '.join(valid_dates_jp)})"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6",
}

# ==============================================================================
# 2. DÉCOUVERTE DYNAMIQUE DES MODÈLES DISPONIBLES (MODÈLES TEXTE COMPLETS EN TÊTE)
# ==============================================================================
def get_available_models():
    preferred = [
        "gemini-3.8-flash",
        "gemini-3.8-flash-lite",
        "gemini-3.5-flash",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-pro-latest",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.1-flash-lite",
    ]
    # Exclusion des modèles 2.5 (404) et des modèles image/audio non-textuels
    excluded = [
        "embedding", "imagen", "veo", "tts", "audio", "vision", "aqa",
        "2.5", "image", "banana", "transcribe", "argon", "customtools"
    ]
    discovered = []
    try:
        for m in client.models.list():
            short = getattr(m, "name", "").replace("models/", "")
            if "gemini" in short and not any(ex in short for ex in excluded):
                discovered.append(short)
        print(f"Modèles actifs détectés : {discovered}")
    except Exception as e:
        print(f"Info liste modèles : {e}")

    final_list = [p for p in preferred if not discovered or p in discovered]
    for d in discovered:
        if d not in final_list:
            final_list.append(d)
    return final_list or ["gemini-3.8-flash"]

AVAILABLE_MODELS = get_available_models()

# ==============================================================================
# 3. CONFIGURATION DES 8 MICRO-AGENTS (16384 TOKENS POUR LA BOXE, 8192 POUR LE RESTE)
# ==============================================================================
AGENTS_CONFIG = [
    {
        "id": "boxe-monde",
        "title": "🥊 1. Boxe Anglaise — Galas Internationaux",
        "max_tokens": 16384,
        "fallback_url": "https://box.live/upcoming-fights-schedule/",
        "empty_msg": "Aucun gala international majeur répertorié dans la fenêtre des 7 prochains jours.",
        "search_hint": (
            "Recherche les galas de boxe anglaise prévus cette semaine et leur carte complète (full fight card / undercard) sur : "
            "box.live, tapology.com/fightcenter, ringtv.com/schedule, matchroomboxing.com/events, queensberry.co.uk, "
            "dazn.com, espn.com/boxing/schedule, fightnights.com, ufcfightpass.com. "
            "Inclus impérativement les cartes Matchroom, Queensberry, Top Rank, PBC, Golden Boy, "
            "les cartes The Ring / Riyadh Season et Zuffa Boxing (UFC Fight Pass)."
        ),
        "specific_rules": (
            "- SOUS-CARTE COMPLÈTE OBLIGATOIRE : Pour chaque gala trouvé (notamment Matchroom, Queensberry, Top Rank, etc.), consulte sa fiche sur Box.live, Tapology ou le site du promoteur et liste TOUS les combats programmés (Main Event, Co-Main Event, Undercard et combats préliminaires 'Before the Bell', soit l'intégralité des 5 à 10 combats de la soirée).\n"
            "- Vérifie les palmarès exacts à jour (Victoires-Défaites-Nuls, KOs) sur Box.live, Tapology ou le site du promoteur.\n"
            "- Indique précisément si une ceinture WBA, WBC, IBF, WBO, The Ring ou EBU est en jeu.\n"
            "- Pour les cartes Zuffa Boxing / 360 Promotions, indique 'UFC Fight Pass' comme diffuseur."
        ),
    },
    {
        "id": "boxe-japon",
        "title": "🇯🇵 2. Boxe Anglaise — Galas au Japon (Korakuen Hall & Arènes)",
        "max_tokens": 16384,
        "fallback_url": "https://boxmob.jp/sp/schedule/",
        "empty_msg": "Aucun gala professionnel japonais répertorié sur cette période de 7 jours.",
        "search_hint": (
            "Recherche le calendrier de boxe professionnelle japonaise (プロボクシング 日程 後楽園ホール) et les cartes complètes sur : "
            "boxmob.jp (Boxing Mobile), boxingnews.jp, tapology.com/fightcenter, ibu4gin.net, asianboxing.info, box.live. "
            "Cherche les galas au Korakuen Hall (後楽園ホール), Ariake Arena, Yokohama, Osaka (Phoenix Battle, Dynamic Glove, Dynamite Glove, Who's Next, Lemino Boxing, etc.)."
        ),
        "specific_rules": (
            "- Liste TOUS les combats programmés pour chaque gala japonais (Main Event, Co-Main et toute la sous-carte).\n"
            "- Transcris tous les noms des boxeurs japonais en alphabet latin (Romaji Hepburn).\n"
            "- Convertis les horaires japonais (JST = UTC+9) en heure de Paris (souvent entre 10h00 et 14h00 heure de Paris pour le Korakuen Hall).\n"
            "- Précise les diffuseurs japonais d'origine (Lemino, Boxing Raise, U-NEXT, ABEMA, Prime Video JP, WOWOW) et la disponibilité en France.\n"
            "- Mentionne les titres nationaux JBC, OPBF ou WBO Asia-Pacific en jeu."
        ),
    },
    {
        "id": "ufc",
        "title": "🔥 3. UFC",
        "max_tokens": 8192,
        "fallback_url": "https://www.ufc.com/events",
        "empty_msg": "Aucun événement UFC programmé cette semaine.",
        "search_hint": (
            "Recherche l'événement UFC prévu aujourd'hui ou dans les 7 prochains jours (UFC numéroté type UFC 333 ou UFC Fight Night) et sa carte complète sur : "
            "ufc.com/events, espn.com/mma/schedule, tapology.com/fightcenter, sherdog.com/events."
        ),
        "specific_rules": (
            "- Inclus impérativement l'événement UFC ayant lieu aujourd'hui même (" + date_str + ") ou cette semaine.\n"
            "- Liste la carte complète (Main Card, Prelims, Early Prelims) avec tous les combats programmés.\n"
            "- Pour la France, indique systématiquement 'RMC Sport / UFC Fight Pass' comme diffuseur.\n"
            "- Convertis les heures du début des Prelims et de la Main Card à l'heure de Paris."
        ),
    },
    {
        "id": "rizin",
        "title": "🌸 4. Rizin Fighting Federation",
        "max_tokens": 8192,
        "fallback_url": "https://jp.rizinff.com/schedules/",
        "empty_msg": "Aucun événement Rizin programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel RIZIN (RIZIN 大会日程 対戦カード) sur : "
            "jp.rizinff.com/schedules, tapology.com/fightcenter, sherdog.com."
        ),
        "specific_rules": (
            "- Vérifie strictement si un gala Rizin (Landmark, numéroté ou Super Rizin) tombe dans la fenêtre autorisée.\n"
            "- Si aucun gala Rizin n'a lieu dans cette période, renvoie immédiatement une liste vide []."
        ),
    },
    {
        "id": "bkfc",
        "title": "👊 5. BKFC (Bare Knuckle)",
        "max_tokens": 8192,
        "fallback_url": "https://www.bkfc.com/events",
        "empty_msg": "Aucun événement BKFC programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel Bare Knuckle Fighting Championship (BKFC numérotés et BKFC Fight Night, incluant ceux d'hier soir et de cette semaine) sur : "
            "bkfc.com/events, tapology.com/fightcenter."
        ),
        "specific_rules": (
            "- Liste TOUS les combats confirmés de la carte (Main Event, Co-Main Event et toute l'Undercard).\n"
            "- Indique 'BKFC App / TrillerTV / DAZN' selon le diffuseur officiel de l'événement."
        ),
    },
    {
        "id": "kick-japon",
        "title": "🦵 6. Kickboxing Japonais (RISE, K-1, Krush)",
        "max_tokens": 8192,
        "fallback_url": "https://www.k-1.co.jp/schedule/",
        "empty_msg": "Aucun gala majeur RISE, K-1 ou Krush programmé cette semaine.",
        "search_hint": (
            "Recherche les événements K-1, Krush et RISE (K-1 Krush RISE 大会日程 対戦カード / K-1 World GP) prévus cette semaine sur : "
            "k-1.co.jp/schedule, k-1.world, rise-rc.com/schedule, tapology.com/fightcenter."
        ),
        "specific_rules": (
            "- Identifie clairement s'il s'agit de K-1, Krush ou RISE, précise la salle et liste tous les combats de la carte.\n"
            "- Indique ABEMA TV (格闘チャンネル) ou DAZN comme diffuseur d'origine lorsque applicable et convertis l'heure en heure de Paris."
        ),
    },
    {
        "id": "glory",
        "title": "🏆 7. Glory Kickboxing",
        "max_tokens": 8192,
        "fallback_url": "https://glorykickboxing.com/events",
        "empty_msg": "Aucun événement Glory Kickboxing programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel Glory Kickboxing sur : "
            "glorykickboxing.com/events, tapology.com/fightcenter."
        ),
        "specific_rules": (
            "- Liste tous les combats annoncés (tournois Grand Prix, championnats du monde Glory, Superfights et Prelims)."
        ),
    },
    {
        "id": "raf",
        "title": "🤼 8. RAF (Real American Freestyle Wrestling)",
        "max_tokens": 8192,
        "fallback_url": "https://www.realamericanfreestyle.com/events-gallery",
        "empty_msg": "Aucun événement RAF programmé cette semaine.",
        "search_hint": (
            "Recherche les événements officiels Real American Freestyle wrestling (RAF) et Joe Hand Promotions sur : "
            "realamericanfreestyle.com/events-gallery, realamericanfreestyle.com, flowrestling.org, themat.com et les communiqués officiels RAF."
        ),
        "specific_rules": (
            "- Liste tous les combats (matchups) programmés lors de l'événement RAF de la période autorisée.\n"
            "- Si aucun événement RAF n'a lieu dans la fenêtre autorisée, renvoie une liste vide []."
        ),
    },
]

# ==============================================================================
# 4. MOTEUR D'EXTRACTION D'AFFICHES + BANNIÈRES OFFICIELLES GARANTIES (SANS DOUBLON)
# ==============================================================================
POSTER_CACHE = {}
USED_EVENT_POSTERS = set()

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
    "k-1.world",
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

def make_svg_banner(bg_start, bg_end, accent_color, main_title, subtitle, letter_spacing="8"):
    """Bannière vectorielle officielle de secours si le site du promoteur bloque le scraping (ex: UFC / RAF)."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 600" width="100%" height="100%">'
        f'<defs><radialGradient id="g" cx="50%" cy="45%" r="75%">'
        f'<stop offset="0%" stop-color="{bg_start}"/><stop offset="100%" stop-color="{bg_end}"/>'
        f'</radialGradient></defs>'
        f'<rect width="1200" height="600" fill="url(#g)"/>'
        f'<rect x="350" y="135" width="500" height="3" fill="{accent_color}" opacity="0.85"/>'
        f'<text x="600" y="265" text-anchor="middle" fill="#FFFFFF" font-family="Outfit, Arial, sans-serif" '
        f'font-size="84" font-weight="800" letter-spacing="{letter_spacing}">{main_title}</text>'
        f'<text x="600" y="335" text-anchor="middle" fill="{accent_color}" font-family="Plus Jakarta Sans, Arial, sans-serif" '
        f'font-size="28" font-weight="700" letter-spacing="12">{subtitle}</text>'
        f'<rect x="350" y="375" width="500" height="3" fill="{accent_color}" opacity="0.85"/>'
        f'</svg>'
    )
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)

FIXED_PROMOTER_BANNERS = {
    "matchroom": make_svg_banner("#141414", "#050505", "#E5E5E5", "MATCHROOM", "B O X I N G", "8"),
    "queensberry": make_svg_banner("#1A140B", "#080604", "#D4AF37", "QUEENSBERRY", "P R O M O T I O N S", "6"),
    "top rank": make_svg_banner("#1B120C", "#090604", "#C89D54", "TOP RANK", "B O X I N G", "8"),
    "golden boy": make_svg_banner("#1F1709", "#0A0703", "#E6B84D", "GOLDEN BOY", "P R O M O T I O N S", "7"),
    "pbc": make_svg_banner("#121824", "#06080E", "#D4AF37", "P B C", "PREMIER BOXING CHAMPIONS", "10"),
    "the ring": make_svg_banner("#1C130E", "#0A0604", "#E2B049", "THE RING", "OFFICIAL FIGHT NIGHT", "8"),
    "zuffa": make_svg_banner("#220D0D", "#0A0404", "#D20A0A", "ZUFFA BOXING", "UFC FIGHT PASS", "6"),
    "ufc": make_svg_banner("#260B0B", "#090303", "#D20A0A", "U F C", "ULTIMATE FIGHTING CHAMPIONSHIP", "14"),
    "rizin": make_svg_banner("#1A1326", "#08050E", "#E5C158", "R I Z I N", "FIGHTING FEDERATION", "12"),
    "bkfc": make_svg_banner("#1E190A", "#090703", "#F5C518", "B K F C", "BARE KNUCKLE FIGHTING CHAMPIONSHIP", "12"),
    "k-1": make_svg_banner("#240C0C", "#090303", "#E63946", "K - 1", "WORLD GRAND PRIX", "12"),
    "krush": make_svg_banner("#1F0E14", "#080406", "#E63946", "K R U S H", "K-1 GROUP OFFICIAL EVENT", "10"),
    "rise": make_svg_banner("#0E1A2B", "#050910", "#F4A261", "R I S E", "EXTREME FIGHT GAME JAPAN", "12"),
    "glory": make_svg_banner("#1D160B", "#080603", "#E6B84D", "G L O R Y", "GLORY KICKBOXING WORLD SERIES", "12"),
    "raf": make_svg_banner("#131C2E", "#070A12", "#E63946", "R A F", "REAL AMERICAN FREESTYLE WRESTLING", "14"),
}

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

def extract_og_image_from_url(url):
    """Suit les redirections HTTP et extrait l'affiche officielle ou le vrai poster Tapology."""
    if not is_url_allowed_for_poster(url):
        return None
    if url in POSTER_CACHE:
        return POSTER_CACHE[url]

    try:
        resp = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
        final_url = resp.url if resp else url

        if resp.status_code >= 400 and any(t in final_url.lower() for t in TRUSTED_PROMOTER_DOMAINS):
            parsed_root = urllib.parse.urlparse(final_url)
            root_url = f"{parsed_root.scheme}://{parsed_root.netloc}/"
            resp = requests.get(root_url, headers=HEADERS, timeout=8, allow_redirects=True)
            final_url = resp.url

        if not is_url_allowed_for_poster(final_url):
            POSTER_CACHE[url] = None
            return None

        if resp.status_code == 200:
            raw = resp.text

            # Cas particulier Tapology : uniquement le poster officiel situé au-dessus des combattants
            if "tapology.com" in final_url.lower():
                tap_match = re.search(
                    r'(https://images\.tapology\.com/poster_images/[0-9]+/profile/[^"\'\s>]+)',
                    raw,
                    flags=re.IGNORECASE,
                )
                if tap_match:
                    tap_img = html.unescape(tap_match.group(1).strip())
                    if is_valid_poster_image(tap_img):
                        POSTER_CACHE[url] = tap_img
                        return tap_img
                POSTER_CACHE[url] = None
                return None

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
                        POSTER_CACHE[url] = img
                        return img
    except Exception:
        pass

    POSTER_CACHE[url] = None
    return None

def get_event_keywords(ev):
    """Extrait les noms de famille des combattants du Main Event pour vérifier qu'un lien correspond bien à ce gala."""
    keywords = []
    combats = ev.get("combats") or []
    if combats:
        for field in ["combattant_a", "combattant_b"]:
            name = (combats[0].get(field) or "").strip()
            parts = [p.lower() for p in re.split(r"[\s\-]+", name) if len(p) >= 4]
            keywords.extend(parts)
    title_words = [w.lower() for w in re.split(r"[\s\-:]+", ev.get("nom_gala") or "") if len(w) >= 5]
    keywords.extend(title_words)
    return list(set(keywords))

def resolve_event_poster(ev, ev_url, grounding_urls, discipline_id, total_events_in_section):
    """
    Cascade sécurisée en 4 étapes :
    1. Poster direct valide
    2. Page officielle du gala (ev_url)
    3. Liens Google Search (uniquement s'ils correspondent à ce combat précis, sans jamais dupliquer une photo)
    4. Bannière officielle du promoteur (web ou SVG haute définition garanti pour UFC, RAF, Matchroom, etc.)
    """
    # 1. URL de poster directe fournie par l'agent
    raw_poster = (ev.get("poster_url") or "").strip()
    if is_url_allowed_for_poster(raw_poster) and is_valid_poster_image(raw_poster):
        if raw_poster not in USED_EVENT_POSTERS:
            USED_EVENT_POSTERS.add(raw_poster)
            return raw_poster

    # 2. Extraction depuis l'URL officielle de l'événement
    found_poster = extract_og_image_from_url(ev_url)
    if found_poster and found_poster not in USED_EVENT_POSTERS:
        USED_EVENT_POSTERS.add(found_poster)
        return found_poster

    # 3. Recherche dans grounding_urls : autorisée si 1 seul événement dans la section
    #    ou si l'URL contient le nom d'un combattant du gala (empêche le doublon Navarrete / Top Tier Boxing)
    ev_keywords = get_event_keywords(ev)
    for g_url in grounding_urls[:6]:
        g_low = g_url.lower()
        if total_events_in_section == 1 or any(kw in g_low for kw in ev_keywords):
            candidate = extract_og_image_from_url(g_url)
            if candidate and candidate not in USED_EVENT_POSTERS:
                USED_EVENT_POSTERS.add(candidate)
                return candidate

    # 4. Filet de sécurité : Bannière officielle du promoteur ou de la ligue
    gala_context = f"{ev.get('nom_gala', '')} {ev.get('diffuseur_origine', '')} {discipline_id}".lower()
    promoter_sites = [
        ("matchroom", "https://www.matchroomboxing.com/", "matchroom"),
        ("queensberry", "https://www.queensberry.co.uk/", "queensberry"),
        ("top rank", "https://www.toprank.com/", "top rank"),
        ("golden boy", "https://www.goldenboy.com/", "golden boy"),
        ("pbc", "https://www.premierboxingchampions.com/", "pbc"),
        ("premier boxing", "https://www.premierboxingchampions.com/", "pbc"),
        ("the ring", "https://www.ringtv.com/", "the ring"),
        ("riyadh season", "https://www.ringtv.com/", "the ring"),
        ("zuffa", "https://www.ufcfightpass.com/", "zuffa"),
        ("bkfc", "https://www.bkfc.com/events", "bkfc"),
        ("ufc", "https://www.ufc.com/events", "ufc"),
        ("rizin", "https://jp.rizinff.com/", "rizin"),
        ("krush", "https://www.k-1.co.jp/", "krush"),
        ("k-1", "https://www.k-1.co.jp/", "k-1"),
        ("rise", "https://rise-rc.com/", "rise"),
        ("glory", "https://glorykickboxing.com/events", "glory"),
        ("raf", "https://www.realamericanfreestyle.com/events-gallery", "raf"),
    ]
    for keyword, site_url, svg_key in promoter_sites:
        if keyword in gala_context:
            web_banner = extract_og_image_from_url(site_url)
            if web_banner:
                return web_banner
            return FIXED_PROMOTER_BANNERS.get(svg_key, "")

    return ""

def verify_and_fix_official_url(ev_url, ev, fallback_url, grounding_urls):
    """Corrige les liens vides ou les sous-pages en erreur 404 (notamment BKFC et Matchroom)."""
    url = (ev_url or "").strip()
    if (not url or "google.com/search" in url) and grounding_urls:
        url = grounding_urls[0]
    if not url.startswith("http"):
        url = fallback_url

    low_url = url.lower()
    if any(d in low_url for d in ["bkfc.com/events/", "matchroomboxing.com/events/", "queensberry.co.uk/"]):
        try:
            chk = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=True)
            head_txt = chk.text[:2000].lower() if chk.text else ""
            if chk.status_code == 404 or chk.status_code >= 500 or "page not found" in head_txt or "doesn't exist" in head_txt:
                if "bkfc.com" in low_url:
                    return "https://www.bkfc.com/events"
                if "matchroomboxing.com" in low_url:
                    return "https://www.matchroomboxing.com/events/"
                if "queensberry.co.uk" in low_url:
                    return "https://www.queensberry.co.uk/"
                return fallback_url
        except Exception:
            if "bkfc.com" in low_url:
                return "https://www.bkfc.com/events"
            if "matchroomboxing.com" in low_url:
                return "https://www.matchroomboxing.com/events/"
            return fallback_url

    return url

def extract_grounding_urls(response):
    """Extrait les URLs réelles visitées par Google Search depuis les métadonnées de la réponse."""
    urls = []
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
                    urls.append(web.uri)
    except Exception:
        pass
    return urls

def parse_agent_json(raw_text):
    """
    Extrait et parse le bloc JSON produit par un agent.
    Inclut une réparation automatique si une longue carte (ex: 14 combats UFC) a été coupée en fin de réponse.
    """
    if not raw_text:
        return None
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

    # Réparation de secours si le JSON a été coupé au milieu du dernier combat d'une longue sous-carte
    if first_brace != -1 and '"evenements"' in cleaned and '"combats"' in cleaned:
        last_complete_bout = cleaned.rfind("}")
        while last_complete_bout > first_brace:
            for suffix in ["]}]}", "]}]"]:
                attempt = cleaned[first_brace : last_complete_bout + 1] + suffix
                attempt = re.sub(r",\s*([}\]])", r"\1", attempt)
                try:
                    data = json.loads(attempt)
                    if isinstance(data, dict) and "evenements" in data:
                        return data["evenements"]
                except Exception:
                    pass
            last_complete_bout = cleaned.rfind("}", first_brace, last_complete_bout)

    return None

# ==============================================================================
# 5. EXÉCUTION D'UN MICRO-AGENT (16384 TOKENS BOXE / 8192 AUTRES + REPLI AUTO)
# ==============================================================================
def run_micro_agent(agent_cfg):
    discipline_id = agent_cfg["id"]
    target_tokens = agent_cfg.get("max_tokens", 8192)
    fallback_url = agent_cfg.get("fallback_url", "https://box.live/upcoming-fights-schedule/")
    print(f"\n--- Lancement Micro-Agent [{discipline_id}] (Plafond : {target_tokens} tokens) ---")

    prompt = f"""Tu es un agent d'extraction de données factuelles spécialisé dans les sports de combat.
Date actuelle : {date_str} (Fuseau horaire : Europe/Paris).
Fenêtre temporelle STRICTE autorisée : {DATE_WINDOW_STR}.

MISSION :
{agent_cfg['search_hint']}

CONSIGNES IMPÉRATIVES :
{agent_cfg['specific_rules']}
- RÈGLE ABSOLUE SUR LES SOUS-CARTES : Tu dois extraire la CARTE COMPLÈTE de chaque événement (Main Event, Co-Main Event, et TOUS les combats de l'Undercard / Prelims répertoriés sur Box.live, Tapology, Boxing Mobile ou le site officiel). Ne tronque jamais une carte à 1 ou 2 combats si d'autres combats sont annoncés.
- Ne retiens QUE les événements ayant lieu dans la fenêtre temporelle ({DATE_WINDOW_STR}).
- ZÉRO HALLUCINATION : N'invente jamais de combat, d'adversaire 'TBA' fictif, ni de palmarès. Si tu n'es pas certain d'un palmarès ou d'une taille, mets "N/C".
- Convertis tous les poids en kilogrammes (ex: "-63,5 kg") et les tailles en mètres (ex: "1m78").
- Convertis toutes les heures à l'heure de Paris (CET/CEST).
- Rédige les présentations et analyses en français journalistique précis en conservant les termes techniques (puncher, out-boxer, southpaw, main event, undercard, KO, TKO, undisputed).

FORMAT DE SORTIE OBLIGATOIRE :
Renvoie UNIQUEMENT un objet JSON valide respectant exactement ce schéma (en incluant TOUS les combats de la soirée dans la liste "combats"), sans aucun texte avant ni après :
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
          "combattant_a": "Prénom Nom",
          "palmares_a": "19-0-0, 14 KOs",
          "taille_a": "1m78",
          "combattant_b": "Prénom Nom",
          "palmares_b": "25-1-0, 11 KOs",
          "taille_b": "1m75",
          "categorie_kg": "Super-légers (-63,5 kg)",
          "rounds": "12 rounds",
          "enjeu_et_analyse": "Titre en jeu et courte description stylistique du duel en 1 à 2 phrases."
        }},
        {{
          "statut": "Co-Main Event",
          "combattant_a": "Prénom Nom",
          "palmares_a": "14-1-0, 8 KOs",
          "taille_a": "1m80",
          "combattant_b": "Prénom Nom",
          "palmares_b": "16-2-0, 9 KOs",
          "taille_b": "1m79",
          "categorie_kg": "Mi-moyens (-66,7 kg)",
          "rounds": "10 rounds",
          "enjeu_et_analyse": "Enjeu et description stylistique du combat."
        }},
        {{
          "statut": "Undercard",
          "combattant_a": "Prénom Nom",
          "palmares_a": "10-0-0, 5 KOs",
          "taille_a": "1m75",
          "combattant_b": "Prénom Nom",
          "palmares_b": "11-3-1, 4 KOs",
          "taille_b": "1m74",
          "categorie_kg": "Légers (-61,2 kg)",
          "rounds": "8 rounds",
          "enjeu_et_analyse": "Enjeu et description stylistique du combat."
        }}
      ]
    }}
  ]
}}
Si aucun événement n'est programmé dans cette fenêtre, renvoie exactement : {{"evenements": []}}
"""

    for idx, model_name in enumerate(AVAILABLE_MODELS):
        try:
            print(f"  Appel {model_name} avec Google Search Grounding...")
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.1,
                        max_output_tokens=target_tokens,
                    ),
                )
            except Exception as token_err:
                # Si un modèle secondaire sur la Boxe n'accepte que 8192 tokens max, repli immédiat à 8192
                if target_tokens > 8192 and ("400" in str(token_err) or "token" in str(token_err).lower()):
                    print(f"  [Adaptation] Repli à 8192 tokens pour compatibilité avec {model_name}...")
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            tools=[types.Tool(google_search=types.GoogleSearch())],
                            temperature=0.1,
                            max_output_tokens=8192,
                        ),
                    )
                else:
                    raise token_err

            if response and getattr(response, "text", None):
                events = parse_agent_json(response.text)

                if events is None:
                    print(f"  [Info] JSON incomplet ou mal formé avec {model_name}, essai du modèle suivant...")
                    time.sleep(3)
                    continue

                grounding_urls = extract_grounding_urls(response)
                total_ev = len(events)

                for ev in events:
                    raw_ev_url = (ev.get("url_officielle") or "").strip()
                    ev_url = verify_and_fix_official_url(raw_ev_url, ev, fallback_url, grounding_urls)
                    ev["url_officielle"] = ev_url
                    ev["poster_url"] = resolve_event_poster(
                        ev, ev_url, grounding_urls, discipline_id, total_ev
                    )

                print(f"  -> {len(events)} événement(s) vérifié(s) extrait(s) pour [{discipline_id}].")
                return events

        except Exception as e:
            err_str = str(e)
            print(f"  [Avertissement] {model_name} sur [{discipline_id}] : {err_str[:160]}")
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait_time = 40 if idx == 0 else 25
                print(f"  Pause de sécurité de {wait_time}s (quota API) avant rotation de modèle...")
                time.sleep(wait_time)
            else:
                time.sleep(4)

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
    if poster_url.startswith("http") or poster_url.startswith("data:image/"):
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

# Exécution séquentielle des 8 Micro-Agents avec pause de régénération du quota par minute (45s)
sections_html = []
total_events_found = 0

for idx, cfg in enumerate(AGENTS_CONFIG):
    events_list = run_micro_agent(cfg)
    total_events_found += len(events_list)
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
        print("  Pause de 45s pour régénérer le quota de jetons par minute (RPM/TPM)...")
        time.sleep(45)

if total_events_found == 0 and os.path.exists("index.html"):
    print("\n[ALERTE QUOTA API] 0 événement extrait. Conservation des fichiers existants.")
    sys.exit(0)

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
