import streamlit as st
import feedparser
import json
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from google.genai.errors import APIError

# --- CONFIGURAZIONE E STILE ---

st.set_page_config(page_title="🌍 Daily News Digest AI", layout="centered")

# --- RECUPERO CHIAVE GEMINI ---
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError:
    st.error("⚠️ Chiave GEMINI_API_KEY non trovata nei secrets. L'API di sintesi fallirà.")

# ----------------------------------------------------------------------
# --- 📍 MAPPAZIONE FISSA DELLE FONTI RSS PER LE TUE ESIGENZE ---
# Questi sono i feed RSS generali (o per le sezioni principali) dei siti richiesti.
# La suddivisione per sezione (Libano, Gaza, etc.) è gestita raggruppando i feed.

FEED_MAPPING = {
    # 1. Libano (Uso L'Orient Le Jour, molto specifico, e Al Jazeera)
    "Libano": [
        "https://www.lorientlejour.com/rss/all.xml", # L'Orient-Le Jour (francese)
        "https://www.aljazeera.com/xml/rss/all.xml" # Al Jazeera (inglese)
    ],
    # 2. Gaza (Uso Middle East Eye e Al Jazeera per la copertura)
    "Gaza": [
        "https://www.middleeasteye.net/rss/all", # Middle East Eye (inglese)
        "https://www.aljazeera.com/xml/rss/all.xml"
    ],
    # 3. Medio Oriente (Generale) (Aggiungiamo Al Monitor e Orient XXI)
    "Medio Oriente (Siria, Palestina)": [
        "https://www.al-monitor.com/rss/news.xml", # Al-Monitor (inglese)
        "https://www.orientxxi.info/public/backend.php?lang=it", # Orient XXI (italiano)
    ],
    # 4. Italia politica interna (Uso fonti italiane)
    "Italia (Politica Interna)": [
        "https://rss.ilmanifesto.it/ilmanifesto.xml", # Il Manifesto
        "https://www.domani.it/rss", # Domani
        "https://espresso.repubblica.it/rss.xml" # L'Espresso
    ],
    # 5. Mondo (Notizie principali) (Uso Internazionale e Al Jazeera generico)
    "Mondo (Principali)": [
        "https://www.internazionale.it/rss", # Internazionale
        "https://www.aljazeera.com/xml/rss/all.xml"
    ]
}
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_news_from_rss(section_name):
    """
    Legge tutti i feed RSS per una sezione data e raccoglie gli articoli recenti.
    """
    articles = []
    feed_list = FEED_MAPPING.get(section_name, [])
    
    # Calcola l'ora di 24 ore fa per filtrare
    yesterday = datetime.now() - timedelta(hours=24)

    for url in feed_list:
        try:
            feed = feedparser.parse(url)
            
            for entry in feed.entries:
                # Controlla se l'articolo è abbastanza recente
                is_recent = True
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_date = datetime(*entry.published_parsed[:6])
                    if published_date < yesterday:
                        is_recent = False # Articolo troppo vecchio

                if is_recent:
                    # Estrai la descrizione se disponibile, altrimenti usa un riassunto
                    description = getattr(entry, 'summary', None) or getattr(entry, 'description', None) or ""
                    
                    # Aggiungiamo la fonte all'articolo
                    source = feed.feed.title if hasattr(feed.feed, 'title') else url.split('/')[2]

                    articles.append({
                        'title': entry.title,
                        'description': description,
                        'url': entry.link,
                        'source': source # Includiamo la fonte per il debug di Gemini
                    })
        except Exception as e:
            st.warning(f"Errore nella lettura del feed RSS {url} per la sezione '{section_name}': {e}")
            continue

    return articles


def run_news_collection():
    """
    Esegue la raccolta delle notizie da tutti i feed definiti.
    """
    raw_digest_data = []
    
    # Ordine delle sezioni per l'output (le chiavi del dizionario)
    sections_order = ["Libano", "Gaza", "Medio Oriente (Siria, Palestina)", 
                      "Italia (Politica Interna)", "Mondo (Principali)"]
    
    for section_title in sections_order:
        # Recupera gli articoli da tutti i feed mappati per questa sezione
        articles = get_news_from_rss(section_title)
        raw_digest_data.append({"section": section_title, "articles": articles})

    return raw_digest_data


def summarize_with_gemini(raw_digest_data):
    """
    Invia i dati grezzi delle notizie a Gemini per sintetizzare e strutturare il digest.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE ---
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    # --- 2. PREPARAZIONE DATI GREZZI PER IL PROMPT ---
    formatted_input = ""
    total_articles = 0
    
    for section_data in raw_digest_data:
        section_title = section_data['section']
        articles = section_data['articles']
        total_articles += len(articles)
        
        if not articles:
            formatted_input += f"### {section_title} (NESSUNA NOTIZIA TROVATA)\n\n"
            continue
        
        formatted_input += f"### {section_title} ({len(articles)} ARTICOLI DA SINTETIZZARE)\n"
        for i, article in enumerate(articles):
            # Passiamo il titolo e la descrizione a Gemini
            formatted_input += f"- Articolo {i+1} [Fonte: {article.get('source', 'Sconosciuta')}]: {article['title']}\n"
            if article['description']:
                formatted_input += f"  Descrizione: {article['description'][:500]}...\n"
        formatted_input += "\n"
        
    # Se non c'è nulla, usciamo subito e mostriamo un errore specifico
    if total_articles == 0:
        st.warning("🚨 ATTENZIONE: Nessun articolo recente trovato in NESSUN feed RSS. Controlla che gli URL siano corretti o che ci siano notizie fresche (ultime 24h).")
        return None # Ritorna None per interrompere la sintesi

    # --- 3. DEFINIZIONE DELLO SCHEMA JSON (OUTPUT FORZATO) ---
    section_summary_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "sintesi_testo": types.Schema(type=types.Type.STRING, description="Riassunto conciso in 3-5 frasi dei fatti principali di questa sezione."),
            "punti_chiave": types.Schema(
                type=types.Type.ARRAY,
                description="Elenco di 3-5 bullet point con i titoli più importanti.",
                items=types.Schema(type=types.Type.STRING)
            ),
            "link_principale": types.Schema(type=types.Type.STRING, description="L'URL dell'articolo più rilevante trovato in questa sezione, dalla fonte originale."),
            "fonte_principale": types.Schema(type=types.Type.STRING, description="Il nome della fonte (es. Al Jazeera, Il Manifesto) da cui proviene il link principale.")
        },
        required=["sintesi_testo", "punti_chiave"]
    )

    final_digest_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "Libano": section_summary_schema,
            "Gaza": section_summary_schema,
            "Medio Oriente (Siria, Palestina)": section_summary_schema,
            "Italia (Politica Interna)": section_summary_schema,
            "Mondo (Principali)": section_summary_schema,
        },
        required=["Libano", "Gaza", "Medio Oriente (Siria, Palestina)", "Italia (Politica Interna)", "Mondo (Principali)"]
    )
    
    # --- 4. PROMPT COMPLETO E CONFIGURAZIONE PER GEMINI ---

    system_instruction = """
    Sei un analista di notizie esperto e molto conciso. Genera un "digest" delle notizie principali delle ultime 24 ore.
    Sintetizza tutti i contenuti in modo obiettivo, neutrale e rigorosamente in italiano, anche se le fonti originali sono in inglese o francese.
    L'output DEVE rispettare lo schema JSON fornito, e il link principale deve essere scelto tra gli articoli forniti.
    """
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=final_digest_schema,
    )

    prompt = f"""
    Genera il digest delle notizie basandoti SOLO ed ESCLUSIVAMENTE sui seguenti articoli grezzi. 
    Mantieni l'ordine delle sezioni.
    
    ARTICOLI GREZZI:
    ---
    {formatted_input}
    ---
    """

    # --- 5. CHIAMATA ALL'API ---
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=config,
        )

        json_string = response.text.strip()
        # Pulizia del JSON
        if json_string.startswith("```json"):
            json_string = json_string[7:]
        if json_string.endswith("```"):
            json_string = json_string[:-3]
            
        digest_data = json.loads(json_string)
        return digest_data

    except APIError as e:
        st.error(f"Errore API Gemini: La chiamata è fallita. Causa: {e}. Controlla chiave e quota.")
        return None
    except json.JSONDecodeError:
        st.error("Errore di decodifica JSON: Gemini non ha restituito un formato JSON valido. Riprova.")
        # Se fallisce, stampiamo la risposta per il debug
        st.code(json_string)
        return None
    except Exception as e:
        st.error(f"Errore inatteso durante la sintesi AI: {e}")
        return None


# ----------------------------------------------------------------------
# --------------------- INTERFACCIA STREAMLIT (Frontend) ---------------
# ----------------------------------------------------------------------

# --- LOGO E TITOLO ---
col_icon, col_title = st.columns([0.5, 6.5]) 

with col_icon:
    st.markdown("## 🌍") 

with col_title:
    st.title("Daily News") 

st.markdown("---")

# --- INTRODUZIONE ---
st.markdown("""
**Ciao! Eccoci per la sintesi dei fatti del giorno!**

Le fonti utilizzate sono: Al Jazeera, Middle East Eye, Al Monitor, L'Orient Le Jour, Orient XXI, L'Espresso, Il Manifesto, Domani, Internazionale.
""")

st.info("""
L'applicazione utilizza i feed RSS delle fonti che hai scelto e il sistema **Gemini AI Flash 2.5** per la sintesi.
""")
st.markdown("---")


# --- ESECUZIONE E DIGEST ---

if st.button("▶️ Genera il Digest Quotidiano", type="primary"):
    
    if not GEMINI_API_KEY:
        st.error("Impossibile procedere. La chiave GEMINI_API_KEY è mancante nei secrets.")
        st.stop()
        
    progress_bar = st.progress(0, text="Avvio della raccolta articoli e sintesi con AI...")
    
    # 1. RACCOLTA DATI GREZZI (solo da RSS)
    progress_bar.progress(30, text="1/3: Raccolta articoli dai feed RSS in corso...")
    raw_news_data = run_news_collection()
    
    # Controlliamo la variabile di ritorno della funzione summarize_with_gemini
    
    # 2. SINTESI CON GEMINI
    final_digest = None
    if raw_news_data:
        progress_bar.progress(70, text="2/3: Sintesi e strutturazione con Gemini AI...")
        final_digest = summarize_with_gemini(raw_news_data) 
    
    progress_bar.empty()
    
    # 3. VISUALIZZAZIONE DEL RISULTATO
    if final_digest:
        st.success("✅ Digest delle notizie generato con successo!")
        st.header("Il tuo Digest Quotidiano 📰")
        
        # Ordine di visualizzazione
        sections_order = ["Libano", "Gaza", "Medio Oriente (Siria, Palestina)", "Italia (Politica Interna)", "Mondo (Principali)"]
        
        for section in sections_order:
            if section in final_digest:
                data = final_digest[section]
                
                # Se la sintesi non è "N/A" (risposta di Gemini in caso di zero articoli)
                if data.get('sintesi_testo', 'N/A') == 'N/A' and not data.get('punti_chiave'):
                    continue
                
                st.subheader(f"➡️ {section}")
                
                # Sintesi
                st.markdown(f"**Sintesi:** {data.get('sintesi_testo', 'Nessuna sintesi disponibile.')}")
                
                # Punti chiave
                st.markdown("**Punti Chiave:**")
                st.markdown("- " + "\n- ".join(data.get('punti_chiave', ['Nessun punto chiave significativo trovato.'])))
                
                # Link
                if data.get('link_principale'):
                    fonte = data.get('fonte_principale', 'Link Sconosciuto')
                    st.markdown(f"🔗 [Leggi l'articolo principale da **{fonte}**]({data['link_principale']})")
                
                st.markdown("---")
        
    else:
        # L'errore specifico (mancanza di articoli o errore Gemini) è gestito internamente
        pass
