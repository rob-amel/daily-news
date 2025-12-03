import streamlit as st
import feedparser
import json
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from google.genai.errors import APIError

# --- CONFIGURAZIONE E STILE ---

st.set_page_config(page_title="🌍 Daily News Digest AI", layout="centered")

# --- RECUPERO CHIAVE GEMINI E INIZIALIZZAZIONE VARIABILI ---
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError:
    st.error("⚠️ Chiave GEMINI_API_KEY non trovata nei secrets. L'API di sintesi fallirà.")

# Inizializzazione della variabile per evitare NameError
final_digest = None 


# ----------------------------------------------------------------------
# --- 📍 MAPPAZIONE UNICA DI TUTTE LE FONTI RSS ---
# Tutti i feed sono raccolti in una lista unica. Sarà Gemini a filtrare per argomento.

ALL_FEED_URLS = [
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

# Definiamo l'ordine e il focus delle sezioni per il prompt a Gemini
SECTIONS_ORDER = [
    "Libano", 
    "Gaza", 
    "Medio Oriente (Siria, Palestina)", 
    "Italia (Politica Interna)", 
    "Mondo (Principali)"
]
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_all_news_from_rss(status_placeholder):
    """
    Legge TUTTI i feed RSS e raccoglie tutti gli articoli recenti in una singola lista.
    """
    articles = []
    yesterday = datetime.now() - timedelta(hours=24)
    total_recent_articles = 0
    
    status_placeholder.info("🔎 Avvio scansione di TUTTI i feed RSS specificati...")

    for url in ALL_FEED_URLS:
        try:
            feed = feedparser.parse(url)
            source = feed.feed.title if hasattr(feed.feed, 'title') else url.split('/')[2]
            
            status_placeholder.markdown(f"&nbsp;&nbsp;→ Scansione: **{source}** ({len(feed.entries)} articoli disponibili)")
            
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
                        'source': source
                    })
                    recent_count += 1
            
            total_recent_articles += recent_count
            status_placeholder.markdown(f"&nbsp;&nbsp;✅ Trovati **{recent_count}** articoli recenti da **{source}**.")
            
        except Exception as e:
            status_placeholder.error(f"❌ Errore nella lettura del feed RSS {url} ({source}): {e}")
            continue

    if total_recent_articles == 0:
        status_placeholder.warning("⚠️ **ATTENZIONE:** Nessun articolo recente trovato in TUTTI i feed RSS. Impossibile generare il digest.")
    else:
        status_placeholder.success(f"✔️ Raccolta completata: Totale **{total_recent_articles}** articoli recenti da tutte le fonti.")
        
    return articles


def summarize_with_gemini(all_articles, status_placeholder):
    """
    Invia TUTTI i dati grezzi a Gemini per filtrare, sintetizzare e strutturare.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE ---
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    # --- 2. PREPARAZIONE DATI GREZZI E CONTROLLO ---
    
    # Dati grezzi come lista JSON per un input più pulito a Gemini
    formatted_input_json = json.dumps(all_articles, indent=2)

    if not all_articles:
        return None # Già gestito in get_all_news_from_rss
        
    # --- 3. DEFINIZIONE DELLO SCHEMA JSON (Output TTS) ---
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
    
    sections_list = ", ".join(SECTIONS_ORDER)

    system_instruction = f"""
    Sei un giornalista radiofonico professionista e molto dettagliato. Il tuo compito è creare lo script per un radiogiornale.
    Hai una lista unica di articoli grezzi da diverse fonti. Devi **filtrare e sintetizzare** gli articoli rilevanti per ciascuna delle seguenti sezioni e presentarle in questo ordine: {sections_list}.
    
    **È FONDAMENTALE che lo script sia descrittivo e approfondito per raggiungere una lunghezza minima di 750 parole totali, equivalente a circa 5 minuti di parlato.**
    Utilizza un tono neutro e informativo. Inizia con una breve introduzione e concludi con una chiusura.
    NON USARE titoli Markdown (#, ##, ***) o elenchi puntati (*, -). Il testo deve essere narrativo e fluido.
    """
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=final_digest_schema,
    )

    prompt = f"""
    Genera lo script TTS (Text-to-Speech) basandoti SOLO ed ESCLUSIVAMENTE sui seguenti articoli grezzi, filtrando per le sezioni richieste:
    
    ARTICOLI GREZZI TOTALI (Formato JSON):
    ---
    {formatted_input_json}
    ---
    """
    
    status_placeholder.info("🧠 Avvio sintesi con Gemini AI. Richiesto script lungo circa 5 minuti...")

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

    except Exception as e:
        status_placeholder.error(f"❌ Errore durante la sintesi AI: {e}. Controlla i logs per dettagli.")
        return None


# ----------------------------------------------------------------------
# --------------------- INTERFACCIA STREAMLIT (Frontend) ---------------
# ----------------------------------------------------------------------

# --- IMPORT NECESSARI PER L'AUDIO ---
from gtts import gTTS
from io import BytesIO

# --- LOGO E TITOLO ---
col_icon, col_title = st.columns([0.5, 6.5]) 

with col_icon:
    st.markdown("## 🌍") 

with col_title:
    st.title("Daily News - Radiogiornale TTS") 

st.markdown("---")

st.markdown("""
**Ciao! Lino Bandi ti aiuta a preparare lo script del tuo radiogiornale.**

Il sistema ora utilizza **tutte le fonti specificate** per ogni singola sezione, chiedendo a Gemini di filtrare in modo intelligente.
""")

st.info("""
Lo script finale è ottimizzato per una durata di circa **5 minuti** di parlato e include la riproduzione audio automatica.
""")
st.markdown("---")


# --- ESECUZIONE E DIGEST ---

if st.button("▶️ Genera il Radiogiornale Quotidiano", type="primary"):
    
    if not GEMINI_API_KEY:
        st.error("Impossibile procedere. La chiave GEMINI_API_KEY è mancante nei secrets.")
        st.stop()
        
    # Placeholder per visualizzare i log di debug in tempo reale
    status_container = st.container()
    
    # 1. RACCOLTA DATI GREZZI (solo da RSS)
    all_articles = get_all_news_from_rss(status_container)
    
    # 2. SINTESI CON GEMINI
    final_digest = summarize_with_gemini(all_articles, status_container)
    
    # Pulizia del container di stato al termine
    status_container.empty()
    
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
            # gTTS non ha bisogno di essere definita ogni volta, ma è il modo più semplice
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
        # La gestione degli errori è inclusa nelle funzioni
        pass
