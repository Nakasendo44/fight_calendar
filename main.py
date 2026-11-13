import os
import sys
import time
import json
import re
import html
import urllib.parse
import xml.etree.ElementTree as ET
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
yesterday_str = (now_paris + timedelta(days=-1)).strftime("%d/%m/%Y")
heure_str = now_paris.strftime("%H:%M")

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# Fenêtre stricte de J-1 (pour garder les galas US de la nuit précédente) à J+7
valid_dates_fr = []
valid_dates_iso = []
valid_dates_us = []
valid_dates_jp = []
valid_month_day_tuples = set()
valid_months_int = set()
ISO_TO_DT = {}

for offset in range(-1, 8):
    d = now_paris + timedelta(days=offset)
    iso_s = d.strftime("%Y-%m-%d")
    valid_dates_fr.append(d.strftime("%d/%m/%Y"))
    valid_dates_iso.append(iso_s)
    valid_dates_us.append(d.strftime("%m.%d.%Y"))
    valid_dates_jp.append(f"{d.month}月{d.day}日")
    valid_month_day_tuples.add((d.month, d.day))
    valid_months_int.add(d.month)
    ISO_TO_DT[iso_s] = d

VALID_DATES_ISO_SET = set(valid_dates_iso)

START_YMD = (now_paris + timedelta(days=-1)).strftime("%Y%m%d")
END_YMD = (now_paris + timedelta(days=7)).strftime("%Y%m%d")

DATE_WINDOW_STR = (
    f"du {valid_dates_fr[0]} au {valid_dates_fr[-1]} inclus "
    f"(Dates ISO autorisées UNIQUEMENT : {', '.join(valid_dates_iso)})"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6",
}

CACHE_FILE = "cache_calendar.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache_data):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [Info Cache] Impossible d'écrire le cache : {e}")

# ==============================================================================
# 2. DÉCOUVERTE DES MODÈLES (OPTIMISÉE FREE TIER)
# ==============================================================================
def get_available_models():
    tier1_preferred = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3-flash-preview",
        "gemini-flash-latest",
    ]
    tier2_lite_backup = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.8-flash-lite",
        "gemini-flash-lite-latest",
    ]
    excluded_tokens = [
        "2.5", "2.0", "pro", "omni", "embedding", "imagen", "veo",
        "tts", "audio", "vision", "aqa", "image", "banana",
        "transcribe", "argon", "customtools", "thinking", "live"
    ]
    discovered_full = []
    discovered_lite = []
    try:
        for m in client.models.list():
            short = getattr(m, "name", "").replace("models/", "")
            low = short.lower()
            if "gemini" in low and not any(ex in low for ex in excluded_tokens):
                if "lite" in low:
                    discovered_lite.append(short)
                else:
                    discovered_full.append(short)
        print(f"Modèles complets détectés : {discovered_full}")
        print(f"Modèles Lite de secours détectés : {discovered_lite}")
    except Exception as e:
        print(f"Avertissement lors du listage des modèles : {e}")

    final_models = []
    for pref in tier1_preferred:
        if (not discovered_full and not discovered_lite) or pref in discovered_full:
            final_models.append(pref)
    for d in discovered_full:
        if d not in final_models:
            final_models.append(d)
    for pref_lite in tier2_lite_backup:
        if pref_lite in discovered_lite and pref_lite not in final_models:
            final_models.append(pref_lite)
    for d_lite in discovered_lite:
        if d_lite not in final_models:
            final_models.append(d_lite)

    return final_models or ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]

AVAILABLE_MODELS = get_available_models()
DEAD_MODELS_SEARCH = set()
DEAD_MODELS_TOTAL = set()
print(f"Ordre global des modèles retenus : {AVAILABLE_MODELS}")

# ==============================================================================
# 3. MOTEUR DE PRÉ-COLLECTE WEB PROFONDE (EXTRACTION CIBLÉE PAR DATE + SOUS-CARTES)
# ==============================================================================
MONTH_NAME_TO_NUM = {
    "january": 1, "jan": 1, "janvier": 1,
    "february": 2, "feb": 2, "février": 2, "fevrier": 2,
    "march": 3, "mar": 3, "mars": 3,
    "april": 4, "apr": 4, "avril": 4,
    "may": 5, "mai": 5,
    "june": 6, "jun": 6, "juin": 6,
    "july": 7, "jul": 7, "juillet": 7,
    "august": 8, "aug": 8, "août": 8, "aout": 8,
    "september": 9, "sep": 9, "sept": 9, "septembre": 9,
    "october": 10, "oct": 10, "octobre": 10,
    "november": 11, "nov": 11, "novembre": 11,
    "december": 12, "dec": 12, "décembre": 12, "decembre": 12,
}

# Mois interdits dans les titres RSS (tous les mois qui ne font pas partie de la fenêtre J-1..J+7)
FORBIDDEN_MONTH_WORDS_RSS = [
    word for word, m_num in MONTH_NAME_TO_NUM.items()
    if len(word) >= 4 and m_num not in valid_months_int
]

WEIGHT_MAP_BOXING_KG = {
    "heavyweight": "Poids lourds (+90,7 kg)",
    "bridgerweight": "Poids bridger (-101,6 kg)",
    "cruiserweight": "Poids lourds-légers (-90,7 kg)",
    "light heavyweight": "Poids mi-lourds (-79,4 kg)",
    "super middleweight": "Poids super-moyens (-76,2 kg)",
    "middleweight": "Poids moyens (-72,6 kg)",
    "super welterweight": "Poids super-welters (-69,9 kg)",
    "junior middleweight": "Poids super-welters (-69,9 kg)",
    "welterweight": "Poids welters (-66,7 kg)",
    "super lightweight": "Poids super-légers (-63,5 kg)",
    "junior welterweight": "Poids super-légers (-63,5 kg)",
    "lightweight": "Poids légers (-61,2 kg)",
    "super featherweight": "Poids super-plumes (-59,0 kg)",
    "junior lightweight": "Poids super-plumes (-59,0 kg)",
    "featherweight": "Poids plumes (-57,2 kg)",
    "super bantamweight": "Poids super-coqs (-55,3 kg)",
    "junior featherweight": "Poids super-coqs (-55,3 kg)",
    "bantamweight": "Poids coqs (-53,5 kg)",
    "super flyweight": "Poids super-mouches (-52,2 kg)",
    "junior bantamweight": "Poids super-mouches (-52,2 kg)",
    "flyweight": "Poids mouches (-50,8 kg)",
    "light flyweight": "Poids mi-mouches (-49,0 kg)",
    "junior flyweight": "Poids mi-mouches (-49,0 kg)",
    "minimumweight": "Poids pailles (-47,6 kg)",
    "strawweight": "Poids pailles (-47,6 kg)",
}

WEIGHT_MAP_MMA_KG = {
    "heavyweight": "Poids lourds (-120,2 kg)",
    "light heavyweight": "Poids mi-lourds (-93,0 kg)",
    "middleweight": "Poids moyens (-83,9 kg)",
    "welterweight": "Poids mi-moyens (-77,1 kg)",
    "lightweight": "Poids légers (-70,3 kg)",
    "featherweight": "Poids plumes (-65,8 kg)",
    "bantamweight": "Poids coqs (-61,2 kg)",
    "flyweight": "Poids mouches (-56,7 kg)",
    "strawweight": "Poids pailles (-52,2 kg)",
}

def translate_weight_to_fr_kg(raw_cat, sport_slug="boxing"):
    if not raw_cat:
        return "Catégorie officielle"
    low = raw_cat.lower().strip()
    is_women = "women" in low or "féminin" in low
    mapping = WEIGHT_MAP_MMA_KG if sport_slug == "mma" else WEIGHT_MAP_BOXING_KG
    for eng_key, fr_val in mapping.items():
        if eng_key in low:
            return f"{fr_val} (Féminin)" if is_women else fr_val
    return raw_cat

def is_night_hour_paris(time_str):
    """Détecte si un horaire (ex: '02h00', 'vers 04h30') se situe entre 00h00 et 06h59 à Paris."""
    if not time_str:
        return False
    m = re.search(r"(?:^|\D)([012]?\d)\s*[hH:]", str(time_str))
    if m:
        hour = int(m.group(1))
        return 0 <= hour <= 6
    return False

def format_paris_date_label(date_iso, heure_debut_str="", heure_ringwalk_str=""):
    """
    Construit le libellé exact de la date en français à partir de la date de soirée (date_iso).
    Si l'événement a lieu ou se termine la nuit (entre 00h00 et 06h59 heure de Paris),
    renvoie 'Nuit du Samedi 14/11 au Dimanche 15/11/2026'.
    """
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
    except Exception:
        return date_str

    night_event = is_night_hour_paris(heure_debut_str) or is_night_hour_paris(heure_ringwalk_str)

    if night_event:
        dt_prev = dt
        dt_next = dt + timedelta(days=1)
        j_prev = JOURS_FR[dt_prev.weekday()]
        j_next = JOURS_FR[dt_next.weekday()]
        return f"Nuit du {j_prev} {dt_prev.strftime('%d/%m')} au {j_next} {dt_next.strftime('%d/%m/%Y')}"
    else:
        jour_nom = JOURS_FR[dt.weekday()]
        return f"{jour_nom} {dt.strftime('%d/%m/%Y')}"

def format_iso_to_paris(iso_str):
    try:
        dt_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_p = dt_utc.astimezone(paris_tz)
        h_str = dt_p.strftime("%Hh%M")
        if 0 <= dt_p.hour <= 6:
            evening_dt = dt_p - timedelta(days=1)
        else:
            evening_dt = dt_p
        date_iso = evening_dt.strftime("%Y-%m-%d")
        label = format_paris_date_label(date_iso, heure_debut_str=h_str)
        return date_iso, label, h_str
    except Exception:
        return now_paris.strftime("%Y-%m-%d"), date_str, "20h00"

def clean_html_to_text(raw_html, max_chars=24000):
    """
    Supprime les scripts/menus et convertit les balises de blocs HTML en sauts de ligne (\n)
    pour préserver la séparation stricte entre chaque gala et chaque combat de sous-carte.
    """
    if not raw_html:
        return ""
    txt = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<svg.*?</svg>", " ", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<nav.*?</nav>", " ", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<footer.*?</footer>", " ", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"</?(?:div|p|li|tr|section|article|h[1-6]|br|hr|table|ul|ol)[^>]*>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in txt.splitlines()]
    clean_lines = [line for line in lines if line]
    return "\n".join(clean_lines)[:max_chars]

def filter_english_html_by_date_blocks(raw_html):
    """
    Parcourt une page calendrier anglophone (ex: box.live, matchroom, bkfc) bloc de date par bloc de date.
    Conserve uniquement les blocs dont la date appartient à la fenêtre J-1..J+7.
    Si un bloc en haut de page mentionne décembre, seul ce bloc est ignoré (le reste de la page n'est plus coupé).
    """
    if not raw_html:
        return ""

    # Détecte "14 November 2026", "14th Nov", "November 14, 2026", "Nov 14"
    date_header_pattern = re.compile(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?[,\s]*"
        r"(?:"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"|"
        r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{1,2})(?:st|nd|rd|th)?"
        r")"
        r"(?:[,\s]+(\d{4}))?",
        flags=re.IGNORECASE
    )

    matches = list(date_header_pattern.finditer(raw_html))
    if not matches:
        return raw_html

    kept_chunks = []
    for i, m in enumerate(matches):
        day_str = m.group(1) or m.group(4)
        month_str = (m.group(2) or m.group(3) or "").lower()
        day_num = int(day_str)
        month_num = MONTH_NAME_TO_NUM.get(month_str, 0)

        chunk_start = max(0, m.start() - 120)
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_html)

        if (month_num, day_num) in valid_month_day_tuples:
            kept_chunks.append(raw_html[chunk_start:chunk_end])

    # Si des blocs de notre fenêtre ont été trouvés, on renvoie uniquement ces blocs.
    # Sinon (page structurée différemment), on renvoie le HTML complet pour ne jamais perdre de données.
    if kept_chunks:
        return "\n".join(kept_chunks)
    return raw_html

def fetch_webpage_and_sublinks(url, link_pattern=None, max_subpages=10, max_chars=22000, subpage_chars=12000, filter_window_en=False):
    """
    Récupère le texte d'une page calendrier et suit les liens des sous-cartes de la semaine.
    """
    combined_texts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200 and r.text:
            working_html = filter_english_html_by_date_blocks(r.text) if filter_window_en else r.text
            main_txt = clean_html_to_text(working_html, max_chars=max_chars)
            if main_txt:
                combined_texts.append(f"[PAGE CALENDRIER {url}] :\n{main_txt}")

            if link_pattern:
                found_links = []
                for m in re.finditer(link_pattern, working_html, flags=re.IGNORECASE):
                    sub_url = html.unescape(m.group(1))
                    if sub_url.startswith("/"):
                        parsed = urllib.parse.urlparse(url)
                        sub_url = f"{parsed.scheme}://{parsed.netloc}{sub_url}"
                    if sub_url not in found_links:
                        found_links.append(sub_url)

                for sub_u in found_links[:max_subpages]:
                    try:
                        sr = requests.get(sub_u, headers=HEADERS, timeout=8)
                        if sr.status_code == 200 and sr.text:
                            sub_txt = clean_html_to_text(sr.text, max_chars=subpage_chars)
                            combined_texts.append(f"[SOUS-CARTE COMPLÈTE {sub_u}] :\n{sub_txt}")
                    except Exception:
                        pass
    except Exception as e:
        print(f"  [Info Web {url}] {e}")
    return "\n\n".join(combined_texts)

def fetch_boxmob_window_data(max_subpages=12, subpage_chars=12000):
    """
    Parcourt Boxmob.jp jour par jour, ignore les galas du début du mois (avant J-1)
    et ceux après J+7, et télécharge l'intégralité des sous-cartes (sid=...) de la semaine.
    """
    url = "https://boxmob.jp/sp/schedule/"
    combined_texts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200 or not r.text:
            return ""

        raw_html = r.text
        # Détecte les en-têtes de date japonais : "11月15日" ou "11/15(日)"
        jp_date_pattern = re.compile(r"(\d{1,2})\s*[月/]\s*(\d{1,2})\s*(?:日|\([月火水木金土日]\)|（[月火水木金土日]）)")
        matches = list(jp_date_pattern.finditer(raw_html))

        kept_blocks = []
        valid_sublinks = []
        link_regex = re.compile(r'href=["\']((?:https://boxmob\.jp)?/sp/schedule/index\.html\?sid=[0-9]+[^"\']*)["\']', re.IGNORECASE)

        if matches:
            for i, m in enumerate(matches):
                m_num = int(m.group(1))
                d_num = int(m.group(2))
                block_start = max(0, m.start() - 80)
                block_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_html)
                block_html = raw_html[block_start:block_end]

                if (m_num, d_num) in valid_month_day_tuples:
                    kept_blocks.append(f"=== DATE JAPON : {m_num}月{d_num}日 ===\n" + clean_html_to_text(block_html, max_chars=4000))
                    for lm in link_regex.finditer(block_html):
                        sub_path = html.unescape(lm.group(1))
                        sub_url = sub_path if sub_path.startswith("http") else f"https://boxmob.jp{sub_path}"
                        if sub_url not in valid_sublinks:
                            valid_sublinks.append((f"{m_num}月{d_num}日", sub_url))
        else:
            kept_blocks.append(clean_html_to_text(raw_html, max_chars=18000))

        if kept_blocks:
            combined_texts.append(f"[CALENDRIER BOXMOB JAPON (FILTRÉ J-1 à J+7) {url}] :\n" + "\n".join(kept_blocks))

        for date_label_jp, sub_u in valid_sublinks[:max_subpages]:
            try:
                sr = requests.get(sub_u, headers=HEADERS, timeout=8)
                if sr.status_code == 200 and sr.text:
                    sub_txt = clean_html_to_text(sr.text, max_chars=subpage_chars)
                    combined_texts.append(f"[SOUS-CARTE COMPLÈTE BOXMOB ({date_label_jp}) - {sub_u}] :\n{sub_txt}")
            except Exception:
                pass
    except Exception as e:
        print(f"  [Info Boxmob] {e}")
    return "\n\n".join(combined_texts)

def fetch_espn_events_direct(sport_slug, league_slug):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/{league_slug}/scoreboard?dates={START_YMD}-{END_YMD}"
    extracted_events = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        for ev in data.get("events", []):
            ev_name = ev.get("name") or ev.get("shortName") or "Événement Officiel"
            ev_date_iso = ev.get("date") or ""
            date_iso_p, date_p_str, heure_debut_p = format_iso_to_paris(ev_date_iso)

            if date_iso_p not in VALID_DATES_ISO_SET:
                continue

            competitions = ev.get("competitions") or []
            lieu_str = "Lieu officiel"
            if competitions:
                venue = competitions[0].get("venue") or {}
                v_name = venue.get("fullName") or ""
                v_city = (venue.get("address") or {}).get("city") or ""
                v_country = (venue.get("address") or {}).get("country") or ""
                lieu_str = ", ".join(x for x in [v_name, v_city, v_country] if x) or "Arène officielle"

            combats_list = []
            total_comps = len(competitions)
            ordered_comps = list(reversed(competitions)) if total_comps > 1 else competitions
            for idx_c, comp in enumerate(ordered_comps):
                competitors = comp.get("competitors") or []
                if len(competitors) < 2:
                    continue
                c_a = competitors[0]
                c_b = competitors[1]
                ath_a = c_a.get("athlete") or {}
                ath_b = c_b.get("athlete") or {}
                name_a = (ath_a.get("displayName") or "").strip()
                name_b = (ath_b.get("displayName") or "").strip()
                if not name_a or not name_b or name_a == "TBA" or name_b == "TBA":
                    continue

                rec_a_list = c_a.get("records") or []
                rec_b_list = c_b.get("records") or []
                rec_a = rec_a_list[0].get("summary", "N/C") if rec_a_list else "N/C"
                rec_b = rec_b_list[0].get("summary", "N/C") if rec_b_list else "N/C"

                raw_cat = (comp.get("type") or {}).get("text") or comp.get("note") or "Catégorie officielle"
                cat_type = translate_weight_to_fr_kg(raw_cat, sport_slug=sport_slug)
                periods = ((comp.get("format") or {}).get("regulation") or {}).get("periods")
                rounds_str = f"{periods} rounds" if periods else ("3 rounds" if sport_slug == "mma" else "10 rounds")

                if idx_c == 0:
                    statut_str = "Main Event"
                elif idx_c == 1:
                    statut_str = "Co-Main Event"
                elif idx_c < 6:
                    statut_str = "Main Card"
                else:
                    statut_str = "Prelims"

                combats_list.append({
                    "statut": statut_str,
                    "combattant_a": name_a,
                    "palmares_a": rec_a,
                    "taille_a": "N/C",
                    "combattant_b": name_b,
                    "palmares_b": rec_b,
                    "taille_b": "N/C",
                    "categorie_kg": cat_type,
                    "rounds": rounds_str,
                    "enjeu_et_analyse": f"Duel officiel programmé sur la carte {ev_name}."
                })

            if combats_list:
                main_c = combats_list[0]
                enjeu_p = f"{main_c['combattant_a']} vs. {main_c['combattant_b']} ({main_c['categorie_kg']})"
            else:
                enjeu_p = ev_name

            extracted_events.append({
                "nom_gala": ev_name,
                "date_iso": date_iso_p,
                "date_paris": date_p_str,
                "lieu": lieu_str,
                "url_officielle": "https://www.ufc.com/events" if league_slug == "ufc" else "https://box.live/upcoming-fights-schedule/",
                "poster_url": "",
                "heure_debut_paris": heure_debut_p,
                "heure_ringwalk_paris": "env. +3h après le début",
                "diffuseur_origine": "ESPN+ / UFC Fight Pass" if league_slug == "ufc" else "DAZN / ESPN",
                "diffuseur_france": "RMC Sport / UFC Fight Pass" if league_slug == "ufc" else "DAZN France",
                "enjeu_principal": enjeu_p,
                "presentation": f"Événement officiel {ev_name} programmé à {lieu_str} avec {len(combats_list)} combats inscrits.",
                "combats": combats_list,
            })
    except Exception as e:
        print(f"  [Info ESPN Direct] {e}")
    return extracted_events

def fetch_rss_snippets(query, lang="en-US", country="US", max_items=22):
    """
    Récupère les articles Google News RSS de la semaine (cartes complètes, horaires, promoteurs)
    en filtrant automatiquement tout article mentionnant un mois hors de la fenêtre J-1..J+7.
    """
    encoded_q = urllib.parse.quote(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_q}&hl={lang}&gl={country}&ceid={country}:{lang.split('-')[0]}"
    items_txt = []
    try:
