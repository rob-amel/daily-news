import streamlit as st
import requests
import json
import os
from datetime import datetime, timedelta
from io import BytesIO 
from google import genai
from google.genai import types
from google.genai.errors import APIError

# --- CONFIGURAZIONE E STILE ---

st.set_page_config(page_title="🌍 Daily News Digest AI", layout="centered")

# --- RECUPERO CHIAVI API ---
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError:
    st.error("⚠️ Chiave GEMINI_API_KEY non trovata nei secrets. L'API di sintesi fallirà.")

try:
    NEWS_API_KEY = st.secrets["NEWS_API_KEY"]
except KeyError:
    st.error("⚠️ Chiave NEWS_API_KEY non trovata nei secrets. La raccolta dati fallirà.")

# URL base per l'API delle notizie (è un esempio, assicurati di usare l'URL del servizio che scegli)
BASE_NEWS_API_URL = "https://newsapi.org/v2/everything"

# ----------------------------------------------------------------------
# --- 📍 FONTI PERSONALIZZATE FISSE NEL CODICE ---
# Se vuoi includere siti specifici, inserisci i loro domini qui.
# Esempio: ["repubblica.it", "ilsole24ore.com", "aljazeera.com"]
# Se non vuoi fonti extra, lascia la lista vuota: []
CUSTOM_FIXED_SOURCES = [] # Lasciato vuoto per ora
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_news_from_api(query, language='it', limit=7):
    """
    Chiama l'API di notizie, filtrando per query nelle ultime 24 ore.
    """
    # Calcola l'ora di inizio 24 ore fa
    date_from = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%S')
    
    params = {
        'q': query,
        'language': language,
        'sortBy': 'publishedAt',
        'from': date_from,
        'pageSize': limit,
        'apiKey': NEWS_API_KEY
    }
    
    try:
        response = requests.get(BASE_NEWS_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        articles = [{'title': a['title'], 'description': a['description'], 'url': a['url']}
                    for a in data.get('articles', []) if a['description'] and a['title']]
        return articles
        
    except requests.exceptions.RequestException as e:
        st.warning(f"Errore nella connessione all'API per '{query}'. Controlla NEWS_API_KEY.")
        return []
    except Exception as e:
        st.warning(f"Errore generico nell'API per '{query}': {e}")
        return []


def run_news_collection(user_sources_list):
    """
    Esegue la raccolta delle notizie secondo l'ordine di importanza definito.
    """
    raw_digest_data = []
    
    # 1. Libano
    lebanon_news = get_news_from_api(query="Libano", language='it', limit=7)
    raw_digest_data.append({"section": "Libano", "articles": lebanon_news})

    # 2. Gaza
    gaza_news = get_news_from_api(query="Gaza", language='it', limit=7)
    raw_digest_data.append({"section": "Gaza", "articles": gaza_news})

    # 3. Medio Oriente (Generale, con focus e esclusione)
    me_news = get_news_from_api(query="(Medio Oriente OR Siria OR Palestina) NOT Libano NOT Gaza", language='it', limit=5)
    raw_digest_data.append({"section": "Medio Oriente (Siria, Palestina)", "articles": me_news})

    # 4. Italia politica interna
    italy_politics = get_news_from_api(query="Politica interna Italia", language='it', limit=5)
    raw_digest_data.append({"section": "Italia (Politica Interna)", "articles": italy_politics})
    
    # 5. Mondo (Notizie principali)
    world_news = get_news_from_api(query="Notizie principali", language='it', limit=5)
    raw_digest_data.append({"section": "Mondo (Principali)", "articles": world_news})
    
    # 6. Fonti Personalizzate (se presenti e definite in CUSTOM_FIXED_SOURCES)
    if user_sources_list:
        custom_news = []
        for site in user_sources_list:
            site_articles = get_news_from_api(query=f"site:{site}", language='it', limit=3)
            custom_news.extend(site_articles)
            
        if custom_news:
            raw_digest_data.append({"section": "Fonti Personalizzate", "articles": custom_news})

    return raw_digest_data


def summarize_with_gemini(raw_digest_data):
    """
    Invia i dati grezzi delle notizie a Gemini per sintetizzare e strutturare il digest.
    Correzione: usa GenerateContentConfig per system_instruction.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE ---
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    # --- 2. PREPARAZIONE DATI GREZZI PER IL PROMPT ---
    formatted_input = ""
    for section_data in raw_digest_data:
        section_title = section_data['section']
        articles = section_data['articles']
        
        if not articles:
            formatted_input += f"### {section_title} (NESSUNA NOTIZIA TROVATA)\n\n"
            continue
        
        formatted_input += f"### {section_title} ({len(articles)} ARTICOLI DA SINTETIZZARE)\n"
        for i, article in enumerate(articles):
            formatted_input += f"- Articolo {i+1}: {article['title']}\n"
            if article['description']:
                formatted_input += f"  Descrizione: {article['description'][:300]}...\n"
        formatted_input += "\n"

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
            "link_principale": types.Schema(type=types.Type.STRING, description="L'URL dell'articolo più rilevante trovato in questa sezione.")
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
            "Fonti Personalizzate": section_summary_schema 
        },
        required=["Libano", "Gaza", "Medio Oriente (Siria, Palestina)", "Italia (Politica Interna)", "Mondo (Principali)"]
    )
    
    # --- 4. PROMPT COMPLETO E CONFIGURAZIONE PER GEMINI ---

    system_instruction = """
    Sei un analista di notizie esperto e molto conciso. Genera un digest delle notizie principali delle ultime 24 ore.
    Sintetizza i contenuti di ogni sezione in modo obiettivo, neutrale e rigorosamente in italiano.
    L'output DEVE rispettare lo schema JSON fornito.
    """
    
    # Nuovo oggetto config che include le istruzioni di sistema (CORREZIONE ERRORE)
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=final_digest_schema,
    )

    prompt = f"""
    Genera il digest delle notizie basandoti sui seguenti articoli grezzi. 
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
            config=config, # Passiamo l'oggetto config completo qui
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
        # L'errore ora dovrebbe essere gestito correttamente dalla sintassi API
        st.error(f"Errore API Gemini: La chiamata è fallita. Causa: {e}. Controlla chiave e quota.")
        return None
    except json.JSONDecodeError:
        st.error("Errore di decodifica JSON: Gemini non ha restituito un formato JSON valido. Riprova.")
        return None
    except Exception as e:
        st.error(f"Errore inatteso durante la sintesi AI: {e}")
        return None


# ----------------------------------------------------------------------
# --------------------- INTERFACCIA STREAMLIT (Frontend) ---------------
# ----------------------------------------------------------------------

# --- LOGO E TITOLO (Corretto il NameError) ---
col_icon, col_title = st.columns([0.5, 6.5]) 

with col_icon:
    st.markdown("## 🌍") 

with col_title:
    st.title("Daily News") 

st.markdown("---")

# --- INTRODUZIONE ---
st.markdown("""
**Ciao! Lino Bandi ti da nuovamente il benvenuto e vuole aiutarti a 
fare una rapida sintesi delle notizie del giorno!**
""")

st.info("""
L'applicazione si appoggia sul sistema **Gemini AI Flash 2.5** e 
pertanto può commettere errori. Le fonti personalizzate sono definite nel codice.
""")
st.markdown("---")


# --- ESECUZIONE E DIGEST ---

# Passiamo la lista delle fonti fisse DEFINITE ALL'INIZIO (CUSTOM_FIXED_SOURCES)
user_sources_list = CUSTOM_FIXED_SOURCES

if st.button("▶️ Genera il Digest Quotidiano", type="primary"):
    
    if not NEWS_API_KEY or not GEMINI_API_KEY:
        st.error("Impossibile procedere. Le chiavi API sono mancanti nei secrets.")
        st.stop()
        
    progress_bar = st.progress(0, text="Avvio della raccolta articoli e sintesi con AI...")
    
    # 1. RACCOLTA DATI GREZZI
    progress_bar.progress(30, text="1/3: Raccolta articoli dalle API in corso...")
    raw_news_data = run_news_collection(user_sources_list)
    
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
        
        # Ordine di visualizzazione come richiesto
        sections_order = ["Libano", "Gaza", "Medio Oriente (Siria, Palestina)", "Italia (Politica Interna)", "Mondo (Principali)", "Fonti Personalizzate"]
        
        for section in sections_order:
            if section in final_digest:
                data = final_digest[section]
                
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
                    st.markdown(f"🔗 [Leggi l'articolo principale]({data['link_principale']})")
                
                st.markdown("---")
        
    else:
        st.error("⚠️ La generazione del digest è fallita. Verifica le chiavi API e prova a generare nuovamente.")
