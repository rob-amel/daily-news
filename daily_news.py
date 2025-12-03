import streamlit as st
import feedparser
import json
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from google.genai.errors import APIError
from gtts import gTTS
from io import BytesIO

# --- CONFIGURAZIONE E STILE ---

st.set_page_config(page_title="🌍 Daily News Digest AI", layout="centered")

# --- RECUPERO CHIAVE GEMINI E INIZIALIZZAZIONE VARIABILI ---
# Inizializza la variabile a None
GEMINI_API_KEY = None 
try:
    # Accesso diretto alla chiave dai secrets
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError:
    # Se la chiave non è nei secrets, mostriamo un warning, ma la variabile resta None.
    st.error("⚠️ KeyError: La chiave GEMINI_API_KEY non è stata trovata nei `secrets` di Streamlit.")

# Inizializzazione della variabile per evitare NameError
final_digest = None 


# ----------------------------------------------------------------------
# --- 📍 MAPPAZIONE IBRIDA DI TUTTE LE FONTI E QUERIES ---

# 1. RSS: Questi sono i feed che proviamo a leggere prima (base)
RSS_FEED_URLS = [
    "https://www.lorientlejour.com/rss/all.xml",
    "https://www.aljazeera.com/xml/rss/all.xml", 
    "https://www.middleeasteye.net/rss/all",
    "https://www.al-monitor.com/rss/news.xml", 
    "https://www.orientxxi.info/public/backend.php?lang=it",
    "https://rss.ilmanifesto.it/ilmanifesto.xml", 
    "https://www.domani.it/rss", 
    "https://espresso.repubblica.it/rss.xml",
    "https://www.internazionale.it/rss"
]

# 2. RICERCA MIRATA (per i siti che non hanno RSS affidabili)
SEARCH_DOMAINS = [
    "lorientlejour.com",
    "middleeasteye.net",
    "al-monitor.com",
    "orientxxi.info",
    "ilmanifesto.it",
    "domani.it",
    "espresso.repubblica.it",
    "internazionale.it"
]

# 3. Sezioni del digest con le parole chiave di ricerca
SECTIONS_MAPPING = {
    "Libano": "Libano OR Beirut OR Hezbolla", 
    "Gaza": "Gaza OR Rafah OR Cisgiordania", 
    "Medio Oriente (Siria, Palestina)": "Siria OR Palestina OR Cisgiordania OR Iran", 
    "Italia (Politica Interna)": "Governo Italia OR Legge Bilancio OR Elezioni Italia", 
    "Mondo (Principali)": "Notizie Principali Globali OR Crisi Internazionali"
}

# Genera le query di ricerca da passare a Gemini (pre-calcolate)
SEARCH_QUERIES_TO_RUN = []
for section, keywords in SECTIONS_MAPPING.items():
    for domain in SEARCH_DOMAINS:
        # Ricerca mirata: "[parole chiave] site:dominio.com"
        SEARCH_QUERIES_TO_RUN.append(f"{keywords} site:{domain}")
        
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_articles_via_rss(status_placeholder):
    """
    Legge TUTTI i feed RSS e raccoglie gli articoli recenti (ultime 48h).
    """
    articles = []
    # Usiamo 48 ore come rilassamento base
    yesterday = datetime.now() - timedelta(hours=48) 
    
    status_placeholder.info("🔎 Tentativo di raccolta da tutti i feed RSS (ultime 48h)...")

    for url in RSS_FEED_URLS:
        try:
            feed = feedparser.parse(url)
            source = feed.feed.title if hasattr(feed.feed, 'title') else url.split('/')[2] 
            
            recent_count = 0
            for entry in feed.entries:
                is_recent = True
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_date = datetime(*entry.published_parsed[:6])
                    if published_date < yesterday:
                        is_recent = False

                if is_recent:
                    description = getattr(entry, 'summary', None) or getattr(entry, 'description', None) or ""
                    articles.append({
                        'title': entry.title,
                        'description': description,
                        'url': entry.link,
                        'source': source,
                        'method': 'RSS'
                    })
                    recent_count += 1
            
            if recent_count > 0:
                 status_placeholder.markdown(f"&nbsp;&nbsp;✅ Trovati **{recent_count}** articoli da **{source}** (via RSS).")

        except Exception:
            continue

    total_articles = len(articles)
    if total_articles < 5:
        # Warning se i dati RSS sono scarsi
        status_placeholder.warning(f"⚠️ **ATTENZIONE:** RSS insufficienti ({total_articles} articoli). La sintesi sarà fortemente dipendente dalla Ricerca AI.")
    else:
        status_placeholder.success(f"✔️ Raccolta RSS completata: Totale **{total_articles}** articoli.")
        
    return articles

def run_news_collection(status_placeholder):
    # La parte di ricerca è nel prompt di Gemini
    return get_articles_via_rss(status_placeholder)


def summarize_with_gemini(rss_articles, search_queries, status_placeholder):
    """
    Invia gli articoli RSS e le query di ricerca a Gemini, con un prompt che forza
    l'esecuzione della ricerca e gestisce l'input RSS vuoto.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE ---
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    # --- 2. PREPARAZIONE DATI GREZZI E CONTROLLO ---
    
    # LOGICA DI FORZA MAGGIORE: Se RSS è vuoto o quasi (meno di 5), lo ignoriamo nel JSON
    if len(rss_articles) < 5:
        formatted_rss_json = "[]"
        rss_warning = "ATTENZIONE: Gli articoli RSS forniti erano insufficienti (< 5) e sono stati rimossi dall'input JSON per forzare la Ricerca AI."
    else:
        formatted_rss_json = json.dumps(rss_articles, indent=2)
        rss_warning = "Gli articoli RSS forniti sono sufficienti e inclusi."


    if not rss_articles and not search_queries:
         status_placeholder.error("⚠️ Nessun dato da processare (RSS vuoti e nessuna query di ricerca da eseguire).")
         return None 
        
    # --- 3. DEFINIZIONE DELLO SCHEMA JSON ---
    final_digest_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "script_tts": types.Schema(
                type=types.Type.STRING, 
                description="L'intero testo del radiogiornale. Deve essere lungo almeno 750 parole per garantire una durata di 5 minuti di parlato. Non usare titoli o elenchi."
            ),
            "titolo_digest": types.Schema(
                type=types.Type.STRING, 
                description="Un titolo conciso (massimo 10 parole) per il digest."
            )
        },
        required=["script_tts", "titolo_digest"]
    )
    
    # --- 4. PROMPT COMPLETO E CONFIGURAZIONE PER GEMINI ---
    
    sections_list = ", ".join(SECTIONS_MAPPING.keys())

    system_instruction = f"""
    Sei un giornalista radiofonico professionista e molto dettagliato. 
    
    **TASK PRIORITARIO: ESEGUIRE LE RICERCHE**
    A causa dell'inaffidabilità dei feed RSS, il tuo primo e fondamentale compito è **ESEGUIRE TUTTE LE QUERY** fornite utilizzando lo strumento Google Search Tool per raccogliere dati aggiornati e mirati dai siti richiesti.
    
    **TASK SECONDARIO:**
    1. **COMBINA** i risultati della Ricerca AI con gli articoli RSS (se presenti).
    2. **FILTRA** e **SINTETIZZA** le informazioni rilevanti per ciascuna delle seguenti sezioni, presentandole in questo ordine: {sections_list}.
    
    **REQUISITI:**
    * **LUNGHEZZA:** Lo script deve essere descrittivo e approfondito per raggiungere una lunghezza minima di 750 parole totali (circa 5 minuti di parlato).
    * **FORMATO:** Inizia con una breve introduzione e concludi con una chiusura. NON USARE titoli Markdown o elenchi puntati.
    """
    
    # CORREZIONE DEL VALIDATION ERROR: definisce il tool in formato types.Tool
    search_tool = types.Tool(google_search={}) 
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=final_digest_schema,
        tools=[search_tool] 
    )

    prompt = f"""
    Genera lo script TTS (Text-to-Speech) basandoti sui dati combinati di RSS e ricerca.
    
    ---
    **AVVISO DATI RSS:** {rss_warning}
    ---
    
    ARTICOLI RSS TROVATI (Base dati iniziale, Formato JSON):
    ---
    {formatted_rss_json}
    ---
    
    QUERY DI RICERCA DA ESEGUIRE PER L'INTEGRAZIONE (Da usare con Google Search Tool):
    ---
    {json.dumps(search_queries, indent=2)}
    ---
    """
    
    status_placeholder.info("🧠 Avvio sintesi con Gemini AI. **Forzata l'esecuzione della Ricerca Mirata** per l'integrazione dati...")

    # --- 5. CHIAMATA ALL'API ---
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=config,
        )
        
        # Pulizia e decodifica del JSON
        json_string = response.text.strip()
        if json_string.startswith("```json"):
            json_string = json_string[7:]
        if json_string.endswith("```"):
            json_string = json_string[:-3]
            
        digest_data = json.loads(json_string)
        return digest_data

    except Exception as e:
        status_placeholder.error(f"❌ Errore durante la sintesi AI: {e}. Controlla i logs per dettagli.")
        return None


# ----------------------------------------------------------------------
# --------------------- INTERFACCIA STREAMLIT (Frontend) ---------------
# ----------------------------------------------------------------------

# --- LOGO E TITOLO ---
col_icon, col_title = st.columns([0.5, 6.5]) 

with col_icon:
    st.markdown("## 🌍") 

with col_title:
    st.title("Daily News - Radiogiornale TTS") 

st.markdown("---")

st.markdown("""
**Ciao! Lino Bandi ti aiuta a preparare lo script del tuo radiogiornale.**

Il sistema usa la logica **Ibrida Forzata**: legge i feed RSS (base) e chiede a **Gemini** di eseguire ricerche mirate (`site:`) per integrare la copertura sui domini da te scelti.
""")

st.info("""
Lo script finale è ottimizzato per una durata di circa **5 minuti** di parlato e include la riproduzione audio automatica.
""")
st.markdown("---")


# --- ESECUZIONE E DIGEST ---

if st.button("▶️ Genera il Radiogiornale Quotidiano", type="primary"):
    
    # CHECK CORRETTO: Verifica se la variabile GEMINI_API_KEY ha un valore
    if not GEMINI_API_KEY:
        st.error("Impossibile procedere. La chiave GEMINI_API_KEY è mancante o non è stata caricata.")
        st.stop()
        
    # Placeholder per visualizzare i log di debug in tempo reale
    status_container = st.container()
    
    # 1. RACCOLTA DATI GREZZI (RSS)
    rss_articles = run_news_collection(status_container)
    
    # 2. SINTESI CON GEMINI (Invia gli articoli RSS e le query di ricerca)
    final_digest = summarize_with_gemini(rss_articles, SEARCH_QUERIES_TO_RUN, status_container)
    
    # 3. VISUALIZZAZIONE DEL RISULTATO
    if final_digest:
        st.success("✅ Script del radiogiornale generato con successo!")
        
        titolo = final_digest.get('titolo_digest', 'Il Tuo Digest Quotidiano')
        script_tts = final_digest.get('script_tts', 'Errore nella generazione dello script.')
        
        st.header(f"🎙️ {titolo}")
        
        st.markdown("---")
        
        # --- BLOCCO RIPRODUZIONE AUDIO ---
        st.subheader("Ascolta il Digest")
        
        try:
            tts = gTTS(
                text=script_tts, 
                lang='it', 
                tld='com',
                slow=False
            )
            
            audio_fp = BytesIO()
            tts.write_to_fp(audio_fp)
            audio_fp.seek(0)
            
            st.audio(audio_fp, format='audio/mp3')
            st.info("Riproduzione automatica avviata. Se non parte, premi play nel widget sopra.")
            
        except Exception as e:
            st.error(f"Impossibile generare l'audio: {e}")
            
        # --- FINE BLOCCO AUDIO ---

        st.markdown("""
        ---
        #### Script Completo (per riferimento)
        """)
        
        # Stampa il conteggio delle parole
        word_count = len(script_tts.split())
        st.markdown(f"*(Lunghezza stimata: **{word_count} parole** — circa {round(word_count / 150, 1)} minuti di parlato)*")
        
        # Mostra lo script
        st.text_area(
            "Script del radiogiornale", 
            script_tts, 
            height=300,
        )
        
        st.markdown("---")
    else:
        # Se final_digest è None, significa che c'è stato un problema nella sintesi o nella raccolta
        pass
