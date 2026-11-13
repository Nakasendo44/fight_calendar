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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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

# Seuls les 3 mois FUTURS suivant la fenêtre (ex: décembre, janvier, février) sont filtrés dans les titres RSS
FUTURE_FORBIDDEN_MONTHS_RSS = set()
for future_offset in range(15, 90, 15):
    fd = now_paris + timedelta(days=future_offset)
    if fd.month not in valid_months_int:
        for m_word, m_num in MONTH_NAME_TO_NUM.items():
            if m_num == fd.month and len(m_word) >= 4:
                FUTURE_FORBIDDEN_MONTHS_RSS.add(m_word)

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

def decode_response_text(resp):
    """Garantit un décodage UTF-8 propre même sur les sites japonais (Boxmob) qui omettent le charset HTTP."""
    if not resp or not resp.content:
        return ""
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        enc = resp.apparent_encoding or "utf-8"
        return resp.content.decode(enc, errors="replace")

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

def extract_boxlive_subpage_text(raw_html):
    """
    Sur les sous-pages box.live/fights/..., l'Undercard se trouve en bas de page après un très long
    tableau de statistiques 'Tale of the Tape'. On extrait le haut (infos clés) + le bas (Undercard).
    """
    full_txt = clean_html_to_text(raw_html, max_chars=45000)
    if len(full_txt) <= 10000:
        return full_txt

    top_part = full_txt[:4500]
    low_txt = full_txt.lower()
    undercard_markers = ["undercard", "also on the", "fight card", "running order", "on the bill", "co-main"]
    best_pos = -1
    for marker in undercard_markers:
        pos = low_txt.find(marker, 2000)
        if pos != -1:
            if best_pos == -1 or pos < best_pos:
                best_pos = pos

    if best_pos != -1:
        start_idx = max(4500, best_pos - 400)
        undercard_part = full_txt[start_idx : start_idx + 10000]
        return top_part + "\n--- SECTION SOUS-CARTE (UNDERCARD) ---\n" + undercard_part

    return full_txt[:9000] + "\n" + full_txt[-5000:]

def fetch_rss_snippets(query, lang="en-US", country="US", max_items=22):
    """
    Récupère les articles Google News RSS de la semaine (cartes complètes, horaires, promoteurs)
    en filtrant uniquement les titres annonçant explicitement un gala d'un mois futur hors-fenêtre.
    """
    encoded_q = urllib.parse.quote(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_q}&hl={lang}&gl={country}&ceid={country}:{lang.split('-')[0]}"
    items_txt = []
    try:
        r = requests.get(rss_url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                title = item.findtext("title") or ""
                pub_date = item.findtext("pubDate") or ""
                desc = clean_html_to_text(item.findtext("description") or "", 480)
                title_low = title.lower()

                # On filtre uniquement si le TITRE annonce un mois futur hors fenêtre (ex: "December", "January")
                if any(re.search(rf"\b{re.escape(m_word)}\b", title_low) for m_word in FUTURE_FORBIDDEN_MONTHS_RSS):
                    continue

                items_txt.append(f"- [{pub_date}] {title} :: {desc}")
                if len(items_txt) >= max_items:
                    break
    except Exception:
        pass
    return "\n".join(items_txt)

def fetch_boxlive_window_and_undercards(max_subpages=10):
    """
    Extrait les galas de la fenêtre J-1..J+7 sur box.live sans couper le début des cartes HTML,
    ouvre chaque sous-page pour extraire le bloc Undercard, et lance une recherche RSS dédiée
    sur chaque affiche trouvée pour garantir 100 % des combats de la sous-carte.
    """
    url = "https://box.live/upcoming-fights-schedule/"
    combined_texts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        raw_html = decode_response_text(r) if r.status_code == 200 else ""
        if not raw_html:
            return ""

        stop_pos = len(raw_html)
        date_pattern = re.compile(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[,\s]+"
            r"(?:(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"|(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?)",
            flags=re.IGNORECASE
        )
        for m in date_pattern.finditer(raw_html):
            d_num = int(m.group(1) or m.group(4))
            m_name = (m.group(2) or m.group(3) or "").lower()
            m_num = MONTH_NAME_TO_NUM.get(m_name, 0)
            if (m_num, d_num) not in valid_month_day_tuples and m.start() > 1500:
                stop_pos = m.start()
                break

        window_html = raw_html[:stop_pos]
        main_txt = clean_html_to_text(window_html, max_chars=18000)
        combined_texts.append(f"[CALENDRIER BOX.LIVE (FILTRÉ J-1 à J+7) {url}] :\n{main_txt}")

        found_links = []
        slug_fighters = []
        link_regex = re.compile(r'href=["\']((?:https://box\.live)?/fights/([a-z0-9\-]+)/?)["\']', re.IGNORECASE)
        for lm in link_regex.finditer(window_html):
            sub_url = urllib.parse.urljoin(url, html.unescape(lm.group(1)))
            slug = lm.group(2)
            if sub_url not in found_links:
                found_links.append(sub_url)
                if "-vs-" in slug:
                    parts = slug.split("-vs-")
                    fa = parts[0].replace("-", " ").strip()
                    fb = parts[1].replace("-", " ").strip()
                    if fa and fb:
                        slug_fighters.append((fa, fb))

        for sub_u in found_links[:max_subpages]:
            try:
                sr = requests.get(sub_u, headers=HEADERS, timeout=8)
                if sr.status_code == 200:
                    sub_txt = extract_boxlive_subpage_text(decode_response_text(sr))
                    combined_texts.append(f"[FICHE & SOUS-CARTE BOX.LIVE {sub_u}] :\n{sub_txt}")
            except Exception:
                pass

        undercard_rss_chunks = []
        for fa, fb in slug_fighters[:8]:
            q = f'"{fa}" "{fb}" ("undercard" OR "fight card" OR "full card" OR "co-main") when:14d'
            snippet = fetch_rss_snippets(q, max_items=6)
            if snippet:
                undercard_rss_chunks.append(f"--- DÉTAILS SOUS-CARTE POUR {fa.upper()} VS {fb.upper()} ---\n{snippet}")
        if undercard_rss_chunks:
            combined_texts.append("[SOUS-CARTES DÉTAILLÉES EXTRAITES DE LA PRESSE SPÉCIALISÉE] :\n" + "\n".join(undercard_rss_chunks))

    except Exception as e:
        print(f"  [Info Box.live] {e}")
    return "\n\n".join(combined_texts)

def fetch_webpage_and_sublinks(url, link_pattern=None, max_subpages=8, max_chars=18000, subpage_chars=14000):
    """
    Récupère le texte complet d'une page officielle (décodée proprement en UTF-8)
    et suit les liens des cartes d'événements.
    """
    combined_texts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            raw_html = decode_response_text(r)
            main_txt = clean_html_to_text(raw_html, max_chars=max_chars)
            if main_txt:
                combined_texts.append(f"[PAGE OFFICIELLE {url}] :\n{main_txt}")

            if link_pattern:
                found_links = []
                for m in re.finditer(link_pattern, raw_html, flags=re.IGNORECASE):
                    sub_url = urllib.parse.urljoin(url, html.unescape(m.group(1)))
                    if sub_url not in found_links:
                        found_links.append(sub_url)

                for sub_u in found_links[:max_subpages]:
                    try:
                        sr = requests.get(sub_u, headers=HEADERS, timeout=8)
                        if sr.status_code == 200:
                            sub_txt = clean_html_to_text(decode_response_text(sr), max_chars=subpage_chars)
                            combined_texts.append(f"[CARTE OFFICIELLE DÉTAILLÉE {sub_u}] :\n{sub_txt}")
                    except Exception:
                        pass
        else:
            print(f"  [Info HTTP {r.status_code}] {url}")
    except Exception as e:
        print(f"  [Info Web {url}] {e}")
    return "\n\n".join(combined_texts)

def fetch_boxmob_window_data(max_subpages=12, subpage_chars=15000):
    """
    Parcourt Boxmob.jp jour par jour (en forçant le décodage UTF-8), conserve uniquement
    les dates de la fenêtre J-1..J+7, et télécharge l'intégralité des sous-cartes (index.html?sid=...).
    """
    url = "https://boxmob.jp/sp/schedule/"
    combined_texts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return ""

        raw_html = decode_response_text(r)
        if not raw_html:
            return ""

        jp_date_pattern = re.compile(r"(\d{1,2})\s*[月/]\s*(\d{1,2})\s*(?:日|\([月火水木金土日]\)|（[月火水木金土日]）)")
        matches = list(jp_date_pattern.finditer(raw_html))

        kept_blocks = []
        valid_sublinks = []
        seen_urls = set()
        link_regex = re.compile(r'href=["\']([^"\']*index\.html\?sid=[0-9]+[^"\']*)["\']', re.IGNORECASE)

        if matches:
            for i, m in enumerate(matches):
                m_num = int(m.group(1))
                d_num = int(m.group(2))
                block_start = max(0, m.start() - 150)
                block_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_html)
                block_html = raw_html[block_start:block_end]

                if (m_num, d_num) in valid_month_day_tuples:
                    iso_for_jp = f"{now_paris.year}-{m_num:02d}-{d_num:02d}"
                    kept_blocks.append(
                        f"=== DATE JAPON : {m_num}月{d_num}日 (date_iso: {iso_for_jp}) ===\n"
                        + clean_html_to_text(block_html, max_chars=4500)
                    )
                    for lm in link_regex.finditer(block_html):
                        sub_path = html.unescape(lm.group(1))
                        sub_url = urllib.parse.urljoin(url, sub_path)
                        if sub_url not in seen_urls:
                            seen_urls.add(sub_url)
                            valid_sublinks.append((f"{m_num}月{d_num}日 (date_iso: {iso_for_jp})", sub_url))
        else:
            kept_blocks.append(clean_html_to_text(raw_html, max_chars=18000))
            for lm in link_regex.finditer(raw_html):
                sub_url = urllib.parse.urljoin(url, html.unescape(lm.group(1)))
                if sub_url not in seen_urls:
                    seen_urls.add(sub_url)
                    valid_sublinks.append(("Semaine en cours", sub_url))

        if kept_blocks:
            combined_texts.append(f"[CALENDRIER BOXMOB JAPON (FILTRÉ J-1 à J+7) {url}] :\n" + "\n".join(kept_blocks))

        for date_label_jp, sub_u in valid_sublinks[:max_subpages]:
            try:
                sr = requests.get(sub_u, headers=HEADERS, timeout=8)
                if sr.status_code == 200:
                    sub_txt = clean_html_to_text(decode_response_text(sr), max_chars=subpage_chars)
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
                "promoteur": "UFC" if league_slug == "ufc" else "World Boxing",
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

def build_prefetched_context(discipline_id):
    chunks = []
    if discipline_id == "ufc":
        espn_ufc = fetch_espn_events_direct("mma", "ufc")
        if espn_ufc:
            chunks.append(
                "DONNÉES OFFICIELLES ESPN API (UFC - CARTE COMPLÈTE VÉRIFIÉE, CONSERVE TOUS LES COMBATS) :\n"
                + json.dumps(espn_ufc, ensure_ascii=False, indent=2)
            )
        rss_ufc = fetch_rss_snippets("UFC full fight card prelims main card schedule when:7d")
        if rss_ufc:
            chunks.append("ACTUALITÉS & CARTES UFC DE LA SEMAINE :\n" + rss_ufc)

    elif discipline_id == "boxe-monde":
        espn_box = fetch_espn_events_direct("boxing", "boxing")
        if espn_box:
            chunks.append("DONNÉES ESPN BOXING API :\n" + json.dumps(espn_box, ensure_ascii=False, indent=2))

        zuffa_txt = fetch_webpage_and_sublinks(
            "https://www.ufc.com/zuffaboxing",
            link_pattern=r'href=["\']((?:https://www\.ufc\.com)?/news/zuffa-boxing-[^"\']*official-fight-card[^"\']*)["\']',
            max_subpages=3,
            max_chars=12000,
            subpage_chars=15000,
        )
        if zuffa_txt:
            chunks.append("DONNÉES OFFICIELLES ZUFFA BOXING (UFC.COM) :\n" + zuffa_txt)

        matchroom_txt = fetch_webpage_and_sublinks(
            "https://www.matchroomboxing.com/events/",
            link_pattern=r'href=["\'](https://www\.matchroomboxing\.com/events/[^"\']+)["\']',
            max_subpages=4,
            max_chars=12000,
            subpage_chars=14000,
        )
        if matchroom_txt:
            chunks.append("DONNÉES OFFICIELLES MATCHROOM BOXING :\n" + matchroom_txt)

        box_deep = fetch_boxlive_window_and_undercards(max_subpages=10)
        if box_deep:
            chunks.append(box_deep)

        uk_tv = fetch_webpage_and_sublinks("https://box.live/uk-boxing-tv-schedule/", max_subpages=0, max_chars=10000)
        if uk_tv:
            chunks.append(uk_tv)
        us_tv = fetch_webpage_and_sublinks("https://box.live/us-boxing-tv-schedule/", max_subpages=0, max_chars=10000)
        if us_tv:
            chunks.append(us_tv)

        # Requêtes RSS ciblées (fonctionnent même quand le modèle tourne en mode Direct sur Flash Lite)
        rss_mf_pro = fetch_rss_snippets(
            '("Viddal Riley" OR "Seydi Coupe" OR "MF Pro" OR "Misfits Boxing" OR "Zelfa Barrett") ("fight card" OR "undercard" OR "Wembley" OR "DAZN") when:14d',
            max_items=18
        )
        rss_zuffa_boxxer = fetch_rss_snippets(
            '("Zuffa Boxing" OR "Boxxer" OR "Queensberry") ("fight card" OR "undercard" OR "schedule") when:10d',
            max_items=18
        )
        rss_majors = fetch_rss_snippets(
            '("Matchroom" OR "Pacheco" OR "Teremoana" OR "Top Rank" OR "PBC" OR "Golden Boy") ("full fight card" OR "undercard" OR "running order") boxing when:10d',
            max_items=20
        )
        rss_combined = "\n".join(x for x in [rss_mf_pro, rss_zuffa_boxxer, rss_majors] if x)
        if rss_combined:
            chunks.append("CARTES COMPLÈTES & SOUS-CARTES PRESSE (MF PRO, ZUFFA, BOXXER, MATCHROOM, DAZN) :\n" + rss_combined)

    elif discipline_id == "boxe-japon":
        jp_deep = fetch_boxmob_window_data(max_subpages=12, subpage_chars=15000)
        if jp_deep:
            chunks.append(jp_deep)
        rss_jp = fetch_rss_snippets(
            "後楽園ホール OR フェニックスバトル OR ダイナミックグローブ OR 大橋ジム OR 帝拳 OR 興行 プロボクシング when:7d",
            lang="ja", country="JP", max_items=22
        )
        if rss_jp:
            chunks.append("ACTUALITÉS BOXE JAPON DE LA SEMAINE :\n" + rss_jp)

    elif discipline_id == "rizin":
        rz_txt = fetch_webpage_and_sublinks(
            "https://jp.rizinff.com/schedules/",
            link_pattern=r'href=["\'](https://jp\.rizinff\.com/schedules/[^"\']+)["\']',
            max_subpages=3,
            max_chars=15000,
            subpage_chars=14000,
        )
        if rz_txt:
            chunks.append(rz_txt)
        rss_rz = fetch_rss_snippets("RIZIN 対戦カード OR RIZIN LANDMARK when:7d", lang="ja", country="JP")
        if rss_rz:
            chunks.append("ACTUALITÉS RIZIN :\n" + rss_rz)

    elif discipline_id == "bkfc":
        bk_txt = fetch_webpage_and_sublinks(
            "https://www.bkfc.com/events",
            link_pattern=r'href=["\'](/events/[^"\']+)["\']',
            max_subpages=3,
            max_chars=16000,
            subpage_chars=14000,
        )
        if bk_txt:
            chunks.append(bk_txt)
        rss_bk = fetch_rss_snippets("BKFC full fight card schedule undercard when:7d")
        if rss_bk:
            chunks.append("ACTUALITÉS BKFC :\n" + rss_bk)

    elif discipline_id == "glory":
        gl_txt = fetch_webpage_and_sublinks(
            "https://glorykickboxing.com/events",
            link_pattern=r'href=["\'](/events/[^"\']+)["\']',
            max_subpages=3,
            max_chars=16000,
            subpage_chars=14000,
        )
        if gl_txt:
            chunks.append(gl_txt)
        rss_gl = fetch_rss_snippets("Glory Kickboxing full fight card schedule when:7d")
        if rss_gl:
            chunks.append("ACTUALITÉS GLORY :\n" + rss_gl)

    return "\n\n".join(chunks)

# ==============================================================================
# 4. CONFIGURATION DES 6 MICRO-AGENTS PILIERS
# ==============================================================================
AGENTS_CONFIG = [
    {
        "id": "boxe-monde",
        "nav_label": "🥊 Boxe Monde",
        "title": "🥊 1. Boxe Anglaise — Grands Galas Internationaux",
        "max_tokens": 16384,
        "fallback_url": "https://box.live/upcoming-fights-schedule/",
        "empty_msg": "Aucun grand gala international programmé dans la fenêtre des 7 prochains jours.",
        "search_hint": (
            "Recherche TOUS les galas majeurs de boxe anglaise prévus entre le "
            + valid_dates_fr[0] + " et le " + valid_dates_fr[-1] + " inclus, "
            "notamment : Matchroom Boxing, Zuffa Boxing (Riyadh Season / Paramount+ / Sky Sports), "
            "MF Pro / Misfits Boxing (DAZN), Queensberry, Boxxer, Top Rank, PBC, Golden Boy, ProBox TV, MVP. "
            "Pour chaque gala, extrais impérativement toute la sous-carte (Main Card + Prelims / Undercard)."
        ),
        "specific_rules": (
            "- PÉRIMÈTRE DES GALAS MAJEURS À INCLURE ABSOLUMENT : Inclus tous les galas programmés cette semaine par "
            "Matchroom Boxing (ex: Pacheco vs Fulghum, Teremoana vs Ahio), "
            "Zuffa Boxing (ex: Zuffa Boxing 13 Adames vs Warburton à Riyad), "
            "MF Pro / Misfits Boxing (ex: Viddal Riley vs Seydi Coupe à l'OVO Arena Wembley sur DAZN le samedi 14/11/2026 avec Zelfa Barrett vs Gavin Gwynne, Kieron Molloy vs Steve Suppan, Dan Toward vs Jak Corrie, Yaser Al Ghena vs Ben Marksby, Tom Welland vs Jack Dillingham), "
            "Queensberry, Boxxer, Top Rank, PBC, Golden Boy, Riyadh Season / The Ring, MVP et ProBox TV.\n"
            "- CHAMP 'promoteur' OBLIGATOIRE : Renseigne précisément le nom de l'organisateur dans 'promoteur' parmi : "
            "'Matchroom Boxing', 'Queensberry', 'Boxxer', 'Zuffa Boxing', 'MF Pro', 'Top Rank', 'PBC', 'Golden Boy', 'The Ring / Riyadh Season', 'DAZN Boxing'.\n"
            "- SOUS-CARTES COMPLÈTES OBLIGATOIRES : Il est INTERDIT de ne mettre qu'un seul combat (le Main Event) quand une sous-carte existe. "
            "Tu dois lister TOUS les combats connus de la carte (Main Event, Co-Main Event, Main Card et Prelims).\n"
            "- VÉRIFICATION STRICTE DES DATES ET LIEUX : Ne mélange jamais deux galas différents et n'inclus aucun gala hors de la fenêtre des 7 jours."
        ),
    },
    {
        "id": "boxe-japon",
        "nav_label": "🇯🇵 Boxe Japon",
        "title": "🇯🇵 2. Boxe Anglaise — Galas au Japon (Arènes & Korakuen Hall)",
        "max_tokens": 16384,
        "fallback_url": "https://boxmob.jp/sp/schedule/",
        "empty_msg": "Aucun gala professionnel japonais répertorié sur cette période de 7 jours.",
        "search_hint": (
            f"Recherche le calendrier complet de boxe professionnelle ayant lieu PHYSIQUEMENT AU JAPON ({', '.join(valid_dates_jp)}) sur : "
            "boxmob.jp (Boxing Mobile), jbc.or.jp, tapology.com/fightcenter. "
            "Cherche les galas dans les arènes japonaises (Ohashi Promotions / Phoenix Battle / Lemino, Teiken Promotions / Prime Video Boxing) "
            "et toutes les réunions du Korakuen Hall à Tokyo (Dynamic Glove, Who's Next, KNOCK OUT BOXING, Dangan, etc.)."
        ),
        "specific_rules": (
            "- EXCLUSIVITÉ GÉOGRAPHIQUE JAPON : N'inclus QUE les galas qui se déroulent physiquement au Japon (Tokyo, Korakuen Hall, Osaka, Aichi, Nagoya, Yokohama, etc.). "
            "N'inclus JAMAIS un gala ayant lieu aux Philippines, aux États-Unis ou en Europe.\n"
            "- EXPLOITATION OBLIGATOIRE DE BOXMOB : Extrais chaque réunion présente dans les blocs [CALENDRIER BOXMOB JAPON] et [SOUS-CARTE COMPLÈTE BOXMOB] avec la 'date_iso' indiquée dans l'en-tête du bloc.\n"
            "- CHAMP 'promoteur' JAPON OBLIGATOIRE : Indique le promoteur ou la série officielle japonaise dans 'promoteur' : "
            "'Ohashi Promotions' (Phoenix Battle / Lemino / Inoue / Ohashi Gym), "
            "'Teiken Promotions' (Prime Video Boxing / Dynamic Glove / Who's Next / Nasukawa / Nakatani / Teiken Gym), "
            "'U-NEXT Boxing' (KNOCK OUT BOXING / U-NEXT), ou 'JBC / Korakuen Hall'.\n"
            "- SOUS-CARTES COMPLÈTES BOXMOB : Pour chaque réunion japonaise retenue, extrais TOUS les combats dont les deux adversaires sont nommés.\n"
            "- Transcris tous les noms des boxeurs japonais en alphabet latin (Romaji Hepburn)."
        ),
    },
    {
        "id": "ufc",
        "nav_label": "🔥 UFC",
        "title": "🔥 3. UFC",
        "max_tokens": 8192,
        "fallback_url": "https://www.ufc.com/events",
        "empty_msg": "Aucun événement UFC programmé cette semaine.",
        "search_hint": (
            "Recherche les événements UFC prévus sur la période (UFC numéroté et UFC Fight Night) et leur carte complète sur : "
            "ufc.com/events, espn.com/mma/schedule, tapology.com/fightcenter, sherdog.com/events."
        ),
        "specific_rules": (
            "- Liste l'intégralité des 10 à 14 combats annoncés pour chaque événement UFC de la période en séparant clairement le statut (Main Card, Prelims, Early Prelims).\n"
            "- Indique 'UFC' dans 'promoteur' et 'RMC Sport / UFC Fight Pass' comme diffuseur France."
        ),
    },
    {
        "id": "rizin",
        "nav_label": "🌸 Rizin",
        "title": "🌸 4. Rizin Fighting Federation",
        "max_tokens": 8192,
        "fallback_url": "https://jp.rizinff.com/schedules/",
        "empty_msg": "Aucun événement Rizin programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel RIZIN (RIZIN 大会日程 対戦カード / RIZIN LANDMARK) sur : "
            "jp.rizinff.com/schedules, tapology.com/fightcenter, sherdog.com."
        ),
        "specific_rules": (
            "- Vérifie si un gala Rizin tombe dans la fenêtre autorisée.\n"
            "- Si un gala Rizin a lieu sur cette période, liste TOUS les combats connus de la carte. Sinon, renvoie une liste vide []."
        ),
    },
    {
        "id": "bkfc",
        "nav_label": "👊 BKFC",
        "title": "👊 5. BKFC (Bare Knuckle)",
        "max_tokens": 8192,
        "fallback_url": "https://www.bkfc.com/events",
        "empty_msg": "Aucun événement BKFC programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel Bare Knuckle Fighting Championship (BKFC numérotés et BKFC Fight Night) sur : "
            "bkfc.com/events, tapology.com/fightcenter."
        ),
        "specific_rules": (
            "- Liste TOUS les combats confirmés de la carte (Main Event, Co-Main Event et toute l'Undercard).\n"
            "- Indique 'BKFC App / TrillerTV / DAZN' selon le diffuseur officiel de l'événement."
        ),
    },
    {
        "id": "glory",
        "nav_label": "🏆 Glory",
        "title": "🏆 6. Glory Kickboxing",
        "max_tokens": 8192,
        "fallback_url": "https://glorykickboxing.com/events",
        "empty_msg": "Aucun événement Glory Kickboxing programmé cette semaine.",
        "search_hint": (
            "Recherche le calendrier officiel Glory Kickboxing sur : "
            "glorykickboxing.com/events, tapology.com/fightcenter."
        ),
        "specific_rules": (
            "- Liste tous les combats annoncés uniquement si l'événement tombe dans la fenêtre des 7 prochains jours."
        ),
    },
]

# ==============================================================================
# 5. SYSTÈME HYBRIDE 100 % LOGOS OFFICIELS (AUTO + SURCHARGE MANUELLE DANS logos/)
# ==============================================================================
LOGOS_DIR = "logos"
os.makedirs(LOGOS_DIR, exist_ok=True)

def build_official_logo_svg(bg_start, bg_end, accent_color, main_title, subtitle, letter_spacing="8", badge_top=""):
    """Génère un fichier logo vectoriel officiel haute définition propre dans logos/<key>.svg."""
    top_text_el = (
        f'<text x="600" y="115" text-anchor="middle" fill="{accent_color}" '
        f'font-family="Plus Jakarta Sans, Arial, sans-serif" font-size="20" font-weight="700" '
        f'letter-spacing="9" opacity="0.9">{badge_top}</text>'
        if badge_top else ""
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 600" width="1200" height="600">\n'
        f'  <defs>\n'
        f'    <radialGradient id="g" cx="50%" cy="42%" r="75%">\n'
        f'      <stop offset="0%" stop-color="{bg_start}"/>\n'
        f'      <stop offset="100%" stop-color="{bg_end}"/>\n'
        f'    </radialGradient>\n'
        f'  </defs>\n'
        f'  <rect width="1200" height="600" fill="url(#g)"/>\n'
        f'  {top_text_el}\n'
        f'  <rect x="330" y="140" width="540" height="3" fill="{accent_color}" opacity="0.85"/>\n'
        f'  <text x="600" y="265" text-anchor="middle" fill="#FFFFFF" font-family="Outfit, Arial Black, sans-serif" '
        f'font-size="82" font-weight="800" letter-spacing="{letter_spacing}">{main_title}</text>\n'
        f'  <text x="600" y="335" text-anchor="middle" fill="{accent_color}" font-family="Plus Jakarta Sans, Arial, sans-serif" '
        f'font-size="25" font-weight="700" letter-spacing="10">{subtitle}</text>\n'
        f'  <rect x="330" y="375" width="540" height="3" fill="{accent_color}" opacity="0.85"/>\n'
        f'</svg>\n'
    )
    return svg

OFFICIAL_LOGOS_CATALOG = {
    # 4 Ligues exclusives
    "ufc": ("#260B0B", "#090303", "#D20A0A", "U F C", "ULTIMATE FIGHTING CHAMPIONSHIP", "14", "OFFICIAL OCTAGON EVENT"),
    "rizin": ("#1A1326", "#08050E", "#E5C158", "R I Z I N", "FIGHTING FEDERATION", "12", "JAPAN MMA SERIES"),
    "bkfc": ("#1E190A", "#090703", "#F5C518", "B K F C", "BARE KNUCKLE FIGHTING CHAMPIONSHIP", "12", "WORLD SERIES"),
    "glory": ("#1D160B", "#080603", "#E6B84D", "G L O R Y", "GLORY KICKBOXING WORLD SERIES", "12", "STAND-UP STRIKING"),
    # Promoteurs Boxe Internationale
    "matchroom": ("#141414", "#050505", "#E5E5E5", "MATCHROOM", "B O X I N G", "8", "EDDIE HEARN PROMOTIONS"),
    "queensberry": ("#1A140B", "#080604", "#D4AF37", "QUEENSBERRY", "P R O M O T I O N S", "6", "FRANK WARREN"),
    "boxxer": ("#0F1D1D", "#050A0A", "#00E5FF", "B O X X E R", "SKY SPORTS & BBC BOXING", "10", "UK CHAMPIONSHIP BOXING"),
    "zuffa": ("#220D0D", "#0A0404", "#D20A0A", "ZUFFA BOXING", "RIYADH SEASON • PARAMOUNT+", "6", "TKO BOXING CHAMPIONSHIP"),
    "misfits": ("#1D1908", "#080702", "#F5C518", "M F   P R O", "MISFITS BOXING • DAZN", "8", "CROSSOVER & PRO SERIES"),
    "toprank": ("#1B120C", "#090604", "#C89D54", "TOP RANK", "B O X I N G", "8", "BOB ARUM PROMOTIONS"),
    "pbc": ("#121824", "#06080E", "#D4AF37", "P B C", "PREMIER BOXING CHAMPIONS", "10", "CHAMPIONSHIP BOXING"),
    "goldenboy": ("#1F1709", "#0A0703", "#E6B84D", "GOLDEN BOY", "P R O M O T I O N S", "7", "OSCAR DE LA HOYA"),
    "thering": ("#1C130E", "#0A0604", "#E2B049", "THE RING", "RIYADH SEASON OFFICIAL", "8", "CHAMPIONSHIP FIGHT NIGHT"),
    "dazn": ("#161616", "#050505", "#F7D388", "DAZN BOXING", "GLOBAL FIGHT NIGHT", "8", "LIVE WORLDWIDE"),
    # Promoteurs & Séries Boxe Japon
    "ohashi": ("#101B2B", "#060A11", "#E2B049", "OHASHI PROMOTIONS", "PHOENIX BATTLE • LEMINO BOXING", "5", "大橋プロモーション • TOKYO / YOKOHAMA"),
    "teiken": ("#241111", "#0B0404", "#E6B84D", "TEIKEN PROMOTIONS", "PRIME VIDEO BOXING • DYNAMIC GLOVE", "5", "帝拳プロモーション • TOKYO"),
    "unext": ("#141A24", "#07090E", "#D4AF37", "U-NEXT BOXING", "DYNAMIC GLOVE & KNOCK OUT BOXING", "6", "KORAKUEN HALL TOKYO"),
    "boxe-japon": ("#260E12", "#0D0406", "#E8C574", "JBC BOXING JAPAN", "KORAKUEN HALL & ARENAS", "7", "日本ボクシングコミッション"),
    "boxe-monde": ("#24150E", "#0C0604", "#D4AF37", "WORLD BOXING", "CHAMPIONSHIP FIGHT NIGHT", "8", "INTERNATIONAL PRO BOXING"),
}

def initialize_logos_directory():
    for key, params in OFFICIAL_LOGOS_CATALOG.items():
        svg_path = os.path.join(LOGOS_DIR, f"{key}.svg")
        if not os.path.exists(svg_path):
            try:
                svg_content = build_official_logo_svg(*params)
                with open(svg_path, "w", encoding="utf-8") as f:
                    f.write(svg_content)
            except Exception as e:
                print(f"  [Info Logo] Impossible de créer {svg_path} : {e}")

initialize_logos_directory()

def get_logo_path_for_key(logo_key):
    for ext in [".png", ".webp", ".jpg", ".jpeg", ".svg"]:
        candidate = f"{LOGOS_DIR}/{logo_key}{ext}"
        if os.path.exists(candidate):
            return candidate
    return f"{LOGOS_DIR}/boxe-monde.svg"

def resolve_event_poster(ev, ev_url, grounding_urls, discipline_id, total_events_in_section):
    if discipline_id in {"ufc", "rizin", "bkfc", "glory"}:
        return get_logo_path_for_key(discipline_id)

    promoteur_field = (ev.get("promoteur") or "").lower()
    nom_gala = (ev.get("nom_gala") or "").lower()
    presentation = (ev.get("presentation") or "").lower()
    diff_orig = (ev.get("diffuseur_origine") or "").lower()
    url_low = (ev_url or "").lower()

    fighters_blob = " ".join(
        f"{b.get('combattant_a', '')} {b.get('combattant_b', '')}".lower()
        for b in (ev.get("combats") or [])
    )
    full_context = f"{promoteur_field} {nom_gala} {url_low} {presentation} {fighters_blob} {diff_orig}"

    if discipline_id == "boxe-japon":
        ohashi_markers = [
            "ohashi", "大橋", "phoenix battle", "フェニックスバトル", "lemino",
            "naoya inoue", "takuma inoue", "yoshiki takei", "jin sasaki"
        ]
        teiken_markers = [
            "teiken", "帝拳", "prime video boxing", "dynamic glove", "ダイナミックグローブ",
            "who's next", "tenshin nasukawa", "junto nakatani", "kenshiro teraji", "ryosuke nishida"
        ]
        unext_markers = ["u-next", "knock out boxing"]

        if any(m in full_context for m in ohashi_markers):
            return get_logo_path_for_key("ohashi")
        if any(m in full_context for m in teiken_markers):
            return get_logo_path_for_key("teiken")
        if any(m in full_context for m in unext_markers):
            return get_logo_path_for_key("unext")
        return get_logo_path_for_key("boxe-japon")

    primary_context = f"{promoteur_field} {nom_gala} {url_low} {presentation}"
    world_promoters_priority = [
        (["zuffa"], "zuffa"),
        (["matchroom"], "matchroom"),
        (["queensberry", "frank warren"], "queensberry"),
        (["boxxer", "ben shalom"], "boxxer"),
        (["mf pro", "misfits", "x series", "viddal riley"], "misfits"),
        (["top rank", "toprank"], "toprank"),
        (["pbc", "premier boxing"], "pbc"),
        (["golden boy", "goldenboy"], "goldenboy"),
        (["the ring", "riyadh season", "ring magazine"], "thering"),
        (["ohashi", "phoenix battle", "lemino"], "ohashi"),
        (["teiken", "prime video boxing"], "teiken"),
    ]
    for keywords, logo_key in world_promoters_priority:
        if any(kw in primary_context for kw in keywords):
            return get_logo_path_for_key(logo_key)

    if "dazn" in full_context:
        return get_logo_path_for_key("dazn")

    return get_logo_path_for_key("boxe-monde")

def verify_and_fix_official_url(ev_url, ev, fallback_url, grounding_urls):
    url = (ev_url or "").strip()
    if (not url or "google.com/search" in url or "vertexaisearch.cloud.google.com" in url) and grounding_urls:
        clean_g_urls = [u for u in grounding_urls if "vertexaisearch.cloud.google.com" not in u]
        if clean_g_urls:
            url = clean_g_urls[0]
    if not url.startswith("http") or "vertexaisearch.cloud.google.com" in url:
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

def extract_iso_date_fallback(date_paris_str):
    if not date_paris_str:
        return None
    matches = re.findall(r"(\d{2})/(\d{2})/(\d{4})", str(date_paris_str))
    if matches:
        d_str, m_str, y_str = matches[0]
        return f"{y_str}-{m_str}-{d_str}"
    return None

def is_location_in_japan(lieu_str):
    if not lieu_str:
        return True
    low = lieu_str.lower()
    non_japan_places = [
        "philippines", "cebu", "manila", "usa", "états-unis", "etats-unis",
        "california", "carson", "las vegas", "new york", "london", "londres",
        "uk", "royaume-uni", "riyadh", "riyad", "arabie", "australia", "melbourne", "mexico"
    ]
    if any(p in low for p in non_japan_places):
        return False
    return True

def clean_and_validate_events(events_list, discipline_id=""):
    if not isinstance(events_list, list):
        return []

    invalid_names = {"n/c", "nc", "n/a", "na", "tba", "tbd", "inconnu", "unknown", "à déterminer", "a determiner", "-"}
    cleaned_events = []

    for ev in events_list:
        if not isinstance(ev, dict):
            continue

        raw_iso = (ev.get("date_iso") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw_iso):
            raw_iso = extract_iso_date_fallback(ev.get("date_paris", "")) or ""

        if raw_iso not in VALID_DATES_ISO_SET:
            print(f"  [Filtre Temporel] Événement rejeté (hors fenêtre {valid_dates_iso[0]}..{valid_dates_iso[-1]}) : {ev.get('nom_gala')} (date_iso={raw_iso})")
            continue

        if discipline_id == "boxe-japon":
            lieu_check = ev.get("lieu") or ""
            gala_check = (ev.get("nom_gala") or "").lower()
            if not is_location_in_japan(lieu_check) or "olascuaga" in gala_check:
                print(f"  [Filtre Géographique Japon] Événement hors Japon rejeté : {ev.get('nom_gala')} ({lieu_check})")
                continue

        ev["date_iso"] = raw_iso

        h_deb = (ev.get("heure_debut_paris") or "").strip()
        h_rw = (ev.get("heure_ringwalk_paris") or "").strip()
        raw_date_paris = (ev.get("date_paris") or "").strip()

        if not raw_date_paris.startswith("Nuit du "):
            ev["date_paris"] = format_paris_date_label(raw_iso, heure_debut_str=h_deb, heure_ringwalk_str=h_rw)

        raw_bouts = ev.get("combats") or []
        valid_bouts = []
        for b in raw_bouts:
            if not isinstance(b, dict):
                continue
            fa = (b.get("combattant_a") or "").strip()
            fb = (b.get("combattant_b") or "").strip()
            if not fa or not fb:
                continue
            if fa.lower() in invalid_names or fb.lower() in invalid_names:
                continue
            valid_bouts.append(b)

        ev["combats"] = valid_bouts
        cleaned_events.append(ev)

    cleaned_events.sort(key=lambda x: x.get("date_iso", "9999-99-99"))
    return cleaned_events

EVENT_JSON_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "evenements": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "nom_gala": {"type": "STRING"},
                    "promoteur": {
                        "type": "STRING",
                        "description": "Nom officiel du promoteur ou de la ligue (ex: Matchroom Boxing, Queensberry, Boxxer, Zuffa Boxing, MF Pro, Top Rank, PBC, Golden Boy, Ohashi Promotions, Teiken Promotions, UFC, Rizin, BKFC, Glory)."
                    },
                    "date_iso": {
                        "type": "STRING",
                        "description": "Date de la soirée de l'événement au format strict YYYY-MM-DD. Doit obligatoirement faire partie de la fenêtre autorisée."
                    },
                    "date_paris": {"type": "STRING"},
                    "lieu": {"type": "STRING"},
                    "url_officielle": {"type": "STRING"},
                    "poster_url": {"type": "STRING"},
                    "heure_debut_paris": {"type": "STRING"},
                    "heure_ringwalk_paris": {"type": "STRING"},
                    "diffuseur_origine": {"type": "STRING"},
                    "diffuseur_france": {"type": "STRING"},
                    "enjeu_principal": {"type": "STRING"},
                    "presentation": {"type": "STRING"},
                    "combats": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "statut": {"type": "STRING"},
                                "combattant_a": {"type": "STRING"},
                                "palmares_a": {"type": "STRING"},
                                "taille_a": {"type": "STRING"},
                                "combattant_b": {"type": "STRING"},
                                "palmares_b": {"type": "STRING"},
                                "taille_b": {"type": "STRING"},
                                "categorie_kg": {"type": "STRING"},
                                "rounds": {"type": "STRING"},
                                "enjeu_et_analyse": {"type": "STRING"},
                            },
                            "required": ["statut", "combattant_a", "combattant_b", "categorie_kg", "rounds", "enjeu_et_analyse"],
                        },
                    },
                },
                "required": [
                    "nom_gala", "promoteur", "date_iso", "date_paris", "lieu", "url_officielle",
                    "heure_debut_paris", "heure_ringwalk_paris", "diffuseur_origine",
                    "diffuseur_france", "enjeu_principal", "presentation", "combats"
                ],
            },
        }
    },
    "required": ["evenements"],
}

def call_gemini_smart(model_name, prompt, target_tokens, use_search_tool):
    cfg_kwargs = {
        "temperature": 0.1,
        "max_output_tokens": target_tokens,
    }
    if use_search_tool:
        cfg_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    else:
        cfg_kwargs["response_mime_type"] = "application/json"
        cfg_kwargs["response_schema"] = EVENT_JSON_SCHEMA

    try:
        return client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
    except Exception as err:
        err_s = str(err)
        if "400" in err_s and "429" not in err_s:
            cfg_kwargs.pop("response_mime_type", None)
            cfg_kwargs.pop("response_schema", None)
            if target_tokens > 8192:
                cfg_kwargs["max_output_tokens"] = 8192
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**cfg_kwargs),
            )
        raise err

def parse_agent_json(raw_text, discipline_id=""):
    if not raw_text:
        return None
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()
    cleaned = re.sub(r"^\s*//.*$", "", cleaned, flags=re.MULTILINE)

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return clean_and_validate_events(data.get("evenements", []), discipline_id)
            if isinstance(data, list):
                return clean_and_validate_events(data, discipline_id)
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
                return clean_and_validate_events(data, discipline_id)
        except Exception:
            pass

    if first_brace != -1 and '"evenements"' in cleaned and '"combats"' in cleaned:
        last_complete_bout = cleaned.rfind("}")
        while last_complete_bout > first_brace:
            for suffix in ["]}]}", "]}]\"}"]:
                attempt = cleaned[first_brace : last_complete_bout + 1] + suffix
                attempt = re.sub(r",\s*([}\]])", r"\1", attempt)
                try:
                    data = json.loads(attempt)
                    if isinstance(data, dict) and "evenements" in data:
                        return clean_and_validate_events(data["evenements"], discipline_id)
                except Exception:
                    pass
            last_complete_bout = cleaned.rfind("}", first_brace, last_complete_bout)

    return None

# ==============================================================================
# 6. EXÉCUTION D'UN MICRO-AGENT + FUSION GARANTIE DES SOUS-CARTES
# ==============================================================================
GENERIC_EVENT_WORDS = {
    "fight", "night", "boxing", "world", "title", "gala", "champion",
    "championship", "arena", "hall", "center", "centre", "stadium",
    "matchroom", "queensberry", "toprank", "golden", "premier", "rizin",
    "glory", "bkfc", "landmark", "dynamic", "glove", "phoenix", "battle",
    "misfits", "series", "zuffa", "boxxer", "ohashi", "teiken", "korakuen", "tokyo"
}

def extract_fighter_tokens(text):
    if not text:
        return set()
    # Seuil abaissé à 2 lettres pour prendre en compte les noms courts japonais/asiatiques (ex: Jin, Sho, Ryo, Lee)
    words = re.findall(r"[a-zA-ZÀ-ÿ]{2,}", text.lower())
    return {w for w in words if w not in GENERIC_EVENT_WORDS and len(w) >= 3}

def get_main_event_signature(ev):
    tokens = set()
    combats = ev.get("combats") or []
    if combats:
        tokens.update(extract_fighter_tokens(combats[0].get("combattant_a", "")))
        tokens.update(extract_fighter_tokens(combats[0].get("combattant_b", "")))
    if not tokens:
        tokens.update(extract_fighter_tokens(ev.get("nom_gala", "")))
    return tokens

def events_are_same_gala(ev_a, ev_b):
    iso_a = ev_a.get("date_iso") or ""
    iso_b = ev_b.get("date_iso") or ""
    if not iso_a or not iso_b:
        return False
    try:
        dt_a = datetime.strptime(iso_a, "%Y-%m-%d")
        dt_b = datetime.strptime(iso_b, "%Y-%m-%d")
        if abs((dt_a - dt_b).days) > 1:
            return False
    except Exception:
        if iso_a != iso_b:
            return False

    sig_a = get_main_event_signature(ev_a)
    sig_b = get_main_event_signature(ev_b)
    if sig_a and sig_b and (sig_a & sig_b):
        return True
    return False

def bouts_are_same_fight(b1, b2):
    t1_a = extract_fighter_tokens(b1.get("combattant_a", ""))
    t1_b = extract_fighter_tokens(b1.get("combattant_b", ""))
    t2_a = extract_fighter_tokens(b2.get("combattant_a", ""))
    t2_b = extract_fighter_tokens(b2.get("combattant_b", ""))

    direct_match = bool(t1_a & t2_a) and bool(t1_b & t2_b)
    reverse_match = bool(t1_a & t2_b) and bool(t1_b & t2_a)
    return direct_match or reverse_match

def merge_undercard_bouts(primary_events, reference_events, add_missing_events=False):
    if not reference_events:
        return primary_events

    merged_list = list(primary_events)
    empty_values = {"", "N/C", "N/A", "n/c", "n/a", "-"}

    for ref_ev in reference_events:
        matched_primary = None
        for prim_ev in merged_list:
            if events_are_same_gala(prim_ev, ref_ev):
                matched_primary = prim_ev
                break

        if matched_primary is not None:
            prim_bouts = matched_primary.get("combats") or []
            ref_bouts = ref_ev.get("combats") or []
            added_count = 0

            for rb in ref_bouts:
                existing_bout = None
                for pb in prim_bouts:
                    if bouts_are_same_fight(pb, rb):
                        existing_bout = pb
                        break

                if existing_bout is not None:
                    for field in ["palmares_a", "palmares_b", "taille_a", "taille_b"]:
                        if (existing_bout.get(field) in empty_values) and (rb.get(field) not in empty_values):
                            existing_bout[field] = rb[field]
                else:
                    prim_bouts.append(rb)
                    added_count += 1

            if added_count > 0:
                print(f"  [Fusion Sous-Carte] +{added_count} combat(s) préliminaire(s) ajouté(s) sur '{matched_primary.get('nom_gala')}'.")
            matched_primary["combats"] = prim_bouts

        elif add_missing_events and len(ref_ev.get("combats") or []) >= 1:
            print(f"  [Ajout Gala Officiel] Gala ajouté depuis la source officielle : '{ref_ev.get('nom_gala')}'.")
            merged_list.append(ref_ev)

    merged_list.sort(key=lambda x: x.get("date_iso", "9999-99-99"))
    return merged_list

def run_micro_agent(agent_cfg, cached_data):
    discipline_id = agent_cfg["id"]
    target_tokens = agent_cfg.get("max_tokens", 8192)
    fallback_url = agent_cfg.get("fallback_url", "https://box.live/upcoming-fights-schedule/")
    print(f"\n--- Lancement Micro-Agent [{discipline_id}] (Plafond : {target_tokens} tokens) ---")

    valid_cached_events = clean_and_validate_events(cached_data.get(discipline_id, []), discipline_id)
    cached_data[discipline_id] = valid_cached_events

    espn_reference_events = []
    if discipline_id == "ufc":
        espn_reference_events = clean_and_validate_events(fetch_espn_events_direct("mma", "ufc"), discipline_id)
    elif discipline_id == "boxe-monde":
        espn_reference_events = clean_and_validate_events(fetch_espn_events_direct("boxing", "boxing"), discipline_id)

    live_web_context = build_prefetched_context(discipline_id)

    base_prompt = f"""Tu es un agent d'extraction de données factuelles spécialisé dans les sports de combat.
Date actuelle : {date_str} (Fuseau horaire : Europe/Paris).
Fenêtre temporelle STRICTE autorisée : {DATE_WINDOW_STR}.

MISSION :
{agent_cfg['search_hint']}

CONSIGNES IMPÉRATIVES :
{agent_cfg['specific_rules']}
- FILTRE TEMPOREL STRICT : Seuls les événements ayant réellement lieu dans la fenêtre {DATE_WINDOW_STR} sont autorisés. Si un combat annoncé dans l'actualité a lieu à une date ultérieure (en décembre ou plus tard), IL EST STRICTEMENT INTERDIT de l'inclure ou de lui attribuer la date d'un autre gala.
- CHAMP 'date_iso' OBLIGATOIRE : Indique toujours la date de la soirée où commence le gala au format YYYY-MM-DD (ex: "2026-11-14" pour un gala dans la nuit du Samedi 14 au Dimanche 15). Cette date doit obligatoirement figurer parmi : {', '.join(valid_dates_iso)}.
- CHAMP 'promoteur' OBLIGATOIRE : Indique le nom exact du promoteur ou de la ligue organisatrice (ex: "Matchroom Boxing", "Queensberry", "Boxxer", "Zuffa Boxing", "MF Pro", "Top Rank", "PBC", "Golden Boy", "Ohashi Promotions", "Teiken Promotions", "U-NEXT Boxing", "JBC / Korakuen Hall", "UFC", "Rizin", "BKFC", "Glory").
- INTERDICTION DE RÉSUMER LES SOUS-CARTES : Tu dois extraire 100 % des combats connus de chaque carte (Main Event, Co-Main Event, Main Card, Prelims, Early Prelims, Undercard) présents dans les données pré-collectées ET via ta recherche. Ne renvoie jamais un gala avec seulement 1 combat si d'autres combats de la carte sont mentionnés dans le contexte.
- CONCISION SUR LES PRELIMS POUR ÉVITER LA COUPURE JSON : Pour le Main Event et le Co-Main Event, rédige une "enjeu_et_analyse" complète (1 à 2 phrases). Pour tous les autres combats de la sous-carte (Main Card / Prelims / Undercard), rédige une "enjeu_et_analyse" très courte (maximum 10 mots) afin d'économiser les jetons et de lister tous les combats sans exception.
- INTERDICTION ABSOLUE DES ADVERSAIRES "N/C" OU "TBA" : Chaque combat listé dans "combats" DOIT avoir le Prénom et Nom de combattant_a ET le Prénom et Nom de combattant_b. Si la taille ou le palmarès est inconnu, écris "N/C" (jamais "N/A").
- DOUBLE INDICATION HORAIRE (HEURE DE PARIS) :
  1. 'heure_debut_paris' : Heure du lancement du gala / début des combats préliminaires (ex: "19h00", "01h00").
  2. 'heure_ringwalk_paris' : Heure annoncée ou estimée de la marche vers le ring (ringwalk) du Main Event à l'heure de Paris (ex: "vers 23h30", "vers 05h00 (estimation)").
- Convertis tous les poids en kilogrammes (ex: "-63,5 kg") et les tailles en mètres (ex: "1m78").

DONNÉES EN DIRECT PRÉ-COLLECTÉES PAR LE SYSTÈME (CONTIENT LES PAGES OFFICIELLES, ZUFFA, MATCHROOM, BOX.LIVE, BOXMOB ET SOUS-CARTES) :
{live_web_context if live_web_context else "Aucune donnée pré-collectée, utilise ta recherche."}

FORMAT DE SORTIE OBLIGATOIRE :
Renvoie UNIQUEMENT un objet JSON valide respectant exactement cette structure, sans commentaire ni texte autour :
{{
  "evenements": [
    {{
      "nom_gala": "Nom officiel du gala : Combattant A vs. Combattant B",
      "promoteur": "Matchroom Boxing",
      "date_iso": "2026-11-14",
      "date_paris": "Nuit du Samedi 14/11 au Dimanche 15/11/2026",
      "lieu": "Salle / Arène, Ville, Pays",
      "url_officielle": "https://...",
      "poster_url": "",
      "heure_debut_paris": "01h00",
      "heure_ringwalk_paris": "vers 04h30",
      "diffuseur_origine": "DAZN Global",
      "diffuseur_france": "DAZN France",
      "enjeu_principal": "Titre mondial WBC Super-Légers (-63,5 kg)",
      "presentation": "2 phrases de présentation factuelle des enjeux de l'événement.",
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
          "enjeu_et_analyse": "Titre en jeu et description stylistique du duel."
        }}
      ]
    }}
  ]
}}
Si aucun événement n'est programmé dans cette fenêtre, renvoie exactement : {{"evenements": []}}
"""

    active_candidates = [m for m in AVAILABLE_MODELS if m not in DEAD_MODELS_TOTAL]
    always_active_disciplines = {"boxe-monde", "boxe-japon"}
    empty_fallback_candidate = None

    for model_name in active_candidates:
        is_lite = "lite" in model_name.lower()
        if is_lite or model_name in DEAD_MODELS_SEARCH:
            modes_to_try = [False]
        else:
            modes_to_try = [True, False]

        for use_search in modes_to_try:
            mode_label = "avec Google Search" if use_search else "mode Direct (sans quota Search)"
            try:
                print(f"  Appel {model_name} ({mode_label})...")
                response = call_gemini_smart(model_name, base_prompt, target_tokens, use_search)

                if response and getattr(response, "text", None):
                    events = parse_agent_json(response.text, discipline_id)
                    if events is None:
                        print(f"  [Info] JSON mal formé sur {model_name} ({mode_label}), suite...")
                        continue

                    if espn_reference_events:
                        events = merge_undercard_bouts(
                            events,
                            espn_reference_events,
                            add_missing_events=(discipline_id in {"ufc", "boxe-monde"}),
                        )
                    if valid_cached_events:
                        events = merge_undercard_bouts(
                            events,
                            valid_cached_events,
                            add_missing_events=(discipline_id in {"boxe-monde", "boxe-japon"}),
                        )

                    if not events and discipline_id in always_active_disciplines:
                        print(f"  [Info] 0 événement retourné sur {model_name} ({mode_label}) pour [{discipline_id}], essai suivant...")
                        empty_fallback_candidate = []
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

                    total_bouts = sum(len(e.get("combats") or []) for e in events)
                    print(f"  -> Succès ({model_name} - {mode_label}) : {len(events)} événement(s) et {total_bouts} combat(s) extrait(s) pour [{discipline_id}].")
                    cached_data[discipline_id] = events
                    return events, True

            except Exception as e:
                err_str = str(e)
                print(f"  [Avertissement] {model_name} ({mode_label}) : {err_str[:380]}")

                if "404" in err_str or "NOT_FOUND" in err_str:
                    DEAD_MODELS_TOTAL.add(model_name)
                    break

                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Si le quota journalier du modèle (20 RPD) est atteint, on passe immédiatement au modèle suivant sans faire un 2e appel inutile
                    DEAD_MODELS_TOTAL.add(model_name)
                    print(f"  [Bascule Quota] Plafond atteint sur {model_name}, passage immédiat au modèle suivant...")
                    time.sleep(2)
                    break

                time.sleep(2)

    if espn_reference_events:
        for ev in espn_reference_events:
            ev["poster_url"] = resolve_event_poster(ev, ev["url_officielle"], [], discipline_id, len(espn_reference_events))
        print(f"  -> [Succès Mode Autonome ESPN] {len(espn_reference_events)} événement(s) [{discipline_id}] extrait(s) directement.")
        cached_data[discipline_id] = espn_reference_events
        return espn_reference_events, True

    if valid_cached_events:
        for ev in valid_cached_events:
            ev["poster_url"] = resolve_event_poster(ev, ev.get("url_officielle", ""), [], discipline_id, len(valid_cached_events))
        print(f"  [Secours Cache] Restauration des {len(valid_cached_events)} événement(s) valides en cache pour [{discipline_id}].")
        return valid_cached_events, False

    if empty_fallback_candidate is not None:
        return [], True

    print(f"  [Info] Aucun résultat récupéré pour [{discipline_id}].")
    return [], False

# ==============================================================================
# 7. CONSTRUCTION DU HTML PAR SECTION À PARTIR DU JSON VÉRIFIÉ
# ==============================================================================
EMPTY_META_TOKENS = {"n/c", "nc", "n/a", "na", "tba", "tbd", "inconnu", "unknown", "-"}

def render_event_card_html(ev):
    nom_gala = html.escape(ev.get("nom_gala") or "Événement Officiel")
    date_paris = html.escape(ev.get("date_paris") or "")
    lieu = html.escape(ev.get("lieu") or "Lieu à confirmer")
    url_off = (ev.get("url_officielle") or "").strip()
    poster_url = (ev.get("poster_url") or "").strip()
    h_debut = html.escape(ev.get("heure_debut_paris") or "N/C")
    h_ringwalk = html.escape(ev.get("heure_ringwalk_paris") or ev.get("heure_main_event_paris") or "N/C")
    diff_orig = html.escape(ev.get("diffuseur_origine") or "International")
    diff_fr = html.escape(ev.get("diffuseur_france") or "Streaming officiel")
    enjeu = html.escape(ev.get("enjeu_principal") or "")
    presentation = html.escape(ev.get("presentation") or "")
    combats = ev.get("combats") or []

    ev_iso = ev.get("date_iso") or ""
    today_iso = now_paris.strftime("%Y-%m-%d")
    yesterday_iso = (now_paris + timedelta(days=-1)).strftime("%Y-%m-%d")

    if ev_iso == today_iso or date_str in date_paris:
        status_badge = '<span class="today-pill">🔴 AUJOURD\'HUI</span>'
    elif ev_iso == yesterday_iso or yesterday_str in date_paris:
        status_badge = '<span class="yesterday-pill">⏮️ HIER / RÉCENT</span>'
    else:
        status_badge = ""

    belt_badge = f'<div class="poster-belt">👑 {enjeu}</div>' if enjeu else ""
    if poster_url:
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
        pa = html.escape((c.get("palmares_a") or "").strip())
        ta = html.escape((c.get("taille_a") or "").strip())
        fb = html.escape(c.get("combattant_b") or "")
        pb = html.escape((c.get("palmares_b") or "").strip())
        tb = html.escape((c.get("taille_b") or "").strip())
        cat = html.escape(c.get("categorie_kg") or "")
        rds = html.escape(c.get("rounds") or "")
        desc = html.escape(c.get("enjeu_et_analyse") or "")

        meta_a = " | ".join(x for x in [pa, ta] if x and x.lower() not in EMPTY_META_TOKENS)
        meta_b = " | ".join(x for x in [pb, tb] if x and x.lower() not in EMPTY_META_TOKENS)
        str_a = f"{fa} ({meta_a})" if meta_a else fa
        str_b = f"{fb} ({meta_b})" if meta_b else fb

        pill_text = " — ".join(x for x in [statut, cat, rds] if x)
        low_statut = statut.lower()
        if "main event" in low_statut and "co" not in low_statut:
            pill_class = "weight-pill pill-main"
        elif "co-main" in low_statut or "main card" in low_statut:
            pill_class = "weight-pill pill-comain"
        else:
            pill_class = "weight-pill"

        pill_html = f'<span class="{pill_class}">{pill_text}</span>' if pill_text else ""

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
            <h3 class="event-title">{status_badge}{nom_gala}</h3>
            {source_btn}
        </div>
        <div class="meta-grid">
            <div class="meta-item">📅 <strong>Date :</strong> <span class="highlight-date">{date_paris}</span> &nbsp;|&nbsp; 📍 <strong>Lieu :</strong> {lieu}</div>
            <div class="meta-item">⏰ <strong>Horaires (Paris) :</strong> Début du gala à <strong>{h_debut}</strong> &nbsp;|&nbsp; 🥊 <strong>Ringwalk Main Event :</strong> <span class="highlight-ringwalk">{h_ringwalk}</span></div>
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
    """, len(bouts_html_list)

# Exécution séquentielle des 6 Micro-Agents
cached_calendar = load_cache()
sections_html = []
nav_links = []
total_events_found = 0
total_bouts_found = 0
successful_extractions = 0

for idx, cfg in enumerate(AGENTS_CONFIG):
    events_list, ok = run_micro_agent(cfg, cached_calendar)
    if ok:
        successful_extractions += 1

    ev_count = len(events_list)
    total_events_found += ev_count
    nav_links.append(f'<a href="#{cfg["id"]}">{cfg["nav_label"]} <span class="nav-count">{ev_count}</span></a>')

    if events_list:
        rendered_cards = []
        for ev in events_list:
            c_html, b_count = render_event_card_html(ev)
            rendered_cards.append(c_html)
            total_bouts_found += b_count
        cards_rendered = "\n".join(rendered_cards)
    else:
        cards_rendered = f'<div class="empty-state">🗓️ {html.escape(cfg["empty_msg"])}</div>'

    section_block = f"""
    <section id="{cfg['id']}" class="sport-section">
        <h2>{cfg['title']}</h2>
        {cards_rendered}
    </section>
    """
    sections_html.append(section_block)

    remaining_models = [m for m in AVAILABLE_MODELS if m not in DEAD_MODELS_TOTAL]
    if idx < len(AGENTS_CONFIG) - 1 and remaining_models:
        print("  Pause de 10s pour préserver le quota de jetons par minute (RPM/TPM)...")
        time.sleep(10)

save_cache(cached_calendar)
nav_links_html = "\n                ".join(nav_links)
all_sections_content = "\n".join(sections_html)

# ==============================================================================
# 8. ASSEMBLAGE DU SITE FINAL (BARRE FIXE EN HAUT SANS STICKY + RECHERCHE)
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
        *, *::before, *::after {{
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
            padding: 0.35rem 0.95rem;
            border-radius: 99px;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        .stats-banner {{
            margin-top: 0.65rem;
            font-size: 0.84rem;
            color: #F7D388;
            font-weight: 600;
        }}
        .sticky-bar {{
            position: relative;
            background-color: var(--bg-card);
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
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
        }}
        .quick-nav a:hover {{
            background-color: var(--accent-main);
            color: #FFFFFF;
        }}
        .nav-count {{
            background-color: rgba(74, 40, 18, 0.14);
            padding: 0.05rem 0.42rem;
            border-radius: 99px;
            font-size: 0.75rem;
        }}
        .quick-nav a:hover .nav-count {{
            background-color: rgba(255, 255, 255, 0.25);
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
            scroll-margin-top: 20px;
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
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 0.5rem;
        }}
        .today-pill {{
            background-color: #C92A2A;
            color: #FFFFFF;
            font-size: 0.72rem;
            font-weight: 800;
            padding: 0.18rem 0.55rem;
            border-radius: 6px;
            letter-spacing: 0.5px;
        }}
        .yesterday-pill {{
            background-color: #6C584C;
            color: #FFFDF9;
            font-size: 0.72rem;
            font-weight: 700;
            padding: 0.18rem 0.55rem;
            border-radius: 6px;
            letter-spacing: 0.4px;
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
        .highlight-ringwalk {{
            color: var(--accent-dark);
            background-color: #F3E4C8;
            padding: 0.1rem 0.5rem;
            border-radius: 5px;
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
        .pill-main {{
            background-color: #C4882B;
            color: #FFFDF9;
        }}
        .pill-comain {{
            background-color: #9A4E24;
            color: #FFFDF9;
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
            <p>Programmation officielle vérifiée : Boxe Mondiale & Japonaise, The Ring, Zuffa, UFC, Rizin, BKFC & Glory</p>
            <span class="update-pill">🔄 Mis à jour le {date_str} à {heure_str} (Heure de Paris) • Période : {valid_dates_fr[0]} au {valid_dates_fr[-1]}</span>
            <div class="stats-banner">📊 {total_events_found} gala(s) et {total_bouts_found} combat(s) répertoriés sur la période</div>
        </header>

        <div class="sticky-bar">
            <nav class="quick-nav">
                {nav_links_html}
            </nav>
            <div class="toolbar">
                <input type="search" id="searchInput" class="search-input" placeholder="🔍 Filtrer par combattant, promoteur (Matchroom, Queensberry, UFC...), ville ou chaîne...">
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
                const isMatch = text.includes(query);
                card.style.display = isMatch ? '' : 'none';
                const details = card.querySelector('details.card-details');
                if (details && query.length >= 2 && isMatch) {{
                    details.open = true;
                }} else if (details && query.length === 0 && !allOpen) {{
                    details.open = false;
                }}
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

print(f"\nSynchronisation terminée avec succès : {total_events_found} événements et {total_bouts_found} combats écrits dans index.html.")
