import streamlit as st
import feedparser
import json
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from google.genai.errors import APIError

# --- IMPORT NECESSARI PER L'AUDIO ---
from gtts import gTTS
from io import BytesIO

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
# --- 📍 MAPPAZIONE IBRIDA DI TUTTE LE FONTI E QUERIES ---

# 1. RSS: Questi sono i feed che proviamo a leggere prima (se funzionano)
RSS_FEED_URLS = [
    "https://www.lorientlejour.com/rss/all.xml",
    "https://www.aljazeera.com/xml/rss/all.xml", # Funziona, da usare!
    "https://www.middleeasteye.net/rss/all",
    "https://www.al-monitor.com/rss/news.xml", 
    "https://www.orientxxi.info/public/backend.php?lang=it",
    "https://rss.ilmanifesto.it/ilmanifesto.xml", 
    "https://www.domani.it/rss", 
    "https://espresso.repubblica.it/rss.xml",
    "https://www.internazionale.it/rss"
]

# 2. RICERCA MIRATA (per i siti che non hanno RSS affidabili)
# Questi domini verranno usati per le ricerche Google mirate (site:dominio.com)
SEARCH_DOMAINS = [
    "lorientlejour.com",
    "middleeasteye.net",
    "al-monitor.com",
    "orientxxi.info",
    "ilmanifesto.it",
    "domani.it",
    "espresso.repubblica.it",
    "internazionale.it"
    # Al Jazeera e altri che funzionano bene via RSS non sono qui.
]

# 3. Sezioni del digest con le parole chiave di ricerca
SECTIONS_MAPPING = {
    "Libano": "Libano OR Beirut OR Hezbolla", 
    "Gaza": "Gaza OR Rafah OR Cisgiordania", 
    "Medio Oriente (Siria, Palestina)": "Siria OR Palestina OR Cisgiordania OR Iran", 
    "Italia (Politica Interna)": "Governo Italia OR Legge Bilancio OR Elezioni Italia", 
    "Mondo (Principali)": "Notizie Principali Globali OR Crisi Internazionali"
}
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_articles_via_rss(status_placeholder):
    """
    Tenta di leggere TUTTI i feed RSS e raccoglie gli articoli recenti (ultime 48h).
    """
    articles = []
    # Usiamo 48 ore come rilassamento base, dato che hai escluso 24 ore.
    yesterday = datetime.now() - timedelta(hours=48) 
    
    status_placeholder.info("🔎 Fase 1/2: Tentativo di raccolta da tutti i feed RSS...")

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
            
            # Solo log per i feed che danno risultati
            if recent_count > 0:
                 status_placeholder.markdown(f"&nbsp;&nbsp;✅ Trovati **{recent_count}** articoli da **{source}** (via RSS).")

        except Exception as e:
            status_placeholder.warning(f"❌ Errore/Fallimento RSS per {url}: {e}")
            continue

    return articles

def get_articles_via_search(rss_articles, status_placeholder):
    """
    Esegue query Google Search mirate (site:dominio.com) per integrare i dati mancanti.
    """
    
    status_placeholder.info("🌐 Fase 2/2: Integrazione con Google Search mirata per le sezioni...")
    
    # Crea un set di tutti i link trovati via RSS per evitare duplicati
    existing_urls = {a['url'] for a in rss_articles}
    
    # Inizializza la lista degli articoli finali con quelli già trovati via RSS
    final_articles = rss_articles.copy()

    for section, keywords in SECTIONS_MAPPING.items():
        search_queries = []
        
        # Genera query per ogni dominio e per la sezione corrente
        for domain in SEARCH_DOMAINS:
            # Ricerca mirata: "[parole chiave] site:dominio.com"
            search_queries.append(f"{keywords} site:{domain}")
        
        # Esegui la ricerca
        try:
            # Chiamata allo strumento di ricerca Google
            search_results = google:search.search(queries=search_queries)

            # Processa i risultati della ricerca
            if search_results and search_results.result:
                # Il risultato è una stringa JSON di risultati
                results = json.loads(search_results.result)
                search_count = 0
                
                for result in results:
                    url = result.get('url')
                    if url and url not in existing_urls:
                        # Estrai la fonte dal dominio
                        source_name = result.get('source', url.split('/')[2])
                        
                        final_articles.append({
                            'title': result.get('title'),
                            'description': result.get('snippet', ''),
                            'url': url,
                            'source': source_name,
                            'method': 'Search'
                        })
                        existing_urls.add(url)
                        search_count += 1

                if search_count > 0:
                    status_placeholder.markdown(f"&nbsp;&nbsp;⭐ Aggiunti **{search_count}** articoli per la sezione **{section}** (via Search).")

        except Exception as e:
            status_placeholder.error(f"❌ Errore durante la ricerca Google per la sezione {section}: {e}")
            
    return final_articles

# La funzione run_news_collection ora è il punto di ingresso per il processo ibrido
def run_news_collection(status_placeholder):
    
    # 1. Raccolta via RSS (Base)
    rss_articles = get_articles_via_rss(status_placeholder)
    
    # 2. Raccolta/Integrazione via Search (Supplementare)
    all_articles = get_articles_via_search(rss_articles, status_placeholder)
    
    total_articles = len(all_articles)

    if total_articles == 0:
        status_placeholder.error("⚠️ **FALLIMENTO TOTALE:** Nessun articolo trovato né via RSS né tramite ricerca mirata.")
        return []
    else:
        status_placeholder.success(f"✔️ Raccolta completata: Totale **{total_articles}** articoli per la sintesi.")

    return all_articles


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
    
    sections_list = ", ".join(SECTIONS_MAPPING.keys())

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

# --- LOGO E TITOLO ---
col_icon, col_title = st.columns([0.5, 6.5]) 

with col_icon:
    st.markdown("## 🌍") 

with col_title:
    st.title("Daily News - Radiogiornale TTS") 

st.markdown("---")

st.markdown("""
**Ciao! Lino Bandi ti aiuta a preparare lo script del tuo radiogiornale.**

Il sistema ora utilizza una **logica ibrida**: prova i feed RSS (più puliti) e li integra con una ricerca mirata Google (**Search Tool**) sui domini specifici per massimizzare la copertura.
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
    
    # 1. RACCOLTA DATI GREZZI (Ibrida)
    all_articles = run_news_collection(status_container)
    
    # 2. SINTESI CON GEMINI
    final_digest = summarize_with_gemini(all_articles, status_container)
    
    # Pulizia del container di stato al termine
    # Lasciamo qui i messaggi finali di successo/fallimento della raccolta (Punto 1)
    
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
        # Se final_digest è None, significa che c'è stato un problema nella sintesi o nella raccolta (già segnalato)
        pass
