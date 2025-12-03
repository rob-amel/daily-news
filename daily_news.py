import streamlit as st
import feedparser
import json
import re # Necessario per la pulizia del JSON e il parsing
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
    # Accesso diretto alla chiave dai secrets di Streamlit
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError:
    # L'errore sarà gestito nel blocco principale, qui solo per inizializzazione
    pass 

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
    return get_articles_via_rss(status_placeholder)


def summarize_with_gemini(rss_articles, search_queries, status_placeholder):
    """
    Invia gli articoli RSS e le query di ricerca a Gemini, con un prompt che forza
    l'esecuzione della ricerca e gestisce l'input RSS vuoto.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE ---
    try:
        # Verifica della chiave API
        if not GEMINI_API_KEY:
            status_placeholder.error("Errore: Chiave API Gemini non trovata. Controlla i secrets di Streamlit.")
            return None
            
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        status_placeholder.error(f"Errore di inizializzazione del client Gemini: {e}")
        return None

    # --- 2. PREPARAZIONE DATI GREZZI E CONTROLLO ---
    
    # LOGICA DI FORZA MAGGIORE: Se RSS è vuoto o quasi (meno di 5), lo ignoriamo nel JSON
    if len(rss_articles) < 5:
        formatted_rss_json = "[]"
        rss_warning = "ATTENZIONE: Gli articoli RSS forniti erano insufficienti (< 5) e sono stati rimossi dall'input JSON per forzare la Ricerca AI."
    else:
        try:
            formatted_rss_json = json.dumps(rss_articles, indent=2)
        except TypeError:
            formatted_rss_json = "[[JSON SERIALIZATION ERROR: Check article objects]]"
        rss_warning = "Gli articoli RSS forniti sono sufficienti e inclusi."


    if not rss_articles and not search_queries:
         status_placeholder.error("⚠️ Nessun dato da processare (RSS vuoti e nessuna query di ricerca da eseguire).")
         return None 
        
    # --- 4. PROMPT COMPLETO E CONFIGURAZIONE PER GEMINI ---
    
    sections_list = ", ".join(SECTIONS_MAPPING.keys())

    # Stringa multiriga con triple virgolette e istruzioni vincolanti (Lunghezza, Tempo, JSON)
    system_instruction = f"""
    Sei un giornalista radiofonico professionista, preciso e **MOLTO CONCISO**.
    
    **OBIETTIVO ASSOLUTO: La lunghezza totale dello script DEVE essere compresa tra 700 e 800 parole per garantire una durata MASSIMA di 5 minuti di parlato.** IGNORA qualsiasi informazione che ti porterebbe a superare questo limite.
    
    **TASK PRIORITARIO: ESEGUIRE LE RICERCHE**
    A causa dell'inaffidabilità dei feed RSS, il tuo primo e fondamentale compito è ESEGUIRE TUTTE LE QUERY fornite utilizzando lo strumento Google Search Tool per raccogliere dati.
    
    **TASK DI SINTESI CRITICO:**
    1. **FILTRO TEMPORALE:** Analizza e seleziona **SOLO** le notizie, gli eventi e gli aggiornamenti più significativi avvenuti nelle **ultime 24-48 ore**. Scarta qualsiasi informazione storica o pregressa che non sia essenziale per contestualizzare gli *ultimi* sviluppi.
    2. **FOCUS:** Il tuo obiettivo è fornire un **AGGIORNAMENTO QUOTIDIANO** e conciso delle vicende più recenti, concentrandoti su ciò che è *nuovo* rispetto al giorno precedente.
    3. **COMBINA** i risultati della Ricerca AI e gli articoli RSS.
    4. **SINTETIZZA** le informazioni per ciascuna delle seguenti sezioni, presentandole in questo ordine: {sections_list}.
    
    **REQUISITI:**
    * **LUNGHEZZA:** Lo script DEVE rispettare il limite **MASSIMO di 800 parole**.
    * **VINCOLO JSON CRITICO:** Assicurati che ogni carattere di virgoletta doppia (") all'interno dei valori delle stringhe ("script_tts" e "titolo_digest") sia correttamente *escaped* (ovvero, deve diventare \"). Tutti i caratteri di newline (\n) devono essere rappresentati come \\n.
    * **FORMATO RISPOSTA ASSOLUTO:** La risposta DEVE essere SOLTANTO un oggetto JSON valido racchiuso in un blocco di codice markdown (```json ... ```) e non DEVE contenere alcun testo di preambolo o spiegazione. Le chiavi JSON obbligatorie sono "script_tts" e "titolo_digest".
    """
    
    search_tool = types.Tool(google_search={}) 
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
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
        
        raw_text = response.text
        
        # --- PARSING AGGRESSIVO E ROBUSTO PER GESTIRE IL JSON (FIX DEFINITIVO) ---
        
        json_string = raw_text.strip() 
        
        # 1. Usa un'espressione regolare per trovare il blocco JSON racchiuso in ```json...```
        match = re.search(r'```json\s*(\{.*\})\s*```', json_string, re.DOTALL)
        
        if match:
            # Estrae solo il contenuto JSON
            json_string = match.group(1).strip()
        
        # 2. PULIZIA FINALE AGGIUNTIVA: Rimuove newlines e caratteri di controllo che rompono JSON se non scappati
        json_string = json_string.replace('\n', '')
        json_string = re.sub(r'[\x00-\x1F\x7F]', '', json_string)
        
        # 3. Tenta di caricare il JSON pulito
        try:
            digest_data = json.loads(json_string)
            return digest_data
        except json.JSONDecodeError as e:
            # Se fallisce, mostriamo l'errore finale e la risposta grezza non riparata.
            status_placeholder.error(f"❌ FALLIMENTO DECODIFICA JSON: Il modello non ha fornito JSON valido. Errore: {e}")
            st.code(f"RISPOSTA GREZZA DEL MODELLO (PULITA):\n{json_string}", language="json")
            return None


    except APIError as e:
        status_placeholder.error(f"❌ Errore API Gemini: {e}. Controlla i permessi e la validità della chiave API.")
        return None
    except Exception as e:
        status_placeholder.error(f"❌ Errore critico durante la sintesi AI: {e}.")
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
Lo script finale è ottimizzato per una durata di circa **5 minuti** di parlato (max 800 parole).
""")
st.markdown("---")


# --- ESECUZIONE E DIGEST ---

if st.button("▶️ Genera il Radiogiornale Quotidiano", type="primary"):
    
    # CHECK CORRETTO: Verifica se la variabile GEMINI_API_KEY ha un valore
    if not GEMINI_API_KEY:
        st.error("Impossibile procedere. La chiave **GEMINI_API_KEY** è mancante o non è stata caricata dai `secrets`.")
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
        
        # --- BLOCCO RIPRODUZIONE AUDIO (con gTTS) ---
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
            st.error(f"Impossibile generare l'audio (gTTS): {e}")
            
        # --- FINE BLOCCO AUDIO ---

        st.markdown("""
        ---
        #### Script Completo (per riferimento)
        """)
        
        # Stampa il conteggio delle parole
        word_count = len(script_tts.split())
        st.markdown(f"*(Lunghezza stimata: **{word_count} parole** — Obiettivo: 700-800 parole)*")
        
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
