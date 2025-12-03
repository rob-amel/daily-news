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
# Ho mappato i feed RSS disponibili per i siti richiesti.
# Nota: La ricerca per argomenti specifici (es. "solo Libano") avviene tramite la sintesi AI.

FEED_MAPPING = {
    # 1. Libano (Focus su L'Orient Le Jour e Al Jazeera)
    "Libano": [
        "https://www.lorientlejour.com/rss/all.xml", 
        "https://www.aljazeera.com/xml/rss/all.xml"
    ],
    # 2. Gaza (Focus su Middle East Eye e Al Jazeera)
    "Gaza": [
        "https://www.middleeasteye.net/rss/all", 
        "https://www.aljazeera.com/xml/rss/all.xml"
    ],
    # 3. Medio Oriente (Generale) (Aggiungiamo Al Monitor e Orient XXI)
    "Medio Oriente (Siria, Palestina)": [
        "https://www.al-monitor.com/rss/news.xml", 
        "https://www.orientxxi.info/public/backend.php?lang=it",
    ],
    # 4. Italia politica interna (Focus sui siti italiani che hai scelto)
    "Italia (Politica Interna)": [
        "https://rss.ilmanifesto.it/ilmanifesto.xml", 
        "https://www.domani.it/rss", 
        "https://espresso.repubblica.it/rss.xml" 
    ],
    # 5. Mondo (Notizie principali) (Internazionale e altri feed non ancora utilizzati)
    "Mondo (Principali)": [
        "https://www.internazionale.it/rss", 
        "https://www.aljazeera.com/xml/rss/all.xml"
    ]
}
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_news_from_rss(section_name):
    """
    Legge tutti i feed RSS per una sezione data e raccoglie gli articoli recenti (ultime 24h).
    """
    articles = []
    feed_list = FEED_MAPPING.get(section_name, [])
    
    # Calcola l'ora di 24 ore fa per filtrare
    yesterday = datetime.now() - timedelta(hours=24)

    for url in feed_list:
        try:
            feed = feedparser.parse(url)
            
            for entry in feed.entries:
                is_recent = True
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_date = datetime(*entry.published_parsed[:6])
                    if published_date < yesterday:
                        is_recent = False

                if is_recent:
                    # Estrai la descrizione se disponibile
                    description = getattr(entry, 'summary', None) or getattr(entry, 'description', None) or ""
                    
                    # Estrai il nome della fonte
                    source = feed.feed.title if hasattr(feed.feed, 'title') else url.split('/')[2]

                    articles.append({
                        'title': entry.title,
                        'description': description,
                        'url': entry.link,
                        'source': source
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
    
    # Ordine delle sezioni
    sections_order = ["Libano", "Gaza", "Medio Oriente (Siria, Palestina)", 
                      "Italia (Politica Interna)", "Mondo (Principali)"]
    
    for section_title in sections_order:
        articles = get_news_from_rss(section_title)
        raw_digest_data.append({"section": section_title, "articles": articles})

    return raw_digest_data


def summarize_with_gemini(raw_digest_data):
    """
    Invia i dati grezzi delle notizie a Gemini per sintetizzare in un unico script vocale.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE ---
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    # --- 2. PREPARAZIONE DATI GREZZI E CONTROLLO ---
    formatted_input = ""
    total_articles = 0
    
    for section_data in raw_digest_data:
        section_title = section_data['section']
        articles = section_data['articles']
        total_articles += len(articles)
        
        # Aggiungiamo le intestazioni per aiutare Gemini a suddividere logicamente
        formatted_input += f"\n\n### SEZIONE: {section_title} ({len(articles)} ARTICOLI DA SINTETIZZARE)\n"
        if not articles:
            formatted_input += "NESSUNA NOTIZIA RECENTE TROVATA IN QUESTA SEZIONE.\n"
            continue
        
        for i, article in enumerate(articles):
            formatted_input += f"- Articolo {i+1} [Fonte: {article.get('source', 'Sconosciuta')}]: {article['title']}\n"
            if article['description']:
                formatted_input += f"  Descrizione: {article['description'][:500]}...\n"
        
    # Se non c'è nulla, usciamo subito
    if total_articles == 0:
        st.warning("🚨 ATTENZIONE: Nessun articolo recente trovato in NESSUN feed RSS. Controlla che gli URL siano corretti o che ci siano notizie fresche (ultime 24h).")
        return None

    # --- 3. DEFINIZIONE DEL NUOVO SCHEMA JSON (Output TTS) ---
    final_digest_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "script_tts": types.Schema(
                type=types.Type.STRING, 
                description="L'intero testo del radiogiornale, formattato per la riproduzione vocale, senza markup Markdown o JSON."
            ),
            "titolo_digest": types.Schema(
                type=types.Type.STRING, 
                description="Un titolo conciso (massimo 10 parole) per il digest."
            )
        },
        required=["script_tts", "titolo_digest"]
    )
    
    # --- 4. PROMPT COMPLETO E CONFIGURAZIONE PER GEMINI ---

    system_instruction = f"""
    Sei un giornalista radiofonico professionista e molto conciso. Il tuo compito è creare lo script per un radiogiornale.
    Sintetizza i contenuti in un testo unico, scorrevole e narrativo, mantenendo il seguente ordine di importanza per le sezioni: Libano, Gaza, Medio Oriente, Italia, Mondo.
    Utilizza un tono neutro e informativo. Inizia con una breve introduzione (es. "Benvenuti al digest di Lino Bandi...") e concludi con una chiusura.
    NON USARE titoli Markdown (#, ##, ***) o elenchi puntati (*, -). La sintesi DEVE essere un testo fluido.
    """
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=final_digest_schema,
    )

    prompt = f"""
    Genera lo script TTS (Text-to-Speech) basandoti SOLO ed ESCLUSIVAMENTE sui seguenti articoli grezzi. 
    
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
**Ciao! Lino Bandi ti da nuovamente il benvenuto e vuole aiutarti a 
preparare lo script per il tuo radiogiornale quotidiano.**

Le fonti utilizzate sono: **Al Jazeera, Middle East Eye, Al Monitor, L'Orient Le Jour, Orient XXI, L'Espresso, Il Manifesto, Domani, Internazionale.**
""")

st.info("""
L'output è in formato testo continuo (TTS) pronto per essere copiato e incollato nel tuo servizio di sintesi vocale preferito.
""")
st.markdown("---")


# --- ESECUZIONE E DIGEST ---

if st.button("▶️ Genera il Radiogiornale Quotidiano", type="primary"):
    
    if not GEMINI_API_KEY:
        st.error("Impossibile procedere. La chiave GEMINI_API_KEY è mancante nei secrets.")
        st.stop()
        
    progress_bar = st.progress(0, text="Avvio della raccolta articoli e sintesi con AI...")
    
    # 1. RACCOLTA DATI GREZZI (solo da RSS)
    progress_bar.progress(30, text="1/3: Raccolta articoli dai feed RSS in corso...")
    raw_news_data = run_news_collection()
    
    # 2. SINTESI CON GEMINI
    final_digest = None
    if raw_news_data:
        progress_bar.progress(70, text="2/3: Sintesi e strutturazione con Gemini AI...")
        # Per debug, salviamo il conteggio degli articoli prima di chiamare Gemini
        total_articles = sum(len(d['articles']) for d in raw_news_data)
        
        # Chiamata alla sintesi
        if total_articles > 0:
             final_digest = summarize_with_gemini(raw_news_data) 
        # La funzione summarize_with_gemini gestisce il caso total_articles=0
    
    progress_bar.empty()
    
    # 3. VISUALIZZAZIONE DEL RISULTATO
    if final_digest:
        st.success("✅ Script del radiogiornale generato con successo!")
        
        titolo = final_digest.get('titolo_digest', 'Il Tuo Digest Quotidiano')
        script_tts = final_digest.get('script_tts', 'Errore nella generazione dello script.')
        
        st.header(f"🎙️ {titolo}")
        
        st.markdown("""
        ---
        #### Script Completo per la Sintesi Vocale (TTS)
        """)
        
        # Mostra lo script in un box di testo fisso (textarea)
        st.text_area(
            "Copia questo script per la riproduzione vocale (Text-to-Speech)", 
            script_tts, 
            height=400,
        )
        
        st.markdown("---")
        
    else:
        # Se final_digest è None, significa che c'è stato un problema (già segnalato da st.error/st.warning)
        pass
