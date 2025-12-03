import streamlit as st
import feedparser
import json
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from google.genai.errors import APIError
import streamlit as st
import feedparser
# ... altri import ...

# --- CORREZIONE: Inizializzazione della variabile (QUESTA RIGA È NECESSARIA!) ---
final_digest = None 
# ----------------------------------------------------------------------------------

# --- CONFIGURAZIONE E STILE ---

st.set_page_config(page_title="🌍 Daily News Digest AI", layout="centered")

# --- RECUPERO CHIAVE GEMINI ---
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError:
    st.error("⚠️ Chiave GEMINI_API_KEY non trovata nei secrets. L'API di sintesi fallirà.")

# ----------------------------------------------------------------------
# --- 📍 MAPPAZIONE FISSA DELLE FONTI RSS ---
# Nota: Questi sono feed RSS generali. La specificità (Libano/Gaza) dipende dalla presenza
# di articoli recenti in questi feed e dalla capacità di Gemini di filtrarli.
# La sezione "Fonti Personalizzate" è stata riutilizzata per la mappatura fissa.

FEED_MAPPING = {
    "Libano": [
        "https://www.lorientlejour.com/rss/all.xml", 
        "https://www.aljazeera.com/xml/rss/all.xml"
    ],
    "Gaza": [
        "https://www.middleeasteye.net/rss/all", 
        "https://www.aljazeera.com/xml/rss/all.xml"
    ],
    "Medio Oriente (Siria, Palestina)": [
        "https://www.al-monitor.com/rss/news.xml", 
        "https://www.orientxxi.info/public/backend.php?lang=it",
    ],
    "Italia (Politica Interna)": [
        "https://rss.ilmanifesto.it/ilmanifesto.xml", 
        "https://www.domani.it/rss", 
        "https://espresso.repubblica.it/rss.xml" 
    ],
    "Mondo (Principali)": [
        "https://www.internazionale.it/rss", 
        "https://www.aljazeera.com/xml/rss/all.xml"
    ]
}
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_news_from_rss(section_name, status_placeholder):
    """
    Legge tutti i feed RSS per una sezione data e raccoglie gli articoli recenti.
    Aggiunto status_placeholder per il debug in tempo reale.
    """
    articles = []
    feed_list = FEED_MAPPING.get(section_name, [])
    yesterday = datetime.now() - timedelta(hours=24)
    
    # Aggiorna lo stato: Inizio scansione sezione
    status_placeholder.info(f"🔎 Avvio scansione feed per la sezione: **{section_name}**")

    for url in feed_list:
        try:
            feed = feedparser.parse(url)
            source = feed.feed.title if hasattr(feed.feed, 'title') else url.split('/')[2]
            
            # Aggiorna lo stato: Scansione sito
            status_placeholder.markdown(f"&nbsp;&nbsp;→ Scansione: **{source}** ({len(feed.entries)} articoli disponibili)")
            
            # Contiamo gli articoli recenti trovati in questo feed
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
            
            # Aggiorna lo stato: Risultato del singolo sito
            status_placeholder.markdown(f"&nbsp;&nbsp;✅ Trovati **{recent_count}** articoli recenti da **{source}**.")
            
        except Exception as e:
            # Aggiorna lo stato: Errore del singolo sito
            status_placeholder.error(f"❌ Errore nella lettura del feed RSS {url} ({source}): {e}")
            continue

    # Aggiorna lo stato: Totale della sezione
    if len(articles) == 0:
        status_placeholder.warning(f"⚠️ **ATTENZIONE:** Nessun articolo recente trovato per **{section_name}**.")
    else:
        status_placeholder.success(f"✔️ Raccolta completata per **{section_name}**: Totale **{len(articles)}** articoli.")

    return articles


def run_news_collection(status_placeholder):
    """
    Esegue la raccolta delle notizie da tutti i feed definiti.
    """
    raw_digest_data = []
    sections_order = ["Libano", "Gaza", "Medio Oriente (Siria, Palestina)", 
                      "Italia (Politica Interna)", "Mondo (Principali)"]
    
    for section_title in sections_order:
        # Passiamo il placeholder allo step specifico di raccolta
        articles = get_news_from_rss(section_title, status_placeholder) 
        raw_digest_data.append({"section": section_title, "articles": articles})

    return raw_digest_data


def summarize_with_gemini(raw_digest_data, status_placeholder):
    """
    Invia i dati grezzi delle notizie a Gemini per sintetizzare in un unico script vocale.
    Aumentato il requisito di lunghezza per uno script di 5 minuti.
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
        
        # Aggiungiamo intestazioni chiare per la strutturazione da parte di Gemini
        formatted_input += f"\n\n### SEZIONE: {section_title} ({len(articles)} ARTICOLI DA SINTETIZZARE)\n"
        if not articles:
            formatted_input += "NESSUNA NOTIZIA RECENTE TROVATA IN QUESTA SEZIONE.\n"
            continue
        
        for i, article in enumerate(articles):
            formatted_input += f"- Articolo {i+1} [Fonte: {article.get('source', 'Sconosciuta')}]: {article['title']}\n"
            if article['description']:
                formatted_input += f"  Descrizione: {article['description'][:500]}...\n"
        
    if total_articles == 0:
        return None

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

    system_instruction = f"""
    Sei un giornalista radiofonico professionista e molto dettagliato. Il tuo compito è creare lo script per un radiogiornale.
    Sintetizza i contenuti in un testo unico, scorrevole e narrativo, mantenendo il seguente ordine di importanza per le sezioni: Libano, Gaza, Medio Oriente, Italia, Mondo.
    **È FONDAMENTALE che lo script sia descrittivo e approfondito per raggiungere una lunghezza minima di 750 parole totali, equivalente a circa 5 minuti di parlato.**
    Utilizza un tono neutro e informativo. Inizia con una breve introduzione e concludi con una chiusura (es. "Benvenuti al digest di Lino Bandi..." e "Il digest di oggi termina qui.").
    NON USARE titoli Markdown (#, ##, ***) o elenchi puntati (*, -). Il testo deve essere narrativo e fluido.
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

# --- LOGO E TITOLO ---
col_icon, col_title = st.columns([0.5, 6.5]) 

with col_icon:
    st.markdown("## 🌍") 

with col_title:
    st.title("Daily News - Radiogiornale TTS") 

st.markdown("---")

st.markdown("""
**Ciao! Lino Bandi ti aiuta a preparare lo script del tuo radiogiornale.**

Le fonti utilizzate sono: **Al Jazeera, Middle East Eye, Al Monitor, L'Orient Le Jour, Orient XXI, L'Espresso, Il Manifesto, Domani, Internazionale.**
""")

st.info("""
Lo script finale è ottimizzato per una durata di circa **5 minuti** di parlato e utilizza i flussi RSS (legali) dei siti da te scelti.
""")
st.markdown("---")


# --- ESECUZIONE E DIGEST ---

if st.button("▶️ Genera il Radiogiornale Quotidiano", type="primary"):
    
    if not GEMINI_API_KEY:
        st.error("Impossibile procedere. La chiave GEMINI_API_KEY è mancante nei secrets.")
        st.stop()
        
    # Placeholder per visualizzare i log di debug in tempo reale
    status_container = st.container()
    
    status_container.info("Inizio processo: Raccolta dati...")
    
    # 1. RACCOLTA DATI GREZZI (solo da RSS)
    raw_news_data = run_news_collection(status_container)
    
    # 2. SINTESI CON GEMINI
    final_digest = summarize_with_gemini(raw_news_data, status_container)
    
    # Pulizia del container di stato al termine
    status_container.empty()
    
    # 3. VISUALIZZAZIONE DEL RISULTATO
if final_digest:
    st.success("✅ Script del radiogiornale generato con successo!")

    titolo = final_digest.get('titolo_digest', 'Il Tuo Digest Quotidiano')
    script_tts = final_digest.get('script_tts', 'Errore nella generazione dello script.')

    st.header(f"🎙️ {titolo}")

    st.markdown("---")

    # --- BLOCCO AGGIUNTO PER LA RIPRODUZIONE AUDIO ---
    st.subheader("Ascolta il Digest")

    try:
        tts = gTTS(
            text=script_tts, 
            lang='it', 
            tld='com', # Usa 'com' o 'it' a seconda della qualità della voce
            slow=False
        )

        # Salvataggio dell'audio in memoria
        audio_fp = BytesIO()
        tts.write_to_fp(audio_fp)

        # Reset del puntatore prima di leggere il contenuto
        audio_fp.seek(0)

        # Widget di riproduzione audio di Streamlit
        st.audio(audio_fp, format='audio/mp3')

        st.info("Riproduzione automatica avviata. Se non parte, premi play nel widget sopra.")

    except Exception as e:
        st.error(f"Impossibile generare l'audio: {e}")
        st.warning("Verifica la connessione internet o i requisiti di gTTS.")

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
    pass

